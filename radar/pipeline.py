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
from datetime import UTC, date, datetime, timedelta

from psycopg import Connection

from radar.config import get_settings
from radar.db import connect, kv_get, kv_set
from radar.dedup import upsert_opportunities
from radar.embed import Embedder, VoyageEmbedder
from radar.http import PoliteClient
from radar.llm import LLM, AnthropicLLM
from radar.normalize import to_opportunity
from radar.notify import DigestItem, build_digest, send_message
from radar.rerank import rank_run
from radar.sources.base import Source, run_source
from radar.sources.registry import build_sources

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


def collect(conn: Connection, sources: list[Source], now: datetime) -> dict[str, str]:
    """Vrati {izvor: greška} za izvore koji su pali u ovoj rundi."""
    since = _since(conn, now)
    errors: dict[str, str] = {}
    for source in sources:
        result = run_source(source, since, conn)
        if result.error:
            errors[result.name] = result.error
            continue
        opps = []
        for raw in result.items:
            try:
                opps.append(to_opportunity(raw, now.date()))
            except Exception:  # noqa: BLE001 - jedna loša stavka ne sme da sruši izvor
                log.exception("normalizacija stavke nije uspela: %s", raw.url)
        new = upsert_opportunities(conn, opps)
        log.info("%s: %d stavki, %d novih", result.name, len(result.items), len(new))
    return errors


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
    """Istekao Gmail token: jedna posebna poruka dnevno, čak i kad nema digesta."""
    for name, err in errors.items():
        if "GmailAuthError" not in err:
            continue
        key = f"alert:{name}:auth"
        if kv_get(conn, key) == today.isoformat():
            continue
        send("⚠ Praksa Radar: Gmail prijava je istekla. Pokreni scripts/gmail_auth.py")
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
) -> dict:
    now = now or datetime.now(UTC)
    run_id = run_id or os.environ.get("GITHUB_RUN_ID") or uuid.uuid4().hex

    def checkpoint(phase: str) -> None:
        _mark(conn, run_id, phase, "done")
        if fail_after == phase:
            raise RuntimeError(f"RADAR_FAIL_AFTER={phase}")

    _mark(conn, run_id, "collect", "running")
    errors = collect(conn, sources, now)
    checkpoint("collect")

    summary = rank_run(conn, run_id, now.date(), llm, embedder, rerank_model)
    ranked = summary.ranked
    checkpoint("rank")

    warnings = [f"izvor {name} nije uspeo" for name in errors] + summary.warnings()
    sent = send_digest(conn, run_id, now, send, warnings)
    checkpoint("send")

    _alert_auth_failures(conn, errors, now.date(), send)

    kv_set(conn, "last_successful_run", now.isoformat())
    return {"run_id": run_id, "ranked": ranked, "sent": sent, "warnings": warnings}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = get_settings()
    client = PoliteClient()
    try:
        with connect() as conn:
            result = run(
                conn,
                build_sources(settings, client),
                fail_after=os.environ.get("RADAR_FAIL_AFTER"),
                llm=(
                    AnthropicLLM(settings.anthropic_api_key, settings.rerank_model)
                    if settings.anthropic_api_key
                    else None
                ),
                embedder=(
                    VoyageEmbedder(settings.voyage_api_key, settings.embed_model)
                    if settings.voyage_api_key
                    else None
                ),
                rerank_model=settings.rerank_model,
            )
    finally:
        client.close()
    log.info("gotovo: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
