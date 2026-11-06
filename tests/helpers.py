"""Pomoćne funkcije za testove (Gmail poruke se prave u kodu, bez pravih mejlova)."""

import base64


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def gmail_message(msg_id: str, sender: str, subject: str, html: str | None, plain: str = ""):
    parts = []
    if plain:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(plain)}})
    if html:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    return {
        "id": msg_id,
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ],
            "parts": parts,
        },
    }


LINKEDIN_ALERT_HTML = """
<html><body><table>
 <tr><td>
   <a href="https://www.linkedin.com/comm/jobs/view/3901234567?trackingId=abc&refId=x">ML Intern</a>
   <p>Firma X</p><p>Beograd, Serbia</p>
 </td></tr>
 <tr><td>
   <a href="https://www.linkedin.com/comm/jobs/view/3907654321?trackingId=def">Embedded Trainee</a>
   <p>Firma Y · Novi Sad, Serbia</p>
 </td></tr>
 <tr><td>
   <a href="https://www.linkedin.com/comm/jobs/view/3901234567?trackingId=dup">ML Intern</a>
 </td></tr>
</table></body></html>
"""

NEWSLETTER_HTML = (
    "<html><body><h1>Embedded Weekend</h1>"
    "<p>Radionica 22-23.11. u Novom Sadu. Prijave do 15.11.2026.</p></body></html>"
)


# ---- lažni LLM i embedder za testove ranga ------------------------------------------------

import re  # noqa: E402
import zlib  # noqa: E402


class FakeLLM:
    """responder(user_prompt) -> dict (odgovor) ili Exception (baca se)."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, *, system, user, schema, model=None):
        self.calls.append((system, user))
        result = self.responder(user)
        if isinstance(result, Exception):
            raise result
        return schema(**result)


class HashEmbedder:
    """Deterministički bag-of-words embedding (1024 dim), dovoljan da sličnost ima smisla."""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.calls = 0

    def embed(self, texts, input_type):
        self.calls += 1
        if self.fail:
            raise self.fail
        out = []
        for t in texts:
            v = [0.0] * 1024
            for w in re.findall(r"\w+", t.lower()):
                v[zlib.crc32(w.encode()) % 1024] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out


# ---- lažni Anthropic klijent (tool use) i LLM po šemi ---------------------------------------

from types import SimpleNamespace  # noqa: E402


def _usage(i, o):
    return SimpleNamespace(input_tokens=i, output_tokens=o)


def text_resp(text, tokens=(100, 50)):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=_usage(*tokens),
    )


def tool_resp(name, args=None, id="t1", tokens=(100, 50)):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id=id, name=name, input=args or {})],
        stop_reason="tool_use",
        usage=_usage(*tokens),
    )


class ScriptedAnthropic:
    """Zamena za anthropic.Anthropic: messages.create vraća unapred pripremljene odgovore."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        # snimak poruka u trenutku poziva (lista se kasnije menja)
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def observations(self, call_index):
        """Tekst svih tool_result blokova poslatih modelu u pozivu `call_index`."""
        out = []
        for m in self.calls[call_index]["messages"]:
            if m["role"] == "user" and isinstance(m["content"], list):
                out += [b["content"] for b in m["content"] if b.get("type") == "tool_result"]
        return "\n".join(out)


class SchemaLLM:
    """LLM koji odgovara po imenu šeme: {"Analysis": {...}, "LetterOut": fn|dict|Exception}."""

    def __init__(self, by_schema):
        self.by = by_schema
        self.calls: list[tuple[str, str, str, str | None]] = []

    def complete_json(self, *, system, user, schema, model=None):
        self.calls.append((schema.__name__, system, user, model))
        r = self.by[schema.__name__]
        r = r(user) if callable(r) else r
        if isinstance(r, Exception):
            raise r
        return schema(**r)
