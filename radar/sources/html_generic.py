"""Generički adapter: URL + CSS selektori iz config/sources.yaml."""

from datetime import datetime
from urllib.parse import urljoin

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
        out.append(
            RawItem(
                source=source,
                url=url,
                title=title_el.get_text(" ", strip=True),
                text=(body or el).get_text(" ", strip=True),
                meta=meta,
            )
        )
    return out


class HtmlGenericSource(Source):
    def __init__(self, name: str, client: PoliteClient, url: str, **selectors):
        self.name = name
        self._client = client
        self._url = url
        self._selectors = selectors

    def fetch(self, since: datetime) -> list[RawItem]:
        resp = self._client.get(self._url)
        return parse_html_generic(resp.text, self._url, self.name, **self._selectors)
