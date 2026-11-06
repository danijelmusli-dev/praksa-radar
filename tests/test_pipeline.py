"""Integracioni testovi (trebaju Postgres sa pgvector: TEST_DATABASE_URL)."""

import os
from datetime import UTC, date, datetime

import pytest

from radar.db import connect, kv_get, kv_set, migrate
from radar.models import RawItem
from radar.pipeline import run
from radar.rank import FILTER_KEY
from radar.sources.base import Source

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL nije postavljen"
)

NOW = datetime(2026, 11, 5, 13, 0, tzinfo=UTC)


@pytest.fixture
def conn():
    with connect(os.environ["TEST_DATABASE_URL"]) as c:
        c.execute("drop schema public cascade")
        c.execute("create schema public")
        c.commit()
        migrate(c)
        kv_set(
            c,
            FILTER_KEY,
            {"include": ["python", "ML"], "exclude": ["senior"], "types": ["praksa", "radionica"]},
        )
        yield c


class FakeSource(Source):
    def __init__(self, name="fake", items=None, error=None):
        self.name = name
        self._items = items if items is not None else []
        self._error = error
        self.calls = 0

    def fetch(self, since):
        self.calls += 1
        if self._error:
            raise self._error
        return self._items


def raw(n, title="ML Intern", text="Python Beograd"):
    return RawItem("fake", f"https://x.rs/{n}?utm_source=a", title, text, {"org": "Firma"})


def outbox():
    sent = []

    def send(text, reply_markup=None):
        sent.append(text)

    return sent, send


def test_round_trip_sends_once(conn):
    src = FakeSource(items=[raw(1), raw(2, title="Senior ML Engineer"), raw(3, title="Cook")])
    sent, send = outbox()

    r1 = run(conn, [src], send=send, now=NOW, run_id="r1")
    assert r1["ranked"] == 3 and r1["sent"] == 1
    assert len(sent) == 1 and "ML Intern" in sent[0] and "https://x.rs/1" in sent[0]
    assert "utm_source" not in sent[0]

    # ista tri stavke ponovo -> dedup po URL-u, nema novog slanja
    r2 = run(conn, [src], send=send, now=NOW, run_id="r2")
    assert r2["ranked"] == 0 and r2["sent"] == 0 and len(sent) == 1

    n = conn.execute("select count(*) from opportunities").fetchone()[0]
    assert n == 3
    row = conn.execute("select sent_at, run_id from scores where passed").fetchone()
    assert row[0] is not None and row[1] == "r1"
    assert conn.execute("select count(*) from source_runs").fetchone()[0] == 2


def test_resume_after_crash_between_collect_and_rank(conn):
    src = FakeSource(items=[raw(1)])
    sent, send = outbox()
    with pytest.raises(RuntimeError, match="RADAR_FAIL_AFTER=collect"):
        run(conn, [src], send=send, now=NOW, run_id="r1", fail_after="collect")
    assert conn.execute("select count(*) from opportunities").fetchone()[0] == 1
    assert conn.execute("select count(*) from scores").fetchone()[0] == 0
    assert kv_get(conn, "last_successful_run") is None

    # sledeća runda ne mora ponovo da prikuplja da bi poslala digest
    r2 = run(conn, [], send=send, now=NOW, run_id="r2")
    assert r2["ranked"] == 1 and r2["sent"] == 1 and len(sent) == 1


def test_failed_send_is_retried_without_duplicates(conn):
    src = FakeSource(items=[raw(1), raw(2)])
    calls = []

    def flaky(text, reply_markup=None):
        calls.append(text)
        raise ConnectionError("telegram nedostupan")

    with pytest.raises(ConnectionError):
        run(conn, [src], send=flaky, now=NOW, run_id="r1")
    assert conn.execute("select count(*) from scores where sent_at is not null").fetchone()[0] == 0

    sent, send = outbox()
    r2 = run(conn, [], send=send, now=NOW, run_id="r2")
    assert r2["sent"] == 2 and len(sent) == 1


def test_failing_source_does_not_stop_others_and_adds_warning(conn):
    bad = FakeSource("bad", error=RuntimeError("HTML promenjen"))
    good = FakeSource(items=[raw(1)])
    sent, send = outbox()
    result = run(conn, [bad, good], send=send, now=NOW, run_id="r1")
    assert result["sent"] == 1
    assert "⚠ izvor bad nije uspeo" in sent[0]
    err = conn.execute("select error from source_runs where source = 'bad'").fetchone()[0]
    assert "HTML promenjen" in err


def test_nothing_above_threshold_sends_nothing(conn):
    sent, send = outbox()
    run(conn, [FakeSource(items=[raw(1, title="Cook", text="kuvar")])], send=send, now=NOW)
    assert sent == []


def test_gmail_auth_failure_alerts_once_per_day(conn):
    from radar.sources.gmail_label import GmailAuthError

    gmail = FakeSource("gmail", error=GmailAuthError("istekao"))
    sent, send = outbox()
    run(conn, [gmail], send=send, now=NOW, run_id="r1")
    run(conn, [gmail], send=send, now=NOW, run_id="r2")
    assert len(sent) == 1 and "Gmail prijava je istekla" in sent[0]
    assert kv_get(conn, "alert:gmail:auth") == date(2026, 11, 5).isoformat()


def test_last_successful_run_set_and_used_as_since(conn):
    seen = []

    class Spy(FakeSource):
        def fetch(self, since):
            seen.append(since)
            return []

    spy = Spy()
    run(conn, [spy], send=lambda t, reply_markup=None: None, now=NOW, run_id="r1")
    assert kv_get(conn, "last_successful_run") == NOW.isoformat()
    run(conn, [spy], send=lambda t, reply_markup=None: None, now=NOW, run_id="r2")
    assert (NOW - seen[1]).days == 1  # last_successful_run minus 1 dan preklapanja
    assert (NOW - seen[0]).days == 7
