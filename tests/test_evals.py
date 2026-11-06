import json
from datetime import date
from pathlib import Path

from evals.run import check, evaluate
from radar.llm import LLMInvalidOutput
from radar.profile import Profile
from tests.helpers import FakeLLM

ROOT = Path(__file__).parent.parent
TODAY = date(2026, 11, 6)
PROFILE = Profile(
    **json.loads((ROOT / "evals" / "profile.example.json").read_text(encoding="utf-8"))
)
EXAMPLES = [
    json.loads(ln)
    for ln in (ROOT / "evals" / "dataset.example.jsonl").read_text(encoding="utf-8").splitlines()
]


def perfect(user):
    if "ML Intern" in user:
        return {"relevance": 9, "type": "praksa", "deadline": "2026-11-20", "reason": "r"}
    if "Embedded Weekend" in user:
        return {"relevance": 8, "type": "radionica", "reason": "r"}
    if "Backend praksa" in user:
        return {"relevance": 8, "type": "praksa", "deadline": "2026-12-01", "reason": "r"}
    if "Frontend Intern" in user:
        return {"relevance": 3, "type": "praksa", "reason": "čist frontend"}
    return {"relevance": 2, "type": "posao", "reason": "r"}


def test_perfect_model_scores_perfectly():
    m = evaluate(EXAMPLES, PROFILE, FakeLLM(perfect), TODAY)
    assert (m["precision"], m["recall"]) == (1.0, 1.0)
    assert m["type_accuracy"] == 1.0 and m["deadline_accuracy"] == 1.0
    assert m["n"] == 5 and m["invalid"] == 0


def test_false_positive_lowers_precision_and_wrong_type_lowers_accuracy():
    def sloppy(user):
        r = perfect(user)
        if "Frontend Intern" in user:  # model ga pogrešno propušta
            return {"relevance": 9, "type": "praksa", "reason": "r"}
        if "Embedded Weekend" in user:
            r["type"] = "praksa"
        return r

    m = evaluate(EXAMPLES, PROFILE, FakeLLM(sloppy), TODAY)
    assert m["fp"] == 1 and m["precision"] < 1.0
    assert m["type_accuracy"] < 1.0


def test_hard_rejects_count_as_negatives_without_llm_call():
    llm = FakeLLM(perfect)
    ex = [
        {"title": "Senior Python dev", "location": "Beograd", "text": "Python", "relevant": False}
    ]
    m = evaluate(ex, PROFILE, llm, TODAY)
    assert llm.calls == [] and m["tn"] == 1


def test_invalid_output_counts_and_is_negative():
    m = evaluate(EXAMPLES[:1], PROFILE, FakeLLM(lambda u: LLMInvalidOutput("x")), TODAY)
    assert m["invalid"] == 1 and m["fn"] == 1


def test_check_thresholds_and_baseline_drop():
    ok = {"precision": 0.80}
    assert check(ok, 0.7, {"precision": 0.82}) == []
    assert check({"precision": 0.65}, 0.7, None) != []
    assert any("5 p.p." in f for f in check({"precision": 0.74}, None, {"precision": 0.82}))
    assert check({"precision": 0.78}, None, {"precision": 0.82}) == []  # pad 4 p.p. je dozvoljen
    assert check({"precision": None}, 0.7, None) != []
