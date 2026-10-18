#!/usr/bin/env python3
"""Generates a schema for a document in a COPY of backend/data, the same way the
app's /schema route does (schema_for_document over its chunk groups), so a document
that has none can be evaluated: schema here, then make golden-generate and
make golden-density.

    ONTOLOGY_DATA_DIR=/path/to/a/COPY/of/backend/data \\
        scripts/generate_schema_for_eval.py "<document stem>" [--document-type legal|general]

The schema is saved as the document's next version and activated. Like the other
evaluation scripts it refuses to run without ONTOLOGY_DATA_DIR, which must not be the
live backend/data.
"""

import argparse
import os
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
BACKEND = SCRIPTS.parent / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stem")
    parser.add_argument("--document-type", choices=["legal", "general"], default="legal")
    parser.add_argument("--save-dir", metavar="DIR", help="also write a record of the run (time, model, LLM calls) there")
    args = parser.parse_args()

    data_dir = os.environ.get("ONTOLOGY_DATA_DIR")
    if not data_dir:
        print("ONTOLOGY_DATA_DIR is not set. Point it at a COPY of backend/data.", file=sys.stderr)
        return 2
    if Path(data_dir).resolve() == (BACKEND / "data").resolve():
        print("ONTOLOGY_DATA_DIR is the live backend/data; use a copy (make eval-data).", file=sys.stderr)
        return 2

    from evaluate_golden_set import load_openrouter_settings

    load_openrouter_settings()

    from app.ontology.chunk_groups import group_chunks_by_budget
    from app.ontology.generate_schema import schema_for_document
    from app.ontology.utils import _load_chunk_items

    chunks = _load_chunk_items(args.stem)
    if chunks is not None:
        groups = len(group_chunks_by_budget(chunks))
        print(f"{len(chunks)} chunks -> {groups} chunk groups: about {groups + 1} LLM calls (one per group, plus consolidation)")
    from run_record import RunRecord

    started = time.time()
    with RunRecord("generate_schema_for_eval", save_dir=args.save_dir, stem=args.stem, document_type=args.document_type) as rec:
        with rec.phase("schema"):
            schema, version = schema_for_document(args.stem, document_type=args.document_type)
        rec.meta.update(version=version, node_types=len(schema["node_types"]), edge_types=len(schema["edge_types"]))
    print(
        f"done in {time.time() - started:.0f}s: schema v{version} with {len(schema['node_types'])} node types "
        f"and {len(schema['edge_types'])} edge types"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
