import functools
import re
import shutil
import threading

from ladybug import Connection, Database

from app.embeddings import EMBEDDING_DIM
from app.paths import data_dir

DB_PATH = data_dir() / "graph" / "graph.ladybugdb"

# Defensive upper bound on expand_hops' variable-length pattern match --
# main.py already clamps the value it passes in, but this guards any other
# caller against an unbounded `MATCH (n)-[*0..hops]-(m)` traversal on a
# dense graph.
MAX_EXPAND_HOPS = 5

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")

_database = None
_connection = None

# FastAPI's synchronous `def` endpoints run on a real worker threadpool, so
# concurrent requests genuinely execute these functions on different
# threads against the single module-level Connection above. write_graph's
# explicit BEGIN TRANSACTION/COMMIT/ROLLBACK (and the DDL statements
# preceding it) are per-connection state; overlapping calls interleave
# destructively (verified: a second thread's BEGIN while the first's
# transaction is open raises "Connection already has an active
# transaction", concurrent CREATE TABLE IF NOT EXISTS-style checks can
# raise "already exists in catalog", etc.). This lock serializes every
# public function in this module. It's an RLock rather than a plain Lock
# because load_graph calls has_graph internally -- both are synchronized,
# and a plain Lock would deadlock a thread against itself on that nested
# acquisition.
_lock = threading.RLock()


def _synchronized(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _lock:
            return func(*args, **kwargs)

    return wrapper


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
            "CREATE NODE TABLE IF NOT EXISTS _ExtractedDocument("
            "id STRING PRIMARY KEY, stem STRING, version INT64)"
        )
    return _connection


def reset_connection() -> None:
    """Drop cached connection/database handles so a fresh one opens next
    time. Needed because tests delete DB_PATH on disk between runs -- the
    cached native handles would otherwise point at a now-missing directory
    -- and by reset_database() below, which deletes DB_PATH itself."""
    global _database, _connection
    if _connection is not None:
        _connection.close()
        _connection = None
    if _database is not None:
        _database.close()
        _database = None


@_synchronized
def reset_database() -> None:
    """Recovery path for a corrupted WAL file (observed to make every query
    against the database fail): closes the cached connection, then deletes
    DB_PATH and every sibling file sharing its name (the main file/dir plus
    the `.wal` file, and any other file the engine may create alongside
    them) so the next call to any public function in this module opens a
    completely fresh, empty database. This wipes every document's
    extracted graph, since they all live in the one shared database --
    each document's `schema.json` is untouched, so re-extraction remains
    possible."""
    reset_connection()
    if DB_PATH.parent.is_dir():
        for path in DB_PATH.parent.glob(DB_PATH.name + "*"):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


@_synchronized
def has_graph(stem: str, version: int = 1) -> bool:
    conn = _get_connection()
    result = conn.execute(
        "MATCH (d:_ExtractedDocument {id: $id}) RETURN d.stem AS stem",
        {"id": f"{stem}::v{version}"},
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


def _has_table_of_kind(conn, kind: str) -> bool:
    # A generic, untyped pattern (`MATCH (n) ...` or `MATCH (a)-[r]->(b) ...`)
    # raises `RuntimeError: Binder exception: Cannot find property ... for
    # n/r.` when the database has zero tables of the relevant kind (NODE or
    # REL) -- there's no table at all for the pattern to bind the property
    # lookup against. This happens on a genuinely fresh database, or after a
    # document extraction that legitimately yields zero nodes/edges when no
    # other document has created a table of that kind either. Guard on table
    # existence rather than catching the error: matching on exception
    # type/message would be fragile to library changes.
    return any(k == kind for k in _existing_tables(conn).values())


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


@_synchronized
def write_graph(stem: str, nodes: list, edges: list, version: int = 1) -> None:
    conn = _get_connection()
    nodes_by_id = {n["id"]: n for n in nodes}

    # Fail fast, before any DDL/transaction work begins: an LLM extraction
    # can hallucinate an edge endpoint that isn't among this document's own
    # extracted nodes. Left unchecked, `nodes_by_id[edge["source"/"target"]]`
    # below raises a raw KeyError instead of the ValueError every other
    # malformed-LLM-output case in this codebase raises.
    for edge in edges:
        if edge["source"] not in nodes_by_id:
            raise ValueError(f"edge references unknown node id: {edge['source']!r}")
        if edge["target"] not in nodes_by_id:
            raise ValueError(f"edge references unknown node id: {edge['target']!r}")

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
                f"CREATE NODE TABLE {t}(id STRING PRIMARY KEY, original_id STRING, "
                f"label STRING, detail STRING, source_document STRING, version INT64, "
                f"embedding FLOAT[{EMBEDDING_DIM}])"
            )
            existing[t] = "NODE"

    for etype, src, dst in edge_specs:
        if etype not in existing:
            conn.execute(
                f"CREATE REL TABLE GROUP {etype}(FROM {src} TO {dst}, "
                f"type STRING, detail STRING, source_document STRING, version INT64)"
            )
            existing[etype] = "REL"
        elif (src, dst) not in _existing_pairs(conn, etype):
            conn.execute(f"ALTER TABLE {etype} ADD FROM {src} TO {dst}")

    conn.execute("BEGIN TRANSACTION")
    try:
        # DETACH DELETE removes each node's own edges too -- edges never
        # cross documents (see Global Constraints), so this alone clears
        # this document *version*'s entire previous graph.
        for name, kind in existing.items():
            if kind == "NODE":
                conn.execute(
                    f"MATCH (n:{name}) WHERE n.source_document = $stem "
                    f"AND n.version = $version DETACH DELETE n",
                    {"stem": stem, "version": version},
                )

        # Batched one UNWIND per distinct node type / edge spec rather than
        # one CREATE per node/edge -- a document with hundreds of nodes was
        # previously hundreds of round-trips to the DB engine.
        nodes_by_type = {}
        for node in nodes:
            nodes_by_type.setdefault(node["type"], []).append(node)
        for node_type, type_nodes in nodes_by_type.items():
            conn.execute(
                f"UNWIND $rows AS row "
                f"CREATE (:{node_type} {{id: row.id, original_id: row.original_id, "
                f"label: row.label, detail: row.detail, source_document: row.stem, "
                f"version: row.version, embedding: row.embedding}})",
                {
                    "rows": [
                        {
                            "id": f"{stem}::v{version}::{node['id']}",
                            "original_id": node["id"],
                            "label": node["label"],
                            "detail": node.get("detail") or "",
                            "stem": stem,
                            "version": version,
                            "embedding": node.get("embedding"),
                        }
                        for node in type_nodes
                    ]
                },
            )

        edges_by_spec = {}
        for edge in edges:
            src_type = nodes_by_id[edge["source"]]["type"]
            dst_type = nodes_by_id[edge["target"]]["type"]
            edges_by_spec.setdefault((edge["type"], src_type, dst_type), []).append(edge)
        for (etype, src_type, dst_type), spec_edges in edges_by_spec.items():
            conn.execute(
                f"UNWIND $rows AS row "
                f"MATCH (a:{src_type} {{id: row.src}}), (b:{dst_type} {{id: row.dst}}) "
                f"CREATE (a)-[:{etype} {{type: row.type, detail: row.detail, "
                f"source_document: row.stem, version: row.version}}]->(b)",
                {
                    "rows": [
                        {
                            "src": f"{stem}::v{version}::{edge['source']}",
                            "dst": f"{stem}::v{version}::{edge['target']}",
                            "type": edge["type"],
                            "detail": edge.get("detail") or "",
                            "stem": stem,
                            "version": version,
                        }
                        for edge in spec_edges
                    ]
                },
            )

        conn.execute(
            "MERGE (d:_ExtractedDocument {id: $id}) SET d.stem = $stem, d.version = $version",
            {"id": f"{stem}::v{version}", "stem": stem, "version": version},
        )
        conn.execute("COMMIT")
        # Flushes the WAL into the main DB file immediately, rather than
        # leaving this write to sit only in the WAL until the engine's own
        # threshold trips. Ladybug Explorer (see podman-compose.yml) opens
        # the main file once at container start and never re-reads it
        # afterward, WAL included -- without this, a long-running Explorer
        # would show stale data for every write made after it started, no
        # matter how many times it's reconnected or re-queried. Verified
        # experimentally that an explicit `CHECKPOINT;` on the still-open
        # connection does NOT do this (the WAL keeps growing) -- only
        # actually closing the connection/database does, so reset_connection
        # (close now, transparently reopened by the next _get_connection()
        # call) is the only way to force it, not a lighter-weight statement.
        reset_connection()
    except Exception:
        # Some engine-level errors (e.g. a constraint violation mid-query)
        # already auto-abort the transaction before this except block runs,
        # so an explicit ROLLBACK on top of that raises its own
        # "No active transaction for ROLLBACK" RuntimeError and masks the
        # real one. Swallow only that case and let the original exception
        # propagate.
        try:
            conn.execute("ROLLBACK")
        except RuntimeError as rollback_error:
            if "No active transaction" not in str(rollback_error):
                raise
        raise


@_synchronized
def update_node_embeddings(stem: str, nodes: list) -> None:
    """Sets the embedding column on nodes write_graph already created for
    this document, without touching labels/details/edges or the
    delete-and-recreate dance write_graph does. Each node dict must carry
    its own `id`, `type` (to route to the right node table), and
    `embedding`. Safe to rerun -- each call simply overwrites the targeted
    nodes' embedding with whatever vector is passed in."""
    if not nodes:
        return
    conn = _get_connection()
    conn.execute("BEGIN TRANSACTION")
    try:
        for node in nodes:
            node_type = _validate_identifier(node["type"])
            conn.execute(
                f"MATCH (n:{node_type} {{id: $id}}) SET n.embedding = $embedding",
                {"id": f"{stem}::{node['id']}", "embedding": node.get("embedding")},
            )
        conn.execute("COMMIT")
        reset_connection()  # see write_graph's identical rationale
    except Exception:
        # See write_graph's identical rationale: some engine-level errors
        # already auto-abort the transaction before this block runs, so an
        # explicit ROLLBACK on top of that raises its own "No active
        # transaction for ROLLBACK" RuntimeError and masks the real one.
        try:
            conn.execute("ROLLBACK")
        except RuntimeError as rollback_error:
            if "No active transaction" not in str(rollback_error):
                raise
        raise


@_synchronized
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
    # No NODE table exists at all when this is the very first write_graph
    # call against a fresh database and that call had zero nodes (or every
    # document written so far had zero nodes) -- see _has_table_of_kind.
    if _has_table_of_kind(conn, "NODE"):
        node_rows = conn.execute(
            "MATCH (n) WHERE n.source_document = $stem "
            "RETURN label(n) AS type, n.id AS id, n.label AS label, n.detail AS detail "
            "ORDER BY n.id",
            {"stem": stem},
        ).rows_as_dict()
        nodes = [_node_from_row(row) for row in node_rows]
    else:
        nodes = []

    # No REL table exists at all when this is the very first write_graph
    # call against a fresh database (or every document written so far had
    # zero edges) -- see _has_table_of_kind.
    if _has_table_of_kind(conn, "REL"):
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


@_synchronized
def find_relevant_nodes(stem: str, type_keywords: dict, allowed_types: list) -> list:
    """type_keywords maps each node type to its own keyword list (e.g.
    {"Person": ["Ada Lovelace"]}) so a term is only matched against nodes of
    the type it was extracted for, not every allowed type at once."""
    if not allowed_types or not type_keywords:
        return []
    pairs = [
        {"type": t, "keywords": kws}
        for t, kws in type_keywords.items()
        if t in allowed_types and kws
    ]
    if not pairs:
        return []
    conn = _get_connection()
    # No NODE table exists at all -- see _has_table_of_kind.
    if not _has_table_of_kind(conn, "NODE"):
        return []
    result = conn.execute(
        "UNWIND $pairs AS tk "
        "MATCH (n) WHERE label(n) = tk.type AND n.source_document = $stem "
        "AND ANY(kw IN tk.keywords WHERE toLower(n.label) CONTAINS toLower(kw) "
        "OR toLower(kw) CONTAINS toLower(n.label)) "
        "RETURN DISTINCT n.id AS id",
        {"pairs": pairs, "stem": stem},
    )
    return [row["id"].split("::", 1)[1] for row in result.rows_as_dict()]


@_synchronized
def find_similar_nodes(
    stem: str, node_type: str, query_embedding: list, top_k: int, min_score: float = 0.0
) -> list:
    """Embedding fallback for a single type when find_relevant_nodes'
    keyword matching comes up empty -- ranks that type's own nodes by
    cosine similarity to query_embedding instead of returning every
    instance of the type. Nodes written before embeddings existed (or any
    node whose embedding call failed) have a NULL embedding column and are
    excluded rather than sorted arbitrarily."""
    _validate_identifier(node_type)
    conn = _get_connection()
    # No NODE table exists at all -- see _has_table_of_kind.
    if not _has_table_of_kind(conn, "NODE"):
        return []
    if node_type not in _existing_tables(conn):
        return []
    result = conn.execute(
        f"MATCH (n:{node_type}) WHERE n.source_document = $stem AND n.embedding IS NOT NULL "
        f"RETURN n.id AS id, array_cosine_similarity(n.embedding, $query_embedding) AS score "
        f"ORDER BY score DESC LIMIT $top_k",
        {"stem": stem, "query_embedding": query_embedding, "top_k": top_k},
    )
    return [
        row["id"].split("::", 1)[1]
        for row in result.rows_as_dict()
        if row["score"] is not None and row["score"] >= min_score
    ]


@_synchronized
def all_nodes_of_types(stem: str, allowed_types: list) -> list:
    if not allowed_types:
        return []
    conn = _get_connection()
    # No NODE table exists at all -- see _has_table_of_kind.
    if not _has_table_of_kind(conn, "NODE"):
        return []
    result = conn.execute(
        "MATCH (n) WHERE label(n) IN $types AND n.source_document = $stem RETURN n.id AS id",
        {"types": allowed_types, "stem": stem},
    )
    return [row["id"].split("::", 1)[1] for row in result.rows_as_dict()]


@_synchronized
def find_matching_edges(stem: str, allowed_types: list, matched_node_ids: set) -> list:
    if not allowed_types or not matched_node_ids:
        return []
    conn = _get_connection()
    # No REL table exists at all -- see _has_table_of_kind.
    if not _has_table_of_kind(conn, "REL"):
        return []
    prefixed_ids = [f"{stem}::{nid}" for nid in matched_node_ids]
    result = conn.execute(
        "MATCH (a)-[r]->(b) WHERE r.type IN $types AND r.source_document = $stem "
        "AND (a.id IN $ids OR b.id IN $ids) "
        "RETURN r.type AS type, r.detail AS detail, a.id AS source, b.id AS target",
        {"types": allowed_types, "stem": stem, "ids": prefixed_ids},
    )
    return [_edge_from_row(row) for row in result.rows_as_dict()]


@_synchronized
def all_edges_of_types(stem: str, allowed_types: list) -> list:
    if not allowed_types:
        return []
    conn = _get_connection()
    # No REL table exists at all -- see _has_table_of_kind.
    if not _has_table_of_kind(conn, "REL"):
        return []
    result = conn.execute(
        "MATCH (a)-[r]->(b) WHERE r.type IN $types AND r.source_document = $stem "
        "RETURN r.type AS type, r.detail AS detail, a.id AS source, b.id AS target",
        {"types": allowed_types, "stem": stem},
    )
    return [_edge_from_row(row) for row in result.rows_as_dict()]


@_synchronized
def expand_hops(stem: str, seed_ids: set, hops: int) -> tuple:
    if not seed_ids:
        return [], []
    conn = _get_connection()
    prefixed_seeds = [f"{stem}::{sid}" for sid in seed_ids]
    hops = max(min(hops, MAX_EXPAND_HOPS), 0)

    # No NODE table exists at all -- see _has_table_of_kind. Nothing can
    # possibly match (there are no nodes in the whole database), and both
    # queries below assume at least one NODE table exists, so short-circuit
    # before running either.
    if not _has_table_of_kind(conn, "NODE"):
        return [], []

    # No REL table exists anywhere in the database yet (e.g. this document's
    # own write_graph call had zero edges, and no other document has ever
    # created a REL table either). This breaks both queries below, in two
    # different ways -- confirmed experimentally against a real database,
    # not assumed from the load_graph precedent:
    #   1. The edges-among-expanded-set query (`MATCH (a)-[r]->(b) ...`) is
    #      the same untyped relationship pattern as load_graph/
    #      find_matching_edges/all_edges_of_types, and raises the same
    #      `RuntimeError: Binder exception: Cannot find property
    #      source_document for r.`
    #   2. The variable-length node-expansion query
    #      (`MATCH (n)-[*0..{hops}]-(m) ...`) does NOT raise -- it runs and
    #      silently returns zero rows, even at hops=0 where m should always
    #      include n itself (verified: the identical query against a
    #      database that *does* have a REL table correctly returns the seed
    #      node at hops=0; against a database with no REL table at all, it
    #      returns nothing, seed included). Left unguarded, expand_hops
    #      would silently drop the seed nodes for any document with no
    #      edges anywhere in the whole database -- worse than an exception,
    #      since nothing would signal the miss.
    # Guard both the same way: fetch seed nodes directly (no relationship
    # pattern at all) and skip the edge query, returning no edges.
    has_rel_table = _has_table_of_kind(conn, "REL")

    if has_rel_table:
        node_rows = conn.execute(
            f"MATCH (n)-[*0..{hops}]-(m) WHERE n.id IN $seeds AND m.source_document = $stem "
            f"RETURN DISTINCT label(m) AS type, m.id AS id, m.label AS label, m.detail AS detail",
            {"seeds": prefixed_seeds, "stem": stem},
        )
    else:
        node_rows = conn.execute(
            "MATCH (n) WHERE n.id IN $seeds AND n.source_document = $stem "
            "RETURN label(n) AS type, n.id AS id, n.label AS label, n.detail AS detail",
            {"seeds": prefixed_seeds, "stem": stem},
        )
    nodes = [_node_from_row(row) for row in node_rows.rows_as_dict()]

    if not has_rel_table:
        return nodes, []

    expanded_ids = [f"{stem}::{n['id']}" for n in nodes]
    edge_rows = conn.execute(
        "MATCH (a)-[r]->(b) WHERE a.id IN $ids AND b.id IN $ids AND r.source_document = $stem "
        "RETURN r.type AS type, r.detail AS detail, a.id AS source, b.id AS target",
        {"ids": expanded_ids, "stem": stem},
    )
    edges = [_edge_from_row(row) for row in edge_rows.rows_as_dict()]

    return nodes, edges
