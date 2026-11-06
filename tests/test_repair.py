"""Agent 1 (samopopravka adaptera): guard, git, GitHub App, popravka i revert."""

import subprocess
from pathlib import Path

import httpx
import jwt
import pytest
import respx
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from radar.agents import guard
from radar.agents.repair import (
    RepairTools,
    check_merged_repairs,
    find_source,
    repair_source,
    repairable,
    revert_repair,
    scrubbed_env,
)
from radar.config import Settings
from radar.db import kv_get, kv_set
from radar.github_app import AppNotConfigured, app_jwt, get_pr, installation_token, open_pr
from radar.gitops import Git, GitError
from radar.http import PoliteClient
from radar.killswitch import set_agents_enabled
from radar.sources.html_generic import parse_html_generic
from radar.tasks import Deps
from tests.helpers import ScriptedAnthropic, text_resp, tool_resp

CONFIG = """\
sources:
  - name: firma
    type: html_generic
    url: https://firma.rs/karijera
    item: ".stari-card"
    title: h3
    link: a
    min_expected: 1
  - name: drugi
    type: html_generic
    url: https://drugi.rs/jobs
    item: ".x"
    title: h3
    link: a
"""
FIXED = CONFIG.replace('".stari-card"', '".job-row"').replace(
    "    min_expected: 1\n", "    min_expected: 1\n    fixture: firma_new.html\n"
)
NEW_PAGE = (
    "<html><body><script>evil()</script>"
    '<div class="job-row"><h3>ML Intern</h3><a href="/j/1">Detalji</a></div>'
    '<div class="job-row"><h3>QA Intern</h3><a href="/j/2">Detalji</a></div></body></html>'
)


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir(parents=True)
    sh(root, "git", "init", "-b", "main")
    sh(root, "git", "config", "user.name", "t")
    sh(root, "git", "config", "user.email", "t@t")
    (root / "config").mkdir()
    (root / "config" / "sources.yaml").write_text(CONFIG, encoding="utf-8")
    (root / "tests" / "fixtures").mkdir(parents=True)
    (root / "tests" / "test_sources.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (root / "radar" / "sources").mkdir(parents=True)
    (root / "radar" / "sources" / "helloworld.py").write_text("X = 1\n", encoding="utf-8")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-m", "init")
    return root


def oracle(root: Path):
    """Zamena za pytest: isti oracle kao tests/test_source_fixtures.py, bez pokretanja procesa."""
    cfg = yaml.safe_load((root / "config" / "sources.yaml").read_text(encoding="utf-8"))
    for e in cfg["sources"]:
        if e.get("fixture"):
            html = (root / "tests" / "fixtures" / e["fixture"]).read_text(encoding="utf-8")
            sel = {k: e[k] for k in ("item", "title", "link") if k in e}
            if not parse_html_generic(html, e["url"], e["name"], **sel):
                return False, "FAILED: selektori ne pogađaju fixture"
    return True, "1 passed"


# ---- guard ----------------------------------------------------------------------------------


def test_branch_modes():
    assert guard.mode_for_branch("agent/repair-firma-202611") == "repair"
    assert guard.mode_for_branch("agent/add-source-x") == "add-source"
    assert guard.mode_for_branch("agent/revert-x") == "revert"
    assert guard.mode_for_branch("feature/x") is None and guard.mode_for_branch("main") is None


def test_path_rules():
    ok = ["radar/sources/helloworld.py", "tests/fixtures/a.html", "config/sources.yaml"]
    assert guard.check_paths(ok) == []
    for bad in (
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/ci.yml",
        "worker/src/index.ts",
        "db/migrations/0009.sql",
        "tests/test_sources.py",
        "radar/pipeline.py",
        "radar/sources/registry.py",
        "radar/sources/base.py",
        "radar/sources/sub/deep.py",
        "../etc/passwd",
        "/etc/passwd",
    ):
        assert guard.check_paths([bad]), bad


def test_yaml_repair_rules():
    base = CONFIG
    assert guard.check_sources_yaml(base, FIXED, "repair") == []
    for name, head in {
        "url": base.replace("https://firma.rs/karijera", "https://evil.rs/"),
        "tip": base.replace(
            "type: html_generic\n    url: https://firma", "type: helloworld\n    url: https://firma"
        ),
        "uklonjen": base.split("  - name: drugi")[0],
        "dodat": base
        + "  - name: novi\n    type: html_generic\n    url: https://n.rs\n    item: a\n    title: a\n    link: a\n",
        "ime": base.replace("name: firma", "name: firma2"),
        "ugasen": base.replace("min_expected: 1", "min_expected: 1\n    enabled: false"),
    }.items():
        assert guard.check_sources_yaml(base, head, "repair"), name
    assert guard.check_sources_yaml(base, "sources: [", "repair")


def test_yaml_add_source_rules():
    base = CONFIG
    new = "  - name: novi\n    type: html_generic\n    url: https://n.rs/jobs\n    item: .c\n    title: h3\n    link: a\n"
    assert guard.check_sources_yaml(base, base + new, "add-source") == []
    assert guard.check_sources_yaml(base, base + new + new.replace("novi", "novi2"), "add-source")
    assert guard.check_sources_yaml(base, base + new.replace("https", "http"), "add-source")
    assert guard.check_sources_yaml(base, base + new.replace("item: .c\n", ""), "add-source")
    assert guard.check_sources_yaml(base, (base + new).replace(".x", ".y"), "add-source")


def test_import_rules_block_new_packages():
    assert (
        guard.check_imports(
            "import re\nfrom bs4 import BeautifulSoup\nfrom radar.models import RawItem\n"
        )
        == []
    )
    assert guard.check_imports("import requests\n")
    assert guard.check_imports("from selenium import webdriver\n")
    assert guard.check_imports("def broken(:\n")
    assert guard.check_imports("from . import x\n") == []


def test_check_diff_combines_all_rules():
    read = lambda p: {"config/sources.yaml": FIXED, "radar/sources/helloworld.py": "import os\n"}[p]  # noqa: E731
    base = lambda p: CONFIG  # noqa: E731
    assert (
        guard.check_diff(["config/sources.yaml", "tests/fixtures/f.html"], base, read, "repair")
        == []
    )
    assert guard.check_diff(["pyproject.toml"], base, read, "repair")
    bad_import = lambda p: "import requests\n"  # noqa: E731
    assert guard.check_diff(["radar/sources/helloworld.py"], base, bad_import, "repair")
    assert guard.check_diff(["radar/sources/helloworld.py"], base, bad_import, "revert") == []


# ---- git ------------------------------------------------------------------------------------


def test_git_branch_commit_push_and_agent_prefix_enforced(tmp_path):
    root = make_repo(tmp_path)
    bare = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "--bare", str(bare))
    git = Git(root, secrets=("sekret",))
    assert git.changed_paths() == []
    (root / "tests" / "fixtures" / "n.html").write_text("<p>x</p>", encoding="utf-8")
    (root / "config" / "sources.yaml").write_text(FIXED, encoding="utf-8")
    assert git.changed_paths() == ["config/sources.yaml", "tests/fixtures/n.html"]

    with pytest.raises(GitError, match="agent/"):
        git.commit_on_new_branch("feature/x", "m", "n", "e@e")
    with pytest.raises(GitError, match="agent/"):
        git.push(str(bare), "main")
    git.commit_on_new_branch("agent/repair-firma-1", "fix", "bot", "bot@x")
    git.push(str(bare), "agent/repair-firma-1")
    assert "refs/heads/agent/repair-firma-1" in sh(tmp_path, "git", "ls-remote", str(bare))
    assert "refs/heads/main" not in sh(tmp_path, "git", "ls-remote", str(bare))  # main netaknut
    assert git.diff_names("main") == ["config/sources.yaml", "tests/fixtures/n.html"]


def test_git_discard_and_error_scrubs_token(tmp_path):
    root = make_repo(tmp_path)
    git = Git(root, secrets=("tajna-123",))
    (root / "novo.txt").write_text("x", encoding="utf-8")
    (root / "config" / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    git.discard_changes()
    assert git.changed_paths() == [] and "firma" in (root / "config" / "sources.yaml").read_text()
    with pytest.raises(GitError) as exc:
        git.run(
            "push", "https://x-access-token:tajna-123@127.0.0.1:1/r.git", "HEAD:refs/heads/agent/x"
        )
    assert "tajna-123" not in str(exc.value)


def test_git_revert_plain_and_merge_commit(tmp_path):
    root = make_repo(tmp_path)
    git = Git(root)
    sh(root, "git", "checkout", "-b", "agent/repair-firma-1")
    (root / "config" / "sources.yaml").write_text(FIXED, encoding="utf-8")
    sh(root, "git", "commit", "-am", "popravka")
    sh(root, "git", "checkout", "main")
    sh(root, "git", "merge", "--no-ff", "-m", "merge PR", "agent/repair-firma-1")
    merge_sha = git.head()
    git.revert(merge_sha, "agent/revert-firma-1", "bot", "bot@x")  # merge commit: -m 1
    assert (root / "config" / "sources.yaml").read_text(encoding="utf-8") == CONFIG
    with pytest.raises(GitError):
        git.revert(merge_sha, "feature/x", "bot", "bot@x")


# ---- GitHub App -----------------------------------------------------------------------------


def make_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public


def test_app_jwt_is_rs256_short_lived_and_accepts_escaped_newlines():
    private, public = make_key()
    for pem in (private, private.replace("\n", "\\n")):
        token = app_jwt("123", pem)
        claims = jwt.decode(token, public, algorithms=["RS256"], options={"verify_exp": False})
        assert claims["iss"] == "123" and claims["exp"] - claims["iat"] <= 10 * 60 + 60


@respx.mock
def test_installation_token_and_pr_calls():
    private, _ = make_key()
    s = Settings(
        _env_file=None,
        gh_app_id="1",
        gh_app_installation_id="9",
        gh_app_private_key=private,
    )
    route = respx.post("https://api.github.com/app/installations/9/access_tokens").respond(
        201, json={"token": "ghs_abc"}
    )
    with httpx.Client() as c:
        assert installation_token(s, c) == "ghs_abc"
        body = route.calls[0].request.read().decode()
        assert '"contents":"write"' in body.replace(" ", "") and "pull_requests" in body
        assert route.calls[0].request.headers["authorization"].startswith("Bearer ey")

        pr = respx.post("https://api.github.com/repos/me/r/pulls").respond(
            201, json={"number": 5, "html_url": "https://github.com/me/r/pull/5"}
        )
        assert open_pr(c, "ghs_abc", "me/r", "agent/x", "t", "b") == (
            5,
            "https://github.com/me/r/pull/5",
        )
        assert '"head":"agent/x"' in pr.calls[0].request.read().decode().replace(" ", "")
        respx.get("https://api.github.com/repos/me/r/pulls/5").respond(200, json={"state": "open"})
        assert get_pr(c, "ghs_abc", "me/r", 5)["state"] == "open"
    with pytest.raises(AppNotConfigured):
        installation_token(Settings(_env_file=None), httpx.Client())


# ---- alati agenta 1 -------------------------------------------------------------------------


def tools_for(root, resolve=lambda h: True):
    entry = find_source(root / "config" / "sources.yaml", "firma")
    return RepairTools(root, entry, PoliteClient(sleep=lambda s: None), resolve, oracle)


@respx.mock
def test_tools_only_touch_the_source_domain_and_allowed_files(tmp_path):
    root = make_repo(tmp_path)
    t = tools_for(root)
    respx.get("https://firma.rs/robots.txt").respond(404)
    evil = respx.get(url__startswith="https://evil.rs").respond(200, html="x")
    respx.get("https://firma.rs/karijera").respond(200, html=NEW_PAGE)

    for url in (
        "https://evil.rs/x",
        "http://firma.rs/karijera",
        "https://firma.rs:8443/x",
        "https://user@firma.rs/x",
    ):
        with pytest.raises(Exception, match="dozvoljen|kredencijale"):
            t.fetch_html({"url": url})
    assert evil.call_count == 0
    html = t.fetch_html({"url": "https://firma.rs/karijera"})["html"]
    assert "job-row" in html and "evil()" not in html

    saved = t.save_fixture({"url": "https://firma.rs/karijera", "name": "firma_new"})
    assert saved["path"] == "tests/fixtures/firma_new.html"
    assert "evil()" not in (root / saved["path"]).read_text(encoding="utf-8")  # skripte skinute
    for bad in ("../x", "Veliko", "a b", ""):
        with pytest.raises(ValueError):
            t.save_fixture({"url": "https://firma.rs/karijera", "name": bad})

    assert "firma" in t.read_file({"path": "config/sources.yaml"})["content"]
    assert t.read_file({"path": "tests/fixtures/firma_new.html"})["path"]
    for bad in ("pyproject.toml", "../secret", ".env", "radar/pipeline.py", "C:/Windows/win.ini"):
        with pytest.raises(ValueError):
            t.read_file({"path": bad})
        with pytest.raises(ValueError):
            t.write_file({"path": bad, "content": "x"})


def test_write_file_enforces_guard_and_own_source_only(tmp_path):
    root = make_repo(tmp_path)
    t = tools_for(root)
    with pytest.raises(ValueError, match="pisanje nije dozvoljeno"):
        t.write_file({"path": "tests/test_sources.py", "content": "def test_x(): assert True"})
    with pytest.raises(ValueError, match="pisanje nije dozvoljeno"):
        t.write_file(
            {"path": "tests/fixtures/lazni.html", "content": "<h3>lažni</h3>"}
        )  # fixture samo preko save_fixture
    with pytest.raises(ValueError):  # menja URL
        t.write_file(
            {"path": "config/sources.yaml", "content": CONFIG.replace("firma.rs", "evil.rs")}
        )
    with pytest.raises(ValueError, match="tuđe izvore"):
        t.write_file({"path": "config/sources.yaml", "content": CONFIG.replace('".x"', '".y"')})
    with pytest.raises(ValueError):
        t.write_file({"path": "config/sources.yaml", "content": "sources: ["})
    assert t.write_file({"path": "config/sources.yaml", "content": FIXED})["bytes"] > 0
    with pytest.raises(ValueError, match="prevelik"):
        t.write_file({"path": "config/sources.yaml", "content": "#" * 300_000})


def test_adapter_edit_blocks_new_imports(tmp_path):
    root = make_repo(tmp_path)
    cfg = {"name": "helloworld", "type": "helloworld"}
    t = RepairTools(root, cfg, PoliteClient(sleep=lambda s: None), lambda h: True, oracle)
    assert t.adapter == "radar/sources/helloworld.py" and t.host == "www.helloworld.rs"
    with pytest.raises(ValueError, match="import"):
        t.write_file({"path": "radar/sources/helloworld.py", "content": "import requests\n"})
    assert t.write_file({"path": "radar/sources/helloworld.py", "content": "import re\nX = 2\n"})[
        "bytes"
    ]
    with pytest.raises(ValueError, match="helloworld_"):
        t.save_fixture({"url": "https://www.helloworld.rs/x", "name": "bilo_sta"})


def test_tests_run_without_secrets():
    import os

    os.environ["DATABASE_URL"] = "postgres://tajna"
    os.environ["ANTHROPIC_API_KEY"] = "sk-tajna"
    os.environ["GITHUB_APP_PRIVATE_KEY"] = "pem"
    env = scrubbed_env()
    assert "PATH" in env or "SYSTEMROOT" in env or env == {} or True
    assert not {"DATABASE_URL", "ANTHROPIC_API_KEY", "GITHUB_APP_PRIVATE_KEY"} & set(env)
    for k in ("DATABASE_URL", "ANTHROPIC_API_KEY", "GITHUB_APP_PRIVATE_KEY"):
        os.environ.pop(k)


def test_repairable_and_find_source(tmp_path):
    root = make_repo(tmp_path)
    cfg = root / "config" / "sources.yaml"
    assert repairable(find_source(cfg, "firma"))
    assert repairable({"type": "helloworld"})
    assert not repairable(find_source(cfg, "nema")) and not repairable({"type": "gmail"})


# ---- cela popravka (nad pravim git repoom) ---------------------------------------------------


class LocalGit(Git):
    """Push ide u lokalni bare repo umesto na github.com."""

    remote = ""

    def push(self, remote_url, branch):
        super().push(self.remote, branch)


def make_deps(conn, client, sent):
    s = Settings(_env_file=None, github_repository="me/praksa-radar")
    return Deps(
        conn=conn,
        settings=s,
        send=sent.append,
        http=PoliteClient(sleep=lambda x: None),
        agent_client=client,
    )


def fixed_agent():
    return ScriptedAnthropic(
        tool_resp(
            "save_fixture", {"url": "https://firma.rs/karijera", "name": "firma_new"}, id="a"
        ),
        tool_resp("fetch_html", {"url": "https://firma.rs/karijera"}, id="b"),
        tool_resp("write_file", {"path": "config/sources.yaml", "content": FIXED}, id="c"),
        tool_resp("run_tests", {}, id="d"),
        text_resp("Selektor je bio .stari-card, sada je .job-row."),
    )


@respx.mock
def test_successful_repair_opens_pr_on_agent_branch_and_never_touches_main(fresh_conn, tmp_path):
    root = make_repo(tmp_path)
    bare = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "--bare", str(bare))
    git = LocalGit(root)
    git.remote = str(bare)
    respx.get("https://firma.rs/robots.txt").respond(404)
    respx.get("https://firma.rs/karijera").respond(200, html=NEW_PAGE)
    pr = respx.post("https://api.github.com/repos/me/praksa-radar/pulls").respond(
        201, json={"number": 7, "html_url": "https://github.com/me/praksa-radar/pull/7"}
    )
    sent, client = [], fixed_agent()
    fresh_conn.execute(
        "insert into source_runs (source, items_found, error) values ('firma', 0, 'HTML promenjen')"
    )
    fresh_conn.commit()

    out = repair_source(make_deps(fresh_conn, client, sent), "firma", root, git=git,
                        token_provider=lambda s, c: "tok", resolve=lambda h: True, pytest_runner=oracle)  # fmt: skip
    assert out.ok and out.number == 7
    body = pr.calls[0].request.read().decode()
    assert '"head":"agent/repair-firma-' in body.replace(
        " ", ""
    ) and '"base":"main"' in body.replace(" ", "")
    assert "pregledaj diff" in body.lower()

    branches = sh(tmp_path, "git", "ls-remote", str(bare))
    assert "refs/heads/agent/repair-firma-" in branches and "refs/heads/main" not in branches
    assert git.current_branch() == "main" and git.changed_paths() == []
    assert (root / "config" / "sources.yaml").read_text(encoding="utf-8") == CONFIG  # main netaknut
    assert any("PR #7" in m for m in sent)
    assert kv_get(fresh_conn, "repair_pr:firma")["status"] == "open"
    assert kv_get(fresh_conn, "repair_attempts:firma") == 1
    run = fresh_conn.execute("select agent, outcome, steps from agent_runs").fetchone()
    assert run == ("agent1-samopopravka", "ok", 5)
    assert "HTML promenjen" in client.calls[0]["messages"][0]["content"]


def run_failed(conn, tmp_path, client, runner=oracle, expect="nije uspela"):
    kv_set(conn, "repair_attempts:firma", 0)  # svaki scenario počinje od prvog pokušaja
    root = make_repo(tmp_path)
    sent = []
    with respx.mock:
        respx.get("https://firma.rs/robots.txt").respond(404)
        respx.get("https://firma.rs/karijera").respond(200, html=NEW_PAGE)
        pr = respx.post("https://api.github.com/repos/me/praksa-radar/pulls").respond(
            201, json={"number": 1, "html_url": "u"}
        )
        out = repair_source(make_deps(conn, client, sent), "firma", root, git=Git(root),
                            token_provider=lambda s, c: "tok", resolve=lambda h: True, pytest_runner=runner)  # fmt: skip
        assert pr.call_count == 0
    assert not out.ok and any(expect in m for m in sent), sent
    assert Git(root).changed_paths() == [] and Git(root).current_branch() == "main"
    return out, sent


def test_failure_modes_never_open_a_pr(fresh_conn, tmp_path):
    # 1) agent ništa ne promeni
    out, _ = run_failed(fresh_conn, tmp_path / "a", ScriptedAnthropic(text_resp("ne znam")))
    assert out.message == "agent nije ništa promenio"
    # 2) testovi ne prolaze posle izmene
    client = ScriptedAnthropic(
        tool_resp(
            "save_fixture", {"url": "https://firma.rs/karijera", "name": "firma_new"}, id="a"
        ),
        tool_resp("write_file", {"path": "config/sources.yaml", "content": FIXED}, id="b"),
        text_resp("gotovo"),
    )
    out, _ = run_failed(
        fresh_conn, tmp_path / "b", client, runner=lambda r: (False, "x\nFAILED: nešto")
    )
    assert out.message.startswith("testovi ne prolaze")
    # 3) pokušaj zabranjenog pisanja: alat odbija, diff ostaje prazan
    client = ScriptedAnthropic(
        tool_resp(
            "write_file",
            {"path": "tests/test_sources.py", "content": "def test_x(): assert True"},
            id="a",
        ),
        text_resp("oslabio sam testove"),
    )
    out, _ = run_failed(fresh_conn, tmp_path / "c", client)
    assert out.message == "agent nije ništa promenio"
    assert "pisanje nije dozvoljeno" in client.observations(1)
    assert kv_get(fresh_conn, "repair_attempts:firma") == 1


def test_gives_up_after_two_attempts_and_respects_kill_switch_and_non_repairable(
    fresh_conn, tmp_path
):
    root = make_repo(tmp_path)
    sent, client = [], ScriptedAnthropic(text_resp("x"))
    deps = make_deps(fresh_conn, client, sent)

    kv_set(fresh_conn, "repair_attempts:firma", 2)
    assert not repair_source(deps, "firma", root).ok
    assert "odustao" in sent[-1] and client.calls == []

    kv_set(fresh_conn, "repair_attempts:firma", 0)
    assert not repair_source(deps, "gmail", root).ok and "ne popravlja automatski" in sent[-1]

    set_agents_enabled(fresh_conn, False)
    assert not repair_source(deps, "firma", root).ok and "isključeni" in sent[-1]
    assert client.calls == []

    set_agents_enabled(fresh_conn, True)
    (root / "prljavo.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="nije čisto"):
        repair_source(deps, "firma", root, git=Git(root))


@respx.mock
def test_revert_pr_for_a_bad_merge(fresh_conn, tmp_path):
    root = make_repo(tmp_path)
    bare = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "--bare", str(bare))
    sh(root, "git", "checkout", "-b", "agent/repair-firma-1")
    (root / "config" / "sources.yaml").write_text(FIXED, encoding="utf-8")
    sh(root, "git", "commit", "-am", "popravka")
    sh(root, "git", "checkout", "main")
    sh(root, "git", "merge", "--no-ff", "-m", "merge", "agent/repair-firma-1")
    git = LocalGit(root)
    git.remote = str(bare)
    kv_set(
        fresh_conn, "repair_pr:firma", {"number": 7, "status": "reverting", "merge_sha": git.head()}
    )
    pr = respx.post("https://api.github.com/repos/me/praksa-radar/pulls").respond(
        201, json={"number": 8, "html_url": "https://github.com/me/praksa-radar/pull/8"}
    )
    sent = []
    out = revert_repair(
        make_deps(fresh_conn, None, sent), "firma", root, git=git, token_provider=lambda s, c: "tok"
    )
    assert out.ok and out.number == 8
    assert '"head":"agent/revert-firma-' in pr.calls[0].request.read().decode().replace(" ", "")
    assert "agent/revert-firma-" in sh(tmp_path, "git", "ls-remote", str(bare))
    assert "odobrenje" in sent[-1] and kv_get(fresh_conn, "repair_pr:firma")["revert_pr"] == 8
    assert git.current_branch() == "main"
    # bez zabeleženog merge commit-a revert nije moguć
    kv_set(fresh_conn, "repair_pr:drugi", {"number": 1})
    assert not revert_repair(make_deps(fresh_conn, None, sent), "drugi", root, git=git).ok


# ---- praćenje spojenih popravki ---------------------------------------------------------------


def pr_state(state, merged=False, sha="abc"):
    return httpx.Response(200, json={"state": state, "merged": merged, "merge_commit_sha": sha})


@respx.mock
def test_merged_repair_that_keeps_returning_nothing_triggers_revert(fresh_conn):
    s = Settings(_env_file=None, github_token="t", github_repository="me/r")
    sent = []
    kv_set(fresh_conn, "repair_pr:firma", {"number": 7, "status": "open"})
    pulls = respx.get("https://api.github.com/repos/me/r/pulls/7")
    dispatch = respx.post("https://api.github.com/repos/me/r/dispatches").respond(204)

    pulls.mock(return_value=pr_state("open"))
    assert check_merged_repairs(fresh_conn, s, sent.append) == []

    pulls.mock(return_value=pr_state("closed", merged=True, sha="deadbeef"))
    check_merged_repairs(fresh_conn, s, sent.append)  # prvi put: beleži baseline
    st = kv_get(fresh_conn, "repair_pr:firma")
    assert st["status"] == "merged" and st["merge_sha"] == "deadbeef"

    fresh_conn.execute("insert into source_runs (source, items_found) values ('firma', 0)")
    fresh_conn.commit()
    assert check_merged_repairs(fresh_conn, s, sent.append) == []  # tek 1 runda posle merge-a
    fresh_conn.execute("insert into source_runs (source, items_found) values ('firma', 0)")
    fresh_conn.commit()
    assert check_merged_repairs(fresh_conn, s, sent.append) == ["revert:firma"]
    assert dispatch.calls[0].request.read().decode().replace(" ", "") == (
        '{"event_type":"agent","client_payload":{"task":"revert","source":"firma"}}'
    )
    assert kv_get(fresh_conn, "repair_pr:firma")["status"] == "reverting"
    assert "revert" in sent[-1]
    assert check_merged_repairs(fresh_conn, s, sent.append) == []  # ne ponavlja


@respx.mock
def test_merged_repair_that_works_is_verified_and_closed_pr_is_forgotten(fresh_conn):
    s = Settings(_env_file=None, github_token="t", github_repository="me/r")
    kv_set(fresh_conn, "repair_pr:firma", {"number": 7, "status": "open"})
    kv_set(fresh_conn, "repair_pr:drugi", {"number": 9, "status": "open"})
    respx.get("https://api.github.com/repos/me/r/pulls/7").mock(
        return_value=pr_state("closed", True)
    )
    respx.get("https://api.github.com/repos/me/r/pulls/9").mock(
        return_value=pr_state("closed", False)
    )
    check_merged_repairs(fresh_conn, s, lambda m: None)
    assert kv_get(fresh_conn, "repair_pr:drugi")["status"] == "closed"
    for n in (30, 28):
        fresh_conn.execute(
            "insert into source_runs (source, items_found) values ('firma', %s)", (n,)
        )
    fresh_conn.commit()
    assert check_merged_repairs(fresh_conn, s, lambda m: None) == ["verified:firma"]


def test_check_merged_repairs_is_noop_without_github_config(fresh_conn):
    assert check_merged_repairs(fresh_conn, Settings(_env_file=None), lambda m: None) == []


# ---- harness eval-a agenta 1 --------------------------------------------------------------------


def test_repair_eval_scenarios_are_solvable_and_the_scoring_works():
    """Za svaki od 10 scenarija postoji ispravan selektor (inače eval ne bi imao smisla)."""
    from evals.repair_eval import SCENARIOS, run_scenario

    assert len(SCENARIOS) == 10 and len({n for n, _, _ in SCENARIOS}) == 10
    for name, stale, html in SCENARIOS:
        assert not parse_html_generic(html, "https://x.test", "x", **stale), (
            f"{name}: stari selektori još rade"
        )

    solved = {
        "preimenovane klase": {"item": ".position-card", "title": "h3", "link": "a"},
        "tabela": {"item": "tr.row", "title": ".naziv", "link": "a"},
        "lista bez klasa": {"item": ".openings li", "title": "a", "link": "a"},
    }
    for name, sel, html in [(n, solved[n], h) for n, _, h in SCENARIOS if n in solved]:
        assert len(parse_html_generic(html, "https://x.test", "x", **sel)) == 4, name

    # naivan agent koji ništa ne menja ne sme da prođe
    name, stale, html = SCENARIOS[0]
    lazy = ScriptedAnthropic(text_resp("ne znam"))
    assert run_scenario(lazy, "m", 1, name, stale, html)["ok"] is False
