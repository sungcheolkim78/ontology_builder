#!/usr/bin/env python3
"""Tries a variant of the extraction instructions on exactly the golden passages the
extracted graph could not answer: re-extracts the chunk (or chunk group) holding each
one twice, with the current prompt and with the variant appended, and reports for each
question whether the new nodes and edges state the facts of the golden answer
(backend/app/ontology/reextraction_probe.py). No scoring run, no search: about two LLM
calls per distinct chunk.

    ONTOLOGY_DATA_DIR=/path/to/a/COPY/of/backend/data \\
        scripts/probe_prompt_variant.py "<document stem>" --variant scripts/prompt_variants/extract_clause_content.txt \\
        [--questions q016,q025,...] [--version 7] [--granularity chunk|group] [--save-dir DIR]

Like the other evaluation scripts it refuses to run without ONTOLOGY_DATA_DIR, which must
not be the live backend/data.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
BACKEND = SCRIPTS.parent / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))

# Golden questions no extraction of the measured document ever answered (docs/ontology/golden_set_evaluation.md)
NEVER_ANSWERED = "q016,q025,q026,q028,q038,q041,q043,q046,q047"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stem")
    parser.add_argument("--variant", required=True, help="a text file appended to the extraction system prompt")
    parser.add_argument("--questions", default=NEVER_ANSWERED, help="comma-separated golden question ids")
    parser.add_argument("--version", type=int, help="schema version (default: the active one)")
    parser.add_argument("--granularity", choices=["chunk", "group"], default="chunk")
    parser.add_argument("--save-dir", metavar="DIR", help="also write the comparison to DIR/<time>.json")
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

    from app.ontology.persistence import get_active_version
    from app.ontology.reextraction_probe import probe_extraction_misses
    from app.preprocess.goldenset import load_goldenset

    variant = Path(args.variant).read_text().strip()
    version = args.version or get_active_version(args.stem)
    ids = [q.strip() for q in args.questions.split(",") if q.strip()]
    golden = {q["id"]: q for q in (load_goldenset(args.stem) or {"questions": []})["questions"]}
    unknown = [i for i in ids if i not in golden]
    if unknown:
        print(f"no such golden question: {', '.join(unknown)}", file=sys.stderr)
        return 2
    report = {"version": version, "questions": [{"id": i, "outcome": "extraction_miss"} for i in ids]}

    from run_record import RunRecord

    arms = {}
    with RunRecord("probe_prompt_variant", stem=args.stem, variant=args.variant, version=version) as rec:
        for name, extra in (("current prompt", None), ("variant", variant)):
            print(f"extracting with the {name}...", file=sys.stderr)
            with rec.phase(name):
                arms[name] = probe_extraction_misses(
                    args.stem, report, granularity=args.granularity, extra_instructions=extra
                )
        run = rec.snapshot()

    base, new = (arms["current prompt"], arms["variant"])
    print(f"\n{args.stem}  schema v{version}  by {args.granularity}   (answer = the new nodes state the golden answer's facts)")
    print(f"{'':6} {'type':10} {'current':12} {'variant':12}")
    for before, after in zip(base["questions"], new["questions"]):
        mark = lambda q: ("answer" if q["answer"] else "-") + ("+quote" if q["outcome"] == "recovered" else "")
        print(f"{before['id']:6} {golden[before['id']]['question_type']:10} {mark(before):12} {mark(after):12}")
    for name, arm in arms.items():
        s = arm["summary"]
        print(f"{name:15} answer {s['answer_recovered']}/{s['probed']}, quoting {s['recovered']}, semantic {s['semantic_recovered']}, {s['llm_calls']} LLM call(s)")

    if args.save_dir:
        save_dir = Path(args.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        saved = save_dir / f"prompt_variant_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        saved.write_text(json.dumps({"options": vars(args), "variant": variant, "run": run, **arms}, ensure_ascii=False, indent=2))
        print(f"\nsaved to {saved}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
