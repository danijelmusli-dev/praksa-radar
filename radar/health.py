"""Nadzor: circuit breaker izvora, nedeljni izveštaj, spoljni watchdog.

Najveći rizik nije tehnički nego tih kvar: izvor prestane da radi, a niko to ne primeti.
Zato su ovde tri nezavisne zaštite: circuit breaker (3 neuspeha zaredom = preskoči 24 h, pa
probna runda), "očekivani minimum" po izvoru (nedelju dana ispod = kvar i bez greške) i ping
spoljnom servisu koji javlja kad sistem sam stane.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import yaml
from psycopg import Connection

log = logging.getLogger(__name__)

BREAKER_FAILS = 3
BREAKER_PAUSE = timedelta(hours=24)
SKIP_PREFIX = "skipped:"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"


def is_failure(items: int, error: str | None, empty_is_failure: bool) -> bool:
    """Greška, ili prazna runda kod izvora koji uvek mora nešto da vrati (liste oglasa)."""
    if error is not None:
        return not error.startswith(SKIP_PREFIX)
    return empty_is_failure and items == 0


def recent_runs(conn: Connection, source: str, n: int = BREAKER_FAILS) -> list[tuple]:
    """Poslednjih n stvarnih pokretanja (preskočena se ne računaju), najnovije prvo."""
    return conn.execute(
        "select started_at, items_found, error from source_runs where source = %s"
        " and (error is null or error not like %s) order by started_at desc, id desc limit %s",
        (source, SKIP_PREFIX + "%", n),
    ).fetchall()


def consecutive_failures(conn: Connection, source: str, empty_is_failure: bool) -> int:
    count = 0
    for _, items, error in recent_runs(conn, source, 10):
        if not is_failure(items, error, empty_is_failure):
            break
        count += 1
    return count


def breaker_open(
    conn: Connection, source: str, now: datetime, empty_is_failure: bool = False
) -> bool:
    """Otvoren: poslednja 3 pokretanja pala, a poslednji pad je mlađi od 24 h.
    Posle 24 h sledi probna runda; ako padne, breaker se opet otvara."""
    rows = recent_runs(conn, source)
    if len(rows) < BREAKER_FAILS:
        return False
    if not all(is_failure(items, err, empty_is_failure) for _, items, err in rows):
        return False
    return now - rows[0][0] < BREAKER_PAUSE


def record_skip(conn: Connection, source: str) -> None:
    conn.execute(
        "insert into source_runs (source, items_found, error) values (%s, 0, %s)",
        (source, f"{SKIP_PREFIX} circuit breaker (3 neuspeha zaredom)"),
    )
    conn.commit()


def expected_minimums(config_path: Path = DEFAULT_CONFIG) -> dict[str, int]:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return {
        e["name"]: int(e.get("min_expected", 0))
        for e in cfg.get("sources") or []
        if e.get("enabled", True)
    }


def weekly_items(conn: Connection, source: str, now: datetime) -> tuple[int, int]:
    """(zbir pronađenih stavki za 7 dana, broj dana istorije; max 7)."""
    row = conn.execute(
        "select coalesce(sum(items_found), 0), min(started_at) from source_runs"
        " where source = %s and started_at >= %s",
        (source, now - timedelta(days=7)),
    ).fetchone()
    first = conn.execute(
        "select min(started_at) from source_runs where source = %s", (source,)
    ).fetchone()[0]
    age_days = (now - first).days if first else 0
    return int(row[0]), min(age_days, 7)


def below_minimum(conn: Connection, source: str, minimum: int, now: datetime) -> bool:
    """Nedelju dana ispod minimuma = kvar i bez greške (tek kad postoji nedelju istorije)."""
    if minimum <= 0:
        return False
    total, age = weekly_items(conn, source, now)
    return age >= 7 and total < minimum


@dataclass
class SourceStat:
    name: str
    runs: int = 0
    errors: int = 0
    items: int = 0
    last_error: str | None = None
    breaker: bool = False
    below_min: tuple[int, int] | None = None  # (nađeno, minimum)


@dataclass
class Report:
    sources: list[SourceStat] = field(default_factory=list)
    last_success: str | None = None
    agent_runs: int = 0
    tokens: int = 0
    anomalies: int = 0
    drafts: int = 0

    def text(self) -> str:
        lines = [
            "Zdravlje sistema (poslednjih 7 dana)",
            f"Poslednja uspešna runda: {self.last_success or '—'}",
        ]
        for s in self.sources:
            mark = (
                "⛔"
                if s.breaker
                else "❌"
                if s.errors and s.errors == s.runs
                else "⚠"
                if (s.errors or s.below_min)
                else "✅"
            )
            line = f"{mark} {s.name}: {s.runs} pokretanja, {s.errors} grešaka, {s.items} stavki"
            if s.breaker:
                line += " · circuit breaker otvoren"
            if s.below_min:
                line += f" · ispod minimuma ({s.below_min[0]} < {s.below_min[1]})"
            if s.last_error and s.errors:
                line += f"\n    poslednja greška: {s.last_error[:100]}"
            lines.append(line)
        lines.append(
            f"Agenti: {self.agent_runs} pokretanja, {self.tokens} tokena, "
            f"{self.anomalies} sa anomalijom"
        )
        lines.append(f"Analize i nacrti: {self.drafts}")
        return "\n".join(lines)


def build_report(
    conn: Connection,
    now: datetime,
    minimums: dict[str, int] | None = None,
    empty_is_failure: dict[str, bool] | None = None,
) -> Report:
    minimums = minimums if minimums is not None else expected_minimums()
    empty_is_failure = empty_is_failure or {}
    since = now - timedelta(days=7)
    report = Report()
    last = conn.execute("select value from kv_state where key = 'last_successful_run'").fetchone()
    report.last_success = last[0][:16].replace("T", " ") if last and last[0] else None

    names = {
        r[0]
        for r in conn.execute(
            "select distinct source from source_runs where started_at >= %s", (since,)
        )
    } | set(minimums)
    for name in sorted(names):
        rows = conn.execute(
            "select items_found, error from source_runs where source = %s and started_at >= %s"
            " and (error is null or error not like %s) order by started_at desc",
            (name, since, SKIP_PREFIX + "%"),
        ).fetchall()
        eif = empty_is_failure.get(name, name in minimums)
        stat = SourceStat(
            name,
            runs=len(rows),
            errors=sum(1 for i, e in rows if is_failure(i, e, eif)),
            items=sum(i for i, _ in rows),
            last_error=next((e for _, e in rows if e), None),
            breaker=breaker_open(conn, name, now, eif),
        )
        if below_minimum(conn, name, minimums.get(name, 0), now):
            stat.below_min = (weekly_items(conn, name, now)[0], minimums[name])
        report.sources.append(stat)

    row = conn.execute(
        "select count(*), coalesce(sum(input_tokens + output_tokens), 0),"
        " count(*) filter (where steps > 10 or outcome in ('budžet', 'petlja', 'limit koraka'))"
        " from agent_runs where started_at >= %s",
        (since,),
    ).fetchone()
    report.agent_runs, report.tokens, report.anomalies = int(row[0]), int(row[1]), int(row[2])
    report.drafts = conn.execute(
        "select count(*) from drafts where created_at >= %s and kind <> 'analysis'", (since,)
    ).fetchone()[0]
    return report


# ---- spoljni watchdog -----------------------------------------------------------------------


def ping_healthcheck(url: str, ok: bool = True, client: httpx.Client | None = None) -> bool:
    """Ping spoljnom servisu (healthchecks.io). Ne ping-uje => servis posle ~26 h šalje upozorenje.
    Nikad ne baca izuzetak: pad watchdog-a ne sme da obori rundu."""
    if not url:
        return False
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        client.get(url.rstrip("/") + ("" if ok else "/fail")).raise_for_status()
        return True
    except httpx.HTTPError:
        log.warning("healthcheck ping nije uspeo")
        return False
    finally:
        if own:
            client.close()


def worker_alive(url: str, client: httpx.Client | None = None) -> bool:
    """Actions proverava da li Worker odgovara (Worker zauzvrat proverava poslednju rundu)."""
    if not url:
        return True  # nije podešeno: ne smatra se kvarom
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        r = client.get(url.rstrip("/") + "/health")
        return r.status_code == 200 and r.text.strip() == "ok"
    except httpx.HTTPError:
        return False
    finally:
        if own:
            client.close()
