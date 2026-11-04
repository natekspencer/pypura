"""Tests for the Pura client."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import Any
from unittest.mock import MagicMock, patch

from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    ParamValidationError,
)
import jwt
from pycognito.exceptions import TokenVerificationException
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


def _response(status: int, content: bytes = b"{}") -> requests.Response:
    """Return a response."""
    response = requests.Response()
    response.status_code = status
    response._content = content
    return response


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
        (_client_error("PasswordResetRequiredException"), PuraAuthenticationError),
        (_client_error("UserNotConfirmedException"), PuraAuthenticationError),
        (_client_error("UserNotFoundException"), PuraAuthenticationError),
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
    """Test an error status raises a Pura exception with the status code."""
    with (
        patch.object(Pura, "get_auth"),
        patch.object(Pura, "get_user"),
        patch("pypura.pura.requests.request", return_value=_response(status)),
        pytest.raises(expected, match=str(status)) as raised,
    ):
        Pura().get_devices()

    assert type(raised.value) is expected
    assert raised.value.status_code == status


def test_request_retries_once_after_refreshing_tokens() -> None:
    """Test a rejected token is refreshed, so a brief 401 doesn't need a new login."""
    auth_headers: list[str] = []
    with (
        patch.object(Pura, "get_user") as get_user,
        patch("pypura.pura.requests.request") as request,
    ):
        user = get_user.return_value
        user.access_token = "access"
        user.id_token = "rejected"
        user.refresh_token = "refresh"

        def renew() -> None:
            user.id_token = "refreshed"

        user.renew_access_token.side_effect = renew

        def send(method: str, url: str, **kwargs: Any) -> requests.Response:
            prepared = kwargs["auth"](requests.Request(method, url).prepare())
            auth_headers.append(prepared.headers["Authorization"])
            return _response(401 if len(auth_headers) == 1 else 200)

        request.side_effect = send
        assert Pura().get_devices() == {}

    user.renew_access_token.assert_called_once_with()
    assert request.call_count == 2
    assert auth_headers == ["Bearer rejected", "Bearer refreshed"]


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


def test_request_skips_refresh_if_tokens_already_changed() -> None:
    """Test a request rejected during another refresh doesn't refresh again."""
    with (
        patch.object(Pura, "get_auth"),
        patch.object(Pura, "get_user") as get_user,
        patch("pypura.pura.requests.request") as request,
    ):
        user = get_user.return_value
        user.id_token = "old"

        def send(*_: object, **__: object) -> requests.Response:
            if request.call_count > 1:
                return _response(200)
            user.id_token = "new"
            return _response(401)

        request.side_effect = send
        assert Pura().get_devices() == {}

    user.renew_access_token.assert_not_called()
    assert request.call_count == 2


@pytest.mark.parametrize("content", [b"not json", b"null", b"[]", b"1"])
def test_request_invalid_response_raises(content: bytes) -> None:
    """Test a response that isn't a JSON object raises a Pura exception."""
    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", return_value=_response(200, content)),
        pytest.raises(PuraApiException, match="Invalid response"),
    ):
        Pura().get_devices()


def test_set_intensity_response() -> None:
    """Test the response to setting the intensity."""
    content = b'{"bay": 1, "controller": "default", "intensity": 1}'
    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", return_value=_response(200, content)),
    ):
        assert Pura().set_intensity("device", bay=1, controller="default", intensity=1)


def test_set_nightlight_response() -> None:
    """Test the response to setting the nightlight."""
    content = (
        b'{"default": {"nightlight": '
        b'{"active": false, "color": "FFFFFF", "brightness": 1}}}'
    )
    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", return_value=_response(200, content)),
    ):
        assert Pura().set_nightlight(
            "device", active=False, brightness=1, color="FFFFFF", controller="default"
        )


@pytest.mark.parametrize(
    ("status", "content", "message"),
    [
        (
            400,
            (
                b'{"message": "Bad Request", "errorData": '
                b'{"error": "Light control is not supported for this device type"}}'
            ),
            (
                "Request failed with status 400: "
                "Light control is not supported for this device type"
            ),
        ),
        (
            404,
            b'{"message": "Not Found", "errorData": "Config Not Found"}',
            "Request failed with status 404: Config Not Found",
        ),
        (
            400,
            b'{"message": "User does not own this device"}',
            "Request failed with status 400: User does not own this device",
        ),
        (500, b"Internal Server Error", "Request failed with status 500"),
        (500, b"[]", "Request failed with status 500"),
        (500, b'{"errorData": {}}', "Request failed with status 500"),
    ],
)
def test_request_error_includes_api_detail(
    status: int, content: bytes, message: str
) -> None:
    """Test an error includes the reason given by the API, if there is one."""
    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", return_value=_response(status, content)),
        pytest.raises(PuraApiException) as raised,
    ):
        Pura().get_devices()

    assert str(raised.value) == message
    assert raised.value.status_code == status


@pytest.mark.parametrize(
    "content",
    [b"{}", b'{"wall": [], "car": []}', b'{"wall": null, "car": null}'],
)
def test_get_devices_account_without_devices(content: bytes) -> None:
    """Test an account with no devices is a valid result, not an error."""
    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", return_value=_response(200, content)),
    ):
        assert Pura().get_devices() == {}


@pytest.mark.parametrize(
    "content",
    [
        b'{"wall": [{"name": "no device id"}]}',
        b'{"wall": "not a list"}',
        b'{"wall": ["not an object"]}',
        b'{"wall": 1}',
    ],
)
def test_get_devices_malformed_device_data_raises(content: bytes) -> None:
    """Test malformed device data raises a Pura exception."""
    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", return_value=_response(200, content)),
        pytest.raises(PuraApiException, match="Invalid response"),
    ):
        Pura().get_devices()


def test_request_empty_response_to_read_raises() -> None:
    """Test an empty response isn't taken to mean the account has no devices."""
    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", return_value=_response(200, b"")),
        pytest.raises(PuraApiException, match="Invalid response"),
    ):
        Pura().get_devices()


def test_request_empty_response_to_change_is_not_an_error() -> None:
    """Test an empty response to a change is treated as an empty object."""
    with (
        patch.object(Pura, "get_auth"),
        patch("pypura.pura.requests.request", return_value=_response(204, b"")),
    ):
        assert Pura().stop_all("device") is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_client_error("InternalErrorException"), PuraApiException),
        (_client_error("TooManyRequestsException"), PuraApiException),
        (EndpointConnectionError(endpoint_url="url"), PuraApiException),
        (requests.ConnectionError("failed"), PuraApiException),
        (_client_error("NotAuthorizedException"), PuraAuthenticationError),
        (ParamValidationError(report="no refresh token"), PuraAuthenticationError),
        (TokenVerificationException("expired"), PuraAuthenticationError),
        (jwt.DecodeError("malformed"), PuraAuthenticationError),
        (IndexError("unknown signing key"), PuraAuthenticationError),
        (KeyError("exp"), PuraAuthenticationError),
    ],
)
@pytest.mark.parametrize("method", ["check_token", "verify_tokens"])
def test_stored_token_errors_are_wrapped(
    method: str, error: Exception, expected: type[PuraApiException]
) -> None:
    """Test only rejected stored tokens need a new login."""
    with (
        patch("pypura.pura.Cognito") as cognito,
        patch("pypura.pura.requests.request") as request,
        pytest.raises(expected) as raised,
    ):
        getattr(cognito.return_value, method).side_effect = error
        Pura(access_token="access", id_token="id").get_devices()

    assert type(raised.value) is expected
    assert raised.value.__cause__ is error
    request.assert_not_called()


@pytest.mark.parametrize(
    "tokens", [{}, {"username": "user"}, {"username": "user", "id_token": "id"}]
)
def test_request_without_access_token_needs_new_login(
    tokens: dict[str, str],
) -> None:
    """Test a request can't be made without an access token."""
    with patch("pypura.pura.Cognito") as cognito:
        cognito.return_value.access_token = None
        account = Pura(**tokens)
        for _ in range(2):
            with pytest.raises(PuraAuthenticationError, match="Unauthenticated"):
                account.get_devices()

    cognito.return_value.check_token.assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_client_error("TooManyRequestsException"), PuraApiException),
        (EndpointConnectionError(endpoint_url="url"), PuraApiException),
        (_client_error("NotAuthorizedException"), PuraAuthenticationError),
    ],
)
@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("authenticate", lambda account: account.authenticate("password")),
        ("logout", lambda account: account.logout()),
    ],
)
def test_account_errors_are_wrapped(
    method: str,
    call: Callable[[Pura], None],
    error: Exception,
    expected: type[PuraApiException],
) -> None:
    """Test Cognito failures outside of a request raise a Pura exception."""
    with (
        patch("pypura.pura.Cognito") as cognito,
        pytest.raises(expected) as raised,
    ):
        getattr(cognito.return_value, method).side_effect = error
        call(Pura())

    assert type(raised.value) is expected


def test_authenticate_recovers_from_rejected_stored_tokens() -> None:
    """Test a new login is possible after stored tokens were rejected."""
    with patch("pypura.pura.Cognito") as cognito:
        cognito.return_value.check_token.side_effect = _client_error(
            "NotAuthorizedException"
        )
        account = Pura(access_token="access", id_token="id")
        with pytest.raises(PuraAuthenticationError):
            account.get_tokens()

        account.authenticate("password")

    cognito.return_value.authenticate.assert_called_once_with(password="password")
