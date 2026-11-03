"""Tests for the Pura client."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from unittest.mock import MagicMock, patch

import pytest

from pypura import Pura, PuraApiException

START = datetime(2026, 1, 1, tzinfo=UTC)
START_TS = int(START.timestamp())

TIMER_URL = "v3/devices/device/v2/timer"
TIMER_RESPONSE = {"timer": {"bay": 1, "intensity": 5, "start": START_TS}}


def _timer_json(end: int) -> dict[str, bool | int]:
    """Return the expected timer request, starting at the frozen time."""
    return {
        "bay": 1,
        "intensity": 5,
        "start": START_TS,
        "end": end,
        "validateOverride": True,
    }


@pytest.mark.freeze_time(START)
@pytest.mark.parametrize(
    ("end", "seconds"),
    [
        (timedelta(minutes=30), 1800),
        (timedelta(hours=23, minutes=59), 86340),
        (timedelta(days=1), 86400),
        (timedelta(days=1, hours=1), 90000),
        (timedelta(days=3, seconds=5), 259205),
    ],
)
def test_set_timer_timedelta_end(end: timedelta, seconds: int) -> None:
    """Test a timedelta end is added to now in full, including whole days."""
    with patch.object(Pura, "_Pura__post", return_value=TIMER_RESPONSE) as post:
        assert Pura().set_timer("device", bay=1, intensity=5, end=end)

    post.assert_called_once_with(TIMER_URL, json=_timer_json(START_TS + seconds))


@pytest.mark.freeze_time(START)
def test_set_timer_default_end() -> None:
    """Test a timer runs for 30 minutes by default."""
    with patch.object(Pura, "_Pura__post", return_value=TIMER_RESPONSE) as post:
        assert Pura().set_timer("device", bay=1, intensity=5)

    post.assert_called_once_with(TIMER_URL, json=_timer_json(START_TS + 1800))


@pytest.mark.freeze_time(START)
@pytest.mark.parametrize("end", [START + timedelta(hours=1), START_TS + 3600])
def test_set_timer_absolute_end(end: datetime | int) -> None:
    """Test a datetime or Unix timestamp end is used as is."""
    with patch.object(Pura, "_Pura__post", return_value=TIMER_RESPONSE) as post:
        assert Pura().set_timer("device", bay=1, intensity=5, end=end)

    post.assert_called_once_with(TIMER_URL, json=_timer_json(START_TS + 3600))


@pytest.mark.freeze_time(START)
@pytest.mark.parametrize(
    "start", [0, START_TS - 3600, START + timedelta(hours=1), START_TS + 3600]
)
def test_set_timer_start_is_deprecated_and_ignored(start: datetime | int) -> None:
    """Test passing start warns, and the timer still starts now."""
    with (
        patch.object(Pura, "_Pura__post", return_value=TIMER_RESPONSE) as post,
        pytest.warns(DeprecationWarning, match="'start' is deprecated"),
    ):
        assert Pura().set_timer(
            "device", bay=1, intensity=5, start=start, end=timedelta(minutes=1)
        )

    post.assert_called_once_with(TIMER_URL, json=_timer_json(START_TS + 60))


@pytest.mark.freeze_time(START)
@pytest.mark.parametrize(
    "end",
    [timedelta(0), timedelta(seconds=-1), START, START_TS, START_TS - 1],
)
def test_set_timer_rejects_end_not_in_future(end: datetime | timedelta | int) -> None:
    """Test a timer that wouldn't run is rejected before calling the API."""
    with (
        patch.object(Pura, "_Pura__post") as post,
        pytest.raises(PuraApiException, match="must be in the future"),
    ):
        Pura().set_timer("device", bay=1, intensity=5, end=end)
    post.assert_not_called()


def test_set_timer_returns_false_without_timer_in_response() -> None:
    """Test a response without a timer is reported as a failure."""
    with patch.object(Pura, "_Pura__post", return_value={}):
        assert not Pura().set_timer("device", bay=1, intensity=5)


def test_request_accepts_created_status(caplog: pytest.LogCaptureFixture) -> None:
    """Test a 201 response from the API isn't treated as an error."""
    response = MagicMock(ok=True, status_code=201)
    response.json.return_value = TIMER_RESPONSE
    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", return_value=response),
        caplog.at_level(logging.ERROR),
    ):
        assert Pura().set_timer("device", bay=1, intensity=5)

    assert not caplog.records
