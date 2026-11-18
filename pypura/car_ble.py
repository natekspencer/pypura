"""Read a Pura Car diffuser's status over Bluetooth LE.

The car only syncs with the cloud through the Pura app, so its cloud data can be
stale or quite old. While a car is on it advertises CAR_SERVICE_UUID and answers commands
locally, without pairing.

Requests are CBOR maps like {"cmd": 36} written to the command characteristic. Each
reply is a CBOR map notified on the response characteristic, with the command it
answers as "ack". Messages are split into chunks that each start with a header
byte, whose low 2 bits say where the chunk falls in its message (see `_Reassembler`).
The car also notifies battery and intensity changes on its own, in the same format.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Final

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
import cbor2

from .exceptions import PuraBluetoothError
from .utils import redact_ids

_LOGGER = logging.getLogger(__name__)

CAR_SERVICE_UUID: Final = "effc7a09-9889-44b0-a80b-1ee079591945"
RESPONSE_UUID: Final = "effc5050-9889-44b0-a80b-1ee079591945"  # notify
COMMAND_UUID: Final = "effc5051-9889-44b0-a80b-1ee079591945"  # write

GET_DEVICE_DETAILS: Final = 1
GET_BATTERY_LEVEL: Final = 34
GET_FRAGRANCE_DETAILS: Final = 36
GET_FRAGRANCE_INTENSITY: Final = 42
GET_CARTRIDGE_ID: Final = 60

# The first firmware that answers GET_CARTRIDGE_ID, as in the app. A Car v1 (on
# 1.x firmware) doesn't reply to it at all.
CARTRIDGE_ID_FIRMWARE: Final = (9, 1, 14)

# What a car replies to a command it doesn't support, e.g. {"ack": 255, "err": 1}
_ERROR_ACK: Final = 255

# The fragrance a car reports for a pod it can't read
UNREADABLE_FRAGRANCE: Final = "NULL"
# The fragrance a car reports for its factory test pod, which the app treats the same
FACTORY_TEST_FRAGRANCE: Final = "BIST"
# The code the app reports an unknown pod as, which the cloud knows as
# "Unknown fragrance"
UNKNOWN_FRAGRANCE_CODE: Final = "UKNC"

# Intensity 0-3 in the cloud's words (its bay1.fanIntensity)
CAR_INTENSITIES: Final = ("off", "low", "medium", "high")

# How long the app waits for a reply
COMMAND_TIMEOUT: Final = 8.0

_SINGLE, _START, _CONTINUATION, _END = 0, 1, 2, 3


def _firmware_at_least(firmware: str, minimum: tuple[int, ...]) -> bool:
    """Whether a version like "9.1.16" is `minimum` or newer; False if unreadable."""
    try:
        version = tuple(int(part) for part in firmware.split("."))
    except ValueError:
        return False
    return version >= minimum


class PuraCarCommandError(PuraBluetoothError):
    """To indicate a Pura Car rejected a command, e.g. one its model doesn't support."""


@dataclass(frozen=True)
class PuraCarStatus:
    """A Pura Car's status, as read over Bluetooth."""

    mac: str
    """The car's MAC address, without separators (e.g. "AAAAAAAAAA04")."""
    firmware: str
    hardware: str
    battery_level: int | None
    """The battery's charge in percent, or `None` for a car without a battery (v1)."""
    charging: bool | None
    """Whether the battery is charging, or `None` for a car without a battery (v1)."""
    intensity: str
    """One of CAR_INTENSITIES. The car can stop by itself and keep this setting."""
    fragrance_code: str | None
    """The pod's fragrance code, or `None` without a pod or with an unknown one."""
    cartridge_id: str | None
    """The pod's ID (the cloud's bay1.refillId), or `None` without a pod.

    Also `None` on firmware older than 9.1.14, such as a Car v1's, which can't
    report it.
    """
    runtime: int | None
    """Seconds the pod has diffused (the cloud's bay1.wearingTime), or `None`."""
    unknown_pod: bool = False
    """Whether a pod is in that the car couldn't read.

    The car then reports its fragrance as "NULL" (or "BIST" for its factory test
    pod) and a runtime of 0, so
    `fragrance_code`, `cartridge_id` and `runtime` are `None`, as without a pod.
    """


class _Reassembler:
    """Join notified chunks into whole messages.

    A chunk's low 2 bits are 0 for a whole message, or 1, 2 and 3 for the start,
    middle and end of a longer one. The high 4 bits number the middle chunks, but
    notifications arrive in order, so they're joined in arrival order.
    """

    def __init__(self) -> None:
        self._parts: list[bytes] | None = None
        self.messages: asyncio.Queue[bytes] = asyncio.Queue()

    def clear(self) -> None:
        """Drop the messages received so far, including one still arriving.

        The rest of a dropped message is then ignored as stray chunks.
        """
        self._parts = None
        while not self.messages.empty():
            self.messages.get_nowait()

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        kind, body = chunk[0] & 3, chunk[1:]
        if kind == _SINGLE:
            self._parts = None
            self.messages.put_nowait(body)
        elif kind == _START:
            self._parts = [body]
        elif self._parts is None:
            _LOGGER.debug("Ignoring a chunk outside a message: %s", chunk.hex())
        else:
            self._parts.append(body)
            if kind == _END:
                self.messages.put_nowait(b"".join(self._parts))
                self._parts = None


class PuraCarClient:
    """Send commands to a connected Pura Car."""

    def __init__(self, client: BleakClient) -> None:
        """Initialize with a connected client; call `start` before sending commands."""
        self._client = client
        self._reassembler = _Reassembler()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Subscribe to the car's replies."""
        await self._client.start_notify(RESPONSE_UUID, self._on_notify)

    def _on_notify(self, _: BleakGATTCharacteristic, data: bytearray) -> None:
        self._reassembler.feed(bytes(data))

    async def request(self, cmd: int) -> dict[str, Any]:
        """Send a command and return the car's reply to it.

        Raises `PuraBluetoothError` if the car doesn't reply in time or the reply
        can't be read, or `PuraCarCommandError` if it rejects the command. Messages
        the car sends on its own meanwhile are skipped.
        """
        async with self._lock:
            # Requests are a few bytes, so they always fit in one chunk.
            data = bytes([_SINGLE]) + cbor2.dumps({"cmd": cmd})
            # Anything already queued came before this request: a message the car
            # sent by itself, or a late reply to one that timed out.
            self._reassembler.clear()
            try:
                # The write can stall too, so the deadline covers it
                async with asyncio.timeout(COMMAND_TIMEOUT):
                    await self._client.write_gatt_char(
                        COMMAND_UUID, data, response=True
                    )
                    while True:
                        reply = _decode(await self._reassembler.messages.get())
                        if reply.get("ack") == cmd:
                            return reply
                        if reply.get("ack") == _ERROR_ACK:
                            # Only one request is sent at a time, so it's this one's
                            raise PuraCarCommandError(
                                f"The car rejected command {cmd}: {reply}"
                            )
                        _LOGGER.debug(
                            "Skipping a message from the car: %s", redact_ids(reply)
                        )
            except TimeoutError as err:
                raise PuraBluetoothError(f"No reply to command {cmd}") from err
            except BleakError as err:
                raise PuraBluetoothError(f"Command {cmd} failed: {err}") from err

    async def read_status(self) -> PuraCarStatus:
        """Read the car's status."""
        details = await self.request(GET_DEVICE_DETAILS)
        try:
            battery = await self.request(GET_BATTERY_LEVEL)
        except PuraCarCommandError:
            battery = None  # a Car v1, which runs on the car's power
        fragrance = await self.request(GET_FRAGRANCE_DETAILS)
        intensity = await self.request(GET_FRAGRANCE_INTENSITY)
        has_pod = fragrance.get("status") == 1
        unknown_pod = has_pod and fragrance.get("fragrance") in (
            UNREADABLE_FRAGRANCE,
            FACTORY_TEST_FRAGRANCE,
        )
        known_pod = has_pod and not unknown_pod
        firmware = str(details.get("fw", ""))
        has_cartridge_id = known_pod and _firmware_at_least(
            firmware, CARTRIDGE_ID_FIRMWARE
        )
        cartridge = await self.request(GET_CARTRIDGE_ID) if has_cartridge_id else {}
        try:
            return PuraCarStatus(
                mac=str(details["mac"]).upper(),
                firmware=str(details["fw"]),
                hardware=str(details["hw"]),
                battery_level=int(battery["level"]) if battery else None,
                charging=bool(battery["charging"]) if battery else None,
                intensity=CAR_INTENSITIES[int(intensity["intensity"])],
                fragrance_code=str(fragrance["fragrance"]) if known_pod else None,
                cartridge_id=str(cartridge["id"]) if has_cartridge_id else None,
                runtime=int(fragrance["runtime"]) if known_pod else None,
                unknown_pod=unknown_pod,
            )
        except (KeyError, IndexError, TypeError, ValueError) as err:
            raise PuraBluetoothError(f"Unexpected reply from the car: {err!r}") from err


def _decode(message: bytes) -> dict[str, Any]:
    try:
        reply = cbor2.loads(message)
    except (cbor2.CBORDecodeError, ValueError) as err:
        raise PuraBluetoothError(f"Couldn't decode {message.hex()}") from err
    if not isinstance(reply, dict):
        raise PuraBluetoothError(f"Expected a map, got {reply!r}")
    return reply


async def read_car_status(device: BLEDevice) -> PuraCarStatus:
    """Connect to a Pura Car, read its status and disconnect.

    The car only accepts connections while it's on. Raises `PuraBluetoothError` if
    it can't be reached or doesn't answer.
    """
    try:
        client = await establish_connection(
            BleakClientWithServiceCache, device, device.name or device.address
        )
    except (BleakError, TimeoutError) as err:
        raise PuraBluetoothError(f"Couldn't connect to {device.address}") from err
    try:
        car = PuraCarClient(client)
        await car.start()
        return await car.read_status()
    except BleakError as err:
        raise PuraBluetoothError(f"Lost the connection to {device.address}") from err
    finally:
        await client.disconnect()


# The app's words for the intensities, which differ from the cloud's car data
_EVENT_INTENSITIES: Final = {
    "off": "off",
    "low": "low",
    "medium": "med",
    "high": "high",
}


def car_status_events(
    status: PuraCarStatus, read_at: float, *, pod_changed: bool = False
) -> list[dict[str, Any]]:
    """Return the events the Pura app reports to the cloud after reading a car.

    These are for `Pura.send_car_events`. As the app does on connecting, they
    report the car's intensity, firmware, pod, runtime and battery. The app sends
    the battery as a separate event; it's sent in the connect event here, which the
    cloud applies the same way, to make fewer writes.

    Args:
        status: The car's status.
        read_at: When it was read, in seconds since the epoch.
        pod_changed: Whether the pod differs from the cloud's (see
            `cloud_fragrance_code`), which the app reports as a separate event.

    Fields without a value, such as the pod's ID on a Car v1, are left out, except a
    removed pod's code, which the cloud requires (as null).
    """
    at = int(read_at * 1000)  # the app reports milliseconds
    events: list[dict[str, Any]] = []
    if pod_changed:
        events.append(_pod_event(status, at))
    events.append(_connect_event(status, at))
    if status.battery_level == 0:  # which the cloud ignores in a connect
        events.append(_battery_event("battery", status, at))
    return events


def car_change_events(
    before: PuraCarStatus,
    after: PuraCarStatus,
    read_at: float,
    *,
    runtime_step: int = 0,
    reported_runtime: int | None = None,
) -> list[dict[str, Any]]:
    """Return the events reporting how a car changed between two readings.

    These are for `Pura.send_car_events`, after `car_status_events` reported the
    first reading: a new or removed pod, a new intensity (always with a new pod),
    the battery starting or stopping charging or its level changing, and the pod's
    runtime.

    The runtime is reported with a connect event, not the app's data event, since
    only a connect moves the cloud's lastConnectedAt and bay1.activeAt on. That
    connect also carries the intensity and battery, so they aren't sent again,
    except for the battery starting or stopping charging, and an empty battery,
    which the cloud ignores in a connect.

    Args:
        before: The previous reading.
        after: The new reading.
        read_at: When it was read, in seconds since the epoch.
        runtime_step: Report the runtime only once it has grown by this many seconds
            since `reported_runtime`, since it grows the whole time the car diffuses.
        reported_runtime: The runtime last reported, if not `before`'s. With a
            `runtime_step`, keep it across batches without a runtime report (no
            changedFruck or connect event), or changes reported in between would keep
            postponing the runtime report.
    """
    at = int(read_at * 1000)
    baseline = before.runtime if reported_runtime is None else reported_runtime
    events: list[dict[str, Any]] = []
    pod_changed = (cloud_fragrance_code(before), before.cartridge_id) != (
        cloud_fragrance_code(after),
        after.cartridge_id,
    ) or _swapped_without_ids(before, after)
    if pod_changed:
        events.append(_pod_event(after, at))
    runtime_due = (
        not pod_changed
        and after.fragrance_code is not None
        and after.runtime is not None
        and after.runtime - (baseline or 0) >= max(runtime_step, 1)
    )
    # A new pod's intensity is sent even if unchanged, so the cloud's new bay has
    # the car's rather than whatever the cloud starts a bay with
    pod_inserted = pod_changed and cloud_fragrance_code(after) is not None
    if runtime_due:
        events.append(_connect_event(after, at))
    elif after.intensity != before.intensity or pod_inserted:
        events.append(
            {
                "eventType": "diffusionChanged",
                "fanIntensity": _EVENT_INTENSITIES[after.intensity],
                "informationSynthesizedTime": at,
            }
        )
    if after.charging is not None and after.charging != before.charging:
        event_type = "startCharging" if after.charging else "stopCharging"
        events.append(_battery_event(event_type, after, at))
    elif (
        after.battery_level is not None
        and after.battery_level != before.battery_level
        # A connect carries any level but 0
        and not (runtime_due and after.battery_level)
    ):
        events.append(_battery_event("battery", after, at))
    return events


def _swapped_without_ids(before: PuraCarStatus, after: PuraCarStatus) -> bool:
    """Whether a pod was swapped for another with the same code, between readings.

    Without pod IDs (a Car v1) the pods can't be told apart, but a runtime only
    goes down for a different pod. The app reports a pod change on every reading
    of a Car v1's pod, so it reports such a swap too.
    """
    return (
        before.cartridge_id is None
        and after.cartridge_id is None
        and before.runtime is not None
        and after.runtime is not None
        and after.runtime < before.runtime
    )


def cloud_fragrance_code(status: PuraCarStatus) -> str | None:
    """Return the pod's code as the app reports it, the cloud's bay1.code.

    That's UNKNOWN_FRAGRANCE_CODE for a pod the car can't read, or `None` without one.
    """
    return UNKNOWN_FRAGRANCE_CODE if status.unknown_pod else status.fragrance_code


def _connect_event(status: PuraCarStatus, at: int) -> dict[str, Any]:
    """Return the event reporting a reading, as the app does on connecting."""
    return _without_none(
        {
            "eventType": "connect",
            "fanIntensity": _EVENT_INTENSITIES[status.intensity],
            "firmwareVersion": status.firmware,
            "fragranceWeartime": status.runtime or 0,
            # The cloud leaves the battery alone for 0, as a Car v1 has none
            "batteryRemaining": status.battery_level or 0,
            "refillId": status.cartridge_id,
            "informationSynthesizedTime": at,
        }
    )


def _battery_event(event_type: str, status: PuraCarStatus, at: int) -> dict[str, Any]:
    """Return a battery, startCharging or stopCharging event."""
    return {
        "eventType": event_type,
        "batteryRemaining": status.battery_level or 0,
        "informationSynthesizedTime": at,
    }


def _pod_event(status: PuraCarStatus, at: int) -> dict[str, Any]:
    """Return the event reporting the car's pod, or that it has none."""
    # The cloud requires the code, so a removed pod is reported with a null one, as
    # the app does
    code = cloud_fragrance_code(status)
    return {"eventType": "changedFruck", "fragranceCode": code} | (
        _without_none(
            {
                "fragranceWeartime": status.runtime or 0,
                "refillId": status.cartridge_id,
                "informationSynthesizedTime": at,
            }
        )
    )


def _without_none(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if value is not None}
