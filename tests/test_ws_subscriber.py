"""Tests for the websocket subscriber."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp

from pypura import Pura
from pypura.ws_subscriber import WebSocketSubscriber


async def _on_message(_: dict) -> None:
    """Handle a message."""


def test_subscribe_for_updates_uses_passed_session() -> None:
    """Test a passed session is used, and left open for its owner to close."""

    async def run() -> None:
        async with aiohttp.ClientSession() as session:
            with (
                patch.object(Pura, "get_tokens", return_value={"id_token": "token"}),
                patch.object(WebSocketSubscriber, "connect", AsyncMock()),
            ):
                subscriber = Pura().subscribe_for_updates(_on_message, session=session)
                await subscriber.stop()

            assert subscriber.session is session
            assert not session.closed

    asyncio.run(run())


def test_stop_closes_own_session_and_start_replaces_it() -> None:
    """Test a session the subscriber created is closed, and replaced on restart."""

    async def run() -> None:
        with patch.object(WebSocketSubscriber, "connect", AsyncMock()):
            subscriber = WebSocketSubscriber("token")
            subscriber.start(_on_message)
            first_session = subscriber.session
            await subscriber.stop()
            assert first_session.closed

            subscriber.start(_on_message)
            assert subscriber.session is not first_session
            assert not subscriber.session.closed
            await subscriber.stop()
            assert subscriber.session.closed

    asyncio.run(run())
