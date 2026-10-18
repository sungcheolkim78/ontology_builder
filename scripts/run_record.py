"""A record of one evaluation run, shared by the scripts here: when it ran, how long
each phase took, which model(s) answered, how many LLM calls were made and how many
tokens they used, the git commit, and the settings that change cost (chunk-group
budgets, concurrency). It is kept next to the scores so that two runs can be compared
on time and cost as well as on results.

    with RunRecord("reextract_for_eval", save_dir=args.save_dir, stem=args.stem) as rec:
        with rec.phase("extract"):
            ...
        rec.meta["version"] = version        # anything worth keeping with the run

On exit it prints a one-line summary to stderr and, when `save_dir` is given, writes
`run_<script>_<time>.json` there. A script that already writes a report of its own
passes no `save_dir` and embeds `rec.snapshot()` in it instead. Import this after
ONTOLOGY_DATA_DIR and any group-size variable are set: it reads the settings the app
actually loaded.
"""

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


class RunRecord:
    def __init__(self, script: str, save_dir: str | None = None, **meta):
        self.script = script
        self.save_dir = Path(save_dir) if save_dir else None
        self.meta = dict(meta)
        self.phases: dict[str, float] = {}
        self._log = None
        self._started = None
        self._started_at = None
        self.saved_path: Path | None = None

    def __enter__(self):
        from app.llm.call_log import CallLog

        self._log = CallLog().__enter__()
        self._started = time.perf_counter()
        self._started_at = datetime.now()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._log.__exit__(exc_type, exc, tb)
        snapshot = self.snapshot()
        if exc_type is not None:
            snapshot["failed"] = f"{exc_type.__name__}: {exc}"
        if self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            self.saved_path = self.save_dir / f"run_{self.script}_{self._started_at.strftime('%Y%m%d_%H%M%S')}.json"
            self.saved_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        print(self.describe(snapshot), file=sys.stderr)
        return False

    @contextmanager
    def phase(self, name: str):
        """Times a named part of the run; a phase entered twice adds up."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.phases[name] = self.phases.get(name, 0.0) + time.perf_counter() - started

    def snapshot(self) -> dict:
        from app.llm.chat import get_model_name
        from app.ontology import chunk_groups
        from app.preprocess.embeddings import get_embedding_model_name

        llm = self._log.summary()
        configured = {op: get_model_name(op) for op in sorted({c["operation"] for c in self._log.calls})}
        return {
            "script": self.script,
            "started_at": self._started_at.isoformat(timespec="seconds"),
            "elapsed_seconds": round(time.perf_counter() - self._started, 2),
            "phases": {name: round(seconds, 2) for name, seconds in self.phases.items()},
            "git": {"commit": _git("rev-parse", "--short", "HEAD"), "dirty": bool(_git("status", "--porcelain"))},
            "settings": {
                "default_model": get_model_name(),
                "embedding_model": get_embedding_model_name(),
                "OPENROUTER_MODEL": os.environ.get("OPENROUTER_MODEL"),
                "EXTRACT_GROUP_CHARS": chunk_groups.EXTRACT_GROUP_CHARS,
                "MAX_CHUNK_GROUP_CHARS": chunk_groups.MAX_CHUNK_GROUP_CHARS,
                "MAX_CONCURRENT_LLM_CALLS": chunk_groups.MAX_CONCURRENT_LLM_CALLS,
            },
            "llm": {**llm, "seconds_in_calls": round(llm["seconds_in_calls"], 2), "configured_models": configured,
                    "by_operation": {
                        name: {k: round(v, 2) if isinstance(v, float) else v for k, v in op.items()}
                        for name, op in llm["by_operation"].items()
                    }},
            **({"meta": self.meta} if self.meta else {}),
        }

    @staticmethod
    def describe(snapshot: dict) -> str:
        llm = snapshot["llm"]
        phases = ", ".join(f"{name} {seconds:.0f}s" for name, seconds in snapshot["phases"].items())
        models = ", ".join(llm["models"]) or "none"
        return (
            f"run: {snapshot['script']} took {snapshot['elapsed_seconds']:.0f}s"
            + (f" ({phases})" if phases else "")
            + f"; {llm['calls']} LLM call(s)"
            + (f", {llm['errors']} failed" if llm["errors"] else "")
            + f" ({llm['seconds_in_calls']:.0f}s waiting on the provider, {llm['input_tokens']:,} tokens in / "
            f"{llm['output_tokens']:,} out); model: {models}"
        )
