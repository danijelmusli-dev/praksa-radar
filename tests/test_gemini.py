import httpx
import pytest
import respx
from pydantic import BaseModel

from radar.config import Settings
from radar.embed import DIM, EmbedError, GeminiEmbedder
from radar.gemini import GeminiClient, GeminiError
from radar.llm import GeminiLLM, LLMInvalidOutput, LLMUnavailable
from radar.providers import make_agent_client, make_embedder, make_llm

GEN = "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
EMB = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"


def cand(parts, finish="STOP", usage=(10, 5, 0)):
    return {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
        "usageMetadata": {
            "promptTokenCount": usage[0],
            "candidatesTokenCount": usage[1],
            "thoughtsTokenCount": usage[2],
        },
    }


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("radar.http.time.sleep", lambda s: None)


@respx.mock
def test_text_response_maps_to_anthropic_shape():
    route = respx.post(GEN).respond(200, json=cand([{"text": "zdravo"}], usage=(10, 5, 7)))
    c = GeminiClient("k", http=httpx.Client())
    r = c.messages.create(
        model="gemini-x",
        max_tokens=100,
        system=[{"type": "text", "text": "sis", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "hej"}],
    )
    assert [b.text for b in r.content] == ["zdravo"]
    assert r.stop_reason == "end_turn"
    assert (r.usage.input_tokens, r.usage.output_tokens) == (10, 12)  # razmišljanje se računa
    sent = route.calls[0].request
    assert sent.headers["x-goog-api-key"] == "k"
    body = __import__("json").loads(sent.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "sis"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hej"}]}]


@respx.mock
def test_tool_use_roundtrip_keeps_signature_and_names():
    import json

    tool = {"name": "fetch_page", "description": "d", "input_schema": {"type": "object"}}
    route = respx.post(GEN).mock(
        side_effect=[
            httpx.Response(
                200,
                json=cand(
                    [
                        {
                            "functionCall": {"name": "fetch_page", "args": {"url": "u"}},
                            "thoughtSignature": "SIG",
                        }
                    ]
                ),
            ),
            httpx.Response(200, json=cand([{"text": "gotovo"}])),
        ]
    )
    c = GeminiClient("k", http=httpx.Client())
    r1 = c.messages.create(
        model="gemini-x", messages=[{"role": "user", "content": "x"}], tools=[tool]
    )
    use = r1.content[0]
    assert (use.type, use.name, use.input, r1.stop_reason) == (
        "tool_use",
        "fetch_page",
        {"url": "u"},
        "tool_use",
    )
    first = json.loads(route.calls[0].request.content)
    decl = first["tools"][0]["functionDeclarations"][0]
    assert decl["name"] == "fetch_page" and decl["parametersJsonSchema"] == {"type": "object"}

    history = [
        {"role": "user", "content": "x"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": use.id, "name": use.name, "input": use.input}],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": use.id, "content": "<p>", "is_error": False}
            ],
        },
    ]
    r2 = c.messages.create(model="gemini-x", messages=history, tools=[tool])
    assert r2.content[0].text == "gotovo" and r2.stop_reason == "end_turn"
    second = json.loads(route.calls[1].request.content)["contents"]
    assert second[1]["parts"][0]["thoughtSignature"] == "SIG"
    assert second[2]["parts"][0]["functionResponse"] == {
        "name": "fetch_page",
        "response": {"output": "<p>"},
    }


@respx.mock
def test_thought_parts_are_dropped():
    respx.post(GEN).respond(
        200, json=cand([{"text": "misli", "thought": True}, {"text": "odgovor"}])
    )
    r = GeminiClient("k", http=httpx.Client()).messages.create(
        model="gemini-x", messages=[{"role": "user", "content": "x"}]
    )
    assert [b.text for b in r.content] == ["odgovor"]


@respx.mock
def test_errors_become_gemini_error():
    c = GeminiClient("k", http=httpx.Client())
    msgs = [{"role": "user", "content": "x"}]
    respx.post(GEN).respond(400, json={})
    with pytest.raises(GeminiError):
        c.messages.create(model="gemini-x", messages=msgs)
    respx.post(GEN).respond(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(GeminiError, match="SAFETY"):
        c.messages.create(model="gemini-x", messages=msgs)


@respx.mock
def test_429_is_retried():
    route = respx.post(GEN).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=cand([{"text": "ok"}]))]
    )
    r = GeminiClient("k", http=httpx.Client()).messages.create(
        model="gemini-x", messages=[{"role": "user", "content": "x"}]
    )
    assert r.content[0].text == "ok" and route.call_count == 2


class Out(BaseModel):
    n: int


@respx.mock
def test_llm_json_retry_then_ok():
    route = respx.post(GEN).mock(
        side_effect=[
            httpx.Response(200, json=cand([{"text": "nije json"}])),
            httpx.Response(200, json=cand([{"text": '```json\n{"n": 3}\n```'}])),
        ]
    )
    llm = GeminiLLM("k", "gemini-x", client=GeminiClient("k", http=httpx.Client()))
    assert llm.complete_json(system="s", user="u", schema=Out).n == 3
    assert route.call_count == 2


@respx.mock
def test_llm_invalid_twice_and_unavailable():
    respx.post(GEN).respond(200, json=cand([{"text": "bla"}]))
    llm = GeminiLLM("k", "gemini-x", client=GeminiClient("k", http=httpx.Client()))
    with pytest.raises(LLMInvalidOutput):
        llm.complete_json(system="s", user="u", schema=Out)
    respx.post(GEN).respond(403, json={})
    with pytest.raises(LLMUnavailable):
        llm.complete_json(system="s", user="u", schema=Out)


# ---- embeddingi ----------------------------------------------------------------------------


def emb_response(n):
    return {"embeddings": [{"values": [3.0] + [0.0] * (DIM - 1)} for _ in range(n)]}


@respx.mock
def test_embedder_dim_task_type_and_normalization():
    import json

    route = respx.post(EMB).respond(200, json=emb_response(2))
    out = GeminiEmbedder("k", client=httpx.Client()).embed(["a", "b"], "query")
    assert len(out) == 2 and len(out[0]) == DIM and out[0][0] == pytest.approx(1.0)
    reqs = json.loads(route.calls[0].request.content)["requests"]
    assert reqs[0]["taskType"] == "RETRIEVAL_QUERY" and reqs[0]["outputDimensionality"] == DIM
    assert reqs[0]["model"] == "models/gemini-embedding-001"


@respx.mock
def test_embedder_batches_and_rejects_bad_dim():
    route = respx.post(EMB).mock(
        side_effect=lambda req: httpx.Response(
            200, json=emb_response(len(__import__("json").loads(req.content)["requests"]))
        )
    )
    assert len(GeminiEmbedder("k", client=httpx.Client()).embed(["t"] * 120, "document")) == 120
    assert route.call_count == 3
    respx.post(EMB).respond(200, json={"embeddings": [{"values": [1.0, 2.0]}]})
    with pytest.raises(EmbedError):
        GeminiEmbedder("k", client=httpx.Client()).embed(["a"], "document")


# ---- izbor provajdera ------------------------------------------------------------------------


def settings(**kw):
    return Settings(_env_file=None, **kw)


def test_gemini_key_selects_gemini_defaults():
    s = settings(gemini_api_key="g")
    assert s.provider == "gemini" and s.has_llm and s.has_embedder
    assert s.rerank_model.startswith("gemini") and s.draft_model.startswith("gemini")
    assert s.embed_model == "gemini-embedding-001"
    assert isinstance(make_llm(s, s.rerank_model), GeminiLLM)
    assert isinstance(make_agent_client(s), GeminiClient)
    assert isinstance(make_embedder(s), GeminiEmbedder)


def test_anthropic_voyage_defaults_and_explicit_override():
    s = settings(anthropic_api_key="a", voyage_api_key="v")
    assert s.provider == "anthropic" and s.rerank_model == "claude-haiku-5-5"
    assert s.embed_provider == "voyage" and s.embed_model == "voyage-3.5-lite"
    both = settings(anthropic_api_key="a", gemini_api_key="g", llm_provider="anthropic")
    assert both.provider == "anthropic" and both.llm_key == "a"


def test_no_keys_means_no_clients():
    s = settings()
    assert not s.has_llm and make_llm(s, "m") is None and make_agent_client(s) is None
    assert make_embedder(s) is None
