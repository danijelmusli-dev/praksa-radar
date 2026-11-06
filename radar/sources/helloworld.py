"""HelloWorld.rs (IT oglasi u Srbiji).

robots.txt dozvoljava /oglasi-za-posao/ (zabranjen je /konkurs/).

Ova stranica ima fiksnu strukturu kartice, pa je parser poseban, a ne CSS iz sources.yaml.
"""

from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from radar.http import PoliteClient
from radar.models import RawItem
from radar.sources.base import Source

BASE = "https://www.helloworld.rs"
# Pretraga je fuzzy po opisima ("intern" vraća i nepovezane oglase), a kad nema pogodaka sajt
# prikazuje zamenske najnovije oglase; to prepoznajemo i preskačemo.
DEFAULT_QUERIES = ["praksa", "pripravnik", "internship", "trainee", "student"]
NO_RESULTS_MARKER = "nema oglasa po traženim kriterijumima"


def _card(anchor):
    node = anchor
    while node is not None and "rounded-lg" not in (node.get("class") or []):
        node = node.parent
    return node or anchor.parent


def _icon_text(card, icon_class: str) -> str | None:
    icon = card.select_one(f"i.{icon_class}")
    if icon is None or icon.parent is None:
        return None
    return icon.parent.get_text(" ", strip=True) or None


def has_no_results(html: str) -> bool:
    return NO_RESULTS_MARKER in html


def parse_helloworld(html: str, source: str = "helloworld") -> list[RawItem]:
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.select("a.__ga4_job_title"):
        href = a.get("href")
        if not href:
            continue
        parts = urlsplit(urljoin(BASE, href))
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))  # bez tracking upita
        if url in seen:  # kartica se pojavljuje i u mobilnom rasporedu
            continue
        seen.add(url)
        card = _card(a)
        # uz tačne pogotke firma je goli <h4><a>, uz zamenske ima klasu __ga4_job_company
        company = card.select_one("a.__ga4_job_company") or card.select_one("h4 a")
        location = _icon_text(card, "la-map-marker")
        deadline = _icon_text(card, "la-clock")
        seniority = card.select_one(".__ga4_job_seniority")
        title = a.get_text(" ", strip=True)
        text = " ".join(
            filter(
                None,
                [
                    title,
                    company.get_text(" ", strip=True) if company else None,
                    location,
                    f"Rok: {deadline}" if deadline else None,
                    seniority.get_text(" ", strip=True) if seniority else None,
                ],
            )
        )
        out.append(
            RawItem(
                source=source,
                url=url,
                title=title,
                text=text,
                meta={
                    "org": company.get_text(" ", strip=True) if company else None,
                    "location": location,
                },
            )
        )
    return out


class HelloWorldSource(Source):
    name = "helloworld"

    def __init__(self, client: PoliteClient, queries: list[str] | None = None):
        self._client = client
        self._queries = queries or DEFAULT_QUERIES

    def fetch(self, since: datetime) -> list[RawItem]:
        items: list[RawItem] = []
        for q in self._queries:
            resp = self._client.get(
                f"{BASE}/oglasi-za-posao/", params={"q": q, "sort": "p_vreme_postavljanja_sort"}
            )
            if has_no_results(resp.text):
                continue
            items.extend(parse_helloworld(resp.text, self.name))
        return items
