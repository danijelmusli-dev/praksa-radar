"""RawItem -> Opportunity: rok, lokacija, tip."""

import re
import unicodedata
from datetime import date

from radar.models import Opportunity, RawItem, canonical_url

MAX_RAW_TEXT = 20_000


def fold(s: str) -> str:
    """Mala slova bez dijakritika (č->c, š->s, đ->dj)."""
    s = s.casefold().replace("đ", "dj")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


# ---- rok ----------------------------------------------------------------------------------
# Datum se uzima samo kad ga ispred ima ključna reč (rok, deadline, do, ...), a numerički
# oblici su stroži da "do 2.5 godine" ili "do 5-6 godina" ne bi postali rok.

_MONTH_STEMS = {
    "januar": 1, "january": 1, "jan": 1,
    "februar": 2, "february": 2, "feb": 2,
    "mart": 3, "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "maj": 5, "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "avgust": 8, "august": 8, "avg": 8, "aug": 8,
    "septembar": 9, "septembr": 9, "september": 9, "sep": 9, "sept": 9,
    "oktobar": 10, "oktobr": 10, "october": 10, "okt": 10, "oct": 10,
    "novembar": 11, "novembr": 11, "november": 11, "nov": 11,
    "decembar": 12, "decembr": 12, "december": 12, "dec": 12,
}  # fmt: skip
_STEMS_BY_LEN = sorted(_MONTH_STEMS, key=len, reverse=True)
_MONTH_RE = "(?:" + "|".join(_STEMS_BY_LEN) + r")(?:a|u|e|i|om)?"
_KEYWORD = (
    r"(?<!\w)(?:rok(?:\s+(?:za\s+)?(?:prijav\w*|konkurs\w*))?|prijav\w*\s+do|konkurs\w*\s+do"
    r"|deadline|apply\s+(?:by|before)|closes?(?:\s+on)?|until|do)"
)
_SEP = r"\s*[:\-]?\s*"
_DOT = re.compile(rf"{_KEYWORD}{_SEP}(\d{{1,2}})\.(\d{{1,2}})\.(\d{{2,4}})?", re.I)
_SLASH = re.compile(rf"{_KEYWORD}{_SEP}(\d{{1,2}})/(\d{{1,2}})/(\d{{2,4}})", re.I)
_DASH = re.compile(rf"{_KEYWORD}{_SEP}(\d{{1,2}})-(\d{{1,2}})-(\d{{4}})", re.I)
_DAY_MONTH = re.compile(
    rf"{_KEYWORD}{_SEP}(\d{{1,2}})(?:st|nd|rd|th)?\.?\s+({_MONTH_RE})\b\.?(?:,?\s+(\d{{4}}))?", re.I
)
_MONTH_DAY = re.compile(
    rf"{_KEYWORD}{_SEP}({_MONTH_RE})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?", re.I
)


def _month_number(word: str) -> int | None:
    w = fold(word)
    return next((_MONTH_STEMS[s] for s in _STEMS_BY_LEN if w.startswith(s)), None)


def _build_date(day: int, month: int, year: int | None, today: date) -> date | None:
    try:
        if year is None:
            d = date(today.year, month, day)
            return d if d >= today else date(today.year + 1, month, day)
        return date(year + 2000 if year < 100 else year, month, day)
    except ValueError:
        return None


def extract_deadline(text: str, today: date | None = None) -> date | None:
    """Prvi važeći datum koji stoji iza ključne reči (rok, deadline, do, ...)."""
    today = today or date.today()
    found: list[tuple[int, date | None]] = []

    def year(g: str | None) -> int | None:
        return int(g) if g else None

    for rx in (_DOT, _SLASH, _DASH):
        for m in rx.finditer(text):
            found.append(
                (m.start(), _build_date(int(m.group(1)), int(m.group(2)), year(m.group(3)), today))
            )
    for m in _DAY_MONTH.finditer(text):
        if mo := _month_number(m.group(2)):
            found.append((m.start(), _build_date(int(m.group(1)), mo, year(m.group(3)), today)))
    for m in _MONTH_DAY.finditer(text):
        if mo := _month_number(m.group(1)):
            found.append((m.start(), _build_date(int(m.group(2)), mo, year(m.group(3)), today)))
    return next((d for _, d in sorted(found, key=lambda t: t[0]) if d), None)


# ---- lokacija -----------------------------------------------------------------------------

_CITIES = {
    "beograd": "Beograd", "belgrade": "Beograd", "novi sad": "Novi Sad", "niš": "Niš",
    "kragujevac": "Kragujevac", "subotica": "Subotica", "čačak": "Čačak", "cacak": "Čačak",
    "pančevo": "Pančevo", "pancevo": "Pančevo", "zrenjanin": "Zrenjanin",
    "kraljevo": "Kraljevo",
}  # fmt: skip
_CITY_RE = re.compile(r"(?<!\w)(" + "|".join(_CITIES) + r")(?!\w)", re.IGNORECASE)
_REMOTE_RE = re.compile(r"(?<!\w)(remote|work from home|online|na daljinu|rad od kuće)(?!\w)", re.I)


def detect_location(meta_location: str | None, title: str, text: str) -> str | None:
    if meta_location and meta_location.strip():
        return meta_location.strip()
    haystack = f"{title}\n{text[:1500]}"
    if m := _CITY_RE.search(haystack):
        return _CITIES[m.group(1).lower()]
    if _REMOTE_RE.search(haystack):
        return "remote"
    return None


# ---- tip ----------------------------------------------------------------------------------

_TYPE_RULES = [
    ("praksa", r"praks|intern(?:ship)?\b|trainee|pripravni|akademij|academy"),
    ("takmičenje", r"takmi[cč]enj|hackathon|hakaton|competition|contest"),
    ("radionica", r"radionic|workshop|bootcamp|obuk"),
    ("projekat", r"projekat|projekt|project"),
]


def guess_type(title: str, text: str) -> str:
    """Naslov ima prednost; tekst se gleda samo ako naslov ništa ne pogodi."""
    for haystack in (title, text[:1500]):
        h = fold(haystack)
        for type_, pattern in _TYPE_RULES:
            if re.search(pattern, h):
                return type_
    return "posao"


# ---- glavna funkcija ----------------------------------------------------------------------


def to_opportunity(raw: RawItem, today: date | None = None) -> Opportunity:
    text = raw.text[:MAX_RAW_TEXT]
    title = " ".join(raw.title.split())
    return Opportunity(
        url=canonical_url(raw.url),
        title=title,
        org=(raw.meta.get("org") or None),
        location=detect_location(raw.meta.get("location"), title, text),
        deadline=extract_deadline(f"{title}\n{text}", today),
        raw_text=text,
        source=raw.source,
        type=guess_type(title, text),
    )
