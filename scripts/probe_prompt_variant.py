#!/usr/bin/env python3
"""Tries ways of closing an extraction's gaps on exactly the golden passages it missed:
re-extracts the chunk (or chunk group) holding each one under several arms and reports,
for each question, whether the new nodes and edges state the facts of the golden answer
(backend/app/ontology/reextraction_probe.py). No scoring run, no search.

Arms, all on the same texts under the same schema:
  control N     the current prompt, N times (--control-repeats): how much a re-extraction
                varies by itself, so that an arm is judged against that and not against luck
  <variant>     the current prompt with the text of a --variant file appended
  second pass   (--second-pass) the current prompt, then a second call that is shown what
                came out and asked for only what is missing (extract_graph.supplement_graph)

    ONTOLOGY_DATA_DIR=/path/to/a/COPY/of/backend/data \\
        scripts/probe_prompt_variant.py "<document stem>" --from-report backups/eval_runs/<scores>.json \\
        --variant scripts/prompt_variants/extract_clause_coverage.txt --second-pass

The questions are those the report (a scoring run of the active version) classed as extraction
misses, or the ids given with --questions. An arm is worth a full re-extraction when it states
the facts of at least 4 more questions than the controls do on average and none that both
controls stated. Like the other evaluation scripts it refuses to run without ONTOLOGY_DATA_DIR,
which must not be the live backend/data.
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

MIN_GAIN = 4  # questions beyond the controls' mean before an arm is worth a full run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stem")
    parser.add_argument("--variant", action="append", default=[], metavar="FILE", help="text appended to the extraction prompt (repeatable)")
    parser.add_argument("--second-pass", action="store_true", help="also try the supplement pass")
    parser.add_argument("--control-repeats", type=int, default=2, metavar="N", help="runs of the unchanged prompt (default 2)")
    parser.add_argument("--from-report", metavar="FILE", help="a scoring report: probe the questions it classed as extraction misses")
    parser.add_argument("--questions", metavar="IDS", help="comma-separated golden question ids (instead of --from-report)")
    parser.add_argument("--version", type=int, help="schema version (default: the report's, else the active one)")
    parser.add_argument("--granularity", choices=["chunk", "group"], default="chunk")
    parser.add_argument("--save-dir", metavar="DIR", help="also write the comparison to DIR/<time>.json")
    args = parser.parse_args()

    if not args.from_report and not args.questions:
        print("give --from-report or --questions", file=sys.stderr)
        return 2
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

    golden = {q["id"]: q for q in (load_goldenset(args.stem) or {"questions": []})["questions"]}
    version = args.version
    if args.from_report:
        scored = json.loads(Path(args.from_report).read_text())
        version = version or scored["version"]
        ids = [q["id"] for q in scored["questions"] if q["answer"]["outcome"] == "extraction_miss"]
    else:
        ids = [q.strip() for q in args.questions.split(",") if q.strip()]
    version = version or get_active_version(args.stem)
    unknown = [i for i in ids if i not in golden]
    if unknown:
        print(f"no such golden question: {', '.join(unknown)}", file=sys.stderr)
        return 2
    report = {"version": version, "questions": [{"id": i, "outcome": "extraction_miss"} for i in ids]}

    arms_to_run = [(f"control {n + 1}", {}) for n in range(args.control_repeats)]
    arms_to_run += [(Path(f).stem, {"extra_instructions": Path(f).read_text().strip()}) for f in args.variant]
    if args.second_pass:
        arms_to_run.append(("second pass", {"second_pass": True}))

    from run_record import RunRecord

    arms = {}
    with RunRecord("probe_prompt_variant", stem=args.stem, version=version, questions=len(ids), arms=[n for n, _ in arms_to_run]) as rec:
        for name, options in arms_to_run:
            print(f"extracting: {name}...", file=sys.stderr)
            with rec.phase(name):
                arms[name] = probe_extraction_misses(args.stem, report, granularity=args.granularity, **options)
        run = rec.snapshot()

    controls = [a for n, a in arms.items() if n.startswith("control ")]
    stated = {n: {q["id"] for q in a["questions"] if q["answer"]} for n, a in arms.items()}
    names = list(arms)
    print(f"\n{args.stem}  schema v{version}  by {args.granularity}   {len(ids)} questions the extraction missed")
    print(f"(a mark means the new nodes state the golden answer's facts)\n{'':6} {'type':10} " + " ".join(f"{n[:14]:14}" for n in names))
    for i in ids:
        print(f"{i:6} {golden[i]['question_type']:10} " + " ".join(f"{('answer' if i in stated[n] else '-'):14}" for n in names))
    print()
    mean = sum(len(stated[n]) for n in names if n.startswith("control ")) / max(1, len(controls))
    both_controls = set.intersection(*[stated[n] for n in names if n.startswith("control ")]) if controls else set()
    for n in names:
        s = arms[n]["summary"]
        line = f"{n:28} states {len(stated[n]):2}/{len(ids)}  ({s['llm_calls']} LLM call(s))"
        if not n.startswith("control "):
            gain, lost = len(stated[n]) - mean, sorted(both_controls - stated[n])
            verdict = "WORTH A FULL RUN" if gain >= MIN_GAIN and not lost else "not worth a full run"
            line += f"   vs controls' mean {mean:.1f}: {gain:+.1f}" + (f", lost {lost}" if lost else "") + f"  -> {verdict}"
        print(line)

    if args.save_dir:
        save_dir = Path(args.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        saved = save_dir / f"prompt_variant_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        saved.write_text(json.dumps({"options": vars(args), "questions": ids, "run": run, "arms": arms}, ensure_ascii=False, indent=2))
        print(f"\nsaved to {saved}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
