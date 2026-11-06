from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from radar.http import PoliteClient
from radar.models import RawItem
from radar.sources.base import Source, run_source
from radar.sources.gmail_label import (
    GmailAuthError,
    GmailLabelSource,
    parse_linkedin_alert,
    parse_message,
)
from radar.sources.helloworld import HelloWorldSource, has_no_results, parse_helloworld
from radar.sources.html_generic import parse_html_generic
from tests.helpers import (
    LINKEDIN_ALERT_HTML,
    NEWSLETTER_HTML,
    gmail_message,
)

FIXTURES = Path(__file__).parent / "fixtures"
SINCE = datetime(2026, 11, 1, tzinfo=UTC)


# ---- HelloWorld ---------------------------------------------------------------------------


def test_helloworld_parses_cards_from_fixture():
    items = parse_helloworld((FIXTURES / "helloworld_list.html").read_text(encoding="utf-8"))
    assert len(items) == 7
    first = items[0]
    assert first.url.startswith("https://www.helloworld.rs/posao/")
    assert "?" not in first.url  # tracking upit uklonjen
    assert first.title and first.meta["org"]
    assert "Rok:" in first.text
    assert all(i.source == "helloworld" for i in items)
    assert len({i.url for i in items}) == 7
    exact = items[-1]  # kartica sa tačnog pogotka: firma je goli <h4><a>
    assert exact.title == "Trainee Functional Consultant MES"
    assert exact.meta == {"org": "Enabl3", "location": "Beograd | Hibrid"}
    assert "Rok: 19.11.2026." in exact.text


def test_helloworld_detects_fallback_page():
    assert has_no_results("<p>Trenutno nema oglasa po traženim kriterijumima pretrage</p>")
    assert not has_no_results((FIXTURES / "helloworld_list.html").read_text(encoding="utf-8"))


@respx.mock
def test_helloworld_skips_queries_without_exact_results():
    page = (FIXTURES / "helloworld_list.html").read_text(encoding="utf-8")
    respx.get("https://www.helloworld.rs/robots.txt").respond(404)
    respx.get("https://www.helloworld.rs/oglasi-za-posao/", params={"q": "praksa"}).respond(
        200, text="nema oglasa po traženim kriterijumima pretrage"
    )
    respx.get("https://www.helloworld.rs/oglasi-za-posao/", params={"q": "trainee"}).respond(
        200, text=page
    )
    client = PoliteClient(sleep=lambda s: None)
    items = HelloWorldSource(client, ["praksa", "trainee"]).fetch(SINCE)
    assert len(items) == 7


# ---- html_generic -------------------------------------------------------------------------

CAREER_HTML = """
<div class="job"><h3>Letnja praksa</h3><a href="/jobs/1">Detalji</a>
  <span class="co">Firma</span><span class="city">Niš</span><p class="d">Python, rok 1.12.</p></div>
<div class="job"><h3>Bez linka</h3></div>
<a class="job" href="https://other.example/2"><h3>Self link</h3></a>
"""


def test_html_generic_parse():
    items = parse_html_generic(
        CAREER_HTML,
        "https://firma.example/karijera",
        "firma",
        item=".job",
        title="h3",
        link="a",
        text=".d",
        org=".co",
        location=".city",
    )
    assert [i.title for i in items] == ["Letnja praksa", "Self link"]
    assert items[0].url == "https://firma.example/jobs/1"
    assert items[0].text == "Python, rok 1.12."
    assert items[0].meta == {"org": "Firma", "location": "Niš"}
    assert items[1].url == "https://other.example/2"


# ---- Gmail --------------------------------------------------------------------------------


def test_linkedin_alert_parser():
    items = parse_linkedin_alert(LINKEDIN_ALERT_HTML, "gmail")
    assert [i.url for i in items] == [
        "https://www.linkedin.com/jobs/view/3901234567",
        "https://www.linkedin.com/jobs/view/3907654321",
    ]  # duplikat uklonjen, kanonski URL
    assert items[0].title == "ML Intern"
    assert items[0].meta == {"org": "Firma X", "location": "Beograd, Serbia"}
    assert items[1].meta == {"org": "Firma Y", "location": "Novi Sad, Serbia"}


def test_parse_message_linkedin_vs_newsletter():
    li = gmail_message(
        "m1", "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "alert", LINKEDIN_ALERT_HTML
    )
    assert len(parse_message(li, "gmail")) == 2

    nl = gmail_message("m2", "Organizacija Y <info@orgy.rs>", "Embedded Weekend", NEWSLETTER_HTML)
    [item] = parse_message(nl, "gmail")
    assert item.title == "Embedded Weekend"
    assert item.url == "https://mail.google.com/mail/u/0/#all/m2"
    assert item.meta["org"] == "Organizacija Y"
    assert "Prijave do 15.11.2026" in item.text


def test_parse_message_plain_text_only():
    msg = gmail_message("m3", "a@b.rs", "Samo tekst", None, plain="Zdravo, rok 20.11.")
    [item] = parse_message(msg, "gmail")
    assert item.text == "Zdravo, rok 20.11."


def make_gmail():
    return GmailLabelSource("cid", "secret", "refresh", http=httpx.Client())


@respx.mock
def test_gmail_fetch_end_to_end():
    respx.post("https://oauth2.googleapis.com/token").respond(200, json={"access_token": "tok"})
    listing = respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages").respond(
        200, json={"messages": [{"id": "m1"}, {"id": "m2"}]}
    )
    respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages/m1").respond(
        200,
        json=gmail_message("m1", "x@linkedin.com", "alert", LINKEDIN_ALERT_HTML),
    )
    respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages/m2").respond(
        200, json=gmail_message("m2", "Org <o@o.rs>", "Newsletter", NEWSLETTER_HTML)
    )
    items = make_gmail().fetch(SINCE)
    assert len(items) == 3
    q = listing.calls[0].request.url.params["q"]
    assert q == f"label:radar after:{int(SINCE.timestamp())}"
    assert listing.calls[0].request.headers["authorization"] == "Bearer tok"


@respx.mock
def test_gmail_expired_refresh_token_raises_auth_error():
    respx.post("https://oauth2.googleapis.com/token").respond(400, json={"error": "invalid_grant"})
    with pytest.raises(GmailAuthError, match="gmail_auth.py"):
        make_gmail().fetch(SINCE)


# ---- run_source ---------------------------------------------------------------------------


class FakeConn:
    def __init__(self):
        self.rows = []

    def execute(self, sql, params):
        self.rows.append(params)

    def commit(self):
        pass


class Boom(Source):
    name = "boom"

    def fetch(self, since):
        raise RuntimeError("sajt promenjen")


class Fine(Source):
    name = "fine"

    def fetch(self, since):
        return [RawItem("fine", "https://x.rs/1", "t")]


def test_run_source_never_raises_and_records():
    conn = FakeConn()
    bad = run_source(Boom(), SINCE, conn)
    good = run_source(Fine(), SINCE, conn)
    assert bad.items == [] and "RuntimeError: sajt promenjen" in bad.error
    assert len(good.items) == 1 and good.error is None
    assert conn.rows == [("boom", 0, bad.error), ("fine", 1, None)]
