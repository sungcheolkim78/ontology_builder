#!/usr/bin/env python3
"""Re-extracts a document's graph under smaller chunk groups, to see whether
extraction density -- how much of a passage-rich text the model turns into
nodes at once -- is what limits a golden-set score (see
backend/app/ontology/reextraction_probe.py: the same schema recovered 6% of the
missed passages from a whole chunk group but 47% from the chunk alone).

    ONTOLOGY_DATA_DIR=/path/to/a/COPY/of/backend/data \\
        scripts/reextract_for_eval.py "<document stem>" --group-chars 3000 [--from-version 3] [--variant FILE] [--dry-run]

The active schema (or --from-version) is copied as a NEW schema version and the
document is extracted under it with EXTRACT_GROUP_CHARS=--group-chars, so the
graph of the version it was copied from is left as it was and both can be scored
(scripts/evaluate_golden_set.py scores the active version; --version picks one).
--dry-run only reports how many chunk groups, hence LLM calls, the run would make.
Like evaluate_golden_set.py it refuses to run without ONTOLOGY_DATA_DIR, which must not
be the live backend/data. Nodes are not embedded (the scorer embeds what it needs itself).
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
    parser.add_argument("stem", help="the document's stem (its folder name under documents/)")
    parser.add_argument("--group-chars", type=int, required=True, metavar="N", help="EXTRACT_GROUP_CHARS for this run")
    parser.add_argument("--from-version", type=int, help="schema version to copy (default: the active one)")
    parser.add_argument("--dry-run", action="store_true", help="only report the number of chunk groups")
    parser.add_argument(
        "--variant", metavar="FILE",
        help="a text file appended to the extraction prompt (scripts/prompt_variants/): extract with those instructions",
    )
    parser.add_argument("--save-dir", metavar="DIR", help="also write a record of the run (time, model, LLM calls) there")
    args = parser.parse_args()

    data_dir = os.environ.get("ONTOLOGY_DATA_DIR")
    if not data_dir:
        print("ONTOLOGY_DATA_DIR is not set. Point it at a COPY of backend/data.", file=sys.stderr)
        return 2
    if Path(data_dir).resolve() == (BACKEND / "data").resolve():
        print("ONTOLOGY_DATA_DIR is the live backend/data; use a copy (make eval-data).", file=sys.stderr)
        return 2
    os.environ["EXTRACT_GROUP_CHARS"] = str(args.group_chars)  # read when chunk_groups is imported

    from evaluate_golden_set import load_openrouter_settings

    load_openrouter_settings()

    from app.ontology.chunk_groups import group_chunks_by_budget
    from app.ontology.extract_graph import extract_for_document
    from app.ontology.persistence import create_schema_version, get_active_version, list_versions, load_schema
    from app.ontology.refinement import unfinished_copy
    from app.ontology.utils import _load_chunk_items

    chunks = _load_chunk_items(args.stem)
    if chunks is None:
        print("the document has no chunks.json; this experiment is about chunk groups", file=sys.stderr)
        return 2
    groups = group_chunks_by_budget(chunks, max_group_chars=args.group_chars)
    print(f"{len(chunks)} chunks -> {len(groups)} chunk groups of at most {args.group_chars} characters")
    if args.dry_run:
        return 0

    continuing = unfinished_copy(args.stem, args.from_version)
    if continuing:
        version, source = continuing
        print(
            f"v{version} is a copy of schema v{source} whose extraction stopped; continuing it "
            f"({len(groups)} chunk groups, the finished ones are reused)..."
        )
    else:
        source = args.from_version or get_active_version(args.stem)
        schema = load_schema(args.stem, source)
        if schema is None:
            print(f"no schema version {source} for {args.stem!r}", file=sys.stderr)
            return 2
        document_type = next((v["document_type"] for v in list_versions(args.stem) if v["version"] == source), "general")
        version = create_schema_version(args.stem, schema, document_type=document_type)
        print(f"copied schema v{source} as v{version}; extracting ({len(groups)} LLM calls)...")

    from run_record import RunRecord

    extra = Path(args.variant).read_text().strip() if args.variant else None
    started = time.time()
    with RunRecord(
        "reextract_for_eval", save_dir=args.save_dir, stem=args.stem, group_chars=args.group_chars,
        copied_from_version=source, new_version=version, chunk_groups=len(groups), variant=args.variant,
    ) as rec:
        with rec.phase("extract"):
            _, graph, _ = extract_for_document(args.stem, extra_instructions=extra)
        rec.meta["nodes"], rec.meta["edges"] = len(graph["nodes"]), len(graph["edges"])
    print(
        f"done in {time.time() - started:.0f}s: v{version} has {len(graph['nodes'])} nodes, {len(graph['edges'])} edges "
        f"({sum(1 for n in graph['nodes'] if n.get('evidence_text'))} nodes cite a passage). "
        f"Score it with: make golden-eval   (v{source} keeps its own graph; --version {source} scores it)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
