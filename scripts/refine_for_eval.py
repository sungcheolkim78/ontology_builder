#!/usr/bin/env python3
"""Applies a merge mapping to a document's schema and graph in a COPY of backend/data
(app.ontology.refinement), saving the result as a new schema version beside the old one:
the candidate schema and the graph relabelled to match, with no LLM call. Score it with
make golden-score; extract the document again under it with make golden-density.

    ONTOLOGY_DATA_DIR=/path/to/a/COPY/of/backend/data \\
        scripts/refine_for_eval.py "<document stem>" --mapping scripts/refinement_mappings/allbaek_level1.json \\
        [--from-version 7] [--dry-run]

--from-version overrides the mapping file's own (default: the file's, else the active one).
--dry-run only reports what the mapping would do. Like the other evaluation scripts it
refuses to run without ONTOLOGY_DATA_DIR, which must not be the live backend/data.
"""

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
BACKEND = SCRIPTS.parent / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stem")
    parser.add_argument("--mapping", required=True, help="a JSON file with node_types and edge_types maps")
    parser.add_argument("--from-version", type=int)
    parser.add_argument("--dry-run", action="store_true")
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

    from app.graph import graphdb
    from app.ontology.persistence import active_schema, load_schema
    from app.ontology.refinement import apply_mapping_to_graph, apply_mapping_to_schema, refine_version

    mapping = json.loads(Path(args.mapping).read_text())
    version = args.from_version or mapping.get("from_version")
    if version is None:
        version, schema = active_schema(args.stem)
    else:
        schema = load_schema(args.stem, version)
        if schema is None:
            print(f"no schema version {version} for {args.stem!r}", file=sys.stderr)
            return 2

    candidate = apply_mapping_to_schema(schema, mapping)
    graph = graphdb.load_graph(args.stem, version=version)
    refined = apply_mapping_to_graph(graph, mapping) if graph else None
    moved = {}
    if graph:
        for node in graph["nodes"]:
            new = mapping.get("node_types", {}).get(node["type"])
            if new:
                moved[f"{node['type']} -> {new}"] = moved.get(f"{node['type']} -> {new}", 0) + 1
    print(f"schema v{version}: node types {len(schema['node_types'])} -> {len(candidate['node_types'])}, "
          f"edge types {len(schema['edge_types'])} -> {len(candidate['edge_types'])}")
    if graph:
        print(f"graph: nodes {len(graph['nodes'])} -> {len(refined['nodes'])}, edges {len(graph['edges'])} -> {len(refined['edges'])}")
        print("nodes retyped:", ", ".join(f"{k} ({n})" for k, n in sorted(moved.items())) or "none")
        dropped = {t: sum(1 for e in graph["edges"] if e["type"] == t) for t, new in (mapping.get("edge_types") or {}).items()}
        print("edges of the mapped edge types:", ", ".join(f"{t}={n}" for t, n in dropped.items()) or "none")
    if args.dry_run:
        return 0
    summary = refine_version(args.stem, mapping, version=version)
    print(f"saved as schema v{summary['version']} (active); v{summary['from_version']} keeps its schema and graph")
    return 0


if __name__ == "__main__":
    sys.exit(main())
