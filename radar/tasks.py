"""Teški zadaci (GitHub Actions, repository_dispatch event "task").

    python -m radar.tasks projects_sync
    python -m radar.tasks analyze <id>
    python -m radar.tasks draft <id> letter|message

U Actions se zadatak čita iz TASK_PAYLOAD (JSON: {"task": "draft", "id": 12, "kind": "letter"}).
Rezultat stiže na Telegram istim putem kao digest. Ništa se ne šalje trećim licima.

Agent 3 radi u dve izolovane faze:
  1. istraživanje (web alati, bez profila i bez baze) -> beleške po fiksnoj šemi
  2. pisanje (profil, projekti, beleške; bez mrežnih alata) -> gap analiza i nacrt
"""

import json
import logging
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
import yaml
from psycopg import Connection

from radar.agents import writer
from radar.agents.discovery import discover_sources
from radar.agents.loop import record_run
from radar.agents.proposals import add_source_pr, proposal_message, store_proposals
from radar.agents.repair import repair_source, revert_repair
from radar.agents.research import WebTools, research_company
from radar.agents.writer import Analysis, strip_urls
from radar.config import Settings, get_settings
from radar.db import connect
from radar.embed import Embedder, vec_literal
from radar.github_api import fetch_readme
from radar.health import build_report
from radar.http import PoliteClient
from radar.insights import (
    MIN_ADS,
    draft_post,
    format_insights,
    make_insights,
    recent_commits,
    valid_repo_name,
    week_ads,
)
from radar.killswitch import agents_enabled
from radar.llm import LLM, LLMError
from radar.models import Opportunity
from radar.notify import send_message
from radar.profile import Profile, get_current_profile
from radar.projects import sync_projects
from radar.providers import make_agent_client, make_embedder, make_llm
from radar.rerank import doc_text
from radar.sources.registry import DEFAULT_CONFIG

log = logging.getLogger("radar.tasks")

NOTES_TTL = timedelta(days=14)
ANALYSIS_TTL = timedelta(days=7)
UNRESEARCHABLE_HOSTS = ("mail.google.com", "linkedin.com")  # iza logina: ne čitamo
DRAFT_KINDS = {"letter": "cover_letter", "message": "message"}


@dataclass
class Deps:
    conn: Connection
    settings: Settings
    send: Callable[..., None]  # send(text) ili send(text, reply_markup=...)
    http: PoliteClient
    llm: LLM | None = None
    agent_client: object | None = None
    embedder: Embedder | None = None
    gh: httpx.Client | None = None
    resolve: Callable[[str], bool] | None = None  # DNS provera (zamena u testovima)
    search_client: httpx.Client | None = None


class TaskError(RuntimeError):
    """Greška koju vredi pokazati korisniku na Telegramu."""


@contextmanager
def as_reader(conn: Connection) -> Iterator[Connection]:
    """Faza pisanja čita bazu isključivo kroz ulogu agent_reader (samo čitanje)."""
    conn.commit()
    with conn.transaction():
        conn.execute("set local role agent_reader")
        yield conn


# ---- učitavanje konteksta -----------------------------------------------------------------


def load_opportunity(conn: Connection, oid: int) -> tuple[Opportunity, str] | None:
    row = conn.execute(
        "select url, title, org, location, deadline, raw_text, source, type"
        " from opportunities where id = %s",
        (oid,),
    ).fetchone()
    if not row:
        return None
    url, title, org, location, deadline, text, source, type_ = row
    return Opportunity(url, title, org, location, deadline, text or "", source, type_), url


def top_projects(deps: Deps, oid: int, opp: Opportunity, n: int = 3) -> list[dict]:
    conn = deps.conn
    has_emb = conn.execute(
        "select embedding is not null from opportunities where id = %s", (oid,)
    ).fetchone()[0]
    if not has_emb and deps.embedder:
        [vec] = deps.embedder.embed([doc_text(opp)], "document")
        conn.execute(
            "update opportunities set embedding = %s::vector where id = %s", (vec_literal(vec), oid)
        )
        has_emb = True
    with as_reader(conn):
        if has_emb:
            rows = conn.execute(
                "select name, repo_url, summary, skills from my_projects"
                " where embedding is not null"
                " order by embedding <=> (select embedding from opportunities where id = %s)"
                " limit %s",
                (oid, n),
            ).fetchall()
        else:
            rows = conn.execute(
                "select name, repo_url, summary, skills from my_projects"
                " order by updated_at desc limit %s",
                (n,),
            ).fetchall()
    return [{"name": r[0], "repo_url": r[1], "summary": r[2], "skills": r[3]} for r in rows]


def researchable(url: str, search_enabled: bool) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    ok_seed = not any(host == h or host.endswith("." + h) for h in UNRESEARCHABLE_HOSTS)
    return ok_seed or search_enabled


def get_notes(deps: Deps, oid: int, opp: Opportunity, url: str) -> dict | None:
    """Faza 1: istraživanje firme (keširano 14 dana). Nema pristup profilu ni bazi."""
    conn, s = deps.conn, deps.settings
    cached = conn.execute(
        "select notes, created_at from company_notes where opportunity_id = %s", (oid,)
    ).fetchone()
    if cached and datetime.now(UTC) - cached[1] < NOTES_TTL:
        return cached[0]
    if deps.agent_client is None or not researchable(url, bool(s.brave_api_key)):
        return None

    seeds = [] if any(h in url for h in UNRESEARCHABLE_HOSTS) else [url]
    web = WebTools(
        deps.http,
        seeds,
        brave_key=s.brave_api_key,
        owner_terms=[t for t in s.owner_terms.split(",") if t.strip()],
        resolve=deps.resolve,
        search_client=deps.search_client,
    )
    notes, run = research_company(
        deps.agent_client,
        s.research_model,
        org=opp.org,
        title=opp.title,
        url=url,
        ad_excerpt=opp.raw_text,
        web=web,
        enabled=lambda: agents_enabled(conn, s),
    )
    record_run(conn, "agent3-istrazivanje", run)
    if run.anomalies:
        deps.send(f"⚠ Anomalija u istraživanju #{oid}: {'; '.join(run.anomalies)}")
    if notes is None:
        return None
    data = notes.model_dump()
    conn.execute(
        "insert into company_notes (opportunity_id, notes) values (%s, %s::jsonb)"
        " on conflict (opportunity_id) do update set notes = excluded.notes, created_at = now()",
        (oid, json.dumps(data, ensure_ascii=False)),
    )
    conn.commit()
    return data


def get_analysis(
    deps: Deps, oid: int, opp: Opportunity, profile: Profile, projects: list[dict], notes
) -> Analysis:
    conn = deps.conn
    row = conn.execute(
        "select content, created_at from drafts where opportunity_id = %s and kind = 'analysis'"
        " order by created_at desc limit 1",
        (oid,),
    ).fetchone()
    if row and datetime.now(UTC) - row[1] < ANALYSIS_TTL:
        try:
            return Analysis.model_validate_json(row[0])
        except ValueError:
            pass  # neispravan keš: računaj ponovo
    analysis = writer.analyze(deps.llm, opp, profile, projects, notes, deps.settings.draft_model)
    conn.execute(
        "insert into drafts (opportunity_id, kind, content) values (%s, 'analysis', %s)",
        (oid, analysis.model_dump_json()),
    )
    conn.commit()
    return analysis


def prepare(deps: Deps, oid: int):
    """Zajednički deo: oglas, beleške (faza 1), profil, projekti. None ako treba stati."""
    if not agents_enabled(deps.conn, deps.settings):
        deps.send("Agenti su isključeni (/pokreni ih uključuje).")
        return None
    if deps.llm is None:
        raise TaskError("LLM ključ nije podešen (GEMINI_API_KEY)")
    loaded = load_opportunity(deps.conn, oid)
    if loaded is None:
        raise TaskError(f"Oglas #{oid} ne postoji")
    opp, url = loaded
    stored = get_current_profile(deps.conn)
    if stored is None:
        raise TaskError("Profil još nije postavljen (python -m radar.cli set-profile)")
    notes = get_notes(deps, oid, opp, url)
    projects = top_projects(deps, oid, opp)
    analysis = get_analysis(deps, oid, opp, stored.profile, projects, notes)
    return opp, url, stored.profile, projects, notes, analysis


# ---- zadaci -------------------------------------------------------------------------------


def format_analysis(oid: int, opp: Opportunity, url: str, a: Analysis, has_notes: bool) -> str:
    head = f"Analiza #{oid} — {opp.title}" + (f" ({opp.org})" if opp.org else "")
    lines = [head, f"Pokrivenost: {a.covered}/{a.total}"]
    for r in a.requirements:
        proof = f" — {strip_urls(r.dokaz)}" if r.dokaz else ""
        lines.append(f"{'✅' if r.pokriven else '❌'} {strip_urls(r.zahtev)}{proof}")
    if a.fali:
        lines += ["", "Fali ti:", *[f"• {strip_urls(x)}" for x in a.fali]]
    if a.predlog:
        lines += ["", f"Predlog: {strip_urls(a.predlog)}"]
    if not has_notes:
        lines += ["", "(bez istraživanja firme)"]
    lines += ["", url]
    return "\n".join(lines)


def task_analyze(deps: Deps, oid: int) -> None:
    ctx = prepare(deps, oid)
    if ctx is None:
        return
    opp, url, _, _, notes, analysis = ctx
    deps.send(format_analysis(oid, opp, url, analysis, notes is not None))


def task_draft(deps: Deps, oid: int, kind: str) -> None:
    if kind not in DRAFT_KINDS:
        raise TaskError("Tip nacrta je 'pismo' ili 'poruka'")
    ctx = prepare(deps, oid)
    if ctx is None:
        return
    opp, url, profile, projects, notes, analysis = ctx
    text = writer.write_draft(
        deps.llm, kind, opp, profile, projects, notes, analysis, deps.settings.draft_model
    )
    deps.conn.execute(
        "insert into drafts (opportunity_id, kind, content) values (%s, %s, %s)",
        (oid, DRAFT_KINDS[kind], text),
    )
    deps.conn.commit()
    label = "pismo" if kind == "letter" else "poruka"
    deps.send(
        f"Nacrt ({label}) za #{oid} — {opp.title}\n\n{text}\n\n"
        f"Ništa nije poslato, šalješ ti.\n{url}"
    )


def task_projects_sync(deps: Deps) -> None:
    s = deps.settings
    user = s.github_user or s.github_repository_owner
    if not user:
        raise TaskError("GITHUB_USER nije podešen")
    if deps.llm is None or deps.embedder is None or deps.gh is None:
        raise TaskError("Trebaju LLM i embedding ključ (GEMINI_API_KEY)")
    report = sync_projects(
        deps.conn, deps.llm, deps.embedder, deps.gh, user, s.github_token, s.rerank_model
    )
    deps.send(report.text())


def task_health(deps: Deps) -> None:
    """Nedeljni izveštaj zdravlja (izvori, runde, agenti, breaker, ispod minimuma)."""
    deps.send(build_report(deps.conn, datetime.now(UTC)).text())


def task_insights(deps: Deps) -> None:
    """Nedeljni uvid u tržište: top 5 veština, poređenje sa profilom, jedan predlog."""
    if not agents_enabled(deps.conn, deps.settings):
        deps.send("Agenti su isključeni (/pokreni ih uključuje).")
        return
    if deps.llm is None:
        raise TaskError("LLM ključ nije podešen (GEMINI_API_KEY)")
    ads = week_ads(deps.conn)
    if len(ads.ads) < MIN_ADS:
        deps.send(f"Tržište ove nedelje: premalo oglasa za uvid ({len(ads.ads)}).")
        return
    stored = get_current_profile(deps.conn)
    if stored is None:
        raise TaskError("Profil još nije postavljen (python -m radar.cli set-profile)")
    skills = [
        s for (row,) in deps.conn.execute("select skills from my_projects").fetchall() for s in row
    ]
    ins = make_insights(
        deps.llm, ads, stored.profile, sorted(set(skills)), deps.settings.draft_model
    )
    deps.send(format_insights(len(ads.ads), ins))


def task_post(deps: Deps, repo: str) -> None:
    """/post <repo>: nacrt LinkedIn posta iz README-a i poslednjih commitova. Objavljujem ručno."""
    s = deps.settings
    user = s.github_user or s.github_repository_owner
    if not user:
        raise TaskError("GITHUB_USER nije podešen")
    if not valid_repo_name(repo):
        raise TaskError("Neispravno ime repoa")
    if not agents_enabled(deps.conn, s):
        deps.send("Agenti su isključeni (/pokreni ih uključuje).")
        return
    if deps.llm is None or deps.gh is None:
        raise TaskError("LLM ključ nije podešen (GEMINI_API_KEY)")
    readme = fetch_readme(deps.gh, f"{user}/{repo}", s.github_token)
    if readme is None:
        raise TaskError(f"Repo {repo} nema README (ili ne postoji)")
    commits = recent_commits(deps.gh, f"{user}/{repo}", s.github_token)
    text = draft_post(deps.llm, repo, readme[0], commits, s.draft_model)
    deps.conn.execute("insert into drafts (kind, content) values ('post', %s)", (text,))
    deps.conn.commit()
    deps.send(f"Nacrt LinkedIn posta za {repo}\n\n{text}\n\nNišta nije objavljeno, objavljuješ ti.")


def _int(v) -> int:
    n = int(v)
    if n <= 0:
        raise ValueError
    return n


SOURCE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
REPO_ROOT = DEFAULT_CONFIG.parents[1]


def task_discover(deps: Deps) -> None:
    """Agent 2: traži nove izvore; svaki predlog proveren kodom i šalje se sa dugmićima."""
    s = deps.settings
    if not agents_enabled(deps.conn, s):
        deps.send("Agenti su isključeni (/pokreni ih uključuje).")
        return
    if deps.agent_client is None:
        raise TaskError("LLM ključ nije podešen (GEMINI_API_KEY)")
    if not s.brave_api_key:
        raise TaskError("BRAVE_API_KEY nije podešen (potreban za otkrivanje izvora)")
    cfg = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")) or {}
    topics = (cfg.get("discovery") or {}).get("topics")
    web = WebTools(  # bez seed URL-ova: sajtove otključava tek pretraga
        deps.http,
        [],
        brave_key=s.brave_api_key,
        owner_terms=[t for t in s.owner_terms.split(",") if t.strip()],
        resolve=deps.resolve,
        search_client=deps.search_client,
    )
    verified, run, rejected = discover_sources(
        deps.agent_client,
        s.research_model,
        web=web,
        config_path=DEFAULT_CONFIG,
        topics=topics,
        enabled=lambda: agents_enabled(deps.conn, s),
    )
    record_run(deps.conn, "agent2-otkrivanje", run)
    if run.anomalies:
        deps.send(f"⚠ Anomalija u agentu 2: {'; '.join(run.anomalies)}")
    if not verified:
        why = f" Odbijeno: {'; '.join(rejected)}" if rejected else ""
        deps.send(f"Agent 2: nijedan nov izvor nije prošao proveru ({run.outcome}).{why}"[:600])
        return
    for pid, (proposal, count) in zip(store_proposals(deps.conn, verified), verified, strict=True):
        text, markup = proposal_message(pid, proposal, count)
        deps.send(text, reply_markup=markup)


def task_add_source(deps: Deps, pid: int) -> None:
    add_source_pr(deps, pid, REPO_ROOT)


def task_repair(deps: Deps, source: str) -> None:
    repair_source(deps, source, REPO_ROOT)


def task_revert(deps: Deps, source: str) -> None:
    revert_repair(deps, source, REPO_ROOT)


def _source(payload: dict) -> str:
    name = str(payload.get("source", ""))
    if not SOURCE_NAME.match(name):
        raise ValueError
    return name


def run_task(deps: Deps, payload: dict) -> None:
    """Payload dolazi iz repository_dispatch pa se parametri strogo validiraju (samo oni)."""
    task = payload.get("task")
    try:
        oid = _int(payload.get("id")) if task in ("analyze", "draft", "add_source") else 0
        source = _source(payload) if task in ("repair", "revert") else ""
        repo = str(payload.get("repo", ""))
        kind = str(payload.get("kind"))
    except (TypeError, ValueError):
        raise TaskError(f"Neispravni parametri zadatka: {task}") from None

    handlers: dict[str, Callable[[], None]] = {
        "projects_sync": lambda: task_projects_sync(deps),
        "health": lambda: task_health(deps),
        "insights": lambda: task_insights(deps),
        "post": lambda: task_post(deps, repo),
        "analyze": lambda: task_analyze(deps, oid),
        "draft": lambda: task_draft(deps, oid, kind),
        "discover": lambda: task_discover(deps),
        "add_source": lambda: task_add_source(deps, oid),
        "repair": lambda: task_repair(deps, source),
        "revert": lambda: task_revert(deps, source),
    }
    handler = handlers.get(str(task))
    if handler is None:
        raise TaskError(f"Nepoznat zadatak: {task}")
    handler()


def auto_analyze(deps: Deps, run_id: str) -> int:
    """Gap analiza za stavke poslate u ovoj rundi sa relevantnošću 8+ (najviše po rundi)."""
    limit = deps.settings.auto_analysis_max
    if limit <= 0 or deps.llm is None:
        return 0
    rows = deps.conn.execute(
        "select s.opportunity_id from scores s where s.run_id = %s and s.sent_at is not null"
        " and s.relevance >= 8 and not exists (select 1 from drafts d"
        " where d.opportunity_id = s.opportunity_id and d.kind = 'analysis')"
        " order by s.relevance desc limit %s",
        (run_id, limit),
    ).fetchall()
    done = 0
    for (oid,) in rows:
        try:
            task_analyze(deps, oid)
            done += 1
        except (TaskError, LLMError):
            log.exception("automatska analiza #%s nije uspela", oid)
        except Exception:  # noqa: BLE001 - pipeline nikad ne pada zbog analize
            log.exception("automatska analiza #%s: neočekivana greška", oid)
    return done


def build_deps(conn: Connection, settings: Settings, client: PoliteClient) -> Deps:
    return Deps(
        conn=conn,
        settings=settings,
        send=send_message,
        http=client,
        llm=make_llm(settings, settings.draft_model, max_tokens=1800),
        agent_client=make_agent_client(settings),
        embedder=make_embedder(settings),
        gh=httpx.Client(timeout=20),
    )


def parse_argv(argv: list[str]) -> dict:
    if argv[:1] == ["projects_sync"]:
        return {"task": "projects_sync"}
    if argv[:1] == ["analyze"] and len(argv) == 2:
        return {"task": "analyze", "id": argv[1]}
    if argv[:1] == ["draft"] and len(argv) == 3:
        return {"task": "draft", "id": argv[1], "kind": argv[2]}
    raise TaskError(__doc__ or "neispravna upotreba")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = get_settings()
    client = PoliteClient()
    try:
        with connect() as conn:
            deps = build_deps(conn, settings, client)
            try:
                payload = json.loads(settings.task_payload) if settings.task_payload else None
                run_task(deps, payload or parse_argv(argv))
            except (TaskError, LLMError) as exc:
                deps.send(f"Zadatak nije uspeo: {exc}"[:400])
                log.error("%s", exc)
                return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
