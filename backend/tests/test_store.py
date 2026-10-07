"""Tests for app.utils.store, the one place that reads and writes the files
under backend/data: atomic writes, JSON reads with a default, and a per-key
lock for read-modify-write."""

import threading
import time

import pytest

from app.utils.store import locked, read_json, write_json


@pytest.fixture
def work(tmp_path):
    """A folder of the test's own: tmp_path also holds the data directory the
    suite's autouse fixture creates, which these tests must not see."""
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def test_write_json_creates_missing_folders_and_round_trips_korean_text(work):
    path = work / "documents" / "doc_raw" / "versions.json"

    write_json(path, {"name": "보험약관", "versions": [1, 2]})

    assert read_json(path) == {"name": "보험약관", "versions": [1, 2]}
    # ensure_ascii=False: the file itself is readable, not \u-escaped
    assert "보험약관" in path.read_text()


def test_read_json_returns_the_default_for_a_missing_file(work):
    assert read_json(work / "nope.json") is None
    assert read_json(work / "nope.json", default=[]) == []


def test_a_failed_replace_leaves_the_old_file_intact_and_no_temp_file_behind(work, monkeypatch):
    path = work / "versions.json"
    write_json(path, {"version": 1})

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("app.utils.store.os.replace", failing_replace)

    with pytest.raises(OSError):
        write_json(path, {"version": 2})

    assert read_json(path) == {"version": 1}
    assert [p.name for p in work.iterdir()] == ["versions.json"]


def test_concurrent_writers_to_one_path_never_fail_and_leave_one_whole_value(work):
    path = work / "progress.json"
    errors = []

    def writer(worker):
        try:
            for attempt in range(40):
                write_json(path, {"worker": worker, "attempt": attempt})
        except Exception as exc:  # noqa: BLE001 -- collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert set(read_json(path)) == {"worker", "attempt"}  # a whole value, not a torn one
    assert [p.name for p in work.iterdir()] == ["progress.json"]


def test_a_locked_read_modify_write_loses_no_update(work):
    path = work / "counter.json"
    write_json(path, {"n": 0})

    def bump():
        for _ in range(25):
            with locked("doc_raw"):
                current = read_json(path)
                time.sleep(0.0005)  # widen the window an unlocked version would race in
                write_json(path, {"n": current["n"] + 1})

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert read_json(path) == {"n": 8 * 25}
