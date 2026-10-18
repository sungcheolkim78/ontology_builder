#!/usr/bin/env python3
"""Checks, without any LLM call, that scripts/evaluate_golden_set.py can run:
the document's folder is in the data copy ONTOLOGY_DATA_DIR points at, it has an
active schema and an extracted graph, and an OpenRouter key is configured.
Run by `make eval-check`; prints what it finds and exits non-zero on a problem.

    check_eval_setup.py "<stem>" [--stage generate|schema|reextract|score]

--stage says what is about to be run and so what must already exist: `generate`
(a golden set) needs only the document; `schema` (generating one) the document;
`reextract` an active schema; `score` (the default) an active schema and an
extracted graph."""

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def main() -> int:
    stem = sys.argv[1]
    stage = sys.argv[sys.argv.index("--stage") + 1] if "--stage" in sys.argv else "score"
    problems = []

    data_dir = os.environ.get("ONTOLOGY_DATA_DIR", "")
    live = (BACKEND / "data").resolve()
    if not data_dir:
        problems.append("ONTOLOGY_DATA_DIR is not set")
    elif Path(data_dir).resolve() == live:
        problems.append("ONTOLOGY_DATA_DIR is the live backend/data -- it must be a copy")
    elif not Path(data_dir).is_dir():
        problems.append(f"{data_dir} does not exist -- run `make eval-data`")
    if problems:
        return report(problems)

    env_file = BACKEND / ".env"
    has_key = bool(os.environ.get("OPENROUTER_API_KEY")) or (
        env_file.is_file()
        and any(
            line.startswith("OPENROUTER_API_KEY=") and line.partition("=")[2].strip().strip("\"'")
            for line in env_file.read_text().splitlines()
        )
    )
    print(f"OpenRouter key: {'set' if has_key else 'MISSING'}")
    if not has_key:
        problems.append("no OPENROUTER_API_KEY in the environment or backend/.env")

    from app.graph import graphdb
    from app.ontology.persistence import get_active_version
    from app.preprocess.goldenset import load_goldenset
    from app.utils.paths import raw_path_for

    raw = raw_path_for(stem)
    if not raw.is_file():
        problems.append(f"no document {stem!r} in {data_dir}")
        return report(problems)
    print(f"document: {len(raw.read_text()):,} characters")
    version = get_active_version(stem)
    print(f"active schema version: {version}")
    if version is None and stage in ("reextract", "score"):
        problems.append("the document has no active schema version")
    graph = graphdb.load_graph(stem, version=version) if version is not None else None
    print("extracted graph:", f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges" if graph else "none")
    if version is not None and graph is None and stage == "score":
        problems.append("the document has a schema but no extracted graph")
    golden = load_goldenset(stem)
    print("golden set:", f"{len(golden['questions'])} questions" if golden else "none (use make golden-eval-generate)")
    return report(problems)


def report(problems: list[str]) -> int:
    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    if not problems:
        print("ready")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
