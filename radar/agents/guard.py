"""Guard za PR-ove agenata: šta smeju da izmene. Koristi ga i agent (pre pušovanja) i CI.

CI odbija svaki diff van radar/sources/<adapter>.py, tests/fixtures/ i selektora u
config/sources.yaml; ne sme da uđe novi paket, workflow, migracija, Worker ni testovi.
"""

import ast
import sys
from pathlib import PurePosixPath

import yaml

MODES = ("repair", "add-source", "revert")
SELECTOR_KEYS = {
    "item",
    "title",
    "link",
    "text",
    "org",
    "location",
    "queries",
    "fixture",
    "fixture_min",
}
NEVER_ADAPTER = {"base.py", "registry.py", "__init__.py"}
THIRD_PARTY_OK = {"httpx", "bs4", "lxml", "yaml", "pydantic", "radar"}  # već u pyproject.toml
STDLIB = set(sys.stdlib_module_names)


def mode_for_branch(branch: str) -> str | None:
    for mode, prefix in (
        ("repair", "agent/repair-"),
        ("add-source", "agent/add-source-"),
        ("revert", "agent/revert-"),
    ):
        if branch.startswith(prefix):
            return mode
    return None


def check_paths(paths: list[str]) -> list[str]:
    """Dozvoljeno: adapter u radar/sources/, fixture-i, config/sources.yaml. Sve ostalo ne."""
    bad = []
    for raw in paths:
        p = PurePosixPath(raw)
        if ".." in p.parts or p.is_absolute():
            bad.append(f"{raw}: nedozvoljena putanja")
        elif p.parts[:2] == ("tests", "fixtures") and len(p.parts) >= 3:
            continue
        elif p.parts[:2] == ("radar", "sources") and len(p.parts) == 3 and p.suffix == ".py":
            if p.name in NEVER_ADAPTER:
                bad.append(f"{raw}: infrastruktura izvora, ne adapter")
        elif raw == "config/sources.yaml":
            continue
        else:
            bad.append(f"{raw}: van dozvoljenih putanja")
    return bad


def _entries(text: str) -> list[dict]:
    return list((yaml.safe_load(text) or {}).get("sources") or [])


def check_sources_yaml(base: str, head: str, mode: str) -> list[str]:
    """repair: samo selektori postojećih izvora. add-source: tačno jedan nov html_generic izvor."""
    if mode == "revert":
        return []
    try:
        before, after = _entries(base), _entries(head)
    except yaml.YAMLError:
        return ["config/sources.yaml nije ispravan YAML"]
    old = {e["name"]: e for e in before}
    new = {e["name"]: e for e in after}
    errors = []
    if mode == "repair":
        if set(old) != set(new):
            errors.append("popravka ne sme da dodaje ni uklanja izvore")
        for name, entry in new.items():
            changed = {
                k
                for k in set(entry) | set(old.get(name, {}))
                if entry.get(k) != old.get(name, {}).get(k)
            }
            if changed - SELECTOR_KEYS:
                errors.append(
                    f"{name}: izmenjena zabranjena polja {sorted(changed - SELECTOR_KEYS)}"
                )
        return errors
    added = set(new) - set(old)
    if len(added) != 1 or set(old) - set(new):
        return ["add-source mora da doda tačno jedan novi izvor i ništa da ne ukloni"]
    if any(new[n] != old[n] for n in old):
        errors.append("postojeći izvori se ne smeju menjati")
    entry = new[next(iter(added))]
    if entry.get("type") != "html_generic" or not str(entry.get("url", "")).startswith("https://"):
        errors.append("novi izvor mora biti html_generic sa https URL-om")
    for key in ("item", "title", "link"):
        if not entry.get(key):
            errors.append(f"novom izvoru fali '{key}'")
    return errors


def check_imports(source: str, filename: str = "adapter") -> list[str]:
    """Bez novih paketa: adapter sme samo stdlib i zavisnosti koje već postoje."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{filename}: sintaksna greška ({exc.msg})"]
    bad = []
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods = [node.module.split(".")[0]]
        bad += [
            f"{filename}: nedozvoljen import '{m}'"
            for m in mods
            if m not in STDLIB | THIRD_PARTY_OK
        ]
    return bad


def check_diff(paths: list[str], read_base, read_head, mode: str) -> list[str]:
    """paths: izmenjene datoteke; read_base/read_head(path) vraćaju sadržaj pre/posle."""
    errors = check_paths(paths)
    if "config/sources.yaml" in paths:
        errors += check_sources_yaml(
            read_base("config/sources.yaml"), read_head("config/sources.yaml"), mode
        )
    for p in paths:
        if p.startswith("radar/sources/") and p.endswith(".py") and mode != "revert":
            errors += check_imports(read_head(p), p)
    return errors
