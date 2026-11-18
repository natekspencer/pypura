"""Tests for reading a Pura Car over Bluetooth."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
import logging
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
    PuraCarCommandError,
    PuraCarStatus,
    car_change_events,
    car_status_events,
    cloud_fragrance_code,
    read_car_status,
)

# Replies from a Car Pro on firmware 9.1.16
REPLIES: dict[int, dict[str, Any]] = {
    1: {"ack": 1, "mac": "AAAAAAAAAA04", "fw": "9.1.16", "hw": "27.5", "env": 0},
    34: {"ack": 34, "level": 85, "charging": 1, "state": 0},
    36: {"ack": 36, "status": 1, "fragrance": "FZYC", "runtime": 190411},
    42: {"ack": 42, "intensity": 1},
    60: {"ack": 60, "id": "00000000-0000-4000-8000-000000000001"},
}
STATUS = PuraCarStatus(
    mac="AAAAAAAAAA04",
    firmware="9.1.16",
    hardware="27.5",
    battery_level=85,
    charging=True,
    intensity="low",
    fragrance_code="FZYC",
    cartridge_id="00000000-0000-4000-8000-000000000001",
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
        assert status.unknown_pod is False

    asyncio.run(run())


def test_read_status_without_battery() -> None:
    """A car without a battery (v1) rejects the battery request, which isn't an error."""

    async def run() -> None:
        car = FakeCar(REPLIES | {34: {"ack": 255, "err": 1}})
        status = await (await _client(car)).read_status()
        assert (status.battery_level, status.charging) == (None, None)
        assert status.fragrance_code == "FZYC"

    asyncio.run(run())


def test_read_status_car_v1() -> None:
    """A Car v1 (firmware 1.5.3) has no battery and isn't asked for its pod ID."""
    v1_replies = {
        1: {"ack": 1, "mac": "AAAAAAAAAA01", "fw": "1.5.3", "hw": "1.5", "env": 2},
        34: {"ack": 255, "err": 1},
        36: {
            "ack": 36,
            "status": 1,
            "fragrance": "VNTC",
            "LowOn": 20,
            "LowOff": 40,
            "runtime": 7320,
        },
        42: {"ack": 42, "intensity": 3},
        # A v1 doesn't reply to 60 at all
    }

    async def run() -> None:
        car = FakeCar(v1_replies)
        with patch.object(car_ble, "COMMAND_TIMEOUT", 0.01):
            status = await (await _client(car)).read_status()
        assert status == PuraCarStatus(
            mac="AAAAAAAAAA01",
            firmware="1.5.3",
            hardware="1.5",
            battery_level=None,
            charging=None,
            intensity="high",
            fragrance_code="VNTC",
            cartridge_id=None,
            runtime=7320,
        )
        assert car.sent == [1, 34, 36, 42]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("firmware", "expected"),
    [
        ("9.1.14", True),
        ("9.1.16", True),
        ("9.2.0", True),  # the app's check gets these two wrong
        ("10.0.0", True),
        ("9.1.13", False),
        ("1.5.3", False),
        ("", False),
        ("9.1.x", False),
    ],
)
def test_firmware_at_least(firmware: str, expected: bool) -> None:
    """Versions compare as numbers, part by part; unreadable ones are too old."""
    assert (
        car_ble._firmware_at_least(firmware, car_ble.CARTRIDGE_ID_FIRMWARE) is expected
    )


def test_rejected_command() -> None:
    """A rejected command raises PuraCarCommandError, without waiting for a timeout."""

    async def run() -> None:
        car = FakeCar({42: {"ack": 255, "err": 1}})
        client = await _client(car)
        with patch.object(car_ble, "COMMAND_TIMEOUT", 60):
            async with asyncio.timeout(1):
                with pytest.raises(PuraCarCommandError, match="rejected command 42"):
                    await client.request(42)

    asyncio.run(run())


@pytest.mark.parametrize("fragrance", ["NULL", "BIST"])
def test_read_status_unknown_pod(fragrance: str) -> None:
    """A pod the car can't read is reported as unknown, without its runtime of 0.

    The app treats the car's factory test pod ("BIST") the same.
    """
    unreadable = {
        "ack": 36,
        "status": 1,
        "fragrance": fragrance,
        "LowOn": 30,
        "LowOff": 30,
        "runtime": 0,
    }

    async def run() -> None:
        car = FakeCar(REPLIES | {36: unreadable})
        status = await (await _client(car)).read_status()
        assert status.unknown_pod is True
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


def test_skipped_messages_log_redacted_mac(caplog: pytest.LogCaptureFixture) -> None:
    """A skipped message keeps only the last 4 digits of the car's MAC in the log."""

    async def run() -> None:
        car = FakeCar(REPLIES)
        client = await _client(car)
        car.before_reply = chunks(cbor2.dumps(REPLIES[1]))
        with caplog.at_level(logging.DEBUG, logger="pypura.car_ble"):
            assert await client.request(42) == REPLIES[42]

    asyncio.run(run())
    assert "'mac': '**REDACTED**AA04'" in caplog.text
    assert "AAAAAAAAAA04" not in caplog.text


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


def test_stalled_write() -> None:
    """A write that never completes times out too."""

    async def run() -> None:
        car = FakeCar(REPLIES)

        async def stall(*args: Any, **kwargs: Any) -> None:
            await asyncio.Event().wait()

        car.client.write_gatt_char.side_effect = stall
        client = await _client(car)
        with (
            patch.object(car_ble, "COMMAND_TIMEOUT", 0.01),
            pytest.raises(PuraBluetoothError, match="No reply to command 42"),
        ):
            await client.request(42)
        # The lock is released for the next request
        car.client.write_gatt_char.side_effect = car._write
        assert await client.request(42) == REPLIES[42]

    asyncio.run(run())


def test_messages_before_request_are_dropped() -> None:
    """A message received before a request isn't taken as its reply."""

    async def run() -> None:
        car = FakeCar(REPLIES)
        client = await _client(car)
        assert car.callback
        # An intensity change the car pushed earlier, and the start of another
        # message whose end arrives after the request is sent
        for chunk in chunks(cbor2.dumps({"ack": 42, "intensity": 3})):
            car.callback(MagicMock(), bytearray(chunk))
        start, *rest = chunks(cbor2.dumps(REPLIES[60] | {"ack": 42}))
        car.callback(MagicMock(), bytearray(start))
        car.before_reply = rest
        assert await client.request(42) == REPLIES[42]

    asyncio.run(run())


def test_late_reply_is_dropped() -> None:
    """A reply arriving after its request timed out isn't taken for the next."""

    async def run() -> None:
        car = FakeCar({})
        client = await _client(car)
        with (
            patch.object(car_ble, "COMMAND_TIMEOUT", 0.01),
            pytest.raises(PuraBluetoothError),
        ):
            await client.request(42)
        assert car.callback
        for chunk in chunks(cbor2.dumps({"ack": 42, "intensity": 3})):
            car.callback(MagicMock(), bytearray(chunk))
        car.replies = REPLIES
        assert await client.request(42) == REPLIES[42]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("reply", "match"),
    [
        (b"\xa1", "Couldn't decode"),  # a map cut short
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
    device.name = "PCarPro-AA04"
    device.address = "AA:AA:AA:AA:AA:04"
    return device


def test_read_car_status() -> None:
    """Connect, read and disconnect."""
    car = FakeCar(REPLIES)
    with patch.object(
        car_ble, "establish_connection", AsyncMock(return_value=car.client)
    ) as connect:
        assert asyncio.run(read_car_status(_device())) == STATUS
    assert connect.call_args.args[2] == "PCarPro-AA04"
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


READ_AT = 1794700000.25  # seconds; the app reports milliseconds
CAR_V1 = PuraCarStatus(
    mac="AAAAAAAAAA01",
    firmware="1.5.3",
    hardware="1.5",
    battery_level=None,
    charging=None,
    intensity="medium",
    fragrance_code="VNTC",
    cartridge_id=None,
    runtime=7320,
)


def test_car_status_events() -> None:
    """A reading is reported like the app does on connecting, battery included."""
    assert car_status_events(STATUS, READ_AT) == [
        {
            "eventType": "connect",
            "fanIntensity": "low",
            "firmwareVersion": "9.1.16",
            "fragranceWeartime": 190411,
            "batteryRemaining": 85,
            "refillId": "00000000-0000-4000-8000-000000000001",
            "informationSynthesizedTime": 1794700000250,
        }
    ]


def test_car_status_events_car_v1() -> None:
    """A Car v1 has no battery (sent as 0, which the cloud ignores) or pod ID, and
    medium is "med" to the app."""
    assert car_status_events(CAR_V1, READ_AT) == [
        {
            "eventType": "connect",
            "fanIntensity": "med",
            "firmwareVersion": "1.5.3",
            "fragranceWeartime": 7320,
            "batteryRemaining": 0,
            "informationSynthesizedTime": 1794700000250,
        }
    ]


def test_car_status_events_pod_changed() -> None:
    """A changed pod is reported first."""
    events = car_status_events(STATUS, READ_AT, pod_changed=True)
    assert [event["eventType"] for event in events] == [
        "changedFruck",
        "connect",
    ]
    assert events[0] == {
        "eventType": "changedFruck",
        "fragranceCode": "FZYC",
        "fragranceWeartime": 190411,
        "refillId": "00000000-0000-4000-8000-000000000001",
        "informationSynthesizedTime": 1794700000250,
    }


def test_car_status_events_pod_removed() -> None:
    """A removed pod is reported with a null code, no ID, and no runtime."""
    no_pod = PuraCarStatus(
        **CAR_V1.__dict__
        | {"intensity": "off", "fragrance_code": None, "runtime": None}
    )
    events = car_status_events(no_pod, READ_AT, pod_changed=True)
    assert events == [
        {
            "eventType": "changedFruck",
            "fragranceCode": None,  # the cloud requires it
            "fragranceWeartime": 0,
            "informationSynthesizedTime": 1794700000250,
        },
        {
            "eventType": "connect",
            "fanIntensity": "off",
            "firmwareVersion": "1.5.3",
            "fragranceWeartime": 0,
            "batteryRemaining": 0,
            "informationSynthesizedTime": 1794700000250,
        },
    ]


def test_car_status_events_unknown_pod() -> None:
    """A pod the car can't identify is reported as unknown, as the app does."""
    unknown = PuraCarStatus(
        **CAR_V1.__dict__
        | {"fragrance_code": None, "runtime": None, "unknown_pod": True}
    )
    events = car_status_events(unknown, READ_AT, pod_changed=True)
    assert events[0] == {
        "eventType": "changedFruck",
        "fragranceCode": "UKNC",
        "fragranceWeartime": 0,
        "informationSynthesizedTime": 1794700000250,
    }
    assert [event["eventType"] for event in events] == ["changedFruck", "connect"]


AT = 1794700000250  # READ_AT in milliseconds


def test_car_change_events_none() -> None:
    """Nothing is reported for an unchanged reading."""
    assert car_change_events(STATUS, STATUS, READ_AT) == []


def test_car_change_events_pod() -> None:
    """A new pod is reported with its code, ID, runtime and intensity."""
    after = replace(STATUS, fragrance_code="VNTC", cartridge_id="new", runtime=60)
    assert car_change_events(STATUS, after, READ_AT) == [
        {
            "eventType": "changedFruck",
            "fragranceCode": "VNTC",
            "fragranceWeartime": 60,
            "refillId": "new",
            "informationSynthesizedTime": AT,
        },
        # Sent though unchanged, so the cloud's new bay has the car's intensity
        {
            "eventType": "diffusionChanged",
            "fanIntensity": "low",
            "informationSynthesizedTime": AT,
        },
    ]


def test_car_change_events_same_code_swap() -> None:
    """A pod swapped for one with the same code is reported if its runtime is lower.

    A Car v1 has no pod IDs, so that's the only sign of a different pod.
    """
    after = replace(CAR_V1, runtime=60)
    assert car_change_events(CAR_V1, after, READ_AT) == [
        {
            "eventType": "changedFruck",
            "fragranceCode": "VNTC",
            "fragranceWeartime": 60,
            "informationSynthesizedTime": AT,
        },
        {
            "eventType": "diffusionChanged",
            "fanIntensity": "med",
            "informationSynthesizedTime": AT,
        },
    ]
    # With pod IDs, the same ID is the same pod, whatever its runtime
    assert car_change_events(STATUS, replace(STATUS, runtime=60), READ_AT) == []


def test_car_change_events_same_code_swap_after_unreported_runtime() -> None:
    """A swap is found from the previous reading's runtime, not the one reported.

    Runtime 0 was reported, 200 wasn't (only an intensity change was), and the new
    pod has 60: that's a drop from 200, though it's more than 0.
    """
    previous = replace(CAR_V1, runtime=200)
    after = replace(CAR_V1, runtime=60)
    events = car_change_events(
        previous, after, READ_AT, runtime_step=300, reported_runtime=0
    )
    assert [event["eventType"] for event in events] == [
        "changedFruck",
        "diffusionChanged",
    ]


def test_car_change_events_pod_removed() -> None:
    """A removed pod is reported with a null code."""
    after = replace(CAR_V1, fragrance_code=None, runtime=None, intensity="off")
    assert car_change_events(CAR_V1, after, READ_AT) == [
        {
            "eventType": "changedFruck",
            "fragranceCode": None,
            "fragranceWeartime": 0,
            "informationSynthesizedTime": AT,
        },
        {
            "eventType": "diffusionChanged",
            "fanIntensity": "off",
            "informationSynthesizedTime": AT,
        },
    ]


def test_car_change_events_unknown_pod() -> None:
    """A pod the car can't identify is reported as unknown, and then left alone."""
    after = replace(CAR_V1, fragrance_code=None, runtime=None, unknown_pod=True)
    assert car_change_events(CAR_V1, after, READ_AT) == [
        {
            "eventType": "changedFruck",
            "fragranceCode": "UKNC",
            "fragranceWeartime": 0,
            "informationSynthesizedTime": AT,
        },
        {
            "eventType": "diffusionChanged",
            "fanIntensity": "med",
            "informationSynthesizedTime": AT,
        },
    ]
    assert car_change_events(after, after, READ_AT) == []


def test_cloud_fragrance_code() -> None:
    """The pod's code as the cloud has it: unknown pods as UKNC, none as None."""
    assert cloud_fragrance_code(CAR_V1) == "VNTC"
    assert cloud_fragrance_code(replace(CAR_V1, fragrance_code=None)) is None
    unknown = replace(CAR_V1, fragrance_code=None, unknown_pod=True)
    assert cloud_fragrance_code(unknown) == "UKNC"


def test_car_change_events_intensity() -> None:
    """A new intensity is reported in the event's words, e.g. "med" for medium."""
    after = replace(STATUS, intensity="medium")
    assert car_change_events(STATUS, after, READ_AT) == [
        {
            "eventType": "diffusionChanged",
            "fanIntensity": "med",
            "informationSynthesizedTime": AT,
        }
    ]


@pytest.mark.parametrize(
    ("charging", "level", "event_type"),
    [(False, 85, "stopCharging"), (True, 86, "battery")],
)
def test_car_change_events_battery(charging: bool, level: int, event_type: str) -> None:
    """The battery starting or stopping charging, or a new level, is reported."""
    after = replace(STATUS, charging=charging, battery_level=level)
    assert car_change_events(STATUS, after, READ_AT) == [
        {
            "eventType": event_type,
            "batteryRemaining": level,
            "informationSynthesizedTime": AT,
        }
    ]


def test_car_change_events_start_charging() -> None:
    """The battery starting to charge is reported with its level."""
    before = replace(STATUS, charging=False)
    assert car_change_events(before, STATUS, READ_AT) == [
        {
            "eventType": "startCharging",
            "batteryRemaining": 85,
            "informationSynthesizedTime": AT,
        }
    ]


def test_car_change_events_runtime() -> None:
    """The runtime is reported once it has grown by the step, with a connect.

    That moves the cloud's times on, unlike the app's data event, and carries any
    other change too.
    """
    before = replace(STATUS, runtime=STATUS.runtime - 300)  # type: ignore[operator]
    assert car_change_events(before, STATUS, READ_AT, runtime_step=301) == []
    connect = {
        "eventType": "connect",
        "fanIntensity": "low",
        "firmwareVersion": "9.1.16",
        "fragranceWeartime": 190411,
        "batteryRemaining": 85,
        "refillId": "00000000-0000-4000-8000-000000000001",
        "informationSynthesizedTime": AT,
    }
    assert car_change_events(before, STATUS, READ_AT, runtime_step=300) == [connect]
    # The intensity and battery go in the same connect
    before = replace(before, intensity="high", battery_level=90)
    assert car_change_events(before, STATUS, READ_AT, runtime_step=300) == [connect]


def test_car_change_events_runtime_since_reported() -> None:
    """The step counts from the runtime last reported, not the previous reading."""
    previous = replace(STATUS, runtime=STATUS.runtime - 100)  # type: ignore[operator]
    reported = STATUS.runtime - 300  # type: ignore[operator]
    assert car_change_events(previous, STATUS, READ_AT, runtime_step=300) == []
    events = car_change_events(
        previous, STATUS, READ_AT, runtime_step=300, reported_runtime=reported
    )
    assert [event["eventType"] for event in events] == ["connect"]


def test_car_change_events_runtime_with_charging() -> None:
    """The battery starting or stopping charging is still reported with a connect."""
    before = replace(STATUS, runtime=STATUS.runtime - 300, charging=False)  # type: ignore[operator]
    events = car_change_events(before, STATUS, READ_AT, runtime_step=300)
    assert [event["eventType"] for event in events] == ["connect", "startCharging"]
    assert events[1]["batteryRemaining"] == 85


def test_car_change_events_runtime_with_empty_battery() -> None:
    """An empty battery is still reported with a connect, which can't carry 0."""
    after = replace(STATUS, battery_level=0)
    before = replace(STATUS, runtime=STATUS.runtime - 300, battery_level=1)  # type: ignore[operator]
    events = car_change_events(before, after, READ_AT, runtime_step=300)
    assert [event["eventType"] for event in events] == ["connect", "battery"]
    assert events[1]["batteryRemaining"] == 0


def test_car_status_events_empty_battery() -> None:
    """An empty battery is reported separately, since a connect can't carry 0."""
    events = car_status_events(replace(STATUS, battery_level=0), READ_AT)
    assert [event["eventType"] for event in events] == ["connect", "battery"]
    assert events[1]["batteryRemaining"] == 0
