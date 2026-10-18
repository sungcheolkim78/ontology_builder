"""A record of the LLM calls made during one run: which model, how long, how many
tokens. Wrap a block in `with CallLog() as log:`; every call that goes through
app.llm.calls inside it -- chat (call_json/call_text) or embedding -- is timed and
noted, and `log.summary()` totals them per operation.

It works by swapping the two telemetry wrappers `app.llm.calls` calls for timing
ones while the block runs and restoring them after, so it needs nothing from the
call sites and changes nothing for a process that never uses it (nothing in the
server does; the evaluation scripts do, see scripts/run_record.py). Calls overlap
(map_concurrently runs several at once), so `seconds_in_calls` can exceed the
wall-clock time of the block: it is the time spent waiting on the provider, summed."""

import threading
import time

from app.llm import calls


class CallLog:
    def __init__(self):
        self.calls: list[dict] = []
        self._lock = threading.Lock()
        self._originals = None

    def __enter__(self):
        self._originals = (calls.invoke_with_telemetry, calls.embed_with_telemetry)
        invoke, embed = self._originals

        def timed_invoke(operation, model, prompt, *args, **kwargs):
            return self._record("chat", operation, model, lambda: invoke(operation, model, prompt, *args, **kwargs))

        def timed_embed(operation, model, texts, *args, **kwargs):
            return self._record("embedding", operation, model, lambda: embed(operation, model, texts, *args, **kwargs))

        calls.invoke_with_telemetry, calls.embed_with_telemetry = timed_invoke, timed_embed
        return self

    def __exit__(self, *exc):
        calls.invoke_with_telemetry, calls.embed_with_telemetry = self._originals
        return False

    def _record(self, kind: str, operation: str, model, run):
        entry = {
            "kind": kind,
            "operation": operation,
            "model": str(getattr(model, "model_name", None) or getattr(model, "model", "unknown")),
            "input_tokens": 0,
            "output_tokens": 0,
            "error": None,
        }
        started = time.perf_counter()
        try:
            result = run()
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
            raise
        else:
            usage = getattr(result, "usage_metadata", None) if kind == "chat" else None
            if usage:
                entry["input_tokens"] = usage.get("input_tokens", 0)
                entry["output_tokens"] = usage.get("output_tokens", 0)
            return result
        finally:
            entry["seconds"] = time.perf_counter() - started
            with self._lock:
                self.calls.append(entry)

    def summary(self) -> dict:
        """Totals over every call so far: how many, how many failed, the models used,
        the time spent in calls, and the same per operation."""
        by_operation: dict[str, dict] = {}
        for call in self.calls:
            op = by_operation.setdefault(
                call["operation"],
                {"calls": 0, "errors": 0, "seconds_total": 0.0, "seconds_max": 0.0, "input_tokens": 0, "output_tokens": 0},
            )
            op["calls"] += 1
            op["errors"] += call["error"] is not None
            op["seconds_total"] += call["seconds"]
            op["seconds_max"] = max(op["seconds_max"], call["seconds"])
            op["input_tokens"] += call["input_tokens"]
            op["output_tokens"] += call["output_tokens"]
        for op in by_operation.values():
            op["seconds_mean"] = op["seconds_total"] / op["calls"]
        return {
            "calls": len(self.calls),
            "errors": sum(call["error"] is not None for call in self.calls),
            "models": sorted({call["model"] for call in self.calls}),
            "seconds_in_calls": sum(call["seconds"] for call in self.calls),
            "input_tokens": sum(call["input_tokens"] for call in self.calls),
            "output_tokens": sum(call["output_tokens"] for call in self.calls),
            "by_operation": by_operation,
        }
