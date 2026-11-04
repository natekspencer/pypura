"""Tests for the websocket subscriber."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiohttp import web
import pytest

from pypura import Pura
from pypura.ws_subscriber import WebSocketSubscriber

TIMEOUT = 5


async def _on_message(_: dict) -> None:
    """Handle a message."""


async def _slow_connect(_: WebSocketSubscriber, __: object) -> None:
    """Stay connected until cancelled, taking a moment to clean up."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await asyncio.sleep(0)
        raise


@contextlib.asynccontextmanager
async def _server(limit: int | None = None) -> AsyncIterator[None]:
    """Run a local websocket server, sending `limit` messages or until disconnect."""

    async def handle(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        async def send() -> None:
            sent = 0
            with contextlib.suppress(ConnectionResetError):
                while limit is None or sent < limit:
                    await ws.send_json({"sent": sent})
                    sent += 1
                    await asyncio.sleep(0.01)
                await ws.close()

        sender = asyncio.create_task(send())
        async for _ in ws:  # Reading is what answers the client's close frame
            pass
        sender.cancel()
        return ws

    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app, shutdown_timeout=0.1)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    port = runner.addresses[0][1]
    try:
        with patch("pypura.ws_subscriber.WEBSOCKET_URL", f"ws://127.0.0.1:{port}/"):
            yield
    finally:
        await runner.cleanup()


def _first_message_handler() -> tuple[AsyncMock, asyncio.Event]:
    """Return a message handler and an event set by its first message."""
    received = asyncio.Event()
    return AsyncMock(side_effect=lambda _: received.set()), received


async def _assert_start_rejected_while_stopping(
    subscriber: WebSocketSubscriber,
) -> None:
    """Assert no new connection can be made until the subscriber has stopped."""
    subscriber.start(_on_message)
    await asyncio.sleep(0)

    stopping = asyncio.create_task(subscriber.stop())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="stopping"):
        subscriber.start(_on_message)
    with pytest.raises(RuntimeError, match="stopping"):
        await subscriber.connect(_on_message)
    await stopping

    subscriber.start(_on_message)
    await subscriber.stop()


def test_subscribe_for_updates_uses_passed_session() -> None:
    """Test a passed session is used, and left open for its owner to close."""

    async def run() -> None:
        async with aiohttp.ClientSession() as session, _server():
            on_message, received = _first_message_handler()
            with patch.object(Pura, "get_tokens", return_value={"id_token": "token"}):
                subscriber = Pura().subscribe_for_updates(on_message, session=session)
            await asyncio.wait_for(received.wait(), TIMEOUT)
            await subscriber.stop()

            assert not subscriber.is_running
            assert not session.closed
            assert subscriber.session is session

    asyncio.run(run())


def test_passed_session_is_kept_even_if_falsy() -> None:
    """Test a passed session is used whatever its truth value."""
    session = MagicMock()
    session.__bool__.return_value = False
    assert WebSocketSubscriber("token", session).session is session


def test_unused_subscriber_creates_no_session() -> None:
    """Test a subscriber that never connects has nothing to clean up."""
    assert WebSocketSubscriber("token").session is None


def test_own_session_is_closed_on_stop_and_replaced_on_restart() -> None:
    """Test each connection gets a session of its own, closed when it ends."""

    async def run() -> None:
        async with _server():
            subscriber = WebSocketSubscriber("token")

            on_message, received = _first_message_handler()
            subscriber.start(on_message)
            await asyncio.wait_for(received.wait(), TIMEOUT)
            first_session = subscriber.session
            assert first_session and not first_session.closed
            await subscriber.stop()
            assert first_session.closed

            on_message, received = _first_message_handler()
            subscriber.start(on_message)
            await asyncio.wait_for(received.wait(), TIMEOUT)
            second_session = subscriber.session
            assert second_session is not first_session
            assert second_session and not second_session.closed
            await subscriber.stop()
            assert second_session.closed

    asyncio.run(run())


def test_own_session_is_closed_when_server_disconnects() -> None:
    """Test a dropped connection doesn't rely on stop to close the session."""

    async def run() -> None:
        async with _server(limit=1):
            subscriber = WebSocketSubscriber("token")
            await subscriber.connect(_on_message)
            assert subscriber.session and subscriber.session.closed

    asyncio.run(run())


def test_connect_after_stop() -> None:
    """Test connect can be used directly after the subscriber was stopped."""

    async def run() -> None:
        async with _server(limit=1):
            subscriber = WebSocketSubscriber("token")
            on_message, received = _first_message_handler()
            subscriber.start(on_message)
            await asyncio.wait_for(received.wait(), TIMEOUT)
            await subscriber.stop()

            on_message = AsyncMock()
            await subscriber.connect(on_message)
            on_message.assert_awaited_once_with({"sent": 0})

    asyncio.run(run())


def test_start_rejected_while_stopping_own_session() -> None:
    """Test a new connection can't be made while stopping with an own session."""

    async def run() -> None:
        async with _server():
            await _assert_start_rejected_while_stopping(WebSocketSubscriber("token"))

    asyncio.run(run())


def test_start_rejected_while_stopping_passed_session() -> None:
    """Test a new connection can't be made while stopping with a passed session."""

    async def run() -> None:
        async with aiohttp.ClientSession() as session, _server():
            await _assert_start_rejected_while_stopping(
                WebSocketSubscriber("token", session)
            )

    asyncio.run(run())


def test_start_rejected_while_running() -> None:
    """Test a second start can't orphan the running connection."""

    async def run() -> None:
        with patch.object(WebSocketSubscriber, "connect", _slow_connect):
            subscriber = WebSocketSubscriber("token")
            subscriber.start(_on_message)
            with pytest.raises(RuntimeError, match="already running"):
                subscriber.start(_on_message)
            await subscriber.stop()

    asyncio.run(run())


def test_start_rejected_with_closed_passed_session() -> None:
    """Test a closed passed session fails right away, not in the background."""

    async def run() -> None:
        session = aiohttp.ClientSession()
        await session.close()
        with pytest.raises(RuntimeError, match="closed session"):
            WebSocketSubscriber("token", session).start(_on_message)

    asyncio.run(run())


def test_overlapping_stops_share_one_shutdown() -> None:
    """Test overlapping stops cancel the connection only once."""
    cancels = 0

    async def connect(_: WebSocketSubscriber, __: object) -> None:
        nonlocal cancels
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancels += 1
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                cancels += 1
            raise

    async def run() -> None:
        with patch.object(WebSocketSubscriber, "connect", connect):
            subscriber = WebSocketSubscriber("token")
            subscriber.start(_on_message)
            await asyncio.sleep(0)

            first = asyncio.create_task(subscriber.stop())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            second = asyncio.create_task(subscriber.stop())
            await asyncio.sleep(0)
            with pytest.raises(RuntimeError, match="stopping"):
                subscriber.start(_on_message)

            await asyncio.gather(first, second)
            assert cancels == 1
            subscriber.start(_on_message)
            await subscriber.stop()

    asyncio.run(run())


def test_stop_from_on_message() -> None:
    """Test stopping from the message handler ends the connection."""

    async def run() -> None:
        async with aiohttp.ClientSession() as session, _server():
            subscriber = WebSocketSubscriber("token", session)
            messages = 0

            async def on_message(_: dict) -> None:
                nonlocal messages
                messages += 1
                await subscriber.stop()

            subscriber.start(on_message)
            task = subscriber._task  # pylint: disable=protected-access
            assert task
            await asyncio.wait([task], timeout=TIMEOUT)

            assert task.cancelled()
            assert messages == 1
            assert not subscriber.is_running

    asyncio.run(run())


def test_cancelling_stop_is_not_swallowed() -> None:
    """Test the caller of stop still sees its own cancellation."""

    async def run() -> None:
        with patch.object(WebSocketSubscriber, "connect", _slow_connect):
            subscriber = WebSocketSubscriber("token")
            subscriber.start(_on_message)
            await asyncio.sleep(0)

            stopping = asyncio.create_task(subscriber.stop())
            await asyncio.sleep(0)
            stopping.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stopping

            await subscriber.stop()
            assert not subscriber.is_running

    asyncio.run(run())


def test_failed_connection_is_logged_once_and_closes_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a failed connection doesn't make stop raise, then or later."""

    async def run() -> None:
        with patch("pypura.ws_subscriber.WEBSOCKET_URL", "ws://127.0.0.1:1/"):
            subscriber = WebSocketSubscriber("token")
            subscriber.start(_on_message)
            task = subscriber._task  # pylint: disable=protected-access
            assert task
            await asyncio.wait([task], timeout=TIMEOUT)

            with caplog.at_level(logging.DEBUG):
                await subscriber.stop()
                await subscriber.stop()

            assert subscriber.session and subscriber.session.closed
            assert caplog.text.count("WebSocket connection failed") == 1

    asyncio.run(run())


def test_stop_straight_after_start_logs_no_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a connection cancelled before it begins isn't reported as failed."""

    async def run() -> None:
        session = MagicMock(closed=False)
        subscriber = WebSocketSubscriber("token", session=session)
        subscriber.start(_on_message)
        with caplog.at_level(logging.DEBUG):
            await subscriber.stop()

        session.ws_connect.assert_not_called()
        assert "WebSocket connection failed" not in caplog.text

    asyncio.run(run())
