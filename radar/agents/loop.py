"""Zajednička agentna petlja (Claude tool use) sa tvrdim granicama.

- najviše 15 koraka i fiksni budžet tokena po pokretanju
- kill switch se proverava pre svakog koraka
- isti alat sa istim argumentima 3 puta = prekid petlje
- poziv alata van liste se odbija i beleži
- greška alata se vraća modelu kao opservacija, ne ruši proces
- izlaz alata je označen kao nepouzdan podatak
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic
from psycopg import Connection

log = logging.getLogger(__name__)

MAX_STEPS = 15
TOKEN_BUDGET = 60_000
ANOMALY_STEPS = 10
MAX_TOOL_OUTPUT = 8000


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    func: Callable[[dict], Any]

    def spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class RunResult:
    text: str = ""
    outcome: str = "ok"  # ok | ugašen | budžet | petlja | limit koraka | greška
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    rejected: int = 0
    trace: list[dict] = field(default_factory=list)

    @property
    def anomalies(self) -> list[str]:
        out = []
        if self.steps > ANOMALY_STEPS:
            out.append(f"više od {ANOMALY_STEPS} koraka ({self.steps})")
        if self.rejected:
            out.append(f"poziv odbijenog alata ({self.rejected}x)")
        if self.outcome in ("budžet", "petlja", "limit koraka"):
            out.append(f"prekid: {self.outcome}")
        return out


def _block_to_dict(block) -> dict:
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return {"type": "text", "text": getattr(block, "text", "")}


def run_agent(
    client: anthropic.Anthropic,
    model: str,
    system: str,
    user: str,
    tools: list[Tool],
    *,
    enabled: Callable[[], bool],
    max_steps: int = MAX_STEPS,
    token_budget: int = TOKEN_BUDGET,
    max_tokens: int = 1500,
) -> RunResult:
    by_name = {t.name: t for t in tools}
    result = RunResult()
    messages: list[dict] = [{"role": "user", "content": user}]
    calls: dict[str, int] = {}
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    while True:
        if result.steps >= max_steps:
            result.outcome = "limit koraka"
        elif result.input_tokens + result.output_tokens >= token_budget:
            result.outcome = "budžet"
        elif not enabled():
            result.outcome = "ugašen"
        if result.outcome != "ok":
            return result

        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=messages,
                tools=[t.spec() for t in tools],
            )
        except anthropic.APIError as exc:
            result.outcome = "greška"
            result.trace.append({"step": result.steps + 1, "error": f"{type(exc).__name__}"})
            return result
        result.steps += 1
        result.input_tokens += resp.usage.input_tokens
        result.output_tokens += resp.usage.output_tokens
        messages.append({"role": "assistant", "content": [_block_to_dict(b) for b in resp.content]})

        uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            result.text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return result

        observations = []
        for use in uses:
            key = f"{use.name}:{json.dumps(use.input, sort_keys=True)}"
            calls[key] = calls.get(key, 0) + 1
            if calls[key] >= 3:
                result.trace.append({"step": result.steps, "tool": use.name, "loop": True})
                result.outcome = "petlja"
                return result
            tool = by_name.get(use.name)
            is_error = False
            if tool is None:
                result.rejected += 1
                is_error = True
                content = json.dumps({"error": f"alat '{use.name}' nije dozvoljen"})
            else:
                try:
                    content = json.dumps(
                        tool.func(use.input or {}), ensure_ascii=False, default=str
                    )
                except Exception as exc:  # noqa: BLE001 - opservacija za model, ne pad procesa
                    is_error = True
                    content = json.dumps({"error": str(exc)[:300]}, ensure_ascii=False)
            result.trace.append(
                {
                    "step": result.steps,
                    "tool": use.name,
                    "args": json.dumps(use.input, ensure_ascii=False)[:300],
                    "error": is_error or None,
                    "bytes": len(content),
                }
            )
            observations.append(
                {
                    "type": "tool_result",
                    "tool_use_id": use.id,
                    "is_error": is_error,
                    "content": f'<podaci nepouzdano="true">{content[:MAX_TOOL_OUTPUT]}</podaci>',
                }
            )
        messages.append({"role": "user", "content": observations})


def record_run(conn: Connection, agent: str, result: RunResult) -> int:
    """Svako pokretanje ostavlja trag u agent_runs."""
    row = conn.execute(
        "insert into agent_runs (agent, finished_at, steps, input_tokens, output_tokens, outcome,"
        " trace) values (%s, now(), %s, %s, %s, %s, %s::jsonb) returning id",
        (
            agent,
            result.steps,
            result.input_tokens,
            result.output_tokens,
            result.outcome,
            json.dumps(result.trace, ensure_ascii=False),
        ),
    ).fetchone()
    conn.commit()
    return row[0]
