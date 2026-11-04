"""WebSocket subscriber."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
from typing import Final

import aiohttp

_LOGGER = logging.getLogger(__name__)

WEBSOCKET_URL: Final = "wss://socket.trypura.io"


class WebSocketSubscriber:
    """Websocket subscriber."""

    _running = False
    _stop_task: asyncio.Task[None] | None = None
    _task: asyncio.Task[None] | None = None

    def __init__(self, token: str, session: aiohttp.ClientSession | None = None):
        """Initialize a websocket subscriber.

        If no session is passed, one is created for each connection and closed
        when that connection ends.
        """
        self.token = token
        self._owns_session = session is None
        self.session = session

    @property
    def is_running(self) -> bool:
        """Return `True` if the subscriber is running."""
        return self._running

    def _check_can_connect(self) -> None:
        """Raise `RuntimeError` if a connection can't be made right now."""
        if self._stop_task:
            raise RuntimeError("Cannot start the subscriber while it is stopping")
        if not self._owns_session and self.session and self.session.closed:
            raise RuntimeError("Cannot start the subscriber with a closed session")

    async def connect(self, on_message: Callable[[dict], Awaitable[None]]) -> None:
        """Connect to the websocket."""
        self._check_can_connect()
        headers = {"Authorization": f"Bearer {self.token}"}

        session = self.session
        if session is None or self._owns_session:
            session = self.session = aiohttp.ClientSession()

        try:
            async with session.ws_connect(WEBSOCKET_URL, headers=headers) as ws:
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
                            except Exception:  # pylint: disable=broad-except
                                _LOGGER.exception("Error handling WebSocket message")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            _LOGGER.debug("WebSocket error: %s", msg.data)
                            break
                finally:
                    self._running = False
                    _LOGGER.debug("WebSocket disconnected")
        finally:
            if self._owns_session:
                await session.close()

    def start(self, on_message: Callable[[dict], Awaitable[None]]) -> None:
        """Start the subscriber in the background."""
        self._check_can_connect()
        if self._task and not self._task.done():
            raise RuntimeError("The subscriber is already running")
        self._task = asyncio.create_task(self.connect(on_message))

    async def stop(self) -> None:
        """Stop the subscriber, logging any error that ended the connection.

        Overlapping calls share one shutdown. When called from `on_message`, the
        call doesn't return because stopping cancels the handler.
        """
        if self._task is None:
            return
        if self._stop_task is None:
            self._task.cancel()
            self._stop_task = asyncio.create_task(self._shutdown(self._task))
        await asyncio.shield(self._stop_task)

    async def _shutdown(self, task: asyncio.Task[None]) -> None:
        """Wait for the cancelled connection task to finish."""
        try:
            await asyncio.wait([task])
        finally:
            self._task = None
            self._stop_task = None
        if not task.cancelled() and (error := task.exception()):
            _LOGGER.debug("WebSocket connection failed: %s", error)
