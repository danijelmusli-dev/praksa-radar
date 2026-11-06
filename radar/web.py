"""Bezbedno čitanje weba za agente: čišćenje sadržaja i provera URL-ova.

Tekst sa stranica je podatak, ne naredba. Pre nego što uđe u prompt, stranica se očisti
(skripte, skriveni tekst, komentari), skrati na 20.000 znakova i označi kao nepouzdana.
"""

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment

MAX_PAGE_CHARS = 20_000
_DROP_TAGS = ["script", "style", "noscript", "template", "iframe", "svg", "object", "embed", "head"]
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d])|opacity\s*:\s*0(?![.\d])"
    r"|text-indent\s*:\s*-\d{3,}",
    re.I,
)
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿\x00-\x08\x0b\x0c\x0e-\x1f]")


class UrlRejected(ValueError):
    pass


def _is_hidden(tag) -> bool:
    attrs = tag.attrs or {}
    if "hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true":
        return True
    if tag.name == "input" and str(attrs.get("type", "")).lower() == "hidden":
        return True
    return bool(_HIDDEN_STYLE.search(str(attrs.get("style", ""))))


def clean_html(html: str, limit: int = MAX_PAGE_CHARS) -> str:
    """HTML -> običan tekst bez skripti, skrivenog teksta i komentara; skraćeno na `limit`."""
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    for tag in [t for t in soup.find_all(True) if _is_hidden(t)]:
        tag.decompose()
    text = _INVISIBLE.sub("", soup.get_text(" ", strip=True))
    text = re.sub(r"\s+", " ", text).strip()
    if title:
        text = f"{title} | {text}"
    return text[:limit]


def extract_links(html: str, base_url: str) -> list[str]:
    """Linkovi koje stranica sama nudi (agent sme da ih otvori); bez skrivenih elemenata."""
    soup = BeautifulSoup(html, "lxml")
    for tag in [t for t in soup.find_all(True) if _is_hidden(t)]:
        tag.decompose()
    out = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"].strip())
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and parts.netloc:
            out.append(urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, "")))
    return list(dict.fromkeys(out))


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def validate_fetch_url(url: str, seen: set[str], resolve=_public_host) -> str:
    """Proveri URL koji je predložio model. Vrati ga normalizovanog ili baci UrlRejected.

    Dozvoljeno: tačno URL koji je već viđen (oglas, link sa stranice, rezultat pretrage), ili
    putanja bez upita na hostu koji je već viđen. Model ne može da izmisli URL sa query
    parametrima (kanal za curenje podataka), ni da ode na nepoznat host.
    """
    parts = urlsplit(url.strip())
    if parts.scheme != "https" and not (parts.scheme == "http" and url in seen):
        raise UrlRejected("dozvoljen je samo https")
    if parts.username or parts.password or parts.fragment:
        raise UrlRejected("URL ne sme imati kredencijale ni fragment")
    if parts.port not in (None, 80, 443):
        raise UrlRejected("nestandardan port")
    host = (parts.hostname or "").lower()
    if not host or host == "localhost" or host.endswith((".local", ".internal")):
        raise UrlRejected("nedozvoljen host")

    normalized = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))
    seen_hosts = {urlsplit(s).hostname for s in seen}
    if normalized not in seen and url not in seen:
        if host not in seen_hosts:
            raise UrlRejected("host nije viđen u ovom istraživanju")
        if parts.query:
            raise UrlRejected("URL sa upitom koji nije došao sa stranice")
    if not resolve(host):
        raise UrlRejected("host nije javna adresa")
    return normalized


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?\d[\s/().-]?){9,}")


def reject_personal_query(query: str, owner_terms: list[str]) -> str:
    """Upit za pretragu ne sme da sadrži lične podatke (mejl, telefon, ime, adresu)."""
    if _EMAIL.search(query) or _PHONE.search(query):
        raise UrlRejected("upit sadrži mejl ili telefon")
    q = query.lower()
    for term in owner_terms:
        if term.strip() and term.strip().lower() in q:
            raise UrlRejected("upit sadrži lični podatak")
    if len(query) > 200:
        raise UrlRejected("upit je predug")
    return query.strip()
