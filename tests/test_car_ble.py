"""Tests for reading a Pura Car over Bluetooth."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.exc import BleakError
import cbor2
import pytest

from pypura import PuraBluetoothError, car_ble
from pypura.car_ble import (
    COMMAND_UUID,
    RESPONSE_UUID,
    PuraCarClient,
    PuraCarStatus,
    read_car_status,
)

# Replies from a Car Pro on firmware 9.1.16
REPLIES: dict[int, dict[str, Any]] = {
    1: {"ack": 1, "mac": "E41A01DB8057", "fw": "9.1.16", "hw": "27.5", "env": 0},
    34: {"ack": 34, "level": 85, "charging": 1, "state": 0},
    36: {"ack": 36, "status": 1, "fragrance": "FZYC", "runtime": 190411},
    42: {"ack": 42, "intensity": 1},
    60: {"ack": 60, "id": "59D6C859-C9D1-4BED-B11E-8B2B47402179"},
}
STATUS = PuraCarStatus(
    mac="E41A01DB8057",
    firmware="9.1.16",
    hardware="27.5",
    battery_level=85,
    charging=True,
    intensity="low",
    fragrance_code="FZYC",
    cartridge_id="59D6C859-C9D1-4BED-B11E-8B2B47402179",
    runtime=190411,
)


def chunks(message: bytes, size: int = 19) -> list[bytes]:
    """Split a message like the car does at the default MTU: 0, 1, then 2s, then 3."""
    parts = [message[i : i + size] for i in range(0, len(message), size)] or [b""]
    if len(parts) == 1:
        return [b"\x00" + parts[0]]
    middle = [bytes([(i + 1) << 4 | 2]) + p for i, p in enumerate(parts[1:-1])]
    return [b"\x01" + parts[0], *middle, b"\x03" + parts[-1]]


class FakeCar:
    """A connected car that answers commands from `replies`."""

    def __init__(self, replies: dict[int, Any]) -> None:
        self.replies = replies
        self.callback: Callable[[Any, bytearray], None] | None = None
        self.before_reply: list[bytes] = []  # chunks to notify before the next reply
        self.sent: list[int] = []
        self.client = MagicMock()
        self.client.start_notify = AsyncMock(side_effect=self._start_notify)
        self.client.write_gatt_char = AsyncMock(side_effect=self._write)
        self.client.disconnect = AsyncMock()

    async def _start_notify(self, uuid: str, callback: Any) -> None:
        assert uuid == RESPONSE_UUID
        self.callback = callback

    async def _write(self, uuid: str, data: bytes, response: bool) -> None:
        assert uuid == COMMAND_UUID
        assert response
        assert data[0] == 0  # a single chunk
        cmd = cbor2.loads(data[1:])["cmd"]
        self.sent.append(cmd)
        if cmd not in self.replies:
            return
        reply = self.replies[cmd]
        message = reply if isinstance(reply, bytes) else cbor2.dumps(reply)
        assert self.callback
        for chunk in [*self.before_reply, *chunks(message)]:
            self.callback(MagicMock(), bytearray(chunk))
        self.before_reply = []


async def _client(car: FakeCar) -> PuraCarClient:
    client = PuraCarClient(car.client)
    await client.start()
    return client


def test_read_status() -> None:
    """Read every field, including replies split over several chunks."""
    assert len(chunks(cbor2.dumps(REPLIES[60]))) == 3

    async def run() -> None:
        car = FakeCar(REPLIES)
        assert await (await _client(car)).read_status() == STATUS
        assert car.sent == [1, 34, 36, 42, 60]

    asyncio.run(run())


def test_read_status_without_pod() -> None:
    """Without a pod there's no fragrance, pod ID or runtime, and no pod ID request."""

    async def run() -> None:
        car = FakeCar(REPLIES | {36: {"ack": 36, "status": 0, "runtime": 0}})
        status = await (await _client(car)).read_status()
        assert (status.fragrance_code, status.cartridge_id, status.runtime) == (
            None,
            None,
            None,
        )
        assert 60 not in car.sent

    asyncio.run(run())


@pytest.mark.parametrize(
    ("value", "expected"), [(0, "off"), (1, "low"), (2, "medium"), (3, "high")]
)
def test_intensity_names(value: int, expected: str) -> None:
    """Intensity uses the cloud's words."""

    async def run() -> None:
        car = FakeCar(REPLIES | {42: {"ack": 42, "intensity": value}})
        assert (await (await _client(car)).read_status()).intensity == expected

    asyncio.run(run())


def test_messages_from_the_car_are_skipped() -> None:
    """Battery and intensity updates the car sends by itself aren't taken as replies."""

    async def run() -> None:
        car = FakeCar(REPLIES)
        client = await _client(car)
        # A pushed intensity change arrives while waiting for the fragrance details
        car.before_reply = chunks(cbor2.dumps({"ack": 42, "intensity": 3}))
        assert await client.request(36) == REPLIES[36]

    asyncio.run(run())


def test_stray_chunks_are_ignored() -> None:
    """A middle or end chunk without a start is dropped."""

    async def run() -> None:
        car = FakeCar(REPLIES)
        client = await _client(car)
        car.before_reply = [b"\x12abc", b"\x03def"]
        assert await client.request(42) == REPLIES[42]

    asyncio.run(run())


def test_no_reply() -> None:
    """A command the car doesn't answer times out."""

    async def run() -> None:
        client = await _client(FakeCar({}))
        with (
            patch.object(car_ble, "COMMAND_TIMEOUT", 0.01),
            pytest.raises(PuraBluetoothError, match="No reply to command 42"),
        ):
            await client.request(42)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("reply", "match"),
    [
        (b"\xff\x00", "Couldn't decode"),
        (cbor2.dumps([42]), "Expected a map"),
    ],
)
def test_unreadable_reply(reply: bytes, match: str) -> None:
    """A reply that isn't a CBOR map is an error."""

    async def run() -> None:
        client = await _client(FakeCar({42: reply}))
        with pytest.raises(PuraBluetoothError, match=match):
            await client.request(42)

    asyncio.run(run())


@pytest.mark.parametrize(
    "replies",
    [
        REPLIES | {34: {"ack": 34}},
        REPLIES | {42: {"ack": 42, "intensity": 4}},
        REPLIES | {36: {"ack": 36, "status": 1, "fragrance": "FZYC"}},
    ],
)
def test_unexpected_reply(replies: dict[int, Any]) -> None:
    """Missing or out-of-range fields are an error."""

    async def run() -> None:
        client = await _client(FakeCar(replies))
        with pytest.raises(PuraBluetoothError, match="Unexpected reply"):
            await client.read_status()

    asyncio.run(run())


def test_write_failure() -> None:
    """A failed write, e.g. after the car disconnects, is an error."""

    async def run() -> None:
        car = FakeCar(REPLIES)
        car.client.write_gatt_char.side_effect = BleakError("disconnected")
        client = await _client(car)
        with pytest.raises(PuraBluetoothError, match="Command 1 failed"):
            await client.request(1)

    asyncio.run(run())


def _device() -> MagicMock:
    device = MagicMock()
    device.name = "PCarPro-8057"
    device.address = "E4:1A:01:DB:80:57"
    return device


def test_read_car_status() -> None:
    """Connect, read and disconnect."""
    car = FakeCar(REPLIES)
    with patch.object(
        car_ble, "establish_connection", AsyncMock(return_value=car.client)
    ) as connect:
        assert asyncio.run(read_car_status(_device())) == STATUS
    assert connect.call_args.args[2] == "PCarPro-8057"
    car.client.disconnect.assert_awaited_once()


def test_read_car_status_cant_connect() -> None:
    """A car that's off or out of range can't be connected to."""
    with (
        patch.object(
            car_ble, "establish_connection", AsyncMock(side_effect=BleakError("gone"))
        ),
        pytest.raises(PuraBluetoothError, match="Couldn't connect"),
    ):
        asyncio.run(read_car_status(_device()))


def test_read_car_status_disconnects_on_failure() -> None:
    """The connection is closed even if subscribing fails."""
    car = FakeCar(REPLIES)
    car.client.start_notify.side_effect = BleakError("disconnected")
    with (
        patch.object(
            car_ble, "establish_connection", AsyncMock(return_value=car.client)
        ),
        pytest.raises(PuraBluetoothError, match="Lost the connection"),
    ):
        asyncio.run(read_car_status(_device()))
    car.client.disconnect.assert_awaited_once()
