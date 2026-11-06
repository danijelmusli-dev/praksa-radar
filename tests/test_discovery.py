"""Agent 2 (otkrivanje izvora) i dodavanje potvrđenog izvora kroz PR."""

import json
import subprocess
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from radar.agents.discovery import (
    Proposal,
    discover_sources,
    entry_for,
    entry_yaml,
    known_hosts,
    parse_proposals,
)
from radar.agents.proposals import add_source_pr, proposal_message, store_proposals
from radar.agents.research import WebTools
from radar.config import Settings
from radar.gitops import Git
from radar.http import PoliteClient
from radar.tasks import Deps, TaskError, run_task
from tests.helpers import ScriptedAnthropic, text_resp, tool_resp

CONFIG = """\
sources:
  - name: helloworld
    type: helloworld
  - name: stari
    type: html_generic
    url: https://stari.rs/jobs
    item: .j
    title: h3
    link: a
  # primer:
  # - name: x
"""
LISTING = (
    "<html><body>"
    + "".join(
        f'<div class="oglas"><h3>Letnja praksa {i}</h3><a href="/o/{i}">više</a></div>'
        for i in range(1, 5)
    )
    + "</body></html>"
)
GOOD = {
    "name": "novi-sajt",
    "url": "https://novi.rs/prakse",
    "item": ".oglas",
    "title": "h3",
    "link": "a",
    "why": "Objavljuje nove prakse svake nedelje",
    "uzorak": ["Letnja praksa 1", "Letnja praksa 2"],
}


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    (root / "config").mkdir(parents=True)
    (root / "tests" / "fixtures").mkdir(parents=True)
    sh(root, "git", "init", "-b", "main")
    sh(root, "git", "config", "user.name", "t")
    sh(root, "git", "config", "user.email", "t@t")
    (root / "config" / "sources.yaml").write_text(CONFIG, encoding="utf-8")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-m", "init")
    return root


def config(tmp_path: Path) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(CONFIG, encoding="utf-8")
    return p


def mock_novi(page=LISTING):
    respx.get("https://novi.rs/robots.txt").respond(404)
    return respx.get("https://novi.rs/prakse").respond(200, html=page)


def search(results):
    return respx.get("https://api.search.brave.com/res/v1/web/search").respond(
        200, json={"web": {"results": results}}
    )


def web(seeds=(), **kw):
    return WebTools(
        PoliteClient(sleep=lambda s: None), list(seeds), brave_key="k", resolve=lambda h: True, **kw
    )


# ---- predlozi i njihova šema ----------------------------------------------------------------


def test_proposal_schema_is_strict():
    assert Proposal(**GOOD).name == "novi-sajt"
    for patch in (
        {"url": "http://novi.rs/prakse"},
        {"url": "https://novi.rs/prakse?id=1"},
        {"name": "Loše Ime"},
        {"why": "x" * 301},
        {"item": ""},
    ):
        with pytest.raises(ValueError):
            Proposal(**{**GOOD, **patch})
    with pytest.raises(ValueError):
        Proposal(**GOOD, dodatno="polje van šeme")


def test_parse_proposals_discards_garbage_and_overlong_lists():
    assert [p.name for p in parse_proposals(json.dumps({"proposals": [GOOD]}))] == ["novi-sajt"]
    assert parse_proposals("nije json") == []
    assert parse_proposals(json.dumps({"proposals": [{**GOOD, "url": "ftp://x"}]})) == []
    assert parse_proposals(json.dumps({"proposals": [GOOD] * 4})) == []


def test_known_hosts_and_entry_yaml(tmp_path):
    assert known_hosts(config(tmp_path)) == {"stari.rs": "stari", "helloworld.rs": "helloworld"}
    entry = entry_for(Proposal(**GOOD), "novi-sajt.html", 4)
    block = entry_yaml(entry)
    merged = yaml.safe_load(CONFIG.rstrip("\n") + "\n" + block)
    assert [e["name"] for e in merged["sources"]] == ["helloworld", "stari", "novi-sajt"]
    assert merged["sources"][2]["fixture"] == "novi-sajt.html" and entry["fixture_min"] == 3


# ---- agent 2 --------------------------------------------------------------------------------


@respx.mock
def test_discovery_proposes_only_what_code_verifies(tmp_path):
    mock_novi()
    search([{"title": "Prakse", "url": "https://novi.rs/prakse", "description": "lista"}])
    same_host = {**GOOD, "name": "isti-host"}
    duplicate = {**GOOD, "name": "dupl", "url": "https://stari.rs/jobs"}
    client = ScriptedAnthropic(
        tool_resp("list_known_sources", {}, id="a"),
        tool_resp("web_search", {"query": "prakse srbija"}, id="b"),
        tool_resp("fetch_page", {"url": "https://novi.rs/prakse"}, id="c"),
        tool_resp(
            "test_selector",
            {"url": "https://novi.rs/prakse", "item": ".oglas", "title": "h3", "link": "a"},
            id="d",
        ),
        text_resp(json.dumps({"proposals": [GOOD, same_host, duplicate]})),
    )
    verified, run, rejected = discover_sources(
        client, "m", web=web(), config_path=config(tmp_path), topics=None, enabled=lambda: True
    )
    assert run.outcome == "ok"
    assert [(p.name, n) for p, n in verified] == [("novi-sajt", 4)]
    assert any("isti-host" in r and "već postoji (novi-sajt)" in r for r in rejected)
    assert any("dupl" in r and "već postoji (stari)" in r for r in rejected)
    # agent vidi samo teme i alate, bez ikakvog profila
    first_user = client.calls[0]["messages"][0]["content"]
    assert first_user.startswith("Teme za pretragu") and "profil" not in first_user.lower()
    assert 'nepouzdano="true"' in client.observations(3)
    assert "stari" in client.observations(1)  # list_known_sources


@respx.mock
def test_selector_that_matches_nothing_is_rejected_by_code(tmp_path):
    mock_novi()
    bad = {**GOOD, "item": ".nema"}
    client = ScriptedAnthropic(text_resp(json.dumps({"proposals": [bad]})))
    verified, _, rejected = discover_sources(
        client, "m", web=web(seeds=[GOOD["url"]]), config_path=config(tmp_path), topics=None,
        enabled=lambda: True,
    )  # fmt: skip
    assert verified == [] and "samo 0 stavki" in rejected[0]


@respx.mock
def test_proposal_for_a_site_the_agent_never_saw_is_rejected(tmp_path):
    page = mock_novi()
    client = ScriptedAnthropic(text_resp(json.dumps({"proposals": [GOOD]})))  # izmišljen URL
    verified, _, rejected = discover_sources(
        client, "m", web=web(), config_path=config(tmp_path), topics=None, enabled=lambda: True
    )
    assert verified == [] and page.call_count == 0 and "provera nije uspela" in rejected[0]


@respx.mock
def test_discovery_cannot_open_urls_that_search_did_not_return(tmp_path):
    evil = respx.get(url__startswith="https://zli.rs").respond(200, html="x")
    search([])
    client = ScriptedAnthropic(
        tool_resp("web_search", {"query": "prakse"}, id="a"),
        tool_resp("fetch_page", {"url": "https://zli.rs/?d=1"}, id="b"),
        text_resp('{"proposals": []}'),
    )
    verified, run, _ = discover_sources(
        client, "m", web=web(), config_path=config(tmp_path), topics=["x"], enabled=lambda: True
    )
    assert evil.call_count == 0 and verified == [] and run.outcome == "ok"
    assert "host nije viđen" in client.observations(2)


@respx.mock
def test_discovery_search_filters_personal_terms_and_respects_kill_switch(tmp_path):
    search([])
    w = web(owner_terms=["pera perić"])
    client = ScriptedAnthropic(
        tool_resp("web_search", {"query": "pera perić prakse"}, id="a"),
        text_resp('{"proposals": []}'),
    )
    discover_sources(
        client, "m", web=w, config_path=config(tmp_path), topics=None, enabled=lambda: True
    )
    assert "lični podatak" in client.observations(1)

    off = ScriptedAnthropic(text_resp("x"))
    _, run, _ = discover_sources(
        off, "m", web=w, config_path=config(tmp_path), topics=None, enabled=lambda: False
    )
    assert run.outcome == "ugašen" and off.calls == []


@respx.mock
def test_unreadable_proposal_page_is_rejected_by_robots(tmp_path):
    respx.get("https://novi.rs/robots.txt").respond(200, text="User-agent: *\nDisallow: /")
    page = respx.get("https://novi.rs/prakse").respond(200, html=LISTING)
    client = ScriptedAnthropic(text_resp(json.dumps({"proposals": [GOOD]})))
    verified, _, rejected = discover_sources(
        client, "m", web=web(seeds=[GOOD["url"]]), config_path=config(tmp_path), topics=None,
        enabled=lambda: True,
    )  # fmt: skip
    assert verified == [] and page.call_count == 0 and "provera nije uspela" in rejected[0]


# ---- dugmići i čuvanje predloga ---------------------------------------------------------------


def test_store_and_message(fresh_conn):
    p = Proposal(**GOOD)
    [pid] = store_proposals(fresh_conn, [(p, 4)])
    text, markup = proposal_message(pid, p, 4)
    assert f"#{pid}" in text and "Letnja praksa 1" in text and "https://novi.rs/prakse" in text
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == [f"sa:{pid}", f"sr:{pid}"]
    assert fresh_conn.execute("select status from source_proposals").fetchone()[0] == "pending"


# ---- PR za dodavanje ---------------------------------------------------------------------------


class LocalGit(Git):
    remote = ""

    def push(self, remote_url, branch):
        super().push(self.remote, branch)


def deps(conn, sent):
    return Deps(
        conn=conn,
        settings=Settings(_env_file=None, github_repository="me/r"),
        send=lambda t, reply_markup=None: sent.append(t),
        http=PoliteClient(sleep=lambda s: None),
    )


def accept(conn, status="accepted"):
    [pid] = store_proposals(conn, [(Proposal(**GOOD), 4)])
    conn.execute("update source_proposals set status = %s where id = %s", (status, pid))
    conn.commit()
    return pid


def oracle(root):
    cfg = yaml.safe_load((root / "config" / "sources.yaml").read_text(encoding="utf-8"))
    return (len(cfg["sources"]) == 3, "1 passed")


@respx.mock
def test_confirmed_proposal_becomes_agent_pr_with_new_source_and_fixture(fresh_conn, tmp_path):
    root = make_repo(tmp_path)
    bare = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "--bare", str(bare))
    git = LocalGit(root)
    git.remote = str(bare)
    mock_novi()
    pr = respx.post("https://api.github.com/repos/me/r/pulls").respond(
        201, json={"number": 3, "html_url": "https://github.com/me/r/pull/3"}
    )
    pid, sent = accept(fresh_conn), []
    out = add_source_pr(deps(fresh_conn, sent), pid, root, git=git, token_provider=lambda s, c: "tok",
                        resolve=lambda h: True, pytest_runner=oracle)  # fmt: skip
    assert out.ok and out.number == 3
    assert '"head":"agent/add-source-novi-sajt-' in pr.calls[0].request.read().decode().replace(
        " ", ""
    )
    assert "agent/add-source-novi-sajt-" in sh(tmp_path, "git", "ls-remote", str(bare))
    assert git.current_branch() == "main" and git.changed_paths() == []
    assert (root / "config" / "sources.yaml").read_text(encoding="utf-8") == CONFIG  # main netaknut
    status, payload = fresh_conn.execute("select status, payload from source_proposals").fetchone()
    assert status == "added" and payload["pr"] == 3
    assert "PR #3" in sent[-1]
    # sadržaj PR-a: nov izvor + fixture, ništa više
    sh(
        root,
        "git",
        "checkout",
        "agent/add-source-novi-sajt-"
        + sh(root, "git", "branch", "--list", "agent/*").split("-")[-1].strip(),
    )
    assert sorted(git.diff_names("main")) == [
        "config/sources.yaml",
        "tests/fixtures/novi-sajt.html",
    ]


@respx.mock
def test_unconfirmed_missing_or_broken_proposals_open_nothing(fresh_conn, tmp_path):
    root = make_repo(tmp_path)
    pr = respx.post("https://api.github.com/repos/me/r/pulls").respond(
        201, json={"number": 1, "html_url": "u"}
    )
    sent = []
    d = deps(fresh_conn, sent)
    kw = dict(token_provider=lambda s, c: "tok", resolve=lambda h: True, pytest_runner=oracle)

    assert not add_source_pr(d, 99, root, **kw).ok and "ne postoji" in sent[-1]
    pending = accept(fresh_conn, "pending")
    assert not add_source_pr(d, pending, root, **kw).ok and "nije potvrđen" in sent[-1]

    mock_novi(page="<html><body><p>promenjena stranica</p></body></html>")  # selektori više ne rade
    accepted = accept(fresh_conn)
    assert not add_source_pr(d, accepted, root, **kw).ok and "0 stavki" in sent[-1]
    assert pr.call_count == 0 and Git(root).changed_paths() == []


@respx.mock
def test_failing_tests_discard_the_change(fresh_conn, tmp_path):
    root = make_repo(tmp_path)
    mock_novi()
    sent = []
    out = add_source_pr(deps(fresh_conn, sent), accept(fresh_conn), root, token_provider=lambda s, c: "tok",
                        resolve=lambda h: True, pytest_runner=lambda r: (False, "x\nFAILED: nešto"))  # fmt: skip
    assert not out.ok and "testovi ne prolaze" in sent[-1]
    assert Git(root).changed_paths() == []


# ---- zadaci iz dispatch-a --------------------------------------------------------------------


def test_task_validation_for_agent_tasks(fresh_conn):
    d = deps(fresh_conn, [])
    for payload in (
        {"task": "repair", "source": "../etc"},
        {"task": "repair", "source": ""},
        {"task": "revert", "source": "A B"},
        {"task": "add_source", "id": "abc"},
        {"task": "add_source"},
    ):
        with pytest.raises(TaskError, match="Neispravni parametri"):
            run_task(d, payload)
    with pytest.raises(TaskError, match="Nepoznat"):
        run_task(d, {"task": "obrisi_sve"})


def test_discover_task_needs_brave_key_and_checks_kill_switch(fresh_conn):
    sent = []
    d = deps(fresh_conn, sent)
    d.agent_client = ScriptedAnthropic(text_resp("x"))
    with pytest.raises(TaskError, match="BRAVE_API_KEY"):
        run_task(d, {"task": "discover"})
    from radar.killswitch import set_agents_enabled

    set_agents_enabled(fresh_conn, False)
    run_task(d, {"task": "discover"})
    assert "isključeni" in sent[-1] and d.agent_client.calls == []
    _ = httpx
