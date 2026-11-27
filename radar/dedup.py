from psycopg import Connection

from radar.models import Opportunity


def dedupe_batch(items: list[Opportunity]) -> list[Opportunity]:
    """Dedup unutar serije po (već kanonskom) URL-u; prvi pobeđuje."""
    seen: set[str] = set()
    out = []
    for item in items:
        if item.url in seen:
            continue
        seen.add(item.url)
        out.append(item)
    return out


def upsert_opportunities(
    conn: Connection, items: list[Opportunity]
) -> list[tuple[int, Opportunity]]:
    """Upiši nove stavke (url unique). Vraća samo stvarno nove, sa id-jem."""
    new = []
    with conn.cursor() as cur:
        for o in dedupe_batch(items):
            cur.execute(
                "insert into opportunities (url, source, title, org, location, type, deadline,"
                " raw_text,event_start,event_all_day,cancelled)"
                " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " on conflict (url) do nothing returning id",
                (
                    o.url,
                    o.source,
                    o.title,
                    o.org,
                    o.location,
                    o.type,
                    o.deadline,
                    o.raw_text,
                    o.event_start,
                    o.event_all_day,
                    o.cancelled,
                ),
            )
            row = cur.fetchone()
            if row:
                new.append((row[0], o))
            elif o.event_start:
                cur.execute(
                    "update opportunities set event_update_pending=event_update_pending or"
                    " (event_start is not null and (event_start, cancelled, event_all_day)"
                    " is distinct from (%s,%s,%s) and exists (select 1 from scores"
                    " where opportunity_id=opportunities.id and sent_at is not null)),"
                    " event_start=%s, event_all_day=%s, cancelled=%s,"
                    " title=%s,"
                    " location=%s, raw_text=%s where url=%s",
                    (
                        o.event_start,
                        o.cancelled,
                        o.event_all_day,
                        o.event_start,
                        o.event_all_day,
                        o.cancelled,
                        o.title,
                        o.location,
                        o.raw_text,
                        o.url,
                    ),
                )
    conn.commit()
    return new
