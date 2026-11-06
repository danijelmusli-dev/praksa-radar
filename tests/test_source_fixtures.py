"""Oracle za samopopravku: izvori sa snimljenim fixture-om moraju da daju stavke.

- html_generic izvor sa `fixture: ime.html` u config/sources.yaml: selektori nad tim fixture-om
  moraju da vrate bar `fixture_min` (podrazumevano 1) stavki
- svi tests/fixtures/helloworld_*.html moraju da se parsiraju (adapter ostaje kompatibilan)
Agent 1 sme da dodaje fixture-e i menja selektore, ali ne i da menja ovaj test.
"""

from pathlib import Path

import pytest
import yaml

from radar.sources.helloworld import parse_helloworld
from radar.sources.html_generic import parse_html_generic

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SELECTOR_KEYS = ("item", "title", "link", "text", "org", "location")


def generic_entries():
    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")) or {}
    return [
        e
        for e in cfg.get("sources") or []
        if e.get("type") == "html_generic" and e.get("fixture") and e.get("enabled", True)
    ]


@pytest.mark.parametrize("entry", generic_entries(), ids=lambda e: e["name"])
def test_generic_source_selectors_match_saved_fixture(entry):
    html = (FIXTURES / entry["fixture"]).read_text(encoding="utf-8")
    items = parse_html_generic(
        html,
        entry["url"],
        entry["name"],
        **{k: entry[k] for k in SELECTOR_KEYS if k in entry},
    )
    assert len(items) >= int(entry.get("fixture_min", 1)), f"{entry['name']}: selektori ne pogađaju"
    assert all(i.title and i.url.startswith("http") for i in items)


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("helloworld_*.html")), ids=lambda p: p.name)
def test_helloworld_parser_handles_every_saved_fixture(path):
    assert parse_helloworld(path.read_text(encoding="utf-8")), f"{path.name}: nema stavki"
