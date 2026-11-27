"""Generički adapter: URL + CSS selektori iz config/sources.yaml."""

from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from radar.http import PoliteClient
from radar.models import RawItem
from radar.sources.base import Source


def parse_html_generic(
    html: str,
    base_url: str,
    source: str,
    *,
    item: str,
    title: str,
    link: str = "a",
    text: str | None = None,
    org: str | None = None,
    location: str | None = None,
    deadline: str | None = None,
) -> list[RawItem]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for el in soup.select(item):
        title_el = el.select_one(title)
        link_el = el.select_one(link) or (el if el.name == "a" else None)
        if title_el is None or link_el is None or not link_el.get("href"):
            continue
        url = urljoin(base_url, link_el["href"])
        if not url.startswith(("http://", "https://")):
            continue
        body = el.select_one(text) if text else None
        meta = {}
        if org and (o := el.select_one(org)):
            meta["org"] = o.get_text(" ", strip=True)
        if location and (loc := el.select_one(location)):
            meta["location"] = loc.get_text(" ", strip=True)
        summary = (body or el).get_text(" ", strip=True)
        if deadline and (date_el := el.select_one(deadline)):
            summary += " Rok: " + date_el.get_text(" ", strip=True)
        out.append(
            RawItem(
                source=source,
                url=url,
                title=title_el.get_text(" ", strip=True),
                text=summary,
                meta=meta,
            )
        )
    return out


class HtmlGenericSource(Source):
    empty_is_failure = True  # prazna lista = verovatno promenjen HTML

    def __init__(
        self,
        name: str,
        client: PoliteClient,
        url: str,
        *,
        pagination=None,
        employer=None,
        **selectors,
    ):
        self.name = name
        self._client = client
        self._url = url
        self._selectors = selectors
        self._pagination = pagination
        self._employer = employer

    def fetch(self, since: datetime) -> list[RawItem]:
        resp = self._client.get(self._url)
        items = parse_html_generic(resp.text, self._url, self.name, **self._selectors)
        if self._pagination:
            seen = {self._url.rstrip("/")}
            for anchor in BeautifulSoup(resp.text, "lxml").select(self._pagination):
                url = urljoin(self._url, anchor.get("href") or "").rstrip("/")
                if url in seen or urlsplit(url).netloc != urlsplit(self._url).netloc:
                    continue
                seen.add(url)
                page = self._client.get(url)
                items.extend(parse_html_generic(page.text, url, self.name, **self._selectors))
                if len(seen) >= 8:
                    break
        if self._employer:
            for item in items:
                item.meta["org"] = self._employer
        return items
