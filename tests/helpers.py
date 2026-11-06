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
