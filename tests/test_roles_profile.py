import psycopg
import pytest

from radar.dedup import upsert_opportunities
from radar.models import Opportunity
from radar.profile import Profile, ensure_profile_embedding, get_current_profile, save_profile
from tests.helpers import HashEmbedder


def opp_id(conn):
    upsert_opportunities(
        conn, [Opportunity("https://x.rs/1", "ML Intern", "F", "Beograd", None, "t", "s", "praksa")]
    )
    return conn.execute("select id from opportunities").fetchone()[0]


def as_role(conn, role):
    conn.rollback()
    conn.execute(f"set local role {role}")


def test_reader_can_select_but_not_write_or_read_kv(fresh_conn):
    opp_id(fresh_conn)
    as_role(fresh_conn, "agent_reader")
    assert fresh_conn.execute("select count(*) from opportunities").fetchone()[0] == 1
    for sql in (
        "insert into applications (opportunity_id, status) values (1, 'sačuvano')",
        "delete from opportunities",
        "update opportunities set title = 'x'",
        "select * from kv_state",
        "insert into pending_changes (kind, payload) values ('profile', '{}')",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            fresh_conn.execute(sql)
        fresh_conn.rollback()
        fresh_conn.execute("set local role agent_reader")


def test_tracker_writes_only_applications_and_reminders_and_never_deletes(fresh_conn):
    oid = opp_id(fresh_conn)
    as_role(fresh_conn, "agent_tracker")
    fresh_conn.execute(
        "insert into applications (opportunity_id, status) values (%s, 'sačuvano')", (oid,)
    )
    fresh_conn.execute(
        "insert into reminders (opportunity_id, due_at, text) values (%s, now(), 'x')", (oid,)
    )
    fresh_conn.execute("update applications set status = 'prijavljen'")
    fresh_conn.commit()
    for sql in (
        "delete from applications",
        "delete from reminders",
        "update opportunities set title = 'x'",
        "insert into feedback (opportunity_id, signal) values (1, 'up')",
        "select * from kv_state",
    ):
        fresh_conn.execute("set local role agent_tracker")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            fresh_conn.execute(sql)
        fresh_conn.rollback()


def test_application_status_is_constrained(fresh_conn):
    oid = opp_id(fresh_conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        fresh_conn.execute(
            "insert into applications (opportunity_id, status) values (%s, 'nepoznat')", (oid,)
        )


def test_profile_without_embedding_is_filled_in_later(fresh_conn):
    p = Profile(interesovanja=["python"])
    fresh_conn.execute(
        "insert into profile_versions (raw_text, structured) values ('w', %s::jsonb)",
        (p.model_dump_json(),),
    )
    fresh_conn.commit()
    stored = get_current_profile(fresh_conn)
    ensure_profile_embedding(fresh_conn, HashEmbedder(), stored)
    assert fresh_conn.execute("select embedding is not null from profile_versions").fetchone()[0]
    emb = HashEmbedder()
    ensure_profile_embedding(fresh_conn, emb, stored)  # već popunjeno: ne zove ponovo
    assert emb.calls == 0
    assert save_profile(fresh_conn, HashEmbedder(), "n", p).id > stored.id
