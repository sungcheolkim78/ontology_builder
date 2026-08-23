import re
from pathlib import Path

from ladybug import Connection, Database

DB_PATH = Path(__file__).parent.parent / "data" / "graph.ladybugdb"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")

_database = None
_connection = None


def _validate_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"unsafe type name for graph DB identifier: {name!r}")
    return name


def _get_connection() -> Connection:
    global _database, _connection
    if _connection is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _database = Database(str(DB_PATH))
        _connection = Connection(_database)
        _connection.execute(
            "CREATE NODE TABLE IF NOT EXISTS _ExtractedDocument(stem STRING PRIMARY KEY)"
        )
    return _connection


def reset_connection() -> None:
    """Test-only: drop cached connection/database handles so a fresh one
    opens next time. Needed because tests delete DB_PATH on disk between
    runs -- the cached native handles would otherwise point at a
    now-missing directory."""
    global _database, _connection
    if _connection is not None:
        _connection.close()
        _connection = None
    if _database is not None:
        _database.close()
        _database = None


def has_graph(stem: str) -> bool:
    conn = _get_connection()
    result = conn.execute(
        "MATCH (d:_ExtractedDocument {stem: $stem}) RETURN d.stem AS stem", {"stem": stem}
    )
    return len(list(result.rows_as_dict())) > 0
