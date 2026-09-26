"""Background-task tracking for deferred PDF -> Markdown conversion (see
convert_pdf_to_markdown_file in app.preprocess.parser).

Conversion of a large policy PDF (some run 1000+ pages) is pure-Python and
CPU-bound -- measured at ~370s for a 1484-page/30MB document. Running it
inline in a request handler means the one HTTP connection watching it is
exposed to every failure mode that can hit a multi-minute request: a proxy
or browser giving up, or (in local dev) uvicorn's --reload watcher
restarting the worker process because an unrelated file changed -- either
kills the connection out from under the client as a bare "socket hang up"
with zero diagnostic info.

A background *thread* is not enough to fix this -- confirmed experimentally
against a real 1484-page document: for reasons internal to pdfplumber/
pdfminer.six's parsing of that document, the conversion can hold the GIL
long enough (well past a minute, observed) that the *entire* process,
including the asyncio event loop handling every other request, stops
accepting new connections at all -- `podman stats` showed the container's
own CPU time essentially frozen while `GET /health` timed out for 5+
minutes straight, recovering only after a manual container restart. A
thread shares the interpreter (and its one GIL) with everything else in the
process; nothing short of a separate OS process actually isolates a
misbehaving CPU-bound call.

So this runs each conversion in its own subprocess via ProcessPoolExecutor:
a lock-up in the child can never block the parent process's event loop,
whatever the child is doing internally. The POST route submits a job and
returns immediately; GET polls status, lazily reaping the Future's result
(or exception) the next time it's checked -- no callback thread needed.

This is deliberately NOT a durable job queue: task state lives only in this
process's memory, and a task in progress is abandoned (its worker process
is not explicitly killed, but nothing waits on it any more either) if the
parent backend process itself restarts. A poll after that finds no task
and reports "idle" again, which the frontend surfaces as "generation was
interrupted, try again" rather than a hang.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Any

from app.preprocess.parser import convert_pdf_to_markdown_file

# A handful of workers is plenty -- this is for the rare "just downloaded a
# huge PDF" case, not a high-throughput queue, and each worker can itself
# use significant memory on a large document.
_executor = ProcessPoolExecutor(max_workers=2)

_lock = threading.Lock()
_tasks: dict[str, dict[str, Any]] = {}


def start_generate_md(stem: str, original_filename: str, data: bytes) -> bool:
    """Submit `data` (PDF bytes) for conversion in a subprocess, tracked
    under `stem`. Returns False without submitting a new job if one for
    this stem is already running; the caller should treat that as "already
    in progress" rather than spawning a duplicate. A prior "error" or
    missing entry both allow a fresh attempt (retry-friendly)."""
    with _lock:
        existing = _tasks.get(stem)
        if existing and existing["status"] == "running":
            return False
        future = _executor.submit(convert_pdf_to_markdown_file, original_filename, data)
        _tasks[stem] = {
            "status": "running",
            "detail": None,
            "result": None,
            "future": future,
            "started_at": time.time(),
        }
    return True


def _reap(stem: str, task: dict[str, Any]) -> None:
    """Move a finished Future's outcome into `task` in place. Called with
    `_lock` held -- cheap and non-blocking since it only runs once
    `future.done()` is already true."""
    future: Future = task["future"]
    try:
        result = future.result()
    except Exception as e:
        task["status"] = "error"
        task["detail"] = f"PDF 변환 실패: {e}"
    else:
        task["status"] = "done"
        task["result"] = result


def get_generate_md_status(stem: str) -> dict[str, Any]:
    """Current status for `stem`'s conversion task: `idle` (never started,
    or this process never saw it -- e.g. after a restart), `running`,
    `done` (`result` holds convert_pdf_to_markdown_file's return value), or
    `error` (`detail` holds the message)."""
    with _lock:
        task = _tasks.get(stem)
        if task is None:
            return {"status": "idle", "detail": None, "result": None}
        if task["status"] == "running" and task["future"].done():
            _reap(stem, task)
        return {"status": task["status"], "detail": task["detail"], "result": task["result"]}
