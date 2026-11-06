import os

import pytest

from radar.db import connect, migrate

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL nije postavljen"
)


@pytest.fixture
def conn():
    with connect(os.environ["TEST_DATABASE_URL"]) as c:
        c.execute("drop schema public cascade")
        c.execute("create schema public")
        c.commit()
        yield c


def test_migrate_creates_tables_and_is_idempotent(conn):
    first = migrate(conn)
    assert "0001_init.sql" in first
    assert migrate(conn) == []

    tables = {
        r[0]
        for r in conn.execute(
            "select table_name from information_schema.tables where table_schema = 'public'"
        )
    }
    assert {"opportunities", "scores", "source_runs", "kv_state"} <= tables
