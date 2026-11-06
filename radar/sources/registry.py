from pathlib import Path

import yaml

from radar.config import Settings
from radar.http import PoliteClient
from radar.sources.base import Source
from radar.sources.gmail_label import GmailLabelSource
from radar.sources.helloworld import HelloWorldSource
from radar.sources.html_generic import HtmlGenericSource

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"
GENERIC_KEYS = ("item", "title", "link", "text", "org", "location")


def build_sources(
    settings: Settings, client: PoliteClient, config_path: Path = DEFAULT_CONFIG
) -> list[Source]:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    sources: list[Source] = []
    for entry in cfg.get("sources") or []:
        if not entry.get("enabled", True):
            continue
        kind = entry["type"]
        if kind == "helloworld":
            sources.append(HelloWorldSource(client, entry.get("queries")))
        elif kind == "html_generic":
            selectors = {k: entry[k] for k in GENERIC_KEYS if k in entry}
            sources.append(HtmlGenericSource(entry["name"], client, entry["url"], **selectors))
        else:
            raise ValueError(f"nepoznat tip izvora: {kind}")
    if settings.gmail_refresh_token:
        sources.append(
            GmailLabelSource(
                settings.gmail_client_id,
                settings.gmail_client_secret,
                settings.gmail_refresh_token,
            )
        )
    return sources
