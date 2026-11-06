"""Predlozi novih izvora: čuvanje, poruka sa dugmićima i PR za dodavanje (posle mog klika)."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from radar.agents import guard
from radar.agents.discovery import (
    DiscoveryTools,
    Proposal,
    entry_for,
    entry_yaml,
    known_hosts,
    verify_proposal,
)
from radar.agents.repair import RepairOutcome, clean_fixture, run_pytest
from radar.agents.research import WebTools
from radar.github_app import installation_token, open_pr, remote_url
from radar.gitops import Git, GitError
from radar.web import _public_host, fetch_following

log = logging.getLogger(__name__)


def store_proposals(conn, verified: list[tuple[Proposal, int]]) -> list[int]:
    ids = []
    for p, count in verified:
        row = conn.execute(
            "insert into source_proposals (name, url, payload)"
            " values (%s, %s, %s::jsonb) returning id",
            (
                p.name,
                p.url,
                json.dumps({"proposal": p.model_dump(), "count": count}, ensure_ascii=False),
            ),
        ).fetchone()
        ids.append(row[0])
    conn.commit()
    return ids


def proposal_message(pid: int, p: Proposal, count: int) -> tuple[str, dict]:
    sample = "\n".join(f"• {t}" for t in p.uzorak[:3])
    text = (
        f"Predlog novog izvora #{pid}: {p.name}\n{p.url}\n"
        f"Selektor daje {count} stavki.\nZašto: {p.why}\n{sample}\n\n"
        "Dodavanje ide kao PR koji ti odobravaš."
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Dodaj", "callback_data": f"sa:{pid}"},
                {"text": "❌ Odbaci", "callback_data": f"sr:{pid}"},
            ]
        ]
    }
    return text.strip(), markup


def add_source_pr(
    deps,
    pid: int,
    root: Path,
    git: Git | None = None,
    gh: httpx.Client | None = None,
    token_provider: Callable = installation_token,
    resolve: Callable[[str], bool] = _public_host,
    pytest_runner: Callable[[Path], tuple[bool, str]] = run_pytest,
) -> RepairOutcome:
    conn, s = deps.conn, deps.settings
    row = conn.execute(
        "select status, payload from source_proposals where id = %s", (pid,)
    ).fetchone()
    if row is None:
        deps.send(f"Predlog #{pid} ne postoji.")
        return RepairOutcome(False, "ne postoji")
    if row[0] != "accepted":
        deps.send(f"Predlog #{pid} nije potvrđen (status: {row[0]}).")
        return RepairOutcome(False, "nije potvrđen")

    p = Proposal.model_validate(row[1]["proposal"])  # ponovo validirano, ne verujemo bazi
    config = root / "config" / "sources.yaml"
    git = git or Git(root)
    if git.changed_paths():
        raise RuntimeError("radno stablo nije čisto")
    base_branch = git.current_branch()

    web = WebTools(deps.http, [p.url], resolve=resolve)  # samo domen predloga
    probe_tools = DiscoveryTools(web, config)

    def fail(reason: str) -> RepairOutcome:
        git.discard_changes()
        deps.send(f"Dodavanje izvora {p.name} nije uspelo: {reason}")
        return RepairOutcome(False, reason)

    ok, why = verify_proposal(p, probe_tools, known_hosts(config))
    if not ok:
        return fail(why)
    count = int(why.split()[0])

    _, resp = fetch_following(deps.http, p.url, web._validate)
    fixture = f"{p.name}.html"
    fx = root / "tests" / "fixtures" / fixture
    fx.parent.mkdir(parents=True, exist_ok=True)
    fx.write_text(clean_fixture(resp.text), encoding="utf-8")
    config.write_text(
        config.read_text(encoding="utf-8").rstrip("\n")
        + "\n"
        + entry_yaml(entry_for(p, fixture, count)),
        encoding="utf-8",
    )

    paths = git.changed_paths()
    errors = guard.check_diff(
        paths,
        lambda x: git.show("HEAD", x),
        lambda x: (root / x).read_text(encoding="utf-8"),
        "add-source",
    )
    if errors:
        return fail("diff van dozvoljenog: " + "; ".join(errors[:3]))
    passed, output = pytest_runner(root)
    if not passed:
        return fail("testovi ne prolaze: " + output.strip().splitlines()[-1][:150])

    gh = gh or httpx.Client(timeout=30)
    try:
        token = token_provider(s, gh)
        branch = f"agent/add-source-{p.name}-{datetime.now(UTC):%Y%m%d%H%M}"
        git.commit_on_new_branch(
            branch, f"feat(izvori): dodaj {p.name} (agent 2)", "praksa-radar-agent",
            "agent@users.noreply.github.com",
        )  # fmt: skip
        git.push(remote_url(s.github_repository, token), branch)
        number, url = open_pr(
            gh, token, s.github_repository, branch, f"feat(izvori): dodaj {p.name}",
            f"Novi izvor predložio agent 2 i potvrdio si ga dugmetom.\n\n{p.why}\n\n"
            f"Selektori: `{p.item}` / `{p.title}` / `{p.link}`, {count} stavki u fixture-u.\n"
            "**Pregledaj `config/sources.yaml` i uslove korišćenja sajta pre merge-a.**",
            base=base_branch if base_branch != "HEAD" else "main",
        )  # fmt: skip
    except (GitError, httpx.HTTPError) as exc:
        git.run("checkout", base_branch, check=False)
        return fail(f"PR nije otvoren: {exc}")
    git.run("checkout", base_branch, check=False)
    conn.execute(
        "update source_proposals set status = 'added', resolved_at = now(),"
        " payload = payload || %s::jsonb where id = %s",
        (json.dumps({"pr": number}), pid),
    )
    conn.commit()
    deps.send(f"PR #{number} za izvor {p.name} čeka tvoj merge.\n{url}")
    return RepairOutcome(True, "PR otvoren", number, url)
