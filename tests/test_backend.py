from datetime import date

import backend


def test_calendar_is_deterministic():
    assert backend._explicit_travel_dates("21 Oct 2026") == [date(2026, 10, 21)]
    assert "Wednesday" in backend._normalize_calendar_claims("Sunday, 21 Oct 2026")


def test_no_date_request_never_gets_calendar_date():
    context = backend._calendar_context("2 ngày ở Hà Nội")
    assert context["explicit_dates_provided"] is False
    assert "Day 1" in context["instruction"]


def test_unrequested_calendar_dates_are_removed_from_draft():
    text = "Day 2 – Sunday, 21 Oct 2026\nWeather snapshot: 2026‑10‑22"
    sanitized = backend._strip_unrequested_calendar_dates(text, "Plan a 2 day trip to Da Nang")
    assert "21 Oct 2026" not in sanitized
    assert "2026‑10‑22" not in sanitized
    assert "Day 2" in sanitized


def test_blocked_grounding_has_blocked_status():
    assert backend._status_for_values({"grounding_status": "blocked"}) == "blocked"


def test_grounding_block_is_explicit():
    message = backend._grounding_block(["missing source"])
    assert "dừng" in message
    assert "missing source" in message
