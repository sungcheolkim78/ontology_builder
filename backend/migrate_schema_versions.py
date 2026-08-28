"""One-time migration: adds per-document schema versioning to existing
backend/data. Run once, with podman-compose stopped (this script and the
backend container must never have graph.ladybugdb open at the same time
-- see CLAUDE.md's virtiofs/WAL notes). Run ./scripts/backup_data.sh
first. Safe to re-run -- every step is a no-op if already applied.

Usage:
    cd backend && source .venv/bin/activate && python migrate_schema_versions.py
"""
import json

from app import graphdb
from app.ontology import GRAPH_DIR


def migrate_schema_files():
    if not GRAPH_DIR.is_dir():
        print("No graph directory found -- nothing to migrate.")
        return
    for stem_dir in sorted(GRAPH_DIR.iterdir()):
        if not stem_dir.is_dir():
            continue
        old_schema = stem_dir / "schema.json"
        versions_path = stem_dir / "versions.json"
        if versions_path.is_file():
            print(f"{stem_dir.name}: already migrated, skipping")
            continue
        if not old_schema.is_file():
            print(f"{stem_dir.name}: no schema.json, skipping")
            continue
        old_schema.rename(stem_dir / "schema_v1.json")
        versions_path.write_text(
            json.dumps(
                {
                    "active_version": 1,
                    "versions": [
                        {"version": 1, "document_type": "unknown", "created_at": None}
                    ],
                }
            )
        )
        print(f"{stem_dir.name}: schema.json -> schema_v1.json, wrote versions.json")


def migrate_graphdb():
    conn = graphdb._get_connection()
    tables = graphdb._existing_tables(conn)

    for name, kind in tables.items():
        columns = {
            row["name"]
            for row in conn.execute(f"CALL table_info('{name}') RETURN *").rows_as_dict()
        }
        if "version" not in columns:
            conn.execute(f"ALTER TABLE {name} ADD version INT64 DEFAULT 1")
            print(f"{name}: added version column (default 1)")
        else:
            print(f"{name}: already has version column, skipping")

        if kind == "NODE" and "original_id" not in columns:
            conn.execute(f"ALTER TABLE {name} ADD original_id STRING")
            rows = list(conn.execute(f"MATCH (n:{name}) RETURN n.id AS id").rows_as_dict())
            for row in rows:
                original_id = row["id"].rsplit("::", 1)[1]
                conn.execute(
                    f"MATCH (n:{name} {{id: $id}}) SET n.original_id = $original_id",
                    {"id": row["id"], "original_id": original_id},
                )
            print(f"{name}: added original_id column, backfilled {len(rows)} row(s)")
        elif kind == "NODE":
            print(f"{name}: already has original_id column, skipping")

    # _ExtractedDocument's primary key changes shape (stem -> a composite
    # "{stem}::v{version}" id), which ALTER TABLE can't do -- rebuild it
    # from its current rows.
    doc_columns = {
        row["name"]
        for row in conn.execute("CALL table_info('_ExtractedDocument') RETURN *").rows_as_dict()
    }
    if "id" in doc_columns:
        print("_ExtractedDocument: already migrated, skipping")
    else:
        existing_stems = [
            row["stem"]
            for row in conn.execute(
                "MATCH (d:_ExtractedDocument) RETURN d.stem AS stem"
            ).rows_as_dict()
        ]
        conn.execute("DROP TABLE _ExtractedDocument")
        conn.execute(
            "CREATE NODE TABLE _ExtractedDocument(id STRING PRIMARY KEY, stem STRING, version INT64)"
        )
        for stem in existing_stems:
            conn.execute(
                "CREATE (:_ExtractedDocument {id: $id, stem: $stem, version: 1})",
                {"id": f"{stem}::v1", "stem": stem},
            )
        print(
            f"_ExtractedDocument: rebuilt with composite key "
            f"({len(existing_stems)} document(s) preserved)"
        )

    graphdb.reset_connection()


if __name__ == "__main__":
    migrate_schema_files()
    migrate_graphdb()
    print("Migration complete.")
