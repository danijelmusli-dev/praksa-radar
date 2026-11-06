import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"fbclid", "gclid", "trk", "refid", "trackingid", "mc_cid", "mc_eid", "ref"}
KEEP_FRAGMENT_HOSTS = {"mail.google.com"}  # fragment je deo adrese (#all/<id>)


@dataclass
class RawItem:
    """Sirova stavka koju vraća adapter."""

    source: str
    url: str
    title: str
    text: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Opportunity:
    """Normalizovan oblik posle adaptera."""

    url: str
    title: str
    org: str | None
    location: str | None  # grad ili "remote"
    deadline: date | None
    raw_text: str
    source: str
    type: str | None = None


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    scheme = (parts.scheme or "https").lower()

    if host.endswith("linkedin.com"):
        m = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)", parts.path)
        if not m:
            m = re.search(r"currentJobId=(\d+)", parts.query)
        if m:
            return f"https://www.linkedin.com/jobs/view/{m.group(1)}"

    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    )
    path = parts.path.rstrip("/")
    fragment = parts.fragment if host in KEEP_FRAGMENT_HOSTS else ""
    return urlunsplit((scheme, host, path, urlencode(query), fragment))
