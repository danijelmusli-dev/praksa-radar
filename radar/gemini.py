"""Gemini API (besplatan nivo) iza istog interfejsa kao anthropic.Anthropic.

Agenti (radar/agents/*) pozivaju `client.messages.create(model, system, messages, tools)` i čitaju
`resp.content` (blokovi `text` / `tool_use`), `resp.stop_reason` i `resp.usage`. GeminiClient
prevodi taj format u generateContent REST poziv i nazad, pa se agenti ne menjaju.

- tool_use ID-jeve izmišljamo mi (Gemini ih ne traži); `thoughtSignature` iz odgovora se čuva i
  vraća uz isti poziv alata (Gemini 3 ga zahteva u višekoračnim petljama)
- 429/5xx se ponavljaju sa dužim čekanjem (besplatan nivo ima mali RPM)
- greška, blokiran odgovor ili prazan odgovor = GeminiError (pozivalac degradira)
"""

from types import SimpleNamespace
from typing import Any

import httpx

from radar.http import request_with_retry

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiError(Exception):
    pass


def _system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    return "\n\n".join(b.get("text", "") for b in system or [] if b.get("type") == "text")


def to_contents(messages: list[dict], id_names: dict[str, str], sigs: dict[str, str]) -> list[dict]:
    """Anthropic poruke -> Gemini `contents`. id_names mapira tool_use id na ime alata."""
    out: list[dict] = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": role, "parts": [{"text": content}]})
            continue
        parts: list[dict] = []
        for b in content:
            kind = b.get("type")
            if kind == "text":
                if b.get("text"):
                    parts.append({"text": b["text"]})
            elif kind == "tool_use":
                id_names[b["id"]] = b["name"]
                part: dict = {"functionCall": {"name": b["name"], "args": b.get("input") or {}}}
                if b["id"] in sigs:
                    part["thoughtSignature"] = sigs[b["id"]]
                parts.append(part)
            elif kind == "tool_result":
                key = "error" if b.get("is_error") else "output"
                parts.append(
                    {
                        "functionResponse": {
                            "name": id_names.get(b["tool_use_id"], "alat"),
                            "response": {key: b.get("content", "")},
                        }
                    }
                )
        if parts:
            out.append({"role": role, "parts": parts})
    return out


class _Messages:
    def __init__(self, owner: "GeminiClient"):
        self._o = owner

    def create(
        self,
        *,
        model: str,
        messages: list[dict],
        system: Any = "",
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        json_mode: bool = False,
        **_ignored: Any,  # npr. cache_control: Gemini ga ne treba
    ) -> SimpleNamespace:
        o = self._o
        gen: dict[str, Any] = {
            # "razmišljanje" se računa u izlazne tokene, pa mu dajemo prostora
            "maxOutputTokens": max_tokens * 2 + 1024,
        }
        if json_mode:
            gen["responseMimeType"] = "application/json"
        body: dict[str, Any] = {
            "contents": to_contents(messages, o.id_names, o.sigs),
            "generationConfig": gen,
        }
        sys_text = _system_text(system)
        if sys_text:
            body["systemInstruction"] = {"parts": [{"text": sys_text}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parametersJsonSchema": t.get("input_schema")
                            or {"type": "object", "properties": {}},
                        }
                        for t in tools
                    ]
                }
            ]
        name = model.removeprefix("models/")
        try:
            resp = request_with_retry(
                o.http,
                "POST",
                f"{BASE}/models/{name}:generateContent",
                json=body,
                headers={"x-goog-api-key": o.api_key},
                max_attempts=4,
                base_delay=4.0,
            )
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise GeminiError(f"Gemini API {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise GeminiError(f"Gemini nedostupan: {type(exc).__name__}") from exc
        return o.parse(data)


class GeminiClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None, timeout: float = 60.0):
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=timeout)
        self.id_names: dict[str, str] = {}
        self.sigs: dict[str, str] = {}
        self._n = 0
        self.messages = _Messages(self)

    def parse(self, data: dict) -> SimpleNamespace:
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "prazan odgovor")
            raise GeminiError(f"Gemini bez odgovora: {reason}")
        cand = cands[0]
        blocks: list[SimpleNamespace] = []
        for p in (cand.get("content") or {}).get("parts") or []:
            if "functionCall" in p:
                self._n += 1
                call_id = f"gemini_{self._n}"
                fc = p["functionCall"]
                self.id_names[call_id] = fc["name"]
                if p.get("thoughtSignature"):
                    self.sigs[call_id] = p["thoughtSignature"]
                blocks.append(
                    SimpleNamespace(
                        type="tool_use", id=call_id, name=fc["name"], input=fc.get("args") or {}
                    )
                )
            elif p.get("text") and not p.get("thought"):
                blocks.append(SimpleNamespace(type="text", text=p["text"]))
        u = data.get("usageMetadata") or {}
        usage = SimpleNamespace(
            input_tokens=u.get("promptTokenCount", 0),
            output_tokens=u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0),
        )
        if any(b.type == "tool_use" for b in blocks):
            stop = "tool_use"
        elif cand.get("finishReason") == "MAX_TOKENS":
            stop = "max_tokens"
        else:
            stop = "end_turn"
        if not blocks and cand.get("finishReason") not in (None, "STOP", "MAX_TOKENS"):
            raise GeminiError(f"Gemini prekid: {cand.get('finishReason')}")
        return SimpleNamespace(content=blocks, stop_reason=stop, usage=usage)
