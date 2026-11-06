from types import SimpleNamespace

import anthropic
import httpx
import pytest
import respx
from pydantic import BaseModel

from radar.embed import DIM, EmbedError, VoyageEmbedder, vec_literal
from radar.llm import AnthropicLLM, LLMInvalidOutput, LLMUnavailable, extract_json


class Out(BaseModel):
    a: int


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)])


def llm_with(*replies):
    messages = FakeMessages(replies)
    llm = AnthropicLLM("k", "model-x", client=SimpleNamespace(messages=messages))
    return llm, messages


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('Evo: {"a": 1} Hvala') == '{"a": 1}'
    with pytest.raises(ValueError):
        extract_json("nema json-a")


def test_complete_json_ok_and_sends_cached_system_prompt():
    llm, m = llm_with('{"a": 5}')
    assert llm.complete_json(system="S", user="U", schema=Out) == Out(a=5)
    call = m.calls[0]
    assert call["model"] == "model-x"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["messages"] == [{"role": "user", "content": "U"}]


def test_model_override():
    llm, m = llm_with('{"a": 1}')
    llm.complete_json(system="S", user="U", schema=Out, model="drugi")
    assert m.calls[0]["model"] == "drugi"


def test_invalid_json_retries_once_with_error_message():
    llm, m = llm_with("ovo nije json", '{"a": 2}')
    assert llm.complete_json(system="S", user="U", schema=Out).a == 2
    retry = m.calls[1]["messages"]
    assert retry[1] == {"role": "assistant", "content": "ovo nije json"}
    assert "nije validan" in retry[2]["content"]


def test_schema_violation_twice_raises_invalid_output():
    llm, m = llm_with('{"a": "x"}', '{"a": "y"}')
    with pytest.raises(LLMInvalidOutput):
        llm.complete_json(system="S", user="U", schema=Out)
    assert len(m.calls) == 2


def test_api_error_becomes_unavailable():
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    llm, _ = llm_with(err)
    with pytest.raises(LLMUnavailable):
        llm.complete_json(system="S", user="U", schema=Out)


# ---- Voyage -------------------------------------------------------------------------------


def vectors(n):
    return {"data": [{"index": i, "embedding": [0.1] * DIM} for i in reversed(range(n))]}


@respx.mock
def test_voyage_embeds_in_order_and_sends_params():
    route = respx.post("https://api.voyageai.com/v1/embeddings").respond(200, json=vectors(2))
    out = VoyageEmbedder("key", client=httpx.Client()).embed(["a", "b"], "document")
    assert len(out) == 2 and len(out[0]) == DIM
    body = route.calls[0].request.read().decode()
    assert '"input_type":"document"' in body.replace(" ", "")
    assert '"output_dimension":1024' in body.replace(" ", "")
    assert route.calls[0].request.headers["authorization"] == "Bearer key"


@respx.mock
def test_voyage_batches_over_64():
    route = respx.post("https://api.voyageai.com/v1/embeddings").mock(
        side_effect=lambda req: httpx.Response(
            200, json=vectors(len(__import__("json").loads(req.content)["input"]))
        )
    )
    out = VoyageEmbedder("key", client=httpx.Client()).embed(["t"] * 70, "query")
    assert len(out) == 70 and route.call_count == 2


@respx.mock
def test_voyage_failure_and_bad_dimension_raise_embed_error(monkeypatch):
    monkeypatch.setattr("radar.http.time.sleep", lambda s: None)
    respx.post("https://api.voyageai.com/v1/embeddings").respond(500)
    with pytest.raises(EmbedError):
        VoyageEmbedder("key", client=httpx.Client()).embed(["a"], "query")

    respx.post("https://api.voyageai.com/v1/embeddings").respond(
        200, json={"data": [{"index": 0, "embedding": [0.1] * 3}]}
    )
    with pytest.raises(EmbedError, match="dimenziju"):
        VoyageEmbedder("key", client=httpx.Client()).embed(["a"], "query")


def test_vec_literal():
    assert vec_literal([0.5, 1.0, -2.25]) == "[0.5,1,-2.25]"
