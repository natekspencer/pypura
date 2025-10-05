"""WebSocket subscriber."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Awaitable, Callable, Final

import websockets

_LOGGER = logging.getLogger(__name__)

WEBSOCKET_URL: Final = "wss://socket.trypura.io"


class WebSocketSubscriber:
    """Websocket subscriber."""

    def __init__(self, token: str) -> None:
        """Init."""
        self.token = token
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Return `True` if the subscriber is running."""
        return self._running

    async def connect(self, on_message: Callable[[dict], Awaitable[None]]) -> None:
        """Connect to the websocket."""
        headers = {"Authorization": self.token}
        async with websockets.connect(
            WEBSOCKET_URL, additional_headers=headers
        ) as websocket:
            _LOGGER.debug("Connected to websocket")
            self._running = True
            try:
                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        data = message
                    await on_message(data)
            except websockets.ConnectionClosed:
                _LOGGER.debug("Websocket connection closed")
            finally:
                self._running = False

    def start(self, on_message: Callable[[dict], Awaitable[None]]) -> None:
        """Run subscriber in the background."""
        self._task = asyncio.create_task(self.connect(on_message))

    async def stop(self) -> None:
        """Stop the subscriber."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            _LOGGER.debug("Websocket connection closed")
