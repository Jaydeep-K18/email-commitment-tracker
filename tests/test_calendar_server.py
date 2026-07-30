"""Tests for the local calendar server endpoints."""
from __future__ import annotations

from icalendar import Calendar as ICalendar

from src.server.calendar_server import app


def _client():
    from fastapi.testclient import TestClient

    return TestClient(app)


def test_health_endpoint_reports_ok():
    with _client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "calendar.ics" in payload["calendar_url"]
    assert isinstance(payload["calendar_events"], int)


def test_calendar_endpoint_serves_a_valid_ics_feed():
    with _client() as client:
        response = client.get("/calendar.ics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.content.startswith(b"BEGIN:VCALENDAR")
    # Must actually parse as a calendar, not merely look like one.
    ICalendar.from_ical(response.content)


def test_calendar_endpoint_asks_subscribers_not_to_cache():
    with _client() as client:
        response = client.get("/calendar.ics")
    assert "no-cache" in response.headers.get("cache-control", "")


def test_unknown_path_is_not_served():
    with _client() as client:
        assert client.get("/secrets").status_code == 404
