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


# The common graph envelope's optional metadata fields (design spec section
# 4.1), added to every existing NODE/REL table via ALTER TABLE ADD rather
# than baked into the CREATE statement -- verified experimentally that
# ALTER TABLE ADD works on this engine (ladybug==0.19.1), so a table created
# before this feature existed and one created after it both end up with the
# same columns, and _ensure_envelope_columns (below) is what makes that
# true regardless of which case a given table is in. `properties` is one
# open MAP(STRING, STRING) column rather than one physical column per
# declared property, since a node/edge type's table is shared by every
# domain schema that uses that type name -- two schemas declaring the same
# type with different property sets would otherwise conflict on fixed
# columns (see design spec section 4.2). `valid_from`/`valid_to` are plain
# strings (ISO dates), not typed as a discrete DATE/interval column: nothing
# in this codebase generates or compares them as dates yet, and a string
# column is the simplest thing that can be widened later without another
# migration once that need is concrete.
_ENVELOPE_EXTRA_COLUMNS = [
    ("confidence", "STRING"),
    ("evidence_text", "STRING"),
    ("source_section", "STRING"),
    ("start_offset", "INT64"),
    ("end_offset", "INT64"),
    ("valid_from", "STRING"),
    ("valid_to", "STRING"),
    ("properties", "MAP(STRING, STRING)"),
]


def _existing_columns(conn, table_name: str) -> set:
    return {
        row["name"] for row in conn.execute(f'CALL TABLE_INFO("{table_name}") RETURN *').rows_as_dict()
    }


def _ensure_envelope_columns(conn, table_name: str) -> None:
    existing_columns = _existing_columns(conn, table_name)
    for column_name, column_type in _ENVELOPE_EXTRA_COLUMNS:
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE {table_name} ADD {column_name} {column_type}")


def _property_lists(properties) -> tuple:
    properties = properties or {}
    return list(properties.keys()), [str(v) for v in properties.values()]


def _envelope_extra_row_values(item: dict) -> dict:
    keys, values = _property_lists(item.get("properties"))
    return {
        "confidence": item.get("confidence"),
        "evidence_text": item.get("evidence_text"),
        "source_section": item.get("source_section"),
        "start_offset": item.get("start_offset"),
        "end_offset": item.get("end_offset"),
        "valid_from": item.get("valid_from"),
        "valid_to": item.get("valid_to"),
        "properties_keys": keys,
        "properties_values": values,
    }


# The CAST(... AS INT64) around start_offset/end_offset is required, not
# decorative -- verified experimentally that an UNWIND row batch where every
# row's start_offset/end_offset is None binds fine as a plain node CREATE,
# but the same all-NULL field in a `MATCH ... CREATE (a)-[:TYPE {...}]->(b)`
# relationship-creation query raises "STRUCT_EXTRACT(row,start_offset) has
# data type STRING but expected INT64" -- the engine infers an untyped-NULL
# struct field as STRING in this query shape, which doesn't implicitly cast
# to the column's actual INT64 type. The CAST makes the target type explicit
# regardless of what the engine inferred for a NULL value.
_ENVELOPE_EXTRA_CREATE_FIELDS = (
    "confidence: row.confidence, evidence_text: row.evidence_text, "
    "source_section: row.source_section, start_offset: CAST(row.start_offset AS INT64), "
    "end_offset: CAST(row.end_offset AS INT64), valid_from: row.valid_from, valid_to: row.valid_to, "
    "properties: map(row.properties_keys, row.properties_values)"
)

def _envelope_return_fields(alias: str) -> str:
    """Builds `<alias>.confidence AS confidence, ...` for every envelope
    extra column -- a plain shared string constant (like
    _ENVELOPE_EXTRA_CREATE_FIELDS) can't work here because RETURN clauses
    combine multiple aliases in one query (e.g. `a`/`b`/`r` in an edge
    query), so each field needs its own alias prefix bound correctly."""
    return ", ".join(
        f"{alias}.{name} AS {name}" for name, _ in _ENVELOPE_EXTRA_COLUMNS
    )


def _apply_envelope_extras(item: dict, row: dict) -> dict:
    """Adds properties/confidence/evidence*/source_section/valid_from/
    valid_to to `item` only when the row actually has a value for them --
    the same additive-only rule as app.ontology's extraction-side
    normalization, so a legacy row (written before this feature, or simply
    never given this metadata) round-trips with exactly its old shape."""
    properties = row.get("properties") or {}
    if properties:
        item["properties"] = properties
    if row.get("confidence"):
        item["confidence"] = row["confidence"]
    if row.get("evidence_text"):
        item["evidence_text"] = row["evidence_text"]
        if row.get("start_offset") is not None:
            item["start_offset"] = row["start_offset"]
        if row.get("end_offset") is not None:
            item["end_offset"] = row["end_offset"]
    if row.get("source_section"):
        item["source_section"] = row["source_section"]
    if row.get("valid_from"):
        item["valid_from"] = row["valid_from"]
    if row.get("valid_to"):
        item["valid_to"] = row["valid_to"]
    return item


def _node_from_row(row: dict) -> dict:
    node = {"id": row["original_id"], "label": row["label"], "type": row["type"]}
    if row.get("detail"):
        node["detail"] = row["detail"]
    return _apply_envelope_extras(node, row)


def _edge_from_row(row: dict) -> dict:
    edge = {
        "source": row["source"],
        "target": row["target"],
        "type": row["type"],
    }
    if row.get("detail"):
        edge["detail"] = row["detail"]
    return _apply_envelope_extras(edge, row)


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
    # LadybugDB's catalog (and Cypher label resolution) treats table names
    # case-insensitively -- verified experimentally: CREATE NODE TABLE FOO
    # after CREATE NODE TABLE foo raises "already exists in catalog", while
    # MATCH (n:FOO) still finds rows created under label foo. LLM-generated
    # type names from two different documents can name "the same" type with
    # different casing (e.g. "worked_on" vs "WORKED_ON"), so existence must
    # be checked case-insensitively too, or a case-only difference looks
    # like a new type and crashes the CREATE below as a duplicate. The same
    # applies to the FROM/TO pair check just below (`_existing_pairs`) --
    # `show_connection` also resolves names case-insensitively, so an
    # ALTER TABLE ADD FROM/TO for a pair that already exists under
    # different casing raises its own "already exists" error instead of
    # correctly recognizing the pair as already registered.
    existing_lower = {name.lower() for name in existing}
    for t in node_types:
        if t.lower() not in existing_lower:
            conn.execute(
                f"CREATE NODE TABLE {t}(id STRING PRIMARY KEY, original_id STRING, "
                f"label STRING, detail STRING, source_document STRING, version INT64, "
                f"embedding FLOAT[{EMBEDDING_DIM}])"
            )
            existing[t] = "NODE"
            existing_lower.add(t.lower())
        # Applies equally to a table just created above (a no-op, since it
        # already has every column) and one that predates this feature --
        # see _ENVELOPE_EXTRA_COLUMNS' own rationale for why this isn't
        # folded into the CREATE statement instead.
        _ensure_envelope_columns(conn, t)

    for etype, src, dst in edge_specs:
        if etype.lower() not in existing_lower:
            conn.execute(
                f"CREATE REL TABLE GROUP {etype}(FROM {src} TO {dst}, "
                f"type STRING, detail STRING, source_document STRING, version INT64)"
            )
            existing[etype] = "REL"
            existing_lower.add(etype.lower())
        elif (src.lower(), dst.lower()) not in {
            (s.lower(), d.lower()) for s, d in _existing_pairs(conn, etype)
        }:
            conn.execute(f"ALTER TABLE {etype} ADD FROM {src} TO {dst}")
        _ensure_envelope_columns(conn, etype)

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
                f"version: row.version, embedding: row.embedding, "
                f"{_ENVELOPE_EXTRA_CREATE_FIELDS}}})",
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
                            **_envelope_extra_row_values(node),
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
                f"source_document: row.stem, version: row.version, "
                f"{_ENVELOPE_EXTRA_CREATE_FIELDS}}}]->(b)",
                {
                    "rows": [
                        {
                            "src": f"{stem}::v{version}::{edge['source']}",
                            "dst": f"{stem}::v{version}::{edge['target']}",
                            "type": edge["type"],
                            "detail": edge.get("detail") or "",
                            "stem": stem,
                            "version": version,
                            **_envelope_extra_row_values(edge),
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
def delete_version_data(stem: str, version: int = 1) -> None:
    """Removes every row belonging to this (stem, version) across all known
    NODE tables (DETACH DELETE also removes their edges) plus the matching
    _ExtractedDocument marker row. Used by ontology.delete_version when a
    schema version is deleted -- the schema file itself is removed by that
    caller, this only clears the graph data side."""
    conn = _get_connection()
    existing = _existing_tables(conn)
    conn.execute("BEGIN TRANSACTION")
    try:
        for name, kind in existing.items():
            if kind == "NODE":
                conn.execute(
                    f"MATCH (n:{name}) WHERE n.source_document = $stem "
                    f"AND n.version = $version DETACH DELETE n",
                    {"stem": stem, "version": version},
                )
        conn.execute(
            "MATCH (d:_ExtractedDocument {id: $id}) DETACH DELETE d",
            {"id": f"{stem}::v{version}"},
        )
        conn.execute("COMMIT")
        reset_connection()
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except RuntimeError as rollback_error:
            if "No active transaction" not in str(rollback_error):
                raise
        raise


@_synchronized
def update_node_embeddings(stem: str, nodes: list, version: int = 1) -> None:
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
                {"id": f"{stem}::v{version}::{node['id']}", "embedding": node.get("embedding")},
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
def load_graph(stem: str, version: int = 1) -> dict | None:
    if not has_graph(stem, version):
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
            "MATCH (n) WHERE n.source_document = $stem AND n.version = $version "
            "RETURN label(n) AS type, n.original_id AS original_id, n.label AS label, "
            f"n.detail AS detail, {_envelope_return_fields('n')} ORDER BY n.id",
            {"stem": stem, "version": version},
        ).rows_as_dict()
        nodes = [_node_from_row(row) for row in node_rows]
    else:
        nodes = []

    # No REL table exists at all when this is the very first write_graph
    # call against a fresh database (or every document written so far had
    # zero edges) -- see _has_table_of_kind.
    if _has_table_of_kind(conn, "REL"):
        edge_rows = conn.execute(
            "MATCH (a)-[r]->(b) WHERE r.source_document = $stem AND r.version = $version "
            "RETURN r.type AS type, r.detail AS detail, a.original_id AS source, "
            f"b.original_id AS target, {_envelope_return_fields('r')} "
            "ORDER BY r.type, a.id, b.id",
            {"stem": stem, "version": version},
        ).rows_as_dict()
        edges = [_edge_from_row(row) for row in edge_rows]
    else:
        edges = []

    return {"nodes": nodes, "edges": edges}


@_synchronized
def find_relevant_nodes(stem: str, type_keywords: dict, allowed_types: list, version: int = 1) -> list:
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
        "AND n.version = $version "
        "AND ANY(kw IN tk.keywords WHERE toLower(n.label) CONTAINS toLower(kw) "
        "OR toLower(kw) CONTAINS toLower(n.label)) "
        "RETURN DISTINCT n.original_id AS id",
        {"pairs": pairs, "stem": stem, "version": version},
    )
    return [row["id"] for row in result.rows_as_dict()]


@_synchronized
def find_similar_nodes(
    stem: str, node_type: str, query_embedding: list, top_k: int, min_score: float = 0.0,
    version: int = 1,
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
        f"MATCH (n:{node_type}) WHERE n.source_document = $stem AND n.version = $version "
        f"AND n.embedding IS NOT NULL "
        f"RETURN n.original_id AS id, array_cosine_similarity(n.embedding, $query_embedding) AS score "
        f"ORDER BY score DESC LIMIT $top_k",
        {"stem": stem, "version": version, "query_embedding": query_embedding, "top_k": top_k},
    )
    return [
        row["id"]
        for row in result.rows_as_dict()
        if row["score"] is not None and row["score"] >= min_score
    ]


@_synchronized
def all_nodes_of_types(stem: str, allowed_types: list, version: int = 1) -> list:
    if not allowed_types:
        return []
    conn = _get_connection()
    # No NODE table exists at all -- see _has_table_of_kind.
    if not _has_table_of_kind(conn, "NODE"):
        return []
    result = conn.execute(
        "MATCH (n) WHERE label(n) IN $types AND n.source_document = $stem "
        "AND n.version = $version RETURN n.original_id AS id",
        {"types": allowed_types, "stem": stem, "version": version},
    )
    return [row["id"] for row in result.rows_as_dict()]


@_synchronized
def find_matching_edges(stem: str, allowed_types: list, matched_node_ids: set, version: int = 1) -> list:
    if not allowed_types or not matched_node_ids:
        return []
    conn = _get_connection()
    # No REL table exists at all -- see _has_table_of_kind.
    if not _has_table_of_kind(conn, "REL"):
        return []
    result = conn.execute(
        "MATCH (a)-[r]->(b) WHERE r.type IN $types AND r.source_document = $stem "
        "AND r.version = $version AND (a.original_id IN $ids OR b.original_id IN $ids) "
        "RETURN r.type AS type, r.detail AS detail, a.original_id AS source, "
        f"b.original_id AS target, {_envelope_return_fields('r')}",
        {"types": allowed_types, "stem": stem, "version": version, "ids": list(matched_node_ids)},
    )
    return [_edge_from_row(row) for row in result.rows_as_dict()]


@_synchronized
def all_edges_of_types(stem: str, allowed_types: list, version: int = 1) -> list:
    if not allowed_types:
        return []
    conn = _get_connection()
    # No REL table exists at all -- see _has_table_of_kind.
    if not _has_table_of_kind(conn, "REL"):
        return []
    result = conn.execute(
        "MATCH (a)-[r]->(b) WHERE r.type IN $types AND r.source_document = $stem "
        "AND r.version = $version "
        "RETURN r.type AS type, r.detail AS detail, a.original_id AS source, "
        f"b.original_id AS target, {_envelope_return_fields('r')}",
        {"types": allowed_types, "stem": stem, "version": version},
    )
    return [_edge_from_row(row) for row in result.rows_as_dict()]


@_synchronized
def expand_hops(stem: str, seed_ids: set, hops: int, version: int = 1) -> tuple:
    if not seed_ids:
        return [], []
    conn = _get_connection()
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

    # seed_ids/expanded_ids are bare original_id values, not globally unique
    # on their own (unlike the old prefixed-id scheme) -- every match below
    # also filters by source_document/version so a seed can't accidentally
    # resolve to a different document's or version's node sharing the same
    # bare id in the same shared type table.
    if has_rel_table:
        node_rows = conn.execute(
            f"MATCH (n)-[*0..{hops}]-(m) WHERE n.original_id IN $seeds "
            f"AND n.source_document = $stem AND n.version = $version "
            f"AND m.source_document = $stem AND m.version = $version "
            f"RETURN DISTINCT label(m) AS type, m.original_id AS original_id, "
            f"m.label AS label, m.detail AS detail, {_envelope_return_fields('m')}",
            {"seeds": list(seed_ids), "stem": stem, "version": version},
        )
    else:
        node_rows = conn.execute(
            "MATCH (n) WHERE n.original_id IN $seeds AND n.source_document = $stem "
            "AND n.version = $version "
            "RETURN label(n) AS type, n.original_id AS original_id, n.label AS label, "
            f"n.detail AS detail, {_envelope_return_fields('n')}",
            {"seeds": list(seed_ids), "stem": stem, "version": version},
        )
    nodes = [_node_from_row(row) for row in node_rows.rows_as_dict()]

    if not has_rel_table:
        return nodes, []

    expanded_ids = [n["id"] for n in nodes]
    edge_rows = conn.execute(
        "MATCH (a)-[r]->(b) WHERE a.original_id IN $ids AND b.original_id IN $ids "
        "AND r.source_document = $stem AND r.version = $version "
        "RETURN r.type AS type, r.detail AS detail, a.original_id AS source, "
        f"b.original_id AS target, {_envelope_return_fields('r')}",
        {"ids": expanded_ids, "stem": stem, "version": version},
    )
    edges = [_edge_from_row(row) for row in edge_rows.rows_as_dict()]

    return nodes, edges
