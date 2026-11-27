"""Gmail labela `radar` (samo čitanje): LinkedIn job alerti i newsletteri.

LinkedIn parser je heuristika nad strukturom alerta (link na /jobs/view/<id>, pa firma i
lokacija u narednim redovima). Podešava se nad pravim primerom mejla (vidi TASKS-MANUAL.md).
"""

import base64
import re
from datetime import datetime
from email.utils import parseaddr
from urllib.parse import parse_qs, urlsplit

import httpx
from bs4 import BeautifulSoup

from radar.http import PoliteClient, RobotsDisallowed, request_with_retry
from radar.models import RawItem, canonical_url
from radar.sources.base import Source
from radar.web import UrlRejected, clean_html, fetch_following, validate_fetch_url

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_TEXT = 20_000
_JOB_LINK = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/", re.I)


class GmailAuthError(RuntimeError):
    """Refresh token nevažeći/istekao; treba ponovna prijava (scripts/gmail_auth.py)."""


# ---- parsiranje poruke --------------------------------------------------------------------


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _find_body(payload: dict, mime: str) -> str | None:
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return _b64(payload["body"]["data"])
    for part in payload.get("parts") or []:
        found = _find_body(part, mime)
        if found:
            return found
    return None


def _headers(message: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in message["payload"].get("headers", [])}


def parse_linkedin_alert(html: str, source: str) -> list[RawItem]:
    soup = BeautifulSoup(html, "lxml")
    items: dict[str, RawItem] = {}
    for a in soup.find_all("a", href=_JOB_LINK):
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        url = canonical_url(a["href"])
        if url in items:
            continue
        # najbliži predak čiji tekst sadrži više od samog naslova = kartica oglasa
        node, lines = a, []
        for _ in range(6):
            node = node.parent
            if node is None:
                break
            lines = [s for s in node.stripped_strings if s]
            if len(lines) > 1:
                break
        rest = [ln for ln in lines if ln != title][:3]
        org = location = None
        if rest:
            first = rest[0]
            if " · " in first:
                org, _, location = (p.strip() for p in first.partition(" · "))
            else:
                org = first
                location = rest[1] if len(rest) > 1 else None
        items[url] = RawItem(
            source=source,
            url=url,
            title=title,
            text=" ".join([title, *rest]),
            meta={"org": org, "location": location},
        )
    return list(items.values())


def parse_message(message: dict, source: str) -> list[RawItem]:
    headers = _headers(message)
    sender = headers.get("from", "")
    subject = headers.get("subject", "(bez naslova)")
    html = _find_body(message["payload"], "text/html")
    plain = _find_body(message["payload"], "text/plain")

    address = parseaddr(sender)[1].lower()
    if address == "googlealerts-noreply@google.com" and html:
        results = {}
        for anchor in BeautifulSoup(html, "lxml").select("a[href]"):
            link = urlsplit(anchor["href"])
            if link.hostname not in {"google.com", "www.google.com"} or link.path != "/url":
                continue
            params = parse_qs(link.query)
            target = (params.get("url") or params.get("q") or [""])[0]
            if not target.startswith(("https://", "http://")):
                continue
            title = anchor.get_text(" ", strip=True)
            if not title:
                continue
            url = canonical_url(target)
            card = anchor.find_parent("td") or anchor.parent
            results[url] = RawItem("google-alerts", url, title, card.get_text(" ", strip=True))
        return list(results.values())
    if address.endswith("@linkedin.com") and html:
        jobs = parse_linkedin_alert(html, source)
        if jobs:
            return jobs

    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True) if html else (plain or "")
    name, addr = parseaddr(sender)
    return [
        RawItem(
            source=source,
            url=f"https://mail.google.com/mail/u/0/#all/{message['id']}",
            title=subject,
            text=text[:MAX_TEXT],
            meta={"org": name or addr or None},
        )
    ]


# ---- API ----------------------------------------------------------------------------------


class GmailLabelSource(Source):
    name = "gmail"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        label: str = "radar",
        http: httpx.Client | None = None,
        publisher_client: PoliteClient | None = None,
    ):
        self._creds = (client_id, client_secret, refresh_token)
        self._publisher = publisher_client
        self._label = label
        self._http = http or httpx.Client(timeout=20)

    def _access_token(self) -> str:
        cid, secret, refresh = self._creds
        resp = self._http.post(
            TOKEN_URL,
            data={
                "client_id": cid,
                "client_secret": secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code in (400, 401):
            raise GmailAuthError(
                "Gmail token nevažeći ili istekao. Ponovi prijavu: "
                "uv run python scripts/gmail_auth.py, pa ažuriraj GMAIL_REFRESH_TOKEN."
            )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _get(self, path: str, token: str, **params) -> dict:
        resp = request_with_retry(
            self._http, "GET", f"{API}/{path}", params=params,
            headers={"Authorization": f"Bearer {token}"},
        )  # fmt: skip
        return resp.json()

    def fetch(self, since: datetime) -> list[RawItem]:
        token = self._access_token()
        query = f"label:{self._label} after:{int(since.timestamp())}"
        ids, page = [], None
        while True:
            params = {"q": query, "maxResults": 100}
            if page:
                params["pageToken"] = page
            data = self._get("messages", token, **params)
            ids += [m["id"] for m in data.get("messages", [])]
            page = data.get("nextPageToken")
            if not page:
                break
        items: list[RawItem] = []
        for mid in ids:
            message = self._get(f"messages/{mid}", token, format="full")
            for item in parse_message(message, self.name):
                if item.source == "google-alerts" and self._publisher:
                    try:
                        url, page = fetch_following(
                            self._publisher,
                            item.url,
                            lambda u, seed=item.url: validate_fetch_url(u, {seed}),
                        )
                    except (UrlRejected, httpx.HTTPStatusError, RobotsDisallowed):
                        continue
                    if "html" not in page.headers.get("content-type", ""):
                        continue
                    item.url = canonical_url(url)
                    item.text = clean_html(page.text, MAX_TEXT)
                items.append(item)
        return items
