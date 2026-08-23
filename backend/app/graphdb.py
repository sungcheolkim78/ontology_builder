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


def _validate_identifier_set(names) -> list:
    # Accepts any iterable; returns a list with duplicates removed and
    # first-occurrence order preserved. Order matters here even though the
    # caller only cares about set-like uniqueness: node table creation order
    # (driven by this) determines the order a later `MATCH (n)` scan across
    # heterogeneous node tables returns rows in, and a plain `set` comprehension
    # has hash-randomized iteration order that varies per process -- observed
    # to flip table-creation order (and therefore load_graph's row order)
    # from run to run.
    ordered = list(dict.fromkeys(names))
    for name in ordered:
        _validate_identifier(name)
    return ordered


def _existing_tables(conn) -> dict:
    # Excludes _ExtractedDocument: it's an internal marker table with no
    # source_document property, so treating it as a regular data table in
    # write_graph's per-table DETACH DELETE loop would raise "Cannot find
    # property source_document for n." (verified against a real database --
    # a single-label MATCH errors on a missing property, unlike a generic
    # MATCH (n) across heterogeneous tables, which silently skips it).
    return {
        row["name"]: row["type"]
        for row in conn.execute("CALL show_tables() RETURN *").rows_as_dict()
        if row["name"] != "_ExtractedDocument"
    }


def _existing_pairs(conn, rel_type: str) -> set:
    rows = conn.execute(f'CALL show_connection("{rel_type}") RETURN *').rows_as_dict()
    return {(row["source table name"], row["destination table name"]) for row in rows}


def _node_from_row(row: dict) -> dict:
    node = {"id": row["id"].split("::", 1)[1], "label": row["label"], "type": row["type"]}
    if row.get("detail"):
        node["detail"] = row["detail"]
    return node


def _edge_from_row(row: dict) -> dict:
    edge = {
        "source": row["source"].split("::", 1)[1],
        "target": row["target"].split("::", 1)[1],
        "type": row["type"],
    }
    if row.get("detail"):
        edge["detail"] = row["detail"]
    return edge


def write_graph(stem: str, nodes: list, edges: list) -> None:
    conn = _get_connection()
    nodes_by_id = {n["id"]: n for n in nodes}

    node_types = _validate_identifier_set(n["type"] for n in nodes)
    # dict.fromkeys over a generator of tuples, same rationale as
    # _validate_identifier_set above: a plain `set` comprehension here has
    # hash-randomized iteration order, which would flip REL table creation
    # order (and therefore load_graph's edge row order) across process runs
    # for a document with 2+ distinct edge type/pair combinations. Tuples
    # are hashable, so dict.fromkeys dedupes them directly while preserving
    # first-occurrence order.
    edge_specs = list(
        dict.fromkeys(
            (
                _validate_identifier(e["type"]),
                _validate_identifier(nodes_by_id[e["source"]]["type"]),
                _validate_identifier(nodes_by_id[e["target"]]["type"]),
            )
            for e in edges
        )
    )

    existing = _existing_tables(conn)
    for t in node_types:
        if t not in existing:
            conn.execute(
                f"CREATE NODE TABLE {t}(id STRING PRIMARY KEY, label STRING, "
                f"detail STRING, source_document STRING)"
            )
            existing[t] = "NODE"

    for etype, src, dst in edge_specs:
        if etype not in existing:
            conn.execute(
                f"CREATE REL TABLE GROUP {etype}(FROM {src} TO {dst}, "
                f"type STRING, detail STRING, source_document STRING)"
            )
            existing[etype] = "REL"
        elif (src, dst) not in _existing_pairs(conn, etype):
            conn.execute(f"ALTER TABLE {etype} ADD FROM {src} TO {dst}")

    conn.execute("BEGIN TRANSACTION")
    try:
        # DETACH DELETE removes each node's own edges too -- edges never
        # cross documents (see Global Constraints), so this alone clears
        # this document's entire previous graph.
        for name, kind in existing.items():
            if kind == "NODE":
                conn.execute(
                    f"MATCH (n:{name}) WHERE n.source_document = $stem DETACH DELETE n",
                    {"stem": stem},
                )

        for node in nodes:
            conn.execute(
                f"CREATE (:{node['type']} {{id: $id, label: $label, "
                f"detail: $detail, source_document: $stem}})",
                {
                    "id": f"{stem}::{node['id']}",
                    "label": node["label"],
                    "detail": node.get("detail") or "",
                    "stem": stem,
                },
            )

        for edge in edges:
            src_type = nodes_by_id[edge["source"]]["type"]
            dst_type = nodes_by_id[edge["target"]]["type"]
            conn.execute(
                f"MATCH (a:{src_type} {{id: $src}}), (b:{dst_type} {{id: $dst}}) "
                f"CREATE (a)-[:{edge['type']} {{type: $type, detail: $detail, "
                f"source_document: $stem}}]->(b)",
                {
                    "src": f"{stem}::{edge['source']}",
                    "dst": f"{stem}::{edge['target']}",
                    "type": edge["type"],
                    "detail": edge.get("detail") or "",
                    "stem": stem,
                },
            )

        conn.execute("MERGE (d:_ExtractedDocument {stem: $stem})", {"stem": stem})
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def load_graph(stem: str) -> dict | None:
    if not has_graph(stem):
        return None
    conn = _get_connection()

    # ORDER BY is required for determinism, not just style: an untyped
    # multi-table pattern match (across heterogeneous NODE/REL tables) was
    # observed to return rows in an order that varies from run to run even
    # with a fixed PYTHONHASHSEED and identical internal catalog table IDs
    # -- i.e. nondeterminism inside the DB engine's own execution of these
    # patterns, not something controllable from this module by influencing
    # table creation order.
    node_rows = conn.execute(
        "MATCH (n) WHERE n.source_document = $stem "
        "RETURN label(n) AS type, n.id AS id, n.label AS label, n.detail AS detail "
        "ORDER BY n.id",
        {"stem": stem},
    ).rows_as_dict()
    nodes = [_node_from_row(row) for row in node_rows]

    # No REL table exists at all when this is the very first write_graph
    # call against a fresh database (or every document written so far had
    # zero edges) -- the query below then has no relationship type to bind
    # `r.source_document` against and raises `RuntimeError: Binder
    # exception: Cannot find property source_document for r.` Guard on
    # table existence rather than catching that error: matching on
    # exception type/message would be fragile to library changes.
    if any(kind == "REL" for kind in _existing_tables(conn).values()):
        edge_rows = conn.execute(
            "MATCH (a)-[r]->(b) WHERE r.source_document = $stem "
            "RETURN r.type AS type, r.detail AS detail, a.id AS source, b.id AS target "
            "ORDER BY r.type, a.id, b.id",
            {"stem": stem},
        ).rows_as_dict()
        edges = [_edge_from_row(row) for row in edge_rows]
    else:
        edges = []

    return {"nodes": nodes, "edges": edges}


def find_relevant_nodes(stem: str, keywords: list, allowed_types: list) -> list:
    if not allowed_types or not keywords:
        return []
    conn = _get_connection()
    result = conn.execute(
        "MATCH (n) WHERE label(n) IN $types AND n.source_document = $stem "
        "AND ANY(kw IN $keywords WHERE toLower(n.label) CONTAINS toLower(kw) "
        "OR toLower(kw) CONTAINS toLower(n.label)) "
        "RETURN n.id AS id",
        {"types": allowed_types, "stem": stem, "keywords": keywords},
    )
    return [row["id"].split("::", 1)[1] for row in result.rows_as_dict()]


def all_nodes_of_types(stem: str, allowed_types: list) -> list:
    if not allowed_types:
        return []
    conn = _get_connection()
    result = conn.execute(
        "MATCH (n) WHERE label(n) IN $types AND n.source_document = $stem RETURN n.id AS id",
        {"types": allowed_types, "stem": stem},
    )
    return [row["id"].split("::", 1)[1] for row in result.rows_as_dict()]
