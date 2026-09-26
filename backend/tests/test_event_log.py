import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.utils.event_log import MAX_EVENTS, clear_events, get_events, log_event


@pytest.fixture(autouse=True)
def _reset_event_log():
    clear_events()
    yield
    clear_events()


def test_log_event_is_returned_by_get_events():
    log_event("command", "GET /api/documents")

    events = get_events()

    assert len(events) == 1
    assert events[0]["kind"] == "command"
    assert events[0]["message"] == "GET /api/documents"
    assert events[0]["timestamp"]


def test_get_events_since_only_returns_newer_events():
    log_event("command", "first")
    log_event("result", "second")
    since = get_events()[0]["id"]

    events = get_events(since=since)

    assert [e["message"] for e in events] == ["second"]


def test_get_events_is_capped_at_max_events():
    for i in range(MAX_EVENTS + 10):
        log_event("command", f"event {i}")

    events = get_events()

    assert len(events) == MAX_EVENTS
    assert events[0]["message"] == "event 10"
    assert events[-1]["message"] == f"event {MAX_EVENTS + 9}"


def test_events_endpoint_logs_a_command_and_a_result_for_a_successful_request():
    client = TestClient(app)

    client.get("/api/hello")
    events = client.get("/api/events").json()["events"]

    messages = [e["message"] for e in events]
    assert "GET /api/hello" in messages
    assert "GET /api/hello -> HTTP 200" in messages
    kinds_by_message = {e["message"]: e["kind"] for e in events}
    assert kinds_by_message["GET /api/hello"] == "command"
    assert kinds_by_message["GET /api/hello -> HTTP 200"] == "result"


def test_events_endpoint_logs_an_error_for_a_failed_request():
    client = TestClient(app)

    client.get("/api/documents/does-not-exist.md/summary")
    events = client.get("/api/events").json()["events"]

    error_events = [e for e in events if e["kind"] == "error"]
    assert any("HTTP 404" in e["message"] for e in error_events)


def test_events_endpoint_polling_itself_is_not_logged():
    client = TestClient(app)

    client.get("/api/events")
    events = client.get("/api/events").json()["events"]

    assert events == []
