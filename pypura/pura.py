"""Pura account."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import logging
import threading
from typing import Any, Final
from urllib.parse import urljoin

from aiohttp import ClientSession
from botocore.exceptions import BotoCoreError, ClientError, ParamValidationError
from jwt import PyJWTError
from pycognito import Cognito
from pycognito.exceptions import TokenVerificationException
from pycognito.utils import RequestsSrpAuth, TokenType
import requests

from .const import CLIENT_ID, USER_POOL_ID
from .exceptions import PuraApiException, PuraAuthenticationError
from .utils import decode
from .ws_subscriber import WebSocketSubscriber

_LOGGER = logging.getLogger(__name__)

BASE_URL: Final = "https://trypura.io/mobile/api/"

TIMER_DURATION_DEFAULT = timedelta(minutes=30)

AUTH_ERROR_CODES: Final = frozenset(
    {
        "NotAuthorizedException",
        "PasswordResetRequiredException",
        "UserNotConfirmedException",
        "UserNotFoundException",
    }
)


@contextmanager
def _translate_errors() -> Iterator[None]:
    """Raise a Pura exception for a failed Cognito or API call."""
    try:
        yield
    except ClientError as err:
        _LOGGER.error(err)
        if err.response["Error"]["Code"] in AUTH_ERROR_CODES:
            raise PuraAuthenticationError(err) from err
        raise PuraApiException(err) from err
    except (
        ParamValidationError,  # A refresh without a refresh token
        TokenVerificationException,
        PyJWTError,
        LookupError,  # A token without an expiry, or from an unknown signing key
    ) as err:
        _LOGGER.error(err)
        raise PuraAuthenticationError(err) from err
    except (BotoCoreError, requests.RequestException) as err:
        raise PuraApiException(err) from err


def _error_message(response: requests.Response) -> str:
    """Return a message for a failed response, with any detail from the API."""
    message = f"Request failed with status {response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return message
    if not isinstance(body, dict):
        return message
    detail = body.get("errorData") or body.get("message")
    if isinstance(detail, dict):
        detail = detail.get("error")
    return f"{message}: {detail}" if detail else message


class Pura:
    """Pura account."""

    _user: Cognito | None = None
    _auth: RequestsSrpAuth | None = None

    def __init__(
        self,
        *,
        username: str | None = None,
        access_token: str | None = None,
        id_token: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        """Initialize."""
        self._username = username
        self._access_token = access_token
        self._id_token = id_token
        self._refresh_token = refresh_token
        self._refresh_lock = threading.Lock()

    def get_user(self) -> Cognito:
        """Return the Cognito user."""
        if self._user is None:
            self._user = Cognito(
                decode(USER_POOL_ID),
                decode(CLIENT_ID),
                username=self._username,
                access_token=self._access_token,
                id_token=self._id_token,
                refresh_token=self._refresh_token,
            )
            if self._access_token:
                with _translate_errors():
                    self._user.check_token()
                    self._user.verify_tokens()
        return self._user

    def get_auth(self) -> RequestsSrpAuth:
        """Return the RequestsSrpAuth."""
        if not self.get_user().access_token:
            raise PuraAuthenticationError("Unauthenticated")
        if self._auth is None:
            self._auth = RequestsSrpAuth(
                cognito=self.get_user(), auth_token_type=TokenType.ID_TOKEN
            )
        return self._auth

    def get_tokens(self) -> dict[str, str]:
        """Return the tokens."""
        if (user := self.get_user()).access_token:
            return {
                "access_token": user.access_token,
                "id_token": user.id_token,
                "refresh_token": user.refresh_token,
            }
        return {}

    def authenticate(self, password: str) -> None:
        """Authenticate a user."""
        with _translate_errors():
            self.get_user().authenticate(password=password)

    def logout(self) -> None:
        """Logout of all clients (including app)."""
        with _translate_errors():
            self.get_user().logout()

    def get_devices(self) -> dict[str, dict[str, Any]]:
        """Get devices."""
        response = self.__get("v3/accounts/v2/devices")
        return {
            device["deviceId"]: device | {"modelType": model_type}
            for model_type, devices in response.items()
            for device in devices or []
        }

    def get_latest_firmware_details(
        self, device_id: str, device_type: int, device_version: str
    ) -> dict[str, int | str]:
        """Get the latest available firmware details for a device.

        Args:
            device_id: The ID of the device to check.
            device_type: The type of the device.
                (1 = wall, 2 = car, 3 = plus, 4 = mini)
            device_version: The device's hardware/firmware version, as
                reported by the API's `deviceVer` field. For wall, plus,
                and mini devices, this is passed through as-is. For car
                devices, the firmware check only accepts "v1" or "v2",
                so raw values are mapped accordingly: the original car
                maps to "v1" and the car pro maps to "v2".

        Returns:
            A dict of firmware details for the device. Car devices
            (`device_type` 2) return `file`, `host`, `version`, `major`,
            `minor`, and `patch`. Wall, plus, and mini devices return
            `file`, `host`, `version`, `interval`, and `size`.
        """
        if device_type == 2 and device_version not in ("v1", "v2"):
            device_version = "v1" if device_version == "1" else "v2"
        resp: dict[str, int | str] = self.__get(
            f"v3/firmware/{device_id}/config",
            params={"deviceType": device_type, "deviceVersion": device_version},
        )
        return resp

    def request_ota_update(
        self,
        device_id: str,
        device_type: int,
        device_version: str | None = None,
        current_ota_version: int | None = None,
    ) -> dict[str, bool | int | str]:
        """Request an OTA (over-the-air) firmware update for a device.

        Args:
            device_id: The ID of the device to request an update for.
            device_type: The type of the device.
                (1 = wall, 2 = car, 3 = plus, 4 = mini)
            device_version: The device's hardware/firmware version, as
                reported by the API's `deviceVer` field, if known.
            current_ota_version: The device's current OTA version, as
                reported by the API's `otaVer` field, if known.

        Returns:
            A dict describing the outcome of the request, including
            whether an OTA update will be pushed to the device (`willOTA`)
            and, if so, the version that will be pushed
            (`prescribedOTAVersion`).
        """
        json: dict[str, str | int] = {"deviceType": device_type}
        if device_version:
            json["deviceVersion"] = device_version
        if current_ota_version:
            json["currentOTAVersion"] = current_ota_version
        resp: dict[str, bool | int | str] = self.__post(
            f"devices/{device_id}/ota/status", json=json
        )
        return resp

    def set_always_on(self, device_id: str, *, bay: int) -> bool:
        """Enable always-on mode for a bay on a device.

        Args:
            device_id: The ID of the device to update.
            bay: The bay number on the device to update.

        Returns:
            True if always-on mode was set successfully.
        """
        json = {"bay": bay}
        resp = self.__post(f"devices/{device_id}/always-on", json=json)
        return resp.get("success") is True

    def set_ambient_mode(self, device_id: str, *, ambient_mode: bool) -> bool:
        """Set ambient mode for a device.

        Args:
            device_id: The ID of the device to update.
            ambient_mode: Whether ambient mode should be on or off.

        Returns:
            True if ambient mode was set successfully.
        """
        json = {"ambientMode": ambient_mode}
        resp = self.__post(f"devices/{device_id}/ambientMode", json=json)
        return resp.get("success") is True

    def set_away_mode(
        self,
        device_id: str,
        *,
        away_mode: bool,
        latitude: float | None = None,
        longitude: float | None = None,
        radius: int | None = None,
    ) -> bool:
        """Set away mode for a device, optionally using a geofence.

        Args:
            device_id: The ID of the device to update.
            away_mode: Whether away mode should be on or off. If `True`,
                `latitude`, `longitude`, and `radius` must all be
                provided to define the geofence. If `False`, any saved
                geofence is deleted, even if away mode was already off.
            latitude: Latitude of the geofence center. Required if
                `away_mode` is `True`.
            longitude: Longitude of the geofence center. Required if
                `away_mode` is `True`.
            radius: Radius of the geofence, in meters. Required if
                `away_mode` is `True`.

        Returns:
            True if away mode was set successfully.
        """
        json: dict[str, Any] = {"awayMode": away_mode}
        if away_mode:
            json.update(
                {"latitude": latitude, "longitude": longitude, "radius": radius}
            )
        resp = self.__post(f"devices/{device_id}/awayMode", json=json)
        return resp.get("success") is True

    def set_diffusion_mode(self, device_id: str, *, mode: str) -> bool:
        """Set the diffusion mode for a device.

        Args:
            device_id: The ID of the device to update.
            mode: The diffusion mode to use ("oscillation-multi-bay" or
                "standard").

        Returns:
            True if the diffusion mode was set successfully.
        """
        json = {"mode": mode}
        resp = self.__post(f"v3/diffusion/{device_id}/mode", json=json)
        return resp.get("success") is True

    def set_intensity(
        self, device_id: str, *, bay: int, controller: str, intensity: int
    ) -> bool:
        """Set the intensity for a bay on a device.

        Args:
            device_id: The ID of the device to update.
            bay: The bay number on the device to update.
            controller: The controller to use ("default", "timer", or a
                schedule ID/index).
            intensity: The intensity level, from 1 to 10.

        Returns:
            True if the intensity was set successfully.
        """
        json = {"bay": bay, "controller": controller, "intensity": intensity}
        resp = self.__put(f"v3/devices/{device_id}/v2/intensity", json=json)
        return bool(resp)

    def set_nightlight(
        self,
        device_id: str,
        *,
        active: bool,
        brightness: int,
        color: str,
        controller: str,
    ) -> bool:
        """Set the nightlight state for a device.

        Args:
            device_id: The ID of the device to update.
            active: Whether the nightlight should be on or off.
            brightness: The brightness level, from 1 to 10.
            color: The nightlight color, as an uppercase hex string
                (e.g. "1A46FF").
            controller: The controller to use  ("default", "timer", or a
                schedule ID/index).

        Returns:
            True if the nightlight was set successfully.
        """
        json = {
            "active": active,
            "brightness": brightness,
            "color": color,
            "controller": controller,
        }
        resp = self.__post(f"v3/devices/{device_id}/nightlight", json=json)
        return bool(resp)

    def set_timer(
        self,
        device_id: str,
        *,
        bay: int,
        intensity: int,
        end: datetime | timedelta | int = TIMER_DURATION_DEFAULT,
    ) -> bool:
        """Set a timer for a bay on a device.

        Args:
            device_id: The ID of the device to update.
            bay: The bay number on the device to update.
            intensity: The intensity level, from 1 to 10.
            end: When the timer should end, as a `datetime`, a
                `timedelta` from now, or a Unix timestamp. Defaults to
                30 minutes.

        Returns:
            True if the timer was set successfully.
        """
        now = int(datetime.now(UTC).timestamp())
        if isinstance(end, datetime):
            end = int(end.timestamp())
        elif isinstance(end, timedelta):
            end = now + int(end.total_seconds())
        if end <= now:
            raise PuraApiException("Timer 'end' time must be in the future")
        json = {
            "bay": bay,
            "intensity": intensity,
            "start": now,
            "end": end,
            "validateOverride": True,
        }
        resp = self.__post(f"v3/devices/{device_id}/v2/timer", json=json)
        return bool(resp.get("timer"))

    def stop_all(self, device_id: str) -> bool:
        """Stop all."""
        resp = self.__post(f"devices/{device_id}/stop-all")
        return resp.get("success") is True

    def subscribe_for_updates(
        self,
        on_message: Callable[[dict], Awaitable[None]],
        *,
        session: ClientSession | None = None,
    ) -> WebSocketSubscriber:
        """Open a web socket connection to receive updates."""
        if not (token := self.get_tokens().get("id_token")):
            raise PuraAuthenticationError("Unauthenticated")

        subscriber = WebSocketSubscriber(token=token, session=session)
        subscriber.start(on_message)
        return subscriber

    def __request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Make a request, refreshing the tokens and retrying once if unauthorized.

        The response must be a JSON object. An empty response to a change is
        returned as an empty object, but reading data must return it, so that
        missing data isn't taken as there being none.
        """
        _LOGGER.debug("Making %s request to %s with %s", method, url, kwargs)
        user = self.get_user()
        id_token = user.id_token
        response = self.__send(method, url, **kwargs)
        if response.status_code == 401 and user.refresh_token:
            _LOGGER.debug("Unauthorized, refreshing the tokens and retrying")
            self.__refresh_tokens(id_token)
            response = self.__send(method, url, **kwargs)
        if not response.ok:
            _LOGGER.error("Status: %s - %s", response.status_code, response.text)
            error = (
                PuraAuthenticationError
                if response.status_code == 401
                else PuraApiException
            )
            raise error(_error_message(response), status_code=response.status_code)
        if not response.content and method != "get":
            return {}
        try:
            data = response.json()
        except ValueError as err:
            raise PuraApiException(f"Invalid response: {err}") from err
        if not isinstance(data, dict):
            raise PuraApiException(f"Invalid response: {data!r}")
        return data

    def __refresh_tokens(self, rejected_id_token: str | None) -> None:
        """Refresh the tokens, unless they changed since the rejected request."""
        with self._refresh_lock, _translate_errors():
            if (user := self.get_user()).id_token == rejected_id_token:
                user.renew_access_token()

    def __send(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Send a request, raising a Pura exception if it can't be made."""
        with _translate_errors():
            return requests.request(
                method,
                urljoin(BASE_URL, url),
                auth=self.get_auth(),
                timeout=10,
                **kwargs,
            )

    def __get(self, url: str, **kwargs: Any) -> Any:
        """Make a get request."""
        return self.__request("get", url, **kwargs)

    def __post(self, url: str, **kwargs: Any) -> Any:
        """Make a post request."""
        return self.__request("post", url, **kwargs)

    def __put(self, url: str, **kwargs: Any) -> Any:
        """Make a put request."""
        return self.__request("put", url, **kwargs)
