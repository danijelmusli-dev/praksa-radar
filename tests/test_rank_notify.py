from datetime import UTC, date, datetime

import pytest

from radar.models import Opportunity
from radar.notify import MAX_LEN, DigestItem, build_digest
from radar.rank import KeywordFilter, keyword_score

TODAY = date(2026, 11, 6)
FILT = KeywordFilter(
    include=["python", "ML", "embedded"],
    exclude=["senior", "sales"],
    locations=["Beograd", "Novi Sad", "remote"],
    types=["praksa", "radionica"],
)


def opp(**kw):
    base = dict(
        url="https://x.rs/1",
        title="ML Intern",
        org="Firma",
        location="Beograd",
        deadline=date(2026, 11, 20),
        raw_text="Traže Python i osnove ML-a",
        source="helloworld",
        type="praksa",
    )
    base.update(kw)
    return Opportunity(**base)


def test_title_and_text_hits_score_and_pass():
    r = keyword_score(opp(), FILT, TODAY)
    assert r.passed and r.relevance == 5 + 2 + 1  # ML u naslovu, python u tekstu
    assert r.reason == "pogodak: ML, python"


def test_single_text_hit_is_below_default_threshold():
    r = keyword_score(opp(title="Intern", raw_text="Python"), FILT, TODAY)
    assert r.relevance == 6 and not r.passed


@pytest.mark.parametrize(
    "change,reason",
    [
        (dict(deadline=date(2026, 11, 1)), "rok je prošao"),
        (dict(type="posao"), "tip"),
        (dict(location="Niš"), "lokacija"),
        (dict(title="Senior ML Engineer"), "isključeno"),
        (dict(title="Intern", raw_text="ništa"), "nema pogodaka"),
    ],
)
def test_hard_filters(change, reason):
    r = keyword_score(opp(**change), FILT, TODAY)
    assert not r.passed and r.relevance == 0 and reason in r.reason


def test_unknown_location_and_deadline_pass():
    assert keyword_score(opp(location=None, deadline=None), FILT, TODAY).passed


def test_text_exclusion_penalizes_but_does_not_block():
    r = keyword_score(opp(raw_text="Python, saradnja sa sales timom"), FILT, TODAY)
    assert r.relevance == 5 + 2 + 1 - 1.5 and "umanjeno: sales" in r.reason


def test_matching_ignores_diacritics_case_and_word_parts():
    f = KeywordFilter(include=["čačak"], exclude=[])
    assert (
        keyword_score(opp(title="Praksa CACAK", raw_text="", location=None), f, TODAY).relevance
        == 7
    )
    f2 = KeywordFilter(include=["ml"])
    assert (
        keyword_score(opp(title="HTML mladi", raw_text=""), f2, TODAY).relevance == 0
    )  # cele reči


# ---- digest -------------------------------------------------------------------------------

NOW = datetime(2026, 11, 5, 13, 0, tzinfo=UTC)  # četvrtak, 14:00 u Beogradu


def item(i=1, **kw):
    base = dict(
        id=i,
        relevance=8,
        title="ML Intern",
        org="Firma X",
        location="Beograd",
        deadline=date(2026, 11, 20),
        type="praksa",
        reason="traže Python",
        url=f"https://x.rs/{i}",
    )
    base.update(kw)
    return DigestItem(**base)


def test_empty_digest_sends_nothing():
    assert build_digest([], NOW, ["izvor x nije uspeo"]) == []


def test_digest_format_and_grouping():
    items = [
        item(1),
        item(2, type="radionica", title="Embedded Weekend", org="Org Y", relevance=7,
             location="Novi Sad", deadline=None, reason="Arduino"),
    ]  # fmt: skip
    [msg] = build_digest(items, NOW)
    assert msg.ids == [1, 2]
    assert msg.text.startswith("Praksa Radar · četvrtak 14:00 · 2 nove")
    assert (
        "PRAKSE\n1) [8/10] ML Intern — Firma X · Beograd · rok 20.11.\n"
        "Zašto: traže Python\nhttps://x.rs/1" in msg.text
    )
    assert "RADIONICE\n2) [7/10] Embedded Weekend — Org Y · Novi Sad\n" in msg.text
    assert msg.text.index("PRAKSE") < msg.text.index("RADIONICE")


@pytest.mark.parametrize(
    "n,word", [(1, "nova"), (2, "nove"), (5, "novih"), (11, "novih"), (21, "nova")]
)
def test_digest_plural(n, word):
    [msg, *_] = build_digest([item(i) for i in range(n)], NOW)
    assert f"{n} {word}" in msg.text.splitlines()[0]


def test_digest_splits_over_4096_and_keeps_every_id_once():
    items = [item(i, reason="x" * 300) for i in range(30)]
    msgs = build_digest(items, NOW, warnings=["izvor a nije uspeo"])
    assert len(msgs) > 1 and all(len(m.text) <= MAX_LEN for m in msgs)
    assert sorted(i for m in msgs for i in m.ids) == list(range(30))
    assert msgs[0].text.startswith("Praksa Radar")
    assert "PRAKSE" in msgs[1].text  # naslov grupe se ponavlja u nastavku
    assert "⚠ izvor a nije uspeo" in msgs[-1].text


def test_digest_warning_line():
    [msg] = build_digest([item()], NOW, ["izvor gmail nije uspeo"])
    assert msg.text.endswith("⚠ izvor gmail nije uspeo")


def test_digest_buttons_use_item_numbers_and_ids():
    items = [item(11), item(12, type="radionica", relevance=7)]
    [msg] = build_digest(items, NOW)
    rows = msg.reply_markup["inline_keyboard"]
    assert [r[0]["text"] for r in rows] == ["1 👍", "2 👍"]
    assert [b["callback_data"] for b in rows[0]] == [
        "fb:up:11",
        "fb:down:11",
        "fb:less:11",
        "sv:11",
    ]
    assert rows[1][3]["callback_data"] == "sv:12"
    assert all(len(b["callback_data"].encode()) <= 64 for r in rows for b in r)


def test_split_messages_keep_global_numbering_and_own_buttons():
    msgs = build_digest([item(i, reason="x" * 300) for i in range(30)], NOW)
    nums = [int(r[0]["text"].split()[0]) for m in msgs for r in m.reply_markup["inline_keyboard"]]
    assert nums == list(range(1, 31))
    assert [len(m.reply_markup["inline_keyboard"]) for m in msgs] == [len(m.ids) for m in msgs]
