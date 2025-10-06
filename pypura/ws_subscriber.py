"""WebSocket subscriber."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Awaitable, Callable, Final

import aiohttp

_LOGGER = logging.getLogger(__name__)

WEBSOCKET_URL: Final = "wss://socket.trypura.io"


class WebSocketSubscriber:
    """Websocket subscriber."""

    _running = False
    _task: asyncio.Task[None] | None = None

    def __init__(self, token: str, session: aiohttp.ClientSession | None = None):
        """Initialize a websocket subscriber."""
        self.token = token
        self.session = session if session else aiohttp.ClientSession()

    @property
    def is_running(self) -> bool:
        """Return `True` if the subscriber is running."""
        return self._running

    async def connect(self, on_message: Callable[[dict], Awaitable[None]]) -> None:
        """Connect to the websocket."""
        headers = {"Authorization": f"Bearer {self.token}"}

        async with self.session.ws_connect(WEBSOCKET_URL, headers=headers) as ws:
            self._running = True
            _LOGGER.debug("Connected to websocket")
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                        except json.JSONDecodeError:
                            data = msg.data
                        try:
                            await on_message(data)
                        except Exception as e:  # pylint: disable=broad-except
                            _LOGGER.exception("Error handling WebSocket message: %s", e)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        _LOGGER.debug("WebSocket error: %s", msg.data)
                        break
            finally:
                self._running = False
                _LOGGER.debug("WebSocket disconnected")

    def start(self, on_message: Callable[[dict], Awaitable[None]]) -> None:
        """Start the subscriber in the background."""
        self._task = asyncio.create_task(self.connect(on_message))

    async def stop(self) -> None:
        """Stop the subscriber."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
