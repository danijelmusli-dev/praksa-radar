import random

import httpx
import pytest
import respx

from radar.http import PoliteClient, RobotsDisallowed, request_with_retry

URL = "https://example.com/jobs"


def make_client(sleeps, **kw):
    t = {"now": 0.0}

    def sleep(s):
        sleeps.append(s)
        t["now"] += s

    return PoliteClient(
        client=httpx.Client(headers={"User-Agent": "PraksaRadar/test"}),
        sleep=sleep,
        clock=lambda: t["now"],
        rng=random.Random(1),
        **kw,
    )


@respx.mock
def test_retries_5xx_then_succeeds():
    respx.get("https://example.com/robots.txt").respond(404)
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, text="ok")]
    )
    sleeps = []
    assert make_client(sleeps).get(URL).text == "ok"
    assert route.call_count == 3


@respx.mock
def test_gives_up_after_max_attempts():
    respx.get("https://example.com/robots.txt").respond(404)
    route = respx.get(URL).respond(500)
    with pytest.raises(httpx.HTTPStatusError):
        make_client([]).get(URL)
    assert route.call_count == 3


@respx.mock
def test_honors_retry_after():
    respx.get("https://example.com/robots.txt").respond(404)
    respx.get(URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)]
    )
    sleeps = []
    make_client(sleeps).get(URL)
    assert 7.0 in sleeps


@respx.mock
def test_retries_timeout():
    respx.get("https://example.com/robots.txt").respond(404)
    route = respx.get(URL).mock(side_effect=[httpx.ConnectTimeout("t"), httpx.Response(200)])
    make_client([]).get(URL)
    assert route.call_count == 2


@respx.mock
def test_no_retry_on_404():
    respx.get("https://example.com/robots.txt").respond(404)
    route = respx.get(URL).respond(404)
    with pytest.raises(httpx.HTTPStatusError):
        make_client([]).get(URL)
    assert route.call_count == 1


@respx.mock
def test_robots_disallow_blocks_without_fetching():
    respx.get("https://example.com/robots.txt").respond(200, text="User-agent: *\nDisallow: /jobs")
    route = respx.get(URL).respond(200)
    with pytest.raises(RobotsDisallowed):
        make_client([]).get(URL)
    assert route.call_count == 0


@respx.mock
def test_robots_5xx_is_treated_as_disallow():
    respx.get("https://example.com/robots.txt").respond(503)
    route = respx.get(URL).respond(200)
    with pytest.raises(RobotsDisallowed):
        make_client([]).get(URL)
    assert route.call_count == 0


@respx.mock
def test_robots_is_cached_per_host():
    robots = respx.get("https://example.com/robots.txt").respond(404)
    respx.get(url__startswith="https://example.com/").respond(200)
    c = make_client([])
    c.get(URL)
    c.get("https://example.com/other")
    assert robots.call_count == 1


@respx.mock
def test_rate_limit_spaces_requests_per_domain():
    respx.get("https://example.com/robots.txt").respond(404)
    respx.get(url__startswith="https://example.com/").respond(200)
    sleeps = []
    c = make_client(sleeps)
    c.get(URL)
    c.get("https://example.com/other")
    assert sleeps and all(2.0 <= s <= 3.0 for s in sleeps)


@respx.mock
def test_sends_clear_user_agent():
    respx.get("https://example.com/robots.txt").respond(404)
    route = respx.get(URL).respond(200)
    make_client([]).get(URL)
    assert route.calls[0].request.headers["user-agent"] == "PraksaRadar/test"


@respx.mock
def test_request_with_retry_plain_client():
    route = respx.get("https://api.test/x").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"a": 1})]
    )
    resp = request_with_retry(httpx.Client(), "GET", "https://api.test/x", sleep=lambda s: None)
    assert resp.json() == {"a": 1}
    assert route.call_count == 2
