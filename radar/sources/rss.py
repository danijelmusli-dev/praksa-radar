from datetime import datetime
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from lxml import etree

from radar.http import PoliteClient
from radar.models import RawItem
from radar.sources.base import Source


class RssSource(Source):
    def __init__(self, name: str, client: PoliteClient, url: str):
        self.name = name
        self.client = client
        self.url = url

    def fetch(self, since: datetime) -> list[RawItem]:
        response = self.client.get(self.url)
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.fromstring(response.content, parser)
        if root.find("channel") is None:
            raise ValueError("expected RSS channel")
        items = []
        for entry in root.findall("channel/item"):
            published = entry.findtext("pubDate")
            if published and parsedate_to_datetime(published) < since:
                continue
            title = entry.findtext("title", "").strip()
            url = entry.findtext("link", "").strip()
            if not title or not url.startswith(("https://", "http://")):
                continue
            body = entry.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
            text = BeautifulSoup(body or entry.findtext("description", ""), "lxml")
            items.append(RawItem(self.name, url, title, text.get_text(" ", strip=True)))
        return items
