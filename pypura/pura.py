"""Pura account."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import requests
from botocore.exceptions import ClientError
from pycognito import Cognito
from pycognito.utils import RequestsSrpAuth, TokenType

from .const import CLIENT_ID, USER_POOL_ID
from .exceptions import PuraAuthenticationError
from .utils import decode

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://trypura.io/mobile/api/"


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
                self._user.verify_tokens()
        return self._user

    def get_auth(self) -> RequestsSrpAuth:
        """Return the RequestsSrpAuth."""
        if self._auth is None:
            self._auth = RequestsSrpAuth(
                cognito=self.get_user(), auth_token_type=TokenType.ID_TOKEN
            )
        return self._auth

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
        return self.__get("users/devices")

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

    def stop_all(self, device_id: str) -> bool:
        """Stop all."""
        resp = self.__post(f"devices/{device_id}/stop-all")
        return resp.get("success") is True

    def __request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Make a request."""
        response = requests.request(
            method, urljoin(BASE_URL, url), auth=self.get_auth(), timeout=10, **kwargs
        )
        json = response.json()
        if (status_code := response.status_code) != 200:
            _LOGGER.error("Status: %s - %s", status_code, json)
        return json

    def __get(self, url: str, **kwargs: Any) -> Any:
        """Make a get request."""
        return self.__request("get", url, **kwargs)

    def __post(self, url: str, **kwargs: Any) -> Any:
        """Make a post request."""
        return self.__request("post", url, **kwargs)
