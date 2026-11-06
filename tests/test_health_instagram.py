from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from radar.health import (
    below_minimum,
    breaker_open,
    build_report,
    consecutive_failures,
    is_failure,
    ping_healthcheck,
    record_skip,
    worker_alive,
)
from radar.models import RawItem
from radar.pipeline import run
from radar.sources.base import Source
from radar.sources.instagram import (
    GRAPH,
    InstagramAuthError,
    InstagramSource,
    looks_like_opportunity,
)

T0 = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def add_run(conn, source, at, items=0, error=None):
    conn.execute(
        "insert into source_runs (source, started_at, items_found, error) values (%s,%s,%s,%s)",
        (source, at, items, error),
    )
    conn.commit()


# ---- circuit breaker --------------------------------------------------------------------------


def test_is_failure_rules():
    assert is_failure(0, "boom", False)
    assert not is_failure(0, None, False)  # prazno je normalno (npr. Gmail)
    assert is_failure(0, None, True)  # lista oglasa nikad ne sme da bude prazna
    assert not is_failure(5, None, True)
    assert not is_failure(0, "skipped: circuit breaker", True)  # preskakanje nije neuspeh


def test_breaker_opens_after_three_failures_for_24h_then_probes(fresh_conn):
    c = fresh_conn
    for i in range(2):
        add_run(c, "s", T0 - timedelta(hours=10 - i), error="boom")
    assert not breaker_open(c, "s", T0)  # tek 2 neuspeha
    add_run(c, "s", T0 - timedelta(hours=1), error="boom")
    assert consecutive_failures(c, "s", False) == 3
    assert breaker_open(c, "s", T0)
    assert breaker_open(c, "s", T0 + timedelta(hours=22))
    assert not breaker_open(c, "s", T0 + timedelta(hours=25))  # probna runda
    # probna runda pada: breaker se opet otvara za 24 h
    add_run(c, "s", T0 + timedelta(hours=25), error="boom")
    assert breaker_open(c, "s", T0 + timedelta(hours=26))


def test_skipped_rows_do_not_count_and_do_not_extend_pause(fresh_conn):
    c = fresh_conn
    for h in (5, 4, 3):
        add_run(c, "s", T0 - timedelta(hours=h), error="boom")
    record_skip(c, "s")
    record_skip(c, "s")
    assert consecutive_failures(c, "s", False) == 3
    assert breaker_open(c, "s", T0 - timedelta(hours=2))
    assert not breaker_open(c, "s", T0 + timedelta(hours=25))  # skip redovi ne produžavaju pauzu


def test_one_success_closes_the_breaker(fresh_conn):
    c = fresh_conn
    for h in (5, 4):
        add_run(c, "s", T0 - timedelta(hours=h), error="boom")
    add_run(c, "s", T0 - timedelta(hours=3), items=4)
    add_run(c, "s", T0 - timedelta(hours=2), error="boom")
    assert consecutive_failures(c, "s", False) == 1 and not breaker_open(c, "s", T0)


def test_empty_runs_open_breaker_only_for_listing_sources(fresh_conn):
    c = fresh_conn
    for h in (3, 2, 1):
        add_run(c, "lista", T0 - timedelta(hours=h), items=0)
    assert breaker_open(c, "lista", T0, empty_is_failure=True)
    assert not breaker_open(c, "lista", T0, empty_is_failure=False)


def test_below_minimum_needs_a_week_of_history(fresh_conn):
    c = fresh_conn
    add_run(c, "s", T0 - timedelta(days=3), items=1)
    assert not below_minimum(c, "s", 50, T0)  # još nema nedelju istorije
    add_run(c, "s", T0 - timedelta(days=8), items=0)
    add_run(c, "s", T0 - timedelta(hours=1), items=2)
    assert below_minimum(c, "s", 50, T0)  # 3 < 50 i istorija je duža od 7 dana
    assert not below_minimum(c, "s", 3, T0)
    assert not below_minimum(c, "s", 0, T0)


def test_weekly_report(fresh_conn):
    c = fresh_conn
    for d in range(8, 0, -1):
        add_run(c, "hello", T0 - timedelta(days=d), items=30)
    for h in (30, 20, 10):
        add_run(c, "pao", T0 - timedelta(hours=h), error="HTML promenjen")
    add_run(c, "sporo", T0 - timedelta(days=9), items=0)
    add_run(c, "sporo", T0 - timedelta(days=1), items=1)
    c.execute(
        "insert into agent_runs (agent, steps, input_tokens, output_tokens, outcome)"
        " values ('a', 12, 1000, 500, 'ok'), ('b', 3, 100, 50, 'petlja')"
    )
    c.execute("insert into drafts (kind, content) values ('cover_letter', 'x'), ('analysis', 'y')")
    c.execute(
        "insert into kv_state values ('last_successful_run', to_jsonb(%s::text))", (T0.isoformat(),)
    )
    c.commit()
    text = build_report(
        c, T0, minimums={"sporo": 10, "hello": 5}, empty_is_failure={"hello": True}
    ).text()
    assert "Poslednja uspešna runda: 2026-11-06 12:00" in text
    assert "✅ hello: 7 pokretanja, 0 grešaka, 210 stavki" in text
    assert "⛔ pao: 3 pokretanja, 3 grešaka" in text and "circuit breaker otvoren" in text
    assert "poslednja greška: HTML promenjen" in text
    assert "ispod minimuma (1 < 10)" in text
    assert "Agenti: 2 pokretanja, 1650 tokena, 2 sa anomalijom" in text
    assert "Analize i nacrti: 1" in text


# ---- pipeline + breaker -----------------------------------------------------------------------


class Flaky(Source):
    name = "flaky"
    empty_is_failure = True

    def __init__(self):
        self.calls = 0
        self.items: list[RawItem] = []

    def fetch(self, since):
        self.calls += 1
        if not self.items:
            raise RuntimeError("HTML promenjen")
        return self.items


def test_pipeline_opens_breaker_once_skips_for_24h_then_probes(fresh_conn):
    c = fresh_conn
    src, opened, sent = Flaky(), [], []

    def go(at):
        return run(
            c, [src], send=lambda t, reply_markup=None: sent.append(t),
            now=at, run_id=str(at), on_breaker_open=opened.append,
        )  # fmt: skip

    for i in range(2):
        go(T0 + timedelta(hours=i))
    assert opened == [] and src.calls == 2
    go(T0 + timedelta(hours=2))  # treći neuspeh zaredom
    assert opened == ["flaky"] and src.calls == 3

    r = go(T0 + timedelta(hours=3))  # breaker otvoren: izvor se ne zove
    assert src.calls == 3 and opened == ["flaky"]
    assert any("preskočen 24 h" in w for w in r["warnings"])
    skip = c.execute("select error from source_runs order by id desc limit 1").fetchone()[0]
    assert skip.startswith("skipped:")

    go(T0 + timedelta(hours=27))  # probna runda
    assert src.calls == 4
    src.items = [RawItem("flaky", "https://x.rs/1", "Python praksa", "text", {})]
    go(T0 + timedelta(hours=60))  # oporavak
    assert src.calls == 5 and not breaker_open(c, "flaky", T0 + timedelta(hours=61), True)


def test_alarm_exception_does_not_break_the_round(fresh_conn):
    def bad_alarm(name):
        raise RuntimeError("telegram pao")

    src = Flaky()
    for i in range(3):
        r = run(fresh_conn, [src], send=lambda *a, **k: None, now=T0 + timedelta(hours=i),
                run_id=f"r{i}", on_breaker_open=bad_alarm)  # fmt: skip
    assert r["run_id"] == "r2"


def test_instagram_auth_failure_alerts_once_a_day(fresh_conn):
    class IgDead(Source):
        name = "instagram"

        def fetch(self, since):
            raise InstagramAuthError("istekao")

    sent = []
    for i in range(2):
        run(fresh_conn, [IgDead()], send=lambda t, reply_markup=None: sent.append(t), now=T0,
            run_id=f"r{i}")  # fmt: skip
    alerts = [m for m in sent if "Instagram token je istekao" in m]
    assert len(alerts) == 1


# ---- spoljni watchdog -------------------------------------------------------------------------


@respx.mock
def test_healthcheck_ping_success_failure_and_errors_never_raise():
    ok = respx.get("https://hc-ping.com/abc").respond(200)
    fail = respx.get("https://hc-ping.com/abc/fail").respond(200)
    assert ping_healthcheck("https://hc-ping.com/abc")
    assert ping_healthcheck("https://hc-ping.com/abc", ok=False)
    assert ok.call_count == 1 and fail.call_count == 1
    assert not ping_healthcheck("")
    respx.get("https://hc-ping.com/down").mock(side_effect=httpx.ConnectError("x"))
    assert not ping_healthcheck("https://hc-ping.com/down")
    respx.get("https://hc-ping.com/500").respond(500)
    assert not ping_healthcheck("https://hc-ping.com/500")


@respx.mock
def test_worker_alive():
    respx.get("https://w.dev/health").respond(200, text="ok")
    assert worker_alive("https://w.dev")
    assert worker_alive("")  # nije podešeno: nije kvar
    respx.get("https://w2.dev/health").respond(502)
    assert not worker_alive("https://w2.dev")
    respx.get("https://w3.dev/health").mock(side_effect=httpx.ConnectError("x"))
    assert not worker_alive("https://w3.dev")


# ---- Instagram --------------------------------------------------------------------------------


class Mem:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.data[k] = v


def ig(state=None, hashtags=("praksasrbija",), accounts=()):
    return InstagramSource(
        "tok", "17841", list(hashtags), list(accounts), state or Mem(), http=httpx.Client(),
        clock=lambda: T0, sleep=lambda s: None,
    )  # fmt: skip


def media(n, caption):
    return {"id": str(n), "caption": caption, "permalink": f"https://www.instagram.com/p/{n}/",
            "timestamp": "2026-11-06T09:00:00+0000", "media_type": "IMAGE"}  # fmt: skip


@respx.mock
def test_hashtag_search_filters_noise_and_caches_ids():
    search = respx.get(f"{GRAPH}/ig_hashtag_search").respond(200, json={"data": [{"id": "H1"}]})
    respx.get(f"{GRAPH}/H1/recent_media").respond(
        200,
        json={"data": [
            media(1, "Otvorene prijave za letnju praksu!\nRok 20.11."),
            media(2, "Moj doručak ☕"),
            media(3, "Hackathon FTN: prijavi tim"),
        ]},
    )  # fmt: skip
    state = Mem()
    items = ig(state).fetch(T0 - timedelta(days=1))
    assert [i.url for i in items] == [
        "https://www.instagram.com/p/1/",
        "https://www.instagram.com/p/3/",
    ]
    assert items[0].title == "Otvorene prijave za letnju praksu!" and items[0].source == "instagram"
    assert state.data["ig_hashtag_ids"] == {"praksasrbija": "H1"}
    assert "praksasrbija" in state.data["ig_hashtags"]
    ig(state).fetch(T0)
    assert search.call_count == 1  # id hashtaga je keširan
    assert search.calls[0].request.url.params["access_token"] == "tok"


@respx.mock
def test_hashtag_quota_30_per_7_days():
    used = {f"tag{i}": (T0 - timedelta(days=2)).isoformat() for i in range(30)}
    used["star"] = (T0 - timedelta(days=9)).isoformat()  # van prozora: ne troši kvotu
    state = Mem({"ig_hashtags": used, "ig_hashtag_ids": {"tag0": "T0"}})
    respx.get(f"{GRAPH}/T0/recent_media").respond(200, json={"data": [media(1, "praksa")]})
    search = respx.get(f"{GRAPH}/ig_hashtag_search").respond(200, json={"data": [{"id": "N"}]})
    items = ig(state, hashtags=["novitag", "tag0"]).fetch(T0)
    assert search.call_count == 0  # novi hashtag preskočen (kvota puna)
    assert len(items) == 1  # već korišćen hashtag sme ponovo
    assert "novitag" not in state.data["ig_hashtags"]


@respx.mock
def test_business_discovery_only_fresh_posts_and_skips_non_business_accounts():
    fresh, old = media(1, "Radionica: Arduino za početnike"), media(2, "Prijave za praksu")
    old["timestamp"] = "2026-10-01T09:00:00+0000"

    def answer(request):
        if "username(ftn_org)" in request.url.params["fields"]:
            return httpx.Response(
                200, json={"business_discovery": {"media": {"data": [fresh, old]}}}
            )
        return httpx.Response(400, json={"error": {"code": 110, "message": "Invalid user id"}})

    respx.get(f"{GRAPH}/17841").mock(side_effect=answer)
    items = ig(hashtags=[], accounts=["ftn_org", "@licni"]).fetch(T0 - timedelta(days=1))
    assert [i.url for i in items] == ["https://www.instagram.com/p/1/"]
    assert items[0].meta == {"org": "ftn_org"}


@respx.mock
def test_expired_token_raises_auth_error():
    respx.get(f"{GRAPH}/ig_hashtag_search").respond(
        400, json={"error": {"code": 190, "message": "Error validating access token"}}
    )
    with pytest.raises(InstagramAuthError, match="INSTAGRAM_ACCESS_TOKEN"):
        ig().fetch(T0)


@respx.mock
def test_other_api_errors_are_plain_failures():
    respx.get(f"{GRAPH}/ig_hashtag_search").respond(
        400, json={"error": {"code": 4, "message": "limit"}}
    )
    with pytest.raises(RuntimeError, match="limit"):
        ig().fetch(T0)


def test_opportunity_filter():
    for yes in ("Prijave za praksu", "Hackathon!", "Call for applications", "Radionica o AI"):
        assert looks_like_opportunity(yes)
    for no in ("Dobar dan svima", "", "Moja nova mačka"):
        assert not looks_like_opportunity(no)
