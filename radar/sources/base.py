import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from psycopg import Connection

from radar.models import RawItem

log = logging.getLogger(__name__)


class Source(ABC):
    name: str
    empty_is_failure: bool = False  # prazna runda je kvar (liste oglasa), ne normalno stanje
    min_expected: int = 0  # nedeljni očekivani minimum stavki (0 = bez provere)

    @abstractmethod
    def fetch(self, since: datetime) -> list[RawItem]:
        """Vraća sirove stavke. Greške se hvataju u run_source, ne ruše rundu."""


@dataclass
class SourceResult:
    name: str
    items: list[RawItem] = field(default_factory=list)
    error: str | None = None


def run_source(source: Source, since: datetime, conn: Connection) -> SourceResult:
    """Pokreni izvor; nikad ne baca izuzetak. Svaki rezultat ide u source_runs."""
    try:
        items = source.fetch(since)
        result = SourceResult(source.name, items)
    except Exception as exc:  # noqa: BLE001 - kvar jednog izvora ne sme da sruši ostale
        log.exception("izvor %s pao", source.name)
        result = SourceResult(source.name, [], f"{type(exc).__name__}: {exc}"[:500])
    conn.execute(
        "insert into source_runs (source, items_found, error) values (%s, %s, %s)",
        (result.name, len(result.items), result.error),
    )
    conn.commit()
    return result
