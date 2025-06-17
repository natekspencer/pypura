"""Pura account."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Final
from urllib.parse import urljoin

import requests
from botocore.exceptions import ClientError
from pycognito import Cognito
from pycognito.utils import RequestsSrpAuth, TokenType

from .const import CLIENT_ID, USER_POOL_ID
from .exceptions import PuraApiException, PuraAuthenticationError
from .utils import decode

_LOGGER = logging.getLogger(__name__)

BASE_URL: Final = "https://trypura.io/mobile/api/"

TIMER_DURATION_DEFAULT = timedelta(hours=4)


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
            if self._access_token or self._id_token:
                try:
                    self._user.check_token()
                    self._user.verify_tokens()
                except ClientError as err:
                    _LOGGER.error(err)
                    raise PuraAuthenticationError(err) from err
        return self._user

    def get_auth(self) -> RequestsSrpAuth:
        """Return the RequestsSrpAuth."""
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
        try:
            self.get_user().authenticate(password=password)
        except ClientError as err:
            _LOGGER.error(err)
            raise PuraAuthenticationError(err) from err

    def logout(self) -> None:
        """Logout of all clients (including app)."""
        self.get_user().logout()

    def get_devices(self) -> Any:
        """Get devices."""
        return self.__get("v2/users/devices")

    def get_latest_firmware_details(self, device_type: str, device_version: str) -> Any:
        """Get latest firmware details."""
        return self.__get(
            "https://prod.api.purascents.com/api/firmware/config",
            headers={
                "pura-device-type": device_type,
                "pura-device-version": device_version,
            },
            text_response=True,
        )

    def set_always_on(self, device_id: str, *, bay: int) -> bool:
        """Set always on."""
        json = {"bay": bay}
        resp = self.__post(f"devices/{device_id}/always-on", json=json)
        return resp.get("success") is True

    def set_ambient_mode(self, device_id: str, *, ambient_mode: bool) -> bool:
        """Set away mode."""
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
        """Set away mode."""
        json: dict[str, Any] = {"awayMode": away_mode}
        if away_mode:
            json.update(
                {"latitude": latitude, "longitude": longitude, "radius": radius}
            )
        resp = self.__post(f"devices/{device_id}/awayMode", json=json)
        return resp.get("success") is True

    def set_intensity(
        self, device_id: str, *, bay: int, controller: str, intensity: int
    ) -> bool:
        """Set intensity."""
        json = {"bay": bay, "controller": controller, "intensity": intensity}
        resp = self.__post(f"devices/{device_id}/intensity", json=json)
        return resp.get("success") is True

    def set_nightlight(
        self,
        device_id: str,
        *,
        active: bool,
        brightness: int,
        color: str,
        controller: str,
    ) -> bool:
        """Set nightlight."""
        json = {
            "active": active,
            "brightness": brightness,
            "color": color,
            "controller": controller,
        }
        resp = self.__post(f"devices/{device_id}/nightlight", json=json)
        return resp.get("success") is True

    def set_timer(
        self,
        device_id: str,
        *,
        bay: int,
        intensity: int,
        start: datetime | int | None = None,
        end: datetime | timedelta | int = TIMER_DURATION_DEFAULT,
    ) -> bool:
        """Set timer."""
        if not start:
            start = datetime.now()
        if isinstance(start, datetime):
            start = int(start.timestamp())
        if isinstance(end, datetime):
            end = int(end.timestamp())
        elif isinstance(end, timedelta):
            end = start + end.seconds
        if end <= start:
            raise PuraApiException("Timer 'end' time must be greater than 'start' time")
        json = {
            "bay": bay,
            "intensity": intensity,
            "start": start,
            "end": end,
            "validateOverride": True,
        }
        resp = self.__post(f"devices/{device_id}/timer", json=json)
        return resp.get("success") is True

    def stop_all(self, device_id: str) -> bool:
        """Stop all."""
        resp = self.__post(f"devices/{device_id}/stop-all")
        return resp.get("success") is True

    def __request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Make a request."""
        text_response = kwargs.pop("text_response", False)
        _LOGGER.debug("Making %s request to %s with %s", method, url, kwargs)
        response = requests.request(
            method, urljoin(BASE_URL, url), auth=self.get_auth(), timeout=10, **kwargs
        )
        if (status_code := response.status_code) != 200:
            _LOGGER.error("Status: %s - %s", status_code, response.text)
            response.raise_for_status()
        return response.text if text_response else response.json()

    def __get(self, url: str, **kwargs: Any) -> Any:
        """Make a get request."""
        return self.__request("get", url, **kwargs)

    def __post(self, url: str, **kwargs: Any) -> Any:
        """Make a post request."""
        return self.__request("post", url, **kwargs)
