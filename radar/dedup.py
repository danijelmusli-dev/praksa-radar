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
                " raw_text) values (%s,%s,%s,%s,%s,%s,%s,%s)"
                " on conflict (url) do nothing returning id",
                (o.url, o.source, o.title, o.org, o.location, o.type, o.deadline, o.raw_text),
            )
            row = cur.fetchone()
            if row:
                new.append((row[0], o))
    conn.commit()
    return new
