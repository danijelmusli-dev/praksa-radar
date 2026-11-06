import os

import pytest

from radar.db import connect, migrate


@pytest.fixture
def fresh_conn():
    """Čista baza sa svim migracijama (preskače se bez TEST_DATABASE_URL)."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL nije postavljen")
    with connect(url) as c:
        c.execute("drop schema public cascade")
        c.execute("create schema public")
        c.commit()
        migrate(c)
        yield c
