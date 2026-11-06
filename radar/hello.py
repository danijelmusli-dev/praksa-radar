"""Faza 0 smoke test: upiši red u bazu i pošalji 'zdravo' na Telegram."""

import json
from datetime import UTC, datetime

from radar.db import connect
from radar.notify import send_message


def main() -> None:
    now = datetime.now(UTC).isoformat()
    with connect() as conn:
        conn.execute(
            "insert into kv_state (key, value) values ('hello', %s) "
            "on conflict (key) do update set value = excluded.value",
            (json.dumps(now),),
        )
        conn.commit()
    send_message(f"Praksa Radar: zdravo ({now})")


if __name__ == "__main__":
    main()
