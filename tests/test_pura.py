"""Tests for the Pura client."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from pypura import Pura, PuraApiException

START = datetime(2026, 1, 1, tzinfo=UTC)
START_TS = int(START.timestamp())


@pytest.mark.parametrize(
    "end",
    [
        timedelta(minutes=30),
        timedelta(hours=23, minutes=59),
        timedelta(days=1),
        timedelta(days=1, hours=1),
        timedelta(days=3, seconds=5),
    ],
)
def test_set_timer_timedelta_end(end: timedelta) -> None:
    """Test a timedelta end is added to start in full, including whole days."""
    with patch.object(Pura, "_Pura__post", return_value={"success": True}) as post:
        assert Pura().set_timer("device", bay=1, intensity=5, start=START, end=end)

    post.assert_called_once_with(
        "devices/device/timer",
        json={
            "bay": 1,
            "intensity": 5,
            "start": START_TS,
            "end": START_TS + int(end.total_seconds()),
            "validateOverride": True,
        },
    )


@pytest.mark.parametrize("end", [timedelta(0), timedelta(seconds=-1)])
def test_set_timer_rejects_non_positive_duration(end: timedelta) -> None:
    """Test a zero-length or negative timer is rejected before calling the API."""
    with (
        patch.object(Pura, "_Pura__post") as post,
        pytest.raises(PuraApiException),
    ):
        Pura().set_timer("device", bay=1, intensity=5, start=START, end=end)
    post.assert_not_called()
