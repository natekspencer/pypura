"""Read a Pura Car diffuser's status over Bluetooth LE.

The car only syncs with the cloud through the Pura app, so its cloud data can be
hours old. While a car is on it advertises CAR_SERVICE_UUID and answers commands
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

_LOGGER = logging.getLogger(__name__)

CAR_SERVICE_UUID: Final = "effc7a09-9889-44b0-a80b-1ee079591945"
RESPONSE_UUID: Final = "effc5050-9889-44b0-a80b-1ee079591945"  # notify
COMMAND_UUID: Final = "effc5051-9889-44b0-a80b-1ee079591945"  # write

GET_DEVICE_DETAILS: Final = 1
GET_BATTERY_LEVEL: Final = 34
GET_FRAGRANCE_DETAILS: Final = 36
GET_FRAGRANCE_INTENSITY: Final = 42
GET_CARTRIDGE_ID: Final = 60

# Intensity 0-3 in the cloud's words (its bay1.fanIntensity)
CAR_INTENSITIES: Final = ("off", "low", "medium", "high")

# How long the app waits for a reply
COMMAND_TIMEOUT: Final = 8.0

_SINGLE, _START, _CONTINUATION, _END = 0, 1, 2, 3


@dataclass(frozen=True)
class PuraCarStatus:
    """A Pura Car's status, as read over Bluetooth."""

    mac: str
    """The car's MAC address, without separators (e.g. "AAAAAAAAAA04")."""
    firmware: str
    hardware: str
    battery_level: int
    charging: bool
    intensity: str
    """One of CAR_INTENSITIES. The car can stop by itself and keep this setting."""
    fragrance_code: str | None
    """The pod's fragrance code, or `None` without a pod."""
    cartridge_id: str | None
    """The pod's ID (the cloud's bay1.refillId), or `None` without a pod."""
    runtime: int | None
    """Seconds the pod has diffused (the cloud's bay1.wearingTime), or `None`."""


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
        can't be read. Messages the car sends on its own meanwhile are skipped.
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
                        _LOGGER.debug("Skipping a message from the car: %s", reply)
            except TimeoutError as err:
                raise PuraBluetoothError(f"No reply to command {cmd}") from err
            except BleakError as err:
                raise PuraBluetoothError(f"Command {cmd} failed: {err}") from err

    async def read_status(self) -> PuraCarStatus:
        """Read the car's status."""
        details = await self.request(GET_DEVICE_DETAILS)
        battery = await self.request(GET_BATTERY_LEVEL)
        fragrance = await self.request(GET_FRAGRANCE_DETAILS)
        intensity = await self.request(GET_FRAGRANCE_INTENSITY)
        has_pod = fragrance.get("status") == 1
        cartridge = await self.request(GET_CARTRIDGE_ID) if has_pod else {}
        try:
            return PuraCarStatus(
                mac=str(details["mac"]).upper(),
                firmware=str(details["fw"]),
                hardware=str(details["hw"]),
                battery_level=int(battery["level"]),
                charging=bool(battery["charging"]),
                intensity=CAR_INTENSITIES[int(intensity["intensity"])],
                fragrance_code=str(fragrance["fragrance"]) if has_pod else None,
                cartridge_id=str(cartridge["id"]) if has_pod else None,
                runtime=int(fragrance["runtime"]) if has_pod else None,
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
