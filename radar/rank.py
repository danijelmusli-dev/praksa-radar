"""Rang za Fazu 1: ključne reči (bez LLM-a). U Fazi 2 dolazi embedding + LLM rerank.

Ključne reči se porede kao cele reči (bez dijakritika, velika/mala slova nebitna), pa za
"praksa" i "prakse" treba navesti oba oblika.
"""

import re
from dataclasses import dataclass
from datetime import date

from psycopg import Connection
from pydantic import BaseModel

from radar.db import kv_get
from radar.models import Opportunity
from radar.normalize import fold

FILTER_KEY = "mvp_filter"
BASE_SCORE = 5.0
TITLE_HIT = 2.0
TEXT_HIT = 1.0
EXCLUDE_TEXT_PENALTY = 1.5


class KeywordFilter(BaseModel):
    include: list[str] = []
    exclude: list[str] = []
    locations: list[str] = []  # prazno = svuda; stavke bez poznate lokacije prolaze
    types: list[str] = []  # prazno = svi tipovi
    threshold: float = 7.0


@dataclass
class KeywordResult:
    relevance: float
    reason: str
    passed: bool


def _hits(keywords: list[str], haystack: str) -> list[str]:
    found = []
    for kw in keywords:
        k = fold(kw).strip()
        if k and re.search(rf"(?<!\w){re.escape(k)}(?!\w)", haystack):
            found.append(kw)
    return found


def hard_reject(
    opp: Opportunity, filt: KeywordFilter, today: date, strict_type: bool = True
) -> str | None:
    """Tvrdi filteri bez troška. Vrati razlog odbijanja ili None.

    strict_type=False: tip pogođen po ključnim rečima je grub ("posao" je samo podrazumevana
    vrednost), pa se odbija samo kad je tip izričito neki drugi poznati; LLM ga ispravlja.
    """
    if opp.deadline and opp.deadline < today:
        return "rok je prošao"
    if filt.types and opp.type not in filt.types and (strict_type or opp.type != "posao"):
        return f"tip '{opp.type}' nije traženi"
    if filt.locations and opp.location:
        loc = fold(opp.location)
        if not any(fold(wanted) in loc for wanted in filt.locations):
            return f"lokacija '{opp.location}' nije tražena"
    if bad := _hits(filt.exclude, fold(opp.title)):
        return f"isključeno: {', '.join(bad)}"
    return None


def keyword_score(
    opp: Opportunity, filt: KeywordFilter, today: date | None = None
) -> KeywordResult:
    today = today or date.today()
    if reason := hard_reject(opp, filt, today):
        return KeywordResult(0, reason, False)

    title, text = fold(opp.title), fold(opp.raw_text)
    title_hits = _hits(filt.include, title)
    text_hits = [h for h in _hits(filt.include, text) if h not in title_hits]
    if not title_hits and not text_hits:
        return KeywordResult(0, "nema pogodaka", False)

    penalty_hits = _hits(filt.exclude, text)
    score = BASE_SCORE + TITLE_HIT * len(title_hits) + TEXT_HIT * len(text_hits)
    score = max(0.0, min(10.0, score - EXCLUDE_TEXT_PENALTY * len(penalty_hits)))
    reason = "pogodak: " + ", ".join(title_hits + text_hits)
    if penalty_hits:
        reason += f" (umanjeno: {', '.join(penalty_hits)})"
    return KeywordResult(score, reason, score >= filt.threshold)


def get_filter(conn: Connection) -> KeywordFilter:
    value = kv_get(conn, FILTER_KEY)
    return KeywordFilter(**value) if value else KeywordFilter()


def fetch_unscored(conn: Connection) -> list[tuple[int, Opportunity]]:
    rows = conn.execute(
        "select o.id, o.url, o.title, o.org, o.location, o.deadline, o.raw_text, o.source,"
        " o.type from opportunities o left join scores s on s.opportunity_id = o.id"
        " where s.opportunity_id is null order by o.id"
    ).fetchall()
    return [(r[0], Opportunity(r[1], r[2], r[3], r[4], r[5], r[6] or "", r[7], r[8])) for r in rows]


def rank_new(
    conn: Connection,
    run_id: str,
    today: date | None = None,
    filt: KeywordFilter | None = None,
    profile_version: int | None = None,
) -> int:
    """Oceni sve stavke bez reda u `scores` po ključnim rečima. Ponovljiv (checkpoint)."""
    filt = filt or get_filter(conn)
    rows = fetch_unscored(conn)
    with conn.cursor() as cur:
        for id_, opp in rows:
            r = keyword_score(opp, filt, today)
            cur.execute(
                "insert into scores (opportunity_id, profile_version, relevance, reason, passed,"
                " run_id) values (%s,%s,%s,%s,%s,%s) on conflict do nothing",
                (id_, profile_version, r.relevance, r.reason, r.passed, run_id),
            )
    conn.commit()
    return len(rows)
