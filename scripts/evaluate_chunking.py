#!/usr/bin/env python3
"""Scores the markdown -> chunks stage against each policy's own table of
contents (see backend/app/preprocess/chunking_eval.py).

    scripts/evaluate_chunking.py [--holdout] [--json out.json] [--verbose]

Reads raw markdown only (backend/data/documents/*/raw.md and samples/*.md) and
chunks it in memory; nothing is written under backend/data. No model call, so
it is free and gives the same numbers every run.

The development set is every document except HOLDOUT. Rules are tuned on that
set; --holdout scores only the held-out documents and is run once, at the end.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.preprocess.chunking import chunk_markdown  # noqa: E402
from app.preprocess.chunking_eval import score_chunking  # noqa: E402

# Different product kinds on purpose (term life, indexed health/medical): held
# out so a rule tuned on the rest can be checked on structure it never saw.
HOLDOUT = ("삼성정기보험_2403_약관", "삼성인터넷급여실손의료비보장보험_2607_약관")


def corpus() -> list[tuple[str, Path]]:
    docs = [(path.parent.name, path) for path in sorted((ROOT / "backend/data/documents").glob("*/raw.md"))]
    docs += [(path.stem, path) for path in sorted((ROOT / "samples").glob("삼성*.md"))]
    return docs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--holdout", action="store_true", help="score only the held-out documents")
    parser.add_argument("--json", help="also write the full scores to this file")
    parser.add_argument("--verbose", action="store_true", help="list missing/extra/suspicious names and examples")
    args = parser.parse_args()

    reports = {}
    for name, path in corpus():
        if (name in HOLDOUT) != args.holdout:
            continue
        text = path.read_text(encoding="utf-8")
        reports[name] = score_chunking(chunk_markdown(text, name), text)

    header = f"{'document':34} {'chunks':>6} {'secs':>9} {'laws':>4} {'match':>7} {'fallbk':>6} {'susp':>4} {'cover':>9} {'dup':>3} {'gap':>3} {'regr':>4} {'h.reg':>5}"
    print(header)
    for name, report in reports.items():
        riders, articles, hierarchy = report["riders"], report["articles"], report["hierarchy"]
        coverage = report["coverage"]
        match = f"{riders['matched']}/{riders['toc_riders']}" if riders["toc_available"] else "no-toc"
        expected = f"{riders['sections']}/{1 + riders['toc_riders']}" if riders["toc_available"] else f"{riders['sections']}/?"
        print(
            f"{name[:34]:34} {articles['chunks']:>6} {expected:>9} {riders['appendix_sections']:>4} {match:>7} {riders['fallback_labels']:>6} "
            f"{len(riders['suspicious_labels']):>4} {coverage['chunked']}/{coverage['heading_lines']:<6} {articles['duplicate_articles']:>3} {articles['gaps']:>3} "
            f"{articles['regressions']:>4} {hierarchy['hierarchy_regressions']:>5}"
        )
        if args.verbose:
            for key in ("missing", "extra"):
                if riders.get(key):
                    print(f"    {key}: {riders[key]}")
            if riders["suspicious_labels"]:
                print(f"    suspicious: {riders['suspicious_labels']}")
            for example in coverage["missed_examples"] + articles["regression_examples"] + hierarchy["hierarchy_examples"]:
                print(f"    {example}")

    if args.json:
        Path(args.json).write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
