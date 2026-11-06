"""Postgres konekcija i migracije. CLI: python -m radar.db migrate"""

import json
import sys
from pathlib import Path

import psycopg

from radar.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or get_settings().database_url, autocommit=False)


def kv_get(conn: psycopg.Connection, key: str):
    row = conn.execute("select value from kv_state where key = %s", (key,)).fetchone()
    return row[0] if row else None


def kv_set(conn: psycopg.Connection, key: str, value) -> None:
    conn.execute(
        "insert into kv_state (key, value) values (%s, %s::jsonb)"
        " on conflict (key) do update set value = excluded.value",
        (key, json.dumps(value, default=str)),
    )
    conn.commit()


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Primeni migracije po redu; vrati imena novo primenjenih."""
    with conn.cursor() as cur:
        cur.execute(
            "create table if not exists schema_migrations ("
            "name text primary key, applied_at timestamptz default now())"
        )
        cur.execute("select name from schema_migrations")
        done = {r[0] for r in cur.fetchall()}
    conn.commit()

    applied = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in done:
            continue
        with conn.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("insert into schema_migrations (name) values (%s)", (path.name,))
        conn.commit()
        applied.append(path.name)
    return applied


def main(argv: list[str]) -> int:
    if argv[:1] != ["migrate"]:
        print("usage: python -m radar.db migrate")
        return 2
    with connect() as conn:
        applied = migrate(conn)
    print(f"applied: {applied or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
