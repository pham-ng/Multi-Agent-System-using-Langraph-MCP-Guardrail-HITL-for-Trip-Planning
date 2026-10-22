from datetime import date

import backend


def test_calendar_is_deterministic():
    assert backend._explicit_travel_dates("21 Oct 2026") == [date(2026, 10, 21)]
    assert "Wednesday" in backend._normalize_calendar_claims("Sunday, 21 Oct 2026")


def test_no_date_request_never_gets_calendar_date():
    context = backend._calendar_context("2 ngày ở Hà Nội")
    assert context["explicit_dates_provided"] is False
    assert "Day 1" in context["instruction"]


def test_grounding_block_is_explicit():
    message = backend._grounding_block(["missing source"])
    assert "dừng" in message
    assert "missing source" in message
