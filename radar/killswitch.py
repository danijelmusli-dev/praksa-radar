"""Kill switch za agente: /stop piše agents_enabled=false u kv_state; rezervno repo varijabla."""

from psycopg import Connection

from radar.config import Settings
from radar.db import kv_get, kv_set


def agents_enabled(conn: Connection, settings: Settings) -> bool:
    """Proverava se pre svakog koraka agenta, ne samo na početku."""
    if kv_get(conn, "agents_enabled") is False:
        return False
    return settings.agents_enabled.strip().lower() != "false"


def set_agents_enabled(conn: Connection, on: bool) -> None:
    kv_set(conn, "agents_enabled", on)
