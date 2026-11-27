from datetime import UTC, datetime, timedelta

import httpx

from radar.dedup import upsert_opportunities
from radar.normalize import to_opportunity
from radar.pipeline import send_digest
from radar.sources.gmail_label import parse_message
from radar.sources.luma import LumaCalendarSource
from tests.helpers import gmail_message


def test_google_alerts_return_distinct_publisher_urls():
    msg = gmail_message(
        "g1",
        "Google Alerts <googlealerts-noreply@google.com>",
        "Alert",
        """
        <td><a href="https://www.google.com/url?url=https://jobs.rs/a">Intern</a></td>
        <td><a href="https://www.google.com/url?url=https://jobs.rs/b">Meetup</a></td>
        <a href="https://www.google.com/alerts/remove">Unsubscribe</a>""",
    )
    assert [i.url for i in parse_message(msg, "gmail")] == [
        "https://jobs.rs/a",
        "https://jobs.rs/b",
    ]


def calendar_item(start, status):
    feed = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:Radar test
BEGIN:VEVENT
UID:1
DTSTAMP:20261126T080000Z
DTSTART:{start}
SUMMARY:Engineering conference
DESCRIPTION:Get information at: https://luma.com/sample
STATUS:{status}
END:VEVENT
END:VCALENDAR"""
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=feed))) as c:
        source = LumaCalendarSource("test-calendar", c, "https://example.org/calendar")
        return to_opportunity(source.fetch(datetime(2026, 11, 26, tzinfo=UTC))[0])


def test_calendar_deadline_is_independent():
    item = calendar_item("20261203T140000Z", "CONFIRMED")
    assert item.type == "konferencija" and item.deadline is None
    assert item.event_start == datetime(2026, 12, 3, 14, tzinfo=UTC)


def test_reschedule_and_cancel_each_notify_once(fresh_conn):
    c = fresh_conn
    seen = []

    def send(message, **kwargs):
        seen.append(message)

    today = datetime(2026, 11, 27, tzinfo=UTC)
    item = calendar_item("20261203T140000Z", "CONFIRMED")
    [(oid, _)] = upsert_opportunities(c, [item])
    c.execute(
        "insert into scores (opportunity_id,relevance,reason,passed) values (%s,9,'ok',true)",
        (oid,),
    )
    send_digest(c, "a", today, send, [])
    for status in ["CONFIRMED", "CANCELLED"]:
        changed = calendar_item("20261204T150000Z", status)
        for _ in range(2):
            upsert_opportunities(c, [changed])
            send_digest(c, "b", today + timedelta(days=1), send, [])
    assert len(seen) == 3
    assert "16:00 CET" in seen[1] and "Otkazano" in seen[2]
