"""Eval agenta 1 (samopopravka): 10 „pokvarenih“ sajtova, pravi model, bez baze i git-a.

    ANTHROPIC_API_KEY=... uv run python -m evals.repair_eval [--min-pass 0.7]

Svaki scenario: izvor ima zastarele selektore, sajt ima novu strukturu. Agent mora da pronađe
nove selektore i da promeni samo svoj izvor u config/sources.yaml. Meri se udeo popravki koje
prolaze oracle (isti kao tests/test_source_fixtures.py), prosečan broj koraka i tokena.
"""

import argparse
import sys
import tempfile
from pathlib import Path

import httpx
import yaml

from radar.agents import guard
from radar.agents.loop import run_agent
from radar.agents.repair import SYSTEM, TOKEN_BUDGET, RepairTools
from radar.config import get_settings
from radar.http import PoliteClient
from radar.providers import make_agent_client
from radar.sources.html_generic import parse_html_generic

JOBS = [
    ("Letnja praksa ML", "/j/1"),
    ("Embedded Intern", "/j/2"),
    ("QA Trainee", "/j/3"),
    ("Backend praksa", "/j/4"),
]


def tpl(item: str, wrap: str = "{}") -> str:
    return (
        "<html><body><h1>Karijera</h1>"
        + wrap.format("".join(item.format(t=t, h=h) for t, h in JOBS))
        + "</body></html>"
    )


# (naziv, zastareli selektori, nova stranica)
SCENARIOS: list[tuple[str, dict, str]] = [
    ("preimenovane klase", {"item": ".job-card", "title": "h3", "link": "a"},
     tpl('<div class="position-card"><h3>{t}</h3><a href="{h}">Detalji</a></div>')),
    ("promenjen tag", {"item": "li.job", "title": "h3", "link": "a"},
     tpl('<article class="vacancy"><h3>{t}</h3><a href="{h}">Prijava</a></article>')),
    ("tabela", {"item": ".job-card", "title": "h3", "link": "a"},
     tpl('<tr class="row"><td class="naziv">{t}</td><td><a href="{h}">otvori</a></td></tr>', "<table>{}</table>")),
    ("ugnježdeno", {"item": ".job-card", "title": ".title", "link": "a"},
     tpl('<div class="listing"><div class="card"><h2 class="heading"><a href="{h}">{t}</a></h2></div></div>')),
    ("data atributi", {"item": ".job", "title": ".t", "link": "a"},
     tpl('<div data-job="1"><span data-field="title">{t}</span><a data-field="link" href="{h}">više</a></div>')),
    ("BEM klase", {"item": ".card", "title": ".card__title", "link": ".card__link"},
     tpl('<div class="jobs__item"><h3 class="jobs__title">{t}</h3><a class="jobs__link" href="{h}">vidi</a></div>')),
    ("utility klase", {"item": ".job-card", "title": "h3", "link": "a"},
     tpl('<div class="flex p-4 border rounded"><h3 class="font-bold text-lg">{t}</h3><a class="text-blue-500" href="{h}">više</a></div>')),
    ("lista bez klasa", {"item": ".job-card", "title": "h3", "link": "a"},
     tpl('<li><a href="{h}">{t}</a></li>', '<ul class="openings">{}</ul>')),
    ("link na samoj kartici", {"item": ".job-card", "title": "h3", "link": "a"},
     tpl('<a class="tile" href="{h}"><span class="tile-title">{t}</span></a>')),
    ("definition lista", {"item": ".job-card", "title": "h3", "link": "a"},
     tpl('<dt class="role"><a href="{h}">{t}</a></dt>', '<dl class="roles">{}</dl>')),
]  # fmt: skip


def oracle(root: Path) -> tuple[bool, str]:
    cfg = yaml.safe_load((root / "config" / "sources.yaml").read_text(encoding="utf-8"))
    for e in cfg["sources"]:
        if e.get("fixture"):
            html = (root / "tests" / "fixtures" / e["fixture"]).read_text(encoding="utf-8")
            sel = {k: e[k] for k in ("item", "title", "link", "text", "org", "location") if k in e}
            n = len(parse_html_generic(html, e["url"], e["name"], **sel))
            if n < int(e.get("fixture_min", 1)):
                return False, f"FAILED: {n} stavki"
    return True, "ok"


def run_scenario(client, model: str, idx: int, name: str, stale: dict, html: str) -> dict:
    host = f"sajt-{idx:02d}.test"
    entry = {
        "name": "izvor",
        "type": "html_generic",
        "url": f"https://{host}/karijera",
        **stale,
        "min_expected": 1,
    }
    cfg_text = yaml.safe_dump({"sources": [entry]}, sort_keys=False)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir()
        (root / "tests" / "fixtures").mkdir(parents=True)
        (root / "config" / "sources.yaml").write_text(cfg_text, encoding="utf-8")

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, html=html, headers={"content-type": "text/html"})

        http = PoliteClient(
            client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
            sleep=lambda s: None,
        )
        tools = RepairTools(root, entry, http, resolve=lambda h: True, pytest_runner=oracle)
        user = f"Izvor: izvor (tip html_generic)\nURL: {entry['url']}\nPoslednja greška: lista je prazna\nTrenutna konfiguracija:\n{entry}"
        result = run_agent(client, model, SYSTEM, user, tools.tools(), enabled=lambda: True,
                           max_steps=12, token_budget=TOKEN_BUDGET, max_tokens=4000)  # fmt: skip
        new_cfg = (root / "config" / "sources.yaml").read_text(encoding="utf-8")
        passed, _ = oracle(root)
        changed = new_cfg != cfg_text
        errors = (
            guard.check_sources_yaml(cfg_text, new_cfg, "repair") if changed else ["nije menjao"]
        )
        return {
            "scenario": name,
            "ok": passed and changed and not errors,
            "steps": result.steps,
            "tokens": result.input_tokens + result.output_tokens,
            "outcome": result.outcome,
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-pass", type=float, default=0.7)
    ap.add_argument("--model", default=None)
    args = ap.parse_args(argv)
    s = get_settings()
    if not s.has_llm:
        print("GEMINI_API_KEY (ili ANTHROPIC_API_KEY) nije postavljen")
        return 2
    client = make_agent_client(s, timeout=90.0)
    rows = [
        run_scenario(client, args.model or s.research_model, i, *sc)
        for i, sc in enumerate(SCENARIOS, 1)
    ]
    for r in rows:
        print(
            f"{'OK  ' if r['ok'] else 'FAIL'} {r['scenario']:<24} koraka={r['steps']:>2} tokena={r['tokens']:>6} ({r['outcome']})"
        )
    rate = sum(r["ok"] for r in rows) / len(rows)
    print(f"\npopravki koje prolaze: {rate:.0%}; prosek: {sum(r['steps'] for r in rows) / len(rows):.1f} koraka, "
          f"{sum(r['tokens'] for r in rows) // len(rows)} tokena")  # fmt: skip
    return 0 if rate >= args.min_pass else 1


if __name__ == "__main__":
    sys.exit(main())
