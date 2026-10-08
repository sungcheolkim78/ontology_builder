#!/usr/bin/env python3
"""Scores a document's golden set against its extracted graph (see
backend/app/ontology/retrieval_eval.py): for every answerable golden question,
did the search the chatbot uses return nodes and edges that cite the passage
that answers it -- and if not, was the passage never extracted, or extracted but
not retrieved?

    ONTOLOGY_DATA_DIR=/path/to/a/COPY/of/backend/data \\
        scripts/evaluate_golden_set.py "<document stem>" [--generate 20] [--version 3] [--hops 1] [--json]

ONTOLOGY_DATA_DIR is required and there is no default, on purpose: this opens the
graph database and, with --generate, writes goldenset.json, so pointing it at the
live backend/data while the backend container is up could corrupt that database.
Work on a copy (scripts/backup_data.sh makes a snapshot).

--generate N writes a fresh N-question golden set first (two LLM calls). Scoring
costs one LLM call per answerable question (the question analysis). The API key and
model are read from backend/.env (OPENROUTER_* entries only; nothing is printed).
"""

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))


def load_openrouter_settings() -> None:
    """Copies OPENROUTER_* entries of backend/.env into the environment unless the
    caller already set them. Only those: this script has no business with the
    tracing keys or anything else the file holds."""
    env_file = BACKEND / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key.startswith("OPENROUTER_") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def print_report(report: dict) -> None:
    summary = report["summary"]
    print(f"\n{report['stem']}  schema v{report['version']}  hops={report['hops']}")
    graph = summary["graph"]
    node_share = graph["nodes_with_evidence"] / graph["nodes"] if graph["nodes"] else 0
    edge_share = graph["edges_with_evidence"] / graph["edges"] if graph["edges"] else 0
    print(
        f"graph: {graph['nodes']} nodes ({graph['nodes_with_evidence']} cite a passage, {node_share:.0%}), "
        f"{graph['edges']} edges ({graph['edges_with_evidence']} cite a passage, {edge_share:.0%})"
    )
    outcomes = summary["outcomes"]
    print("outcomes:", ", ".join(f"{name}={count}" for name, count in outcomes.items()))
    if summary["scored"]:
        print(
            f"scored {summary['scored']}: hit rate {summary['hit_rate']:.0%}, "
            f"mean evidence recall {summary['mean_recall']:.0%}, "
            f"{summary['repeated_passages']} answering passage(s) quote text that appears more than once"
        )
        print("by question type:")
        for question_type, entry in sorted(summary["by_question_type"].items()):
            print(f"  {question_type:12} {entry['hits']}/{entry['scored']} hit")
    else:
        print("nothing could be scored")
    print("\nper question:")
    for question in report["questions"]:
        recall = "-" if question["recall"] is None else f"{question['recall']:.2f}"
        print(f"  {question['id']}  {question['outcome']:16} recall={recall}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stem", help="the document's stem (its folder name under documents/)")
    parser.add_argument("--generate", type=int, metavar="N", help="generate an N-question golden set first")
    parser.add_argument("--version", type=int, help="schema version to score (default: the active one)")
    parser.add_argument("--hops", type=int, default=1, help="graph expansion hops, as in chat (default 1)")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON instead")
    args = parser.parse_args()

    if not os.environ.get("ONTOLOGY_DATA_DIR"):
        print(
            "ONTOLOGY_DATA_DIR is not set. Point it at a COPY of backend/data -- see this "
            "script's docstring for why there is no default.",
            file=sys.stderr,
        )
        return 2
    load_openrouter_settings()

    from app.ontology.retrieval_eval import score_golden_set
    from app.preprocess.goldenset import generate_goldenset, save_goldenset
    from app.utils.paths import raw_path_for

    if args.generate:
        report = generate_goldenset(
            raw_path_for(args.stem).read_text(), source_name=f"{args.stem}.md", question_count=args.generate
        )
        save_goldenset(args.stem, report)
        print(f"generated {len(report['questions'])} golden questions", file=sys.stderr)

    report = score_golden_set(args.stem, version=args.version, hops=args.hops)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
