"""One-time migration: adds the flexible-ontology-graph-schema envelope
columns (confidence, evidence_text, source_section, start_offset,
end_offset, valid_from, valid_to, properties) to every existing NODE/REL
table in backend/data/graph/graph.ladybugdb.

Required before deploying this change against real data -- without it, any
table `write_graph` hasn't re-touched since this feature shipped is missing
these columns entirely, and `load_graph`/`find_matching_edges`/
`all_edges_of_types`/`expand_hops` (which now unconditionally SELECT them)
raise `Binder exception: Cannot find property confidence for n/r/m.` the
first time they touch such a table -- verified experimentally against a
synthetic legacy-shaped fixture. Reading a table that already has the
columns (freshly extracted after this change) is unaffected; this script is
what closes the gap for every table extracted before it.

Run once, with podman-compose stopped (this script and the backend
container must never have graph.ladybugdb open at the same time -- see
CLAUDE.md's virtiofs/WAL notes). Run ./scripts/backup_data.sh first. Safe
to re-run -- every step is a no-op if already applied (same idempotency
contract as migrate_schema_versions.py, migrate_data_layout.py).

Usage:
    cd backend && source .venv/bin/activate && python migrate_envelope_columns.py
"""
from app import graphdb


def migrate_envelope_columns():
    conn = graphdb._get_connection()
    tables = graphdb._existing_tables(conn)

    if not tables:
        print("No NODE/REL tables found -- nothing to migrate.")
        return

    for name in tables:
        existing_columns = graphdb._existing_columns(conn, name)
        missing = [
            (col, typ)
            for col, typ in graphdb._ENVELOPE_EXTRA_COLUMNS
            if col not in existing_columns
        ]
        if not missing:
            print(f"{name}: already has every envelope column, skipping")
            continue
        for col, typ in missing:
            conn.execute(f"ALTER TABLE {name} ADD {col} {typ}")
        print(f"{name}: added {', '.join(col for col, _ in missing)}")

    graphdb.reset_connection()


if __name__ == "__main__":
    migrate_envelope_columns()
    print("Migration complete.")
