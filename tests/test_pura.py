"""Tests for the Pura client."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError, EndpointConnectionError
import pytest
import requests

from pypura import Pura, PuraApiException, PuraAuthenticationError

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


def _client_error(code: str) -> ClientError:
    """Return a Cognito client error."""
    return ClientError({"Error": {"Code": code, "Message": code}}, "InitiateAuth")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (requests.ConnectionError("failed"), PuraApiException),
        (requests.Timeout("timed out"), PuraApiException),
        (EndpointConnectionError(endpoint_url="url"), PuraApiException),
        (_client_error("InternalErrorException"), PuraApiException),
        (_client_error("NotAuthorizedException"), PuraAuthenticationError),
    ],
)
def test_request_errors_are_wrapped(
    error: Exception, expected: type[PuraApiException]
) -> None:
    """Test failures to make a request raise a Pura exception."""
    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", side_effect=error),
        pytest.raises(expected) as raised,
    ):
        Pura().get_devices()

    assert type(raised.value) is expected
    assert raised.value.__cause__ is error


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, PuraApiException),
        (401, PuraAuthenticationError),
        (403, PuraApiException),
        (500, PuraApiException),
    ],
)
def test_request_error_status_raises(
    status: int, expected: type[PuraApiException]
) -> None:
    """Test an error status raises a Pura exception."""
    response = requests.Response()
    response.status_code = status

    with (
        patch.object(Pura, "get_auth"),
        patch.object(Pura, "get_user"),
        patch("pypura.pura.requests.request", return_value=response),
        pytest.raises(expected, match=str(status)) as raised,
    ):
        Pura().get_devices()

    assert type(raised.value) is expected


def _response(status: int, content: bytes = b"{}") -> requests.Response:
    """Return a response."""
    response = requests.Response()
    response.status_code = status
    response._content = content
    return response


def test_request_retries_once_after_refreshing_tokens() -> None:
    """Test a rejected token is refreshed, so a brief 401 doesn't need a new login."""
    with (
        patch.object(Pura, "get_auth"),
        patch.object(Pura, "get_user") as get_user,
        patch(
            "pypura.pura.requests.request",
            side_effect=[_response(401), _response(200)],
        ) as request,
    ):
        assert Pura().get_devices() == {}

    get_user.return_value.renew_access_token.assert_called_once_with()
    assert request.call_count == 2


def test_request_stops_after_second_unauthorized() -> None:
    """Test a token that is still rejected after refreshing is an auth error."""
    with (
        patch.object(Pura, "get_auth"),
        patch.object(Pura, "get_user") as get_user,
        patch("pypura.pura.requests.request", return_value=_response(401)) as request,
        pytest.raises(PuraAuthenticationError),
    ):
        Pura().get_devices()

    get_user.return_value.renew_access_token.assert_called_once_with()
    assert request.call_count == 2


def test_request_without_refresh_token_needs_new_login() -> None:
    """Test a 401 is an auth error when there is no token to refresh with."""
    with (
        patch.object(Pura, "get_auth"),
        patch.object(Pura, "get_user") as get_user,
        patch("pypura.pura.requests.request", return_value=_response(401)) as request,
        pytest.raises(PuraAuthenticationError),
    ):
        get_user.return_value.refresh_token = None
        Pura().get_devices()

    get_user.return_value.renew_access_token.assert_not_called()
    assert request.call_count == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_client_error("NotAuthorizedException"), PuraAuthenticationError),
        (_client_error("InternalErrorException"), PuraApiException),
        (EndpointConnectionError(endpoint_url="url"), PuraApiException),
    ],
)
def test_request_refresh_failure(
    error: Exception, expected: type[PuraApiException]
) -> None:
    """Test a failed token refresh only needs a new login if it was rejected."""
    with (
        patch.object(Pura, "get_auth"),
        patch.object(Pura, "get_user") as get_user,
        patch("pypura.pura.requests.request", return_value=_response(401)),
        pytest.raises(expected) as raised,
    ):
        get_user.return_value.renew_access_token.side_effect = error
        Pura().get_devices()

    assert type(raised.value) is expected


def test_request_invalid_json_raises() -> None:
    """Test a response that isn't JSON raises a Pura exception."""
    response = requests.Response()
    response.status_code = 200
    response._content = b"not json"

    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", return_value=response),
        pytest.raises(PuraApiException, match="Invalid response"),
    ):
        Pura().get_devices()
