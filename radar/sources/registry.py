import logging
from pathlib import Path

import yaml
from psycopg import Connection

from radar.config import Settings
from radar.db import KvStore
from radar.http import PoliteClient
from radar.sources.base import Source
from radar.sources.gmail_label import GmailLabelSource
from radar.sources.helloworld import HelloWorldSource
from radar.sources.html_generic import HtmlGenericSource
from radar.sources.instagram import InstagramSource

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"
GENERIC_KEYS = ("item", "title", "link", "text", "org", "location")


def build_sources(
    settings: Settings,
    client: PoliteClient,
    config_path: Path = DEFAULT_CONFIG,
    conn: Connection | None = None,
) -> list[Source]:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    sources: list[Source] = []
    for entry in cfg.get("sources") or []:
        if not entry.get("enabled", True):
            continue
        kind = entry["type"]
        if kind == "helloworld":
            source: Source = HelloWorldSource(client, entry.get("queries"))
        elif kind == "html_generic":
            selectors = {k: entry[k] for k in GENERIC_KEYS if k in entry}
            source = HtmlGenericSource(entry["name"], client, entry["url"], **selectors)
        elif kind == "instagram":
            if not (settings.instagram_access_token and settings.instagram_user_id and conn):
                log.info("instagram izvor preskočen: nema tokena ili baze")
                continue
            source = InstagramSource(
                settings.instagram_access_token,
                settings.instagram_user_id,
                entry.get("hashtags") or [],
                entry.get("accounts") or [],
                KvStore(conn),
            )
        else:
            raise ValueError(f"nepoznat tip izvora: {kind}")
        source.min_expected = int(entry.get("min_expected", 0))  # nedeljni očekivani minimum
        sources.append(source)
    if settings.gmail_refresh_token:
        sources.append(
            GmailLabelSource(
                settings.gmail_client_id,
                settings.gmail_client_secret,
                settings.gmail_refresh_token,
            )
        )
    return sources
