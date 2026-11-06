"""Rang Faze 2 nad pravim Postgresom (TEST_DATABASE_URL), sa lažnim LLM-om i embedderom."""

from datetime import UTC, date, datetime

import pytest

from radar.dedup import upsert_opportunities
from radar.llm import LLMInvalidOutput, LLMUnavailable
from radar.models import Opportunity
from radar.pipeline import run
from radar.profile import Profile, get_current_profile, parse_profile, save_profile
from radar.rerank import rank_run, sanitize_reason
from tests.helpers import FakeLLM, HashEmbedder

TODAY = date(2026, 11, 6)
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)

PROFILE = Profile(
    interesovanja=["python", "ML"],
    iskljuceno=["senior"],
    lokacije=["Beograd", "remote"],
    tipovi=["praksa", "radionica"],
    prag_relevantnosti=7,
)


def opp(n, title, text="", location="Beograd", deadline=None, type_="posao"):
    return Opportunity(f"https://x.rs/{n}", title, "Firma", location, deadline, text, "t", type_)


def answer(relevance, type_="praksa", reason="razlog", deadline=None):
    return {"relevance": relevance, "type": type_, "reason": reason, "deadline": deadline}


@pytest.fixture
def conn(fresh_conn):
    save_profile(fresh_conn, HashEmbedder(), "tekst", PROFILE)
    return fresh_conn


def scores(conn):
    rows = conn.execute(
        "select o.title, s.passed, s.relevance, s.reason, s.similarity, s.profile_version"
        " from scores s join opportunities o on o.id = s.opportunity_id order by o.id"
    ).fetchall()
    return {r[0]: r[1:] for r in rows}


def test_profile_roundtrip(conn):
    stored = get_current_profile(conn)
    assert stored.profile == PROFILE and stored.raw_text == "tekst"
    newer = save_profile(conn, HashEmbedder(), "novi", Profile(interesovanja=["embedded"]))
    assert get_current_profile(conn).id == newer.id > stored.id


def test_parse_profile_uses_llm_schema():
    llm = FakeLLM(lambda user: {"interesovanja": ["AI"], "tipovi": ["praksa"]})
    p = parse_profile(llm, "hoću AI praksu")
    assert p.interesovanja == ["AI"] and p.prag_relevantnosti == 7
    assert llm.calls[0][1] == "hoću AI praksu"


def test_full_ai_pass(conn):
    upsert_opportunities(
        conn,
        [
            opp(1, "Python ML intern", "Python i ML za studente", type_="praksa"),
            opp(2, "Kuvar pomoćnik", "kuhinja"),
            opp(3, "Python intern Niš", "Python", location="Niš", type_="praksa"),
            opp(4, "Python praksa istekla", "Python", deadline=date(2026, 11, 1), type_="praksa"),
            opp(5, "Senior Python", "Python"),
        ],
    )

    def respond(user):
        if "Python ML intern" in user:
            return answer(
                9, reason="Poklapa se. Vidi https://evil.example/x\nnovi red", deadline="2026-11-20"
            )
        return answer(2, "nebitno", "nije prilika")

    llm = FakeLLM(respond)
    summary = rank_run(conn, "r1", TODAY, llm, HashEmbedder())

    s = scores(conn)
    assert s["Python ML intern"][0] is True and s["Python ML intern"][1] == 9
    assert "http" not in s["Python ML intern"][2] and "\n" not in s["Python ML intern"][2]
    assert s["Kuvar pomoćnik"][0] is False
    assert "lokacija" in s["Python intern Niš"][2]
    assert "rok je prošao" in s["Python praksa istekla"][2]
    assert "isključeno" in s["Senior Python"][2]
    assert len(llm.calls) == 2  # samo preživeli tvrde filtere
    assert all(v[4] is not None for v in s.values())  # profile_version upisan
    assert (summary.ranked, summary.reranked, summary.degraded) == (5, 2, False)

    row = conn.execute(
        "select type, deadline from opportunities where title = 'Python ML intern'"
    ).fetchone()
    assert row == ("praksa", date(2026, 11, 20))  # tip i rok ispravljeni iz modela


def test_similarity_orders_candidates_and_top_n_limits_llm_calls(conn):
    upsert_opportunities(
        conn,
        [
            opp(1, "Python ML praksa", "python ML python", type_="praksa"),
            opp(2, "Kuvar", "kuhinja supa"),
        ],
    )
    llm = FakeLLM(lambda user: answer(8))
    rank_run(conn, "r1", TODAY, llm, HashEmbedder(), top_n=1)
    assert len(llm.calls) == 1 and "Python ML praksa" in llm.calls[0][1]
    s = scores(conn)
    assert s["Kuvar"][0] is False and "ispod top 1" in s["Kuvar"][2]


def test_expired_deadline_from_model_blocks_pass(conn):
    upsert_opportunities(conn, [opp(1, "Python praksa", "Python", type_="praksa")])
    rank_run(conn, "r1", TODAY, FakeLLM(lambda u: answer(9, deadline="2026-10-01")), HashEmbedder())
    assert scores(conn)["Python praksa"][0] is False


def test_type_must_be_in_profile_types(conn):
    upsert_opportunities(conn, [opp(1, "Python posao", "Python")])
    rank_run(conn, "r1", TODAY, FakeLLM(lambda u: answer(9, "posao")), HashEmbedder())
    assert scores(conn)["Python posao"][0] is False


def test_llm_unavailable_degrades_to_keywords(conn):
    upsert_opportunities(
        conn,
        [opp(1, "Python ML praksa", "ML", type_="praksa"), opp(2, "Kuvar", "kuhinja")],
    )
    llm = FakeLLM(lambda u: LLMUnavailable("budžet potrošen"))
    summary = rank_run(conn, "r1", TODAY, llm, HashEmbedder())
    assert summary.degraded and "bez AI ranga" in summary.warnings()[0]
    s = scores(conn)
    assert s["Python ML praksa"][0] is True and s["Kuvar"][0] is False  # ključne reči
    assert len(s) == 2


def test_embedder_failure_degrades_too(conn):
    from radar.embed import EmbedError

    upsert_opportunities(conn, [opp(1, "Python praksa", "ML", type_="praksa")])
    summary = rank_run(
        conn, "r1", TODAY, FakeLLM(lambda u: answer(9)), HashEmbedder(EmbedError("x"))
    )
    assert summary.degraded and len(scores(conn)) == 1


def test_missing_llm_with_profile_is_degraded(conn):
    upsert_opportunities(conn, [opp(1, "Python praksa", "ML", type_="praksa")])
    summary = rank_run(conn, "r1", TODAY, None, None)
    assert summary.degraded and scores(conn)["Python praksa"][0] is True


def test_invalid_output_leaves_item_for_next_round(conn):
    upsert_opportunities(
        conn,
        [
            opp(1, "Python praksa", "Python", type_="praksa"),
            opp(2, "ML praksa", "ML", type_="praksa"),
        ],
    )
    llm = FakeLLM(lambda u: LLMInvalidOutput("loš json") if "Python praksa" in u else answer(8))
    summary = rank_run(conn, "r1", TODAY, llm, HashEmbedder())
    assert summary.invalid == 1 and list(scores(conn)) == ["ML praksa"]
    assert any("preskočeno" in w for w in summary.warnings())  # 1/2 > 20%

    again = rank_run(conn, "r2", TODAY, FakeLLM(lambda u: answer(8)), HashEmbedder())
    assert again.reranked == 1 and set(scores(conn)) == {"Python praksa", "ML praksa"}


def test_feedback_examples_go_into_system_prompt(conn):
    upsert_opportunities(
        conn, [opp(1, "Stara ocena", "x"), opp(2, "Python praksa", "Python", type_="praksa")]
    )
    conn.execute(
        "insert into scores (opportunity_id, relevance, reason, passed)"
        " values (1, 8, 'dobar fit', true)"
    )
    conn.execute("insert into feedback (opportunity_id, signal) values (1, 'less_like_this')")
    conn.commit()
    llm = FakeLLM(lambda u: answer(8))
    rank_run(conn, "r1", TODAY, llm, HashEmbedder())
    system = llm.calls[0][0]
    assert "Stara ocena" in system and "MANJE OVAKVIH" in system and "dobar fit" in system


def test_no_profile_falls_back_to_mvp_filter(fresh_conn):
    from radar.db import kv_set
    from radar.rank import FILTER_KEY

    kv_set(fresh_conn, FILTER_KEY, {"include": ["python"], "types": ["praksa"]})
    upsert_opportunities(fresh_conn, [opp(1, "Python praksa", "x", type_="praksa")])
    summary = rank_run(fresh_conn, "r1", TODAY, FakeLLM(lambda u: answer(1)), HashEmbedder())
    assert not summary.degraded and scores(fresh_conn)["Python praksa"][0] is True


def test_digest_is_labeled_when_degraded(conn):
    from radar.models import RawItem
    from radar.sources.base import Source

    class Src(Source):
        name = "s"

        def fetch(self, since):
            return [RawItem("s", "https://x.rs/9", "Python ML praksa", "ML Beograd", {"org": "F"})]

    sent = []
    run(
        conn, [Src()], send=lambda t, reply_markup=None: sent.append(t), now=NOW, run_id="r1"
    )  # bez llm/embeddera
    assert len(sent) == 1 and "⚠ bez AI ranga" in sent[0]


def test_sanitize_reason():
    assert sanitize_reason("Dobro www.zlo.com i http://x.y/z\n  kraj") == "Dobro i kraj"
    assert sanitize_reason("   ") == "bez obrazloženja"
    assert len(sanitize_reason("x" * 1000)) == 300
