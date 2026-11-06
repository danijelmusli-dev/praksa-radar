"""Telegram isporuka i format digesta.

Čist tekst, bez parse_mode (zaštita od ubrizganog formatiranja). Linkovi u porukama dolaze
samo iz kolone `url` u bazi.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from radar.config import get_settings

MAX_LEN = 4096
TZ = ZoneInfo("Europe/Belgrade")
DAYS = ["ponedeljak", "utorak", "sreda", "četvrtak", "petak", "subota", "nedelja"]
GROUPS = [
    ("praksa", "PRAKSE"),
    ("projekat", "PROJEKTI"),
    ("radionica", "RADIONICE"),
    ("takmičenje", "TAKMIČENJA"),
    ("posao", "POSAO"),
]


def send_message(
    text: str, reply_markup: dict | None = None, client: httpx.Client | None = None
) -> None:
    s = get_settings()
    url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": s.telegram_chat_id,
        "text": text[:MAX_LEN],
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
    finally:
        if own:
            client.close()


@dataclass
class DigestItem:
    id: int
    relevance: float
    title: str
    org: str | None
    location: str | None
    deadline: date | None
    type: str | None
    reason: str
    url: str


@dataclass
class Message:
    text: str
    ids: list[int] = field(default_factory=list)
    reply_markup: dict | None = None  # inline dugmići ispod stavki (Worker ih obrađuje)


def item_buttons(n: int, opportunity_id: int) -> list[list[dict]]:
    """Dva reda dugmića za stavku broj n. callback_data (max 64 bajta):
    fb:<up|down|less>:<id>, sv:<id>, an:<id> (analiza), nc:<id> (nacrt)."""
    i = opportunity_id
    return [
        [
            {"text": f"{n} 👍", "callback_data": f"fb:up:{i}"},
            {"text": f"{n} 👎", "callback_data": f"fb:down:{i}"},
            {"text": f"{n} Manje", "callback_data": f"fb:less:{i}"},
        ],
        [
            {"text": f"{n} 💾", "callback_data": f"sv:{i}"},
            {"text": f"{n} Analiza", "callback_data": f"an:{i}"},
            {"text": f"{n} Nacrt", "callback_data": f"nc:{i}"},
        ],
    ]


def _plural_new(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "nova"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "nove"
    return "novih"


def format_item(item: DigestItem, n: int) -> str:
    head = f"{n}) [{round(item.relevance)}/10] {item.title}"
    if item.org:
        head += f" — {item.org}"
    extra = [item.location, f"rok {item.deadline:%d.%m.}" if item.deadline else None]
    extra = [e for e in extra if e]
    if extra:
        head += " · " + " · ".join(extra)
    return f"{head}\nZašto: {item.reason}\n{item.url}"


def build_digest(
    items: list[DigestItem], now: datetime, warnings: list[str] | None = None
) -> list[Message]:
    """Jedna poruka po rundi, grupisano po tipu; deli se tek preko 4096 znakova.

    Prazno za prazan niz stavki: ako nema ničeg iznad praga, ništa se ne šalje.
    """
    if not items:
        return []
    local = now.astimezone(TZ)
    header = (
        f"Praksa Radar · {DAYS[local.weekday()]} {local:%H:%M} · "
        f"{len(items)} {_plural_new(len(items))}"
    )
    known = {key for key, _ in GROUPS}
    sections: list[tuple[str, list[DigestItem]]] = [
        (label, [i for i in items if i.type == key]) for key, label in GROUPS
    ]
    sections.append(("OSTALO", [i for i in items if i.type not in known]))

    messages: list[Message] = []
    text, ids, rows = header, [], []
    number = 0  # redni broj stavke kroz sve poruke; isti broj je na dugmićima

    def flush() -> None:
        nonlocal text, ids, rows
        markup = {"inline_keyboard": rows} if rows else None
        messages.append(Message(text.strip(), ids, markup))
        text, ids, rows = "", [], []

    for label, group in sections:
        group.sort(key=lambda i: -i.relevance)
        first_in_message = True
        for item in group:
            number += 1
            body = format_item(item, number)
            block = ("\n\n" + label if first_in_message else "") + "\n" + body
            if len(text) + len(block) > MAX_LEN and ids:
                flush()
                block = label + "\n" + body
            text += block
            ids.append(item.id)
            rows.extend(item_buttons(number, item.id))
            first_in_message = False

    if warnings:
        warn = "\n\n⚠ " + "; ".join(warnings)
        if len(text) + len(warn) > MAX_LEN:
            flush()
            warn = warn.lstrip()
        text += warn
    flush()
    return messages
