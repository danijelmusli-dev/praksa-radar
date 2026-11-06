"""HTTP sloj: retry sa eksponencijalnim čekanjem, robots.txt, rate limit po domenu."""

import logging
import random
import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "PraksaRadar/0.1 (+https://github.com/praksa-radar)"
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 60.0


class RobotsDisallowed(RuntimeError):
    pass


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return min(max(float(value), 0.0), MAX_RETRY_AFTER)
    except ValueError:
        return None


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    **kwargs,
) -> httpx.Response:
    """Ponovi na 5xx/429/timeout/mrežnu grešku (ukupno max_attempts pokušaja).

    Poštuje Retry-After. Poslednji neuspeh baca izuzetak (HTTPStatusError ili httpx.HTTPError).
    """
    rng = rng or random.Random()
    for attempt in range(1, max_attempts + 1):
        last = attempt == max_attempts
        try:
            resp = client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError):
            if last:
                raise
            sleep(base_delay * 2 ** (attempt - 1) + rng.uniform(0, 0.5))
            continue
        if resp.status_code in RETRY_STATUS and not last:
            wait = _retry_after(resp)
            if wait is None:
                wait = base_delay * 2 ** (attempt - 1) + rng.uniform(0, 0.5)
            sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    raise AssertionError("unreachable")


class PoliteClient:
    """GET klijent: jasan User-Agent, robots.txt, 2-3 s razmaka po domenu, timeout 20 s."""

    def __init__(
        self,
        user_agent: str = USER_AGENT,
        timeout: float = 20.0,
        min_delay: float = 2.0,
        max_delay: float = 3.0,
        max_attempts: int = 3,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ):
        self.user_agent = user_agent
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_attempts = max_attempts
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": user_agent}, follow_redirects=True
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_request: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self._rng.uniform(self.min_delay, self.max_delay) - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _robots_for(self, scheme: str, host: str) -> RobotFileParser | None:
        """None = sve zabranjeno (robots.txt nedostupan sa 5xx ili mrežnom greškom)."""
        if host in self._robots:
            return self._robots[host]
        rp = RobotFileParser()
        self._throttle(host)
        try:
            resp = self._client.get(f"{scheme}://{host}/robots.txt")
            if resp.status_code >= 500:
                rp = None
            elif resp.status_code >= 400:
                rp.parse([])  # nema robots.txt: dozvoljeno
            else:
                rp.parse(resp.text.splitlines())
        except httpx.HTTPError:
            rp = None
        self._robots[host] = rp
        return rp

    def get(self, url: str, **kwargs) -> httpx.Response:
        parts = urlsplit(url)
        rp = self._robots_for(parts.scheme, parts.netloc)
        if rp is None or not rp.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"robots.txt zabranjuje ili nije dostupan: {url}")
        self._throttle(parts.netloc)
        return request_with_retry(
            self._client,
            "GET",
            url,
            max_attempts=self.max_attempts,
            sleep=self._sleep,
            rng=self._rng,
            **kwargs,
        )
