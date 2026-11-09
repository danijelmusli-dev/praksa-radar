"""Runda: collect -> rank -> send. Svaka faza je ponovljiva (checkpoint u bazi).

- collect: izvori -> opportunities (upsert po URL-u)
- rank: ocenjuje sve stavke bez reda u `scores` (nastavlja posle prekinute runde)
- send: šalje samo scores.passed and sent_at is null; sent_at se piše posle svake poruke
"""

import logging
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from psycopg import Connection

from radar.agents.repair import check_merged_repairs
from radar.config import Settings, get_settings
from radar.db import connect, kv_get, kv_set
from radar.dedup import upsert_opportunities
from radar.embed import Embedder
from radar.github_api import dispatch_event
from radar.health import (
    BREAKER_FAILS,
    breaker_open,
    consecutive_failures,
    is_failure,
    ping_healthcheck,
    record_skip,
    worker_alive,
)
from radar.http import PoliteClient
from radar.llm import LLM
from radar.normalize import to_opportunity
from radar.notify import DigestItem, build_digest, send_message
from radar.providers import make_embedder, make_llm
from radar.rerank import rank_run
from radar.sources.base import Source, run_source
from radar.sources.registry import build_sources
from radar.tasks import auto_analyze, build_deps

log = logging.getLogger("radar.pipeline")

DEFAULT_LOOKBACK = timedelta(days=7)
OVERLAP = timedelta(days=1)


def _mark(conn: Connection, run_id: str, phase: str, status: str) -> None:
    kv_set(conn, f"run:{run_id}", {"phase": phase, "status": status, "at": datetime.now(UTC)})


def _since(conn: Connection, now: datetime) -> datetime:
    last = kv_get(conn, "last_successful_run")
    if last:
        return datetime.fromisoformat(last) - OVERLAP
    return now - DEFAULT_LOOKBACK


@dataclass
class CollectResult:
    errors: dict[str, str] = field(default_factory=dict)  # izvori koji su pali u ovoj rundi
    skipped: list[str] = field(default_factory=list)  # circuit breaker: preskočeni 24 h
    opened: list[str] = field(default_factory=list)  # breaker se upravo otvorio (3. neuspeh)


def collect(conn: Connection, sources: list[Source], now: datetime) -> CollectResult:
    since = _since(conn, now)
    out = CollectResult()
    errors = out.errors
    for source in sources:
        eif = getattr(source, "empty_is_failure", False)
        if breaker_open(conn, source.name, now, eif):
            record_skip(conn, source.name, now)  # vidljivo u /izvori, ali se ne računa kao neuspeh
            out.skipped.append(source.name)
            continue
        result = run_source(source, since, conn, now)
        if is_failure(len(result.items), result.error, eif) and (
            consecutive_failures(conn, source.name, eif) == BREAKER_FAILS
        ):
            out.opened.append(source.name)
        if result.error:
            errors[result.name] = result.error
            continue
        if not is_failure(len(result.items), None, eif) and kv_get(
            conn, f"repair_attempts:{source.name}"
        ):
            kv_set(
                conn, f"repair_attempts:{source.name}", 0
            )  # izvor radi: agent opet ima 2 pokušaja
        opps = []
        for raw in result.items:
            try:
                opps.append(to_opportunity(raw, now.date()))
            except Exception:  # noqa: BLE001 - jedna loša stavka ne sme da sruši izvor
                log.exception("normalizacija stavke nije uspela: %s", raw.url)
        new = upsert_opportunities(conn, opps)
        log.info("%s: %d stavki, %d novih", result.name, len(result.items), len(new))
    return out


def send_digest(
    conn: Connection,
    run_id: str,
    now: datetime,
    send: Callable[..., None],
    warnings: list[str],
) -> int:
    rows = conn.execute(
        "select o.id, s.relevance, o.title, o.org, o.location, o.deadline, o.type, s.reason, o.url"
        " from scores s join opportunities o on o.id = s.opportunity_id"
        " where s.passed and s.sent_at is null order by s.relevance desc, o.id"
    ).fetchall()
    items = [DigestItem(*r) for r in rows]
    messages = build_digest(items, now, warnings)
    for msg in messages:
        send(msg.text, reply_markup=msg.reply_markup)
        if msg.ids:  # sent_at tek posle uspešnog slanja ove poruke
            conn.execute(
                "update scores set sent_at = now(), run_id = %s where opportunity_id = any(%s)",
                (run_id, msg.ids),
            )
            conn.commit()
    return len(items)


def _alert_auth_failures(
    conn: Connection, errors: dict[str, str], today: date, send: Callable[[str], None]
) -> None:
    """Istekao token (Gmail, Instagram): jedna posebna poruka dnevno, čak i kad nema digesta."""
    fixes = {
        "GmailAuthError": "Gmail prijava je istekla. Pokreni scripts/gmail_auth.py",
        "InstagramAuthError": "Instagram token je istekao. Generiši novi INSTAGRAM_ACCESS_TOKEN",
    }
    for name, err in errors.items():
        kind = next((k for k in fixes if k in err), None)
        if kind is None:
            continue
        key = f"alert:{name}:auth"
        if kv_get(conn, key) == today.isoformat():
            continue
        send(f"⚠ Praksa Radar: {fixes[kind]}")
        kv_set(conn, key, today.isoformat())


def run(
    conn: Connection,
    sources: list[Source],
    send: Callable[..., None] = send_message,
    now: datetime | None = None,
    run_id: str | None = None,
    fail_after: str | None = None,
    llm: LLM | None = None,
    embedder: Embedder | None = None,
    rerank_model: str | None = None,
    on_breaker_open: Callable[[str], None] | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    run_id = run_id or os.environ.get("GITHUB_RUN_ID") or uuid.uuid4().hex

    def checkpoint(phase: str) -> None:
        _mark(conn, run_id, phase, "done")
        if fail_after == phase:
            raise RuntimeError(f"RADAR_FAIL_AFTER={phase}")

    _mark(conn, run_id, "collect", "running")
    collected = collect(conn, sources, now)
    errors = collected.errors
    for name in collected.opened:
        if on_breaker_open:
            try:
                on_breaker_open(name)  # alarm na Telegram + zadatak popravke (agent 1)
            except Exception:  # noqa: BLE001 - alarm ne sme da sruši rundu
                log.exception("on_breaker_open(%s) nije uspeo", name)
    checkpoint("collect")

    summary = rank_run(conn, run_id, now.date(), llm, embedder, rerank_model)
    ranked = summary.ranked
    checkpoint("rank")

    warnings = (
        [f"izvor {name} nije uspeo" for name in errors if name not in collected.skipped]
        + [f"izvor {name} preskočen 24 h (circuit breaker)" for name in collected.skipped]
        + summary.warnings()
    )
    sent = send_digest(conn, run_id, now, send, warnings)
    checkpoint("send")

    _alert_auth_failures(conn, errors, now.date(), send)

    kv_set(conn, "last_successful_run", now.isoformat())
    return {"run_id": run_id, "ranked": ranked, "sent": sent, "warnings": warnings}


def _auto_analysis(settings, run_id: str) -> None:
    """Gap analiza za stavke sa relevantnošću 8+ (max auto_analysis_max). Ne ruši rundu."""
    if settings.auto_analysis_max <= 0 or not settings.has_llm:
        return
    client = PoliteClient()
    try:
        with connect() as conn:
            done = auto_analyze(build_deps(conn, settings, client), run_id)
        log.info("automatskih analiza: %d", done)
    except Exception:  # noqa: BLE001
        log.exception("automatska analiza nije uspela")
    finally:
        client.close()


def _breaker_alarm(settings: Settings, send: Callable[..., None]) -> Callable[[str], None]:
    """Kad izvor padne 3 puta zaredom: poruka na Telegram + zadatak popravke (agent 1)."""

    def alarm(name: str) -> None:
        repair = dispatch_event(settings, "agent", {"task": "repair", "source": name})
        send(
            f"⛔ Izvor {name}: 3 neuspeha zaredom, preskačem ga 24 h. "
            + (
                "Agent 1 pokušava da popravi adapter; PR čeka tvoj merge."
                if repair
                else "Automatska popravka nije pokrenuta (proveri GITHUB_TOKEN)."
            )
        )

    return alarm


def _watchdog(conn: Connection, settings: Settings, send: Callable[..., None], today: date) -> None:
    """Actions proverava Worker (a Worker proverava poslednju rundu): pad jednog javlja drugi."""
    if worker_alive(settings.worker_url):
        return
    key = "alert:worker:down"
    if kv_get(conn, key) != today.isoformat():
        send("⚠ Worker ne odgovara na /health. Telegram komande verovatno ne rade.")
        kv_set(conn, key, today.isoformat())


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = get_settings()
    client = PoliteClient()
    try:
        with connect() as conn:
            result = run(
                conn,
                build_sources(settings, client, conn=conn),
                fail_after=os.environ.get("RADAR_FAIL_AFTER"),
                llm=make_llm(settings, settings.rerank_model),
                embedder=make_embedder(settings),
                rerank_model=settings.rerank_model,
                on_breaker_open=_breaker_alarm(settings, send_message),
            )
            try:
                _watchdog(conn, settings, send_message, datetime.now(UTC).date())
                check_merged_repairs(conn, settings, send_message)
            except Exception:  # noqa: BLE001
                log.exception("provera Worker-a / popravki nije uspela")
    except Exception:
        ping_healthcheck(settings.healthcheck_url, ok=False)
        raise
    finally:
        client.close()
    log.info("gotovo: %s", result)
    ping_healthcheck(settings.healthcheck_url)  # bez pinga 26 h => spoljni servis upozorava
    _auto_analysis(settings, result["run_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
