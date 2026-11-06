"""Instagram preko zvaničnog Graph API-ja (nivo A). Ništa iza logina, bez scrapovanja.

- Hashtag Search: ig_hashtag_search + recent_media (samo objave iz poslednja 24 h)
- Business Discovery: objave javnih business/creator naloga (studentske organizacije, firme)

Ograničenja API-ja: najviše 30 različitih hashtagova u 7 dana (ovde se prati u kv_state), treba
Business nalog i Meta app review. Istekao/nevažeći token (kod 190) = InstagramAuthError.
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from radar.http import request_with_retry
from radar.models import RawItem
from radar.sources.base import Source

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"
MAX_HASHTAGS = 30
WINDOW = timedelta(days=7)
STATE_KEY = "ig_hashtags"
IDS_KEY = "ig_hashtag_ids"

# Objava ima smisla samo ako liči na priliku; ostalo je šum (hashtag recent_media je bučan).
OPPORTUNITY = re.compile(
    r"praks|intern|radionic|workshop|hackathon|hakaton|takmi[cč]enj|konkurs|prijav|akademij|"
    r"trainee|stipendij|bootcamp|apply|applications?|open call|call for|junior",
    re.I,
)


class InstagramAuthError(RuntimeError):
    """Token nevažeći ili istekao; treba novi (vidi TASKS-MANUAL.md)."""


class StateStore(Protocol):
    def get(self, key: str) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


def looks_like_opportunity(caption: str) -> bool:
    return bool(OPPORTUNITY.search(caption or ""))


def _title(caption: str) -> str:
    first = next((ln.strip() for ln in caption.splitlines() if ln.strip()), "Instagram objava")
    return first[:120]


class InstagramSource(Source):
    name = "instagram"
    empty_is_failure = False  # objava možda nema; "ispod minimuma" hvata tihe kvarove

    def __init__(
        self,
        token: str,
        user_id: str,
        hashtags: list[str],
        accounts: list[str],
        state: StateStore,
        http: httpx.Client | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] | None = None,
    ):
        self._token = token
        self._user_id = user_id
        self._hashtags = [h.lstrip("#").lower() for h in hashtags]
        self._accounts = [a.lstrip("@") for a in accounts]
        self._state = state
        self._http = http or httpx.Client(timeout=20)
        self._clock = clock
        self._sleep = sleep

    # ---- API ------------------------------------------------------------------------------

    def _get(self, path: str, **params) -> dict:
        kwargs = {"sleep": self._sleep} if self._sleep else {}
        try:
            resp = request_with_retry(
                self._http,
                "GET",
                f"{GRAPH}/{path}",
                params={**params, "access_token": self._token},
                **kwargs,
            )
        except httpx.HTTPStatusError as exc:
            err = {}
            try:
                err = exc.response.json().get("error", {})
            except ValueError:
                pass
            if err.get("code") == 190 or exc.response.status_code == 401:
                raise InstagramAuthError(
                    "Instagram token nevažeći ili istekao. Generiši novi i ažuriraj "
                    "INSTAGRAM_ACCESS_TOKEN (vidi TASKS-MANUAL.md)."
                ) from exc
            raise RuntimeError(
                f"Instagram API {exc.response.status_code}: {err.get('message', '')[:150]}"
            ) from exc
        return resp.json()

    # ---- hashtagovi (kvota 30 / 7 dana) ---------------------------------------------------

    def _active_hashtags(self, now: datetime) -> dict[str, str]:
        used: dict[str, str] = self._state.get(STATE_KEY) or {}
        return {t: ts for t, ts in used.items() if now - datetime.fromisoformat(ts) < WINDOW}

    def _hashtag_id(self, tag: str) -> str | None:
        ids: dict[str, str] = self._state.get(IDS_KEY) or {}
        if tag in ids:
            return ids[tag]
        data = self._get("ig_hashtag_search", user_id=self._user_id, q=tag).get("data") or []
        if not data:
            return None
        ids[tag] = data[0]["id"]
        self._state.set(IDS_KEY, ids)
        return ids[tag]

    def _fetch_hashtags(self, now: datetime) -> list[RawItem]:
        active = self._active_hashtags(now)
        out: list[RawItem] = []
        for tag in self._hashtags:
            if tag not in active and len(active) >= MAX_HASHTAGS:
                log.warning("hashtag #%s preskočen: kvota od 30 hashtagova / 7 dana", tag)
                continue
            hid = self._hashtag_id(tag)
            if hid is None:
                continue
            active.setdefault(tag, now.isoformat())  # kvota se troši na prvom upitu hashtaga
            self._state.set(STATE_KEY, active)
            media = self._get(
                f"{hid}/recent_media",
                user_id=self._user_id,
                fields="id,caption,permalink,timestamp,media_type",
            ).get("data", [])
            out += self._items(media, org=None)
        return out

    # ---- Business Discovery ---------------------------------------------------------------

    def _fetch_accounts(self, since: datetime) -> list[RawItem]:
        out: list[RawItem] = []
        for username in self._accounts:
            fields = (
                f"business_discovery.username({username})"
                "{media.limit(10){caption,permalink,timestamp,media_type}}"
            )
            try:
                data = self._get(self._user_id, fields=fields)
            except RuntimeError as exc:  # nalog nije business/creator ili ne postoji: preskoči
                if isinstance(exc, InstagramAuthError):
                    raise
                log.warning("business discovery @%s: %s", username, exc)
                continue
            media = data.get("business_discovery", {}).get("media", {}).get("data", [])
            fresh = [m for m in media if _after(m.get("timestamp"), since)]
            out += self._items(fresh, org=username)
        return out

    @staticmethod
    def _items(media: list[dict], org: str | None) -> list[RawItem]:
        items = []
        for m in media:
            caption = m.get("caption") or ""
            if not m.get("permalink") or not looks_like_opportunity(caption):
                continue
            items.append(
                RawItem(
                    source="instagram",
                    url=m["permalink"],
                    title=_title(caption),
                    text=caption[:3000],
                    meta={"org": org},
                )
            )
        return items

    def fetch(self, since: datetime) -> list[RawItem]:
        now = self._clock()
        items = self._fetch_hashtags(now) + self._fetch_accounts(since)
        return list({i.url: i for i in items}.values())


def _after(ts: str | None, since: datetime) -> bool:
    if not ts:
        return True
    try:
        return datetime.fromisoformat(ts) >= since
    except ValueError:
        return True
