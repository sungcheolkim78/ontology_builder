#!/usr/bin/env python3
"""Scores a document's golden set against its extracted graph (see
backend/app/ontology/retrieval_eval.py): for every answerable golden question,
did the search the chatbot uses return nodes and edges that cite the passage
that answers it -- and if not, was the passage never extracted, or extracted but
not retrieved?

    ONTOLOGY_DATA_DIR=/path/to/a/COPY/of/backend/data \\
        scripts/evaluate_golden_set.py "<document stem>" [--generate 50 [--context-chars 60000]] [--version 3] [--hops 1] [--probe-misses [group|chunk]] [--judge] [--json]

ONTOLOGY_DATA_DIR is required and there is no default, on purpose: this opens the
graph database and, with --generate, writes goldenset.json, so pointing it at the
live backend/data while the backend container is up could corrupt that database.
Work on a copy (scripts/backup_data.sh makes a snapshot).

--generate N writes a fresh N-question golden set first: two LLM calls per batch of up to
10 questions, each batch shown a different window of the document (--context-chars sets
how much of it one call sees; a bigger view means fewer, better-grounded windows). A golden
set it replaces is kept beside it as goldenset.{time}.json. Scoring
costs one LLM call per answerable question (the question analysis). The API key and
model are read from backend/.env (OPENROUTER_* entries only; nothing is printed).

--probe-misses then re-extracts, under the same schema, only the chunk groups that hold
a passage an extraction miss needed (one LLM call per such group; see
backend/app/ontology/reextraction_probe.py) and says whether the second extraction
cites it: `recovered` means the schema could hold the fact and the first run left it
out, `still_missed` means this schema and prompt do not get it out of the text.
"""

import argparse
import json
import os
import sys
from datetime import datetime
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
    semantic = summary.get("semantic")
    if semantic:
        print(
            f"\nsemantic reading (a node or edge within cosine {semantic['threshold']} of the passage counts as citing it):"
        )
        print("outcomes:", ", ".join(f"{name}={count}" for name, count in semantic["outcomes"].items()))
        if semantic["scored"]:
            print(
                f"hit rate {semantic['hit_rate']:.0%} (strict {summary['hit_rate']:.0%}), "
                f"mean evidence recall {semantic['mean_recall']:.0%} (strict {summary['mean_recall']:.0%})"
            )
    answer = summary.get("answer")
    if answer and answer["scored"]:
        print("\nanswer reading (do the nodes the search returned state the facts of the golden answer?):")
        print("outcomes:", ", ".join(f"{name}={count}" for name, count in answer["outcomes"].items()))
        print(f"hit rate {answer['hit_rate']:.0%}, mean fact recall {answer['mean_recall']:.0%}")
    judge = summary.get("judge")
    if judge and judge["scored"]:
        print(
            f"\njudge reading (an LLM, shown only what the chatbot would be shown): "
            f"{judge['answerable']}/{judge['scored']} answerable ({judge['rate']:.0%})"
        )
    header = "   strict" + ("  |  semantic" if semantic else "") + ("  |  answer" if answer else "") + ("  |  judge" if judge else "")
    print("\nper question:" + header)
    for question in report["questions"]:
        recall = "-" if question["recall"] is None else f"{question['recall']:.2f}"
        line = f"  {question['id']}  {question['outcome']:16} {recall:>4}"
        for key in ("semantic", "answer"):
            if key in question and (key != "semantic" or semantic) and (key != "answer" or answer):
                other = question[key]
                line += f"  |  {other['outcome']:16} {'-' if other['recall'] is None else format(other['recall'], '.2f'):>4}"
        if "judge" in question:
            line += f"  |  {'answerable' if question['judge']['answerable'] else 'NOT answerable'}"
        print(line)


def print_probe(probe: dict) -> None:
    summary = probe["summary"]
    print(
        f"\nre-extraction, by {probe['granularity']}, of the text behind {summary['probed']} extraction miss(es) "
        f"({summary['llm_calls']} LLM call(s)): {summary['recovered']} recovered (a new node quotes the passage), "
        f"{summary['still_missed']} still missed, {summary['not_locatable']} not locatable; "
        f"{summary['semantic_recovered']} have a new node or edge that says it (semantic)"
    )
    for question in probe["questions"]:
        said = "says it" if question["semantic"] else "-"
        print(f"  {question['id']}  {question['outcome']:14} {said:8} {probe['granularity']}s={question['groups']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stem", help="the document's stem (its folder name under documents/)")
    parser.add_argument("--generate", type=int, metavar="N", help="generate an N-question golden set first")
    parser.add_argument(
        "--context-chars", type=int, default=None, metavar="N",
        help="characters of the document one generation call sees (default: the module's own, 24000)",
    )
    parser.add_argument("--version", type=int, help="schema version to score (default: the active one)")
    parser.add_argument("--hops", type=int, default=1, help="graph expansion hops, as in chat (default 1)")
    parser.add_argument(
        "--semantic-threshold", type=float, default=None, metavar="COSINE",
        help="cosine similarity at which a node or edge counts as citing a passage it does not quote "
        "(default 0.75; 0 or less turns the semantic reading off)",
    )
    parser.add_argument(
        "--probe-misses",
        choices=["group", "chunk"],
        nargs="?",
        const="group",
        help="re-extract the text behind the extraction misses -- the chunk group (default) or only the "
        "chunk(s) quoting the passage -- and report which passages come back",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="also ask an LLM, per answerable question, whether the context the search returned can answer it "
        "(one extra LLM call each)",
    )
    parser.add_argument(
        "--save-dir", metavar="DIR",
        help="also write the full report (scores, probe, the options used) to DIR/<time>.json",
    )
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
    from app.preprocess.goldenset import DEFAULT_CONTEXT_CHARS, generate_goldenset, save_goldenset
    from app.utils.paths import raw_path_for

    if args.generate:
        report = generate_goldenset(
            raw_path_for(args.stem).read_text(),
            source_name=f"{args.stem}.md",
            question_count=args.generate,
            question_context_chars=args.context_chars or DEFAULT_CONTEXT_CHARS,
        )
        save_goldenset(args.stem, report)
        print(f"generated {len(report['questions'])} golden questions", file=sys.stderr)

    threshold = {} if args.semantic_threshold is None else {
        "semantic_threshold": args.semantic_threshold if args.semantic_threshold > 0 else None
    }
    report = score_golden_set(args.stem, version=args.version, hops=args.hops, judge=args.judge, **threshold)
    probe = None
    if args.probe_misses:
        from app.ontology.reextraction_probe import probe_extraction_misses

        probe = probe_extraction_misses(args.stem, report, granularity=args.probe_misses)
    full = {**report, "probe": probe} if probe else report
    if args.save_dir:
        save_dir = Path(args.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        saved = save_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        saved.write_text(
            json.dumps(
                {"saved_at": datetime.now().isoformat(), "options": vars(args), **full},
                ensure_ascii=False, indent=2,
            )
        )
        print(f"\nsaved the full report to {saved}", file=sys.stderr)
    if args.json:
        print(json.dumps(full, ensure_ascii=False, indent=2))
    else:
        print_report(report)
        if probe:
            print_probe(probe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
