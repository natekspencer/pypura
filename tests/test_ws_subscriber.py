"""Tests for the websocket subscriber."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

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


def test_start_rejected_while_stopping_own_session() -> None:
    """Test a new connection can't use a session that is about to be closed."""

    async def slow_connect(_: WebSocketSubscriber, __: object) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            raise

    async def run() -> None:
        with patch.object(WebSocketSubscriber, "connect", slow_connect):
            subscriber = WebSocketSubscriber("token")
            subscriber.start(_on_message)
            await asyncio.sleep(0)

            stopping = asyncio.create_task(subscriber.stop())
            await asyncio.sleep(0)
            with pytest.raises(RuntimeError, match="stopping"):
                subscriber.start(_on_message)
            await stopping

            subscriber.start(_on_message)
            assert not subscriber.session.closed
            await subscriber.stop()

    asyncio.run(run())


def test_start_rejected_while_stopping_passed_session() -> None:
    """Test the connection being stopped can't be replaced on a passed session."""

    async def slow_connect(_: WebSocketSubscriber, __: object) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            raise

    async def run() -> None:
        async with aiohttp.ClientSession() as session:
            with patch.object(WebSocketSubscriber, "connect", slow_connect):
                subscriber = WebSocketSubscriber("token", session)
                subscriber.start(_on_message)
                await asyncio.sleep(0)

                stopping = asyncio.create_task(subscriber.stop())
                await asyncio.sleep(0)
                with pytest.raises(RuntimeError, match="stopping"):
                    subscriber.start(_on_message)
                await stopping

                subscriber.start(_on_message)
                await subscriber.stop()

    asyncio.run(run())


def test_start_rejected_until_overlapping_stops_finish() -> None:
    """Test overlapping stops keep rejecting start until the last one finishes."""

    async def run() -> None:
        subscriber = WebSocketSubscriber("token")
        real_close = subscriber.session.close
        releases = [asyncio.Event(), asyncio.Event()]
        close_calls = 0

        async def gated_close() -> None:
            nonlocal close_calls
            close_calls += 1
            await releases[close_calls - 1].wait()

        with patch.object(subscriber.session, "close", gated_close):
            first = asyncio.create_task(subscriber.stop())
            second = asyncio.create_task(subscriber.stop())
            await asyncio.sleep(0)

            releases[0].set()
            await first
            await asyncio.sleep(0)

            assert not second.done()
            with pytest.raises(RuntimeError, match="stopping"):
                subscriber.start(_on_message)

            releases[1].set()
            await second
        await real_close()

    asyncio.run(run())
