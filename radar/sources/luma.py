"""Luma's public organizer iCalendar, including reschedules and cancellations."""

import re
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from icalendar import Calendar

from radar.http import PoliteClient
from radar.models import RawItem
from radar.normalize import guess_type
from radar.sources.base import Source

EVENT_URL = re.compile(r"https://(?:luma\.com|lu\.ma)/[\w/-]+")
LOCAL = ZoneInfo("Europe/Belgrade")


class LumaCalendarSource(Source):
    def __init__(self, name: str, client: PoliteClient, url: str):
        self.name = name
        self.client = client
        self.url = url

    def fetch(self, since: datetime) -> list[RawItem]:
        calendar = Calendar.from_ical(self.client.get(self.url).content)
        items = []
        for event in calendar.walk("VEVENT"):
            if not event.get("DTSTART"):
                continue
            start = event.decoded("DTSTART")
            all_day = not isinstance(start, datetime)
            if all_day:
                start = datetime.combine(start, time.min, LOCAL)
            elif start.tzinfo is None:
                start = start.replace(tzinfo=LOCAL)
            if start < since:
                continue
            title = str(event.get("summary", "")).strip()
            body = str(event.get("description", ""))
            location = str(event.get("location", ""))
            match = EVENT_URL.search(str(event.get("url", "")) or body) or EVENT_URL.search(
                location
            )
            if not match or not title:
                continue
            kind = guess_type(title, body)
            items.append(
                RawItem(
                    source=self.name,
                    url=match.group().replace("https://lu.ma/", "https://luma.com/"),
                    title=title,
                    text=body,
                    meta={
                        "event_start": start.astimezone(UTC),
                        "event_all_day": all_day,
                        "cancelled": str(event.get("status", "")).upper() == "CANCELLED",
                        "org": str(calendar.get("X-WR-CALNAME", "")),
                        "location": location if not location.startswith("http") else None,
                        "type": kind if kind != "posao" else "meetup",
                    },
                )
            )
        return items
