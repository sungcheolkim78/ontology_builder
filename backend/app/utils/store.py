"""Reads and writes the files under backend/data (see app.utils.paths for where
they live).

Every write goes to a temp file next to its target and is then renamed over it
(`os.replace`), so a reader -- a request thread, or the process converting a
PDF -- sees the old file or the new one, never a half-written one, and a write
that fails part-way leaves the old file untouched.
"""

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique per process and thread: two writers to the same target at once
    # (a double-clicked button, two tabs) must not rename each other's temp
    # file away.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_text(path: Path, text: str) -> None:
    write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, value) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False))


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text())


_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


@contextmanager
def locked(key: str):
    """Serializes read-modify-write sections that share `key` (a document's
    stem, or a domain's name) -- e.g. computing the next schema version from
    versions.json and writing it back. Re-entrant, so a locked function can
    call another that takes the same key. In-process only: the server is one
    process whose request threads are what actually overlap, and the one other
    process that writes here (the PDF converter) only replaces whole files."""
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        yield
