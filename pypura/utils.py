"""Utilities."""

from __future__ import annotations

from base64 import b64decode
from datetime import datetime, timezone
import logging
from typing import Any, Final

from .const import DEVICE_VERSION_MODEL_MAP, MODEL_TYPE_MAP

_LOGGER = logging.getLogger(__name__)

ENCODING: Final = "utf-8"

EVENT_INSERT = "INSERT"
EVENT_MODIFY = "MODIFY"
EVENT_REMOVE = "REMOVE"

RECORD_TYPE_DEVICE = "DEVICE"
RECORD_TYPE_SCHEDULE = "SCHEDULE"
RECORD_TYPE_TIMER = "TIMER"


def decode(value: str) -> str:
    """Decode a value."""
    return b64decode(value).decode(ENCODING)


def dig(obj: Any, path: str) -> Any | None:
    """Safely retrieve nested dictionary fields using a dot-separated path."""
    cur = obj
    for key in path.split("."):
        if not isinstance(cur, dict):
            return None
        if (cur := cur.get(key)) is None:
            return None
    return cur


def get_device_name(device: dict[str, Any]) -> str:
    """Get the device name from a dictionary."""
    if not (name := dig(device, "displayName.name") or device.get("roomName")):
        return "Diffuser"
    return name if "diffuser" in name.lower() else f"{name} Diffuser"


def get_fragrance_name(device: dict, bay: int | str) -> str | None:
    """Get the fragrance name."""
    bay_data = device.get(f"bay{bay}") or {}
    if (fragrance := bay_data.get("fragrance") or {}) and (
        name := fragrance.get("name")
    ):
        return str(name)
    return f"Fragrance: {code}" if (code := bay_data.get("code")) else None


def get_fragrance_remaining(device: dict, bay: int | str) -> float | None:
    """Get the fragrance remaining."""
    bay_data = device.get(f"bay{bay}") or {}
    if (remaining := bay_data.get("remaining")) and "percent" in remaining:
        return float(remaining["percent"])
    if (fragrance := bay_data.get("fragrance")) and "expectedLifeHours" in fragrance:
        expected_life: int = fragrance["expectedLifeHours"] * 3600
        return (
            max(expected_life - get_fragrance_runtime(device, bay), 0) / expected_life
        ) * 100
    return None


def get_fragrance_runtime(device: dict, bay: int | str) -> int:
    """Get the fragrance runtime."""
    bay_data = device.get(f"bay{bay}") or {}
    wearing_time: int = bay_data.get("wearingTime") or 0
    if (active_at := bay_data.get("activeAt")) and not device.get("lastConnectedAt"):
        active_time = datetime.now(timezone.utc) - datetime.fromtimestamp(
            active_at, timezone.utc
        )
        wearing_time += int(active_time.total_seconds())
    return wearing_time


def get_model_name(device: dict[str, Any]) -> str:
    """Get the device model name from a dictionary."""
    if not (model := DEVICE_VERSION_MODEL_MAP.get(device.get("deviceVer", ""))):
        model = DEVICE_VERSION_MODEL_MAP.get(
            MODEL_TYPE_MAP.get(device.get("model", 0), "")
        )
    return f"Pura {model}" if model else "Pura"


def has_fragrance(device: dict[str, Any], bay: int) -> bool:
    """Check if the specified bay has a fragrance."""
    return bool((bay_data := device.get(f"bay{bay}")) and bay_data.get("code"))


def parse_intensity(intensity: int | str) -> str:
    """Parse the intensity into a string representation.

    1 = subtle,
    2-3 = light,
    4-5 = medium,
    6-7 = pronounced,
    8-10 = strong
    """
    if intensity and isinstance(intensity, int):
        if intensity <= 1:
            return "subtle"
        if intensity <= 3:
            return "light"
        if intensity <= 5:
            return "medium"
        if intensity <= 7:
            return "pronounced"
        return "strong"
    return intensity or "off"


def merge_websocket_update(
    devices: dict[str, dict[str, Any]], update: dict[str, Any]
) -> None:
    """Merge a device update from a websocket message with the full device list.

    Mutates `devices` in place.
    """
    if not (device_id := update.get("deviceId")):
        _LOGGER.warning("Received unknown update: %s", update)
        return

    event_type = update.get("eventType")
    record_type = update.get("recordType")

    if record_type == RECORD_TYPE_DEVICE:
        _merge_device_record(devices, device_id, event_type, update)
        return

    device = devices.get(device_id)
    if device is None:
        _LOGGER.debug("Device %s does not exist, skipping: %s", device_id, update)
        return

    if record_type == RECORD_TYPE_TIMER:
        _merge_timer_record(device, device_id, event_type, update)
    elif record_type == RECORD_TYPE_SCHEDULE:
        _merge_schedule_record(device, device_id, event_type, update)
    else:
        _LOGGER.warning("Received unknown update: %s", update)


def _merge_device_record(
    devices: dict[str, dict[str, Any]],
    device_id: str,
    event_type: str | None,
    update: dict[str, Any],
) -> None:
    """Merge a device record."""
    if event_type == EVENT_REMOVE:
        if devices.pop(device_id, None) is not None:
            _LOGGER.debug("Removed device %s", device_id)
        else:
            _LOGGER.debug("Device %s does not exist, no need to remove", device_id)
        return

    if not isinstance(record := update.get("deviceRecord"), dict):
        _LOGGER.debug("Cannot update device %s: %s", device_id, update)
        return

    if not record.get("deviceId"):
        # deviceId may be unpopulated on deviceRecord inserts, so we set it
        record["deviceId"] = device_id

    if event_type == EVENT_INSERT:
        if device_id in devices:
            _LOGGER.debug("Device %s exists, updating", device_id)
            _merge_device_update(devices[device_id], record)
        else:
            _LOGGER.debug("Insert device %s", device_id)
            model_type = MODEL_TYPE_MAP.get(record.get("model", 0))
            devices[device_id] = record | {"modelType": model_type}
    elif event_type == EVENT_MODIFY:
        if device_id in devices:
            _LOGGER.debug("Updated device %s", device_id)
            _merge_device_update(devices[device_id], record)
        else:
            _LOGGER.debug(
                "Device %s does not exist, skipping update: %s", device_id, record
            )
    else:
        _LOGGER.warning("Received unknown update: %s", update)


def _merge_device_update(device: dict[str, Any], update: dict[str, Any]) -> None:
    """Recursively merge a device update into an existing device dict.

    Mutates `device` in place. Any changed "code" value invalidates the
    sibling "fragrance" entry if missing from the update.
    """
    for key, value in update.items():
        if key in device and isinstance(device[key], dict) and isinstance(value, dict):
            _merge_device_update(device[key], value)
        elif key == "code" and "fragrance" in device and "fragrance" not in update:
            device[key] = value
            del device["fragrance"]
        elif key not in device or device[key] != value:
            device[key] = value


def _merge_timer_record(
    device: dict[str, Any],
    device_id: str,
    event_type: str | None,
    update: dict[str, Any],
) -> None:
    """Merge a timer record."""
    if event_type == EVENT_REMOVE:
        _LOGGER.debug("Removed timer from device %s", device_id)
        device["timer"] = None
    elif event_type in (EVENT_INSERT, EVENT_MODIFY):
        _LOGGER.debug("%s timer on device %s", event_type.capitalize(), device_id)
        device["timer"] = update.get("timerRecord")
    else:
        _LOGGER.warning("Received unknown update: %s", update)


def _merge_schedule_record(
    device: dict[str, Any],
    device_id: str,
    event_type: str | None,
    update: dict[str, Any],
) -> None:
    """Merge a schedule record."""
    if not (record := update.get("scheduleRecord")):
        _LOGGER.debug("Schedule missing: %s", update)
        return

    if (record_id := record.get("id")) is None:
        _LOGGER.debug("Schedule ID missing: %s", update)
        return

    if (schedules := device.get("schedules")) is None:
        device["schedules"] = schedules = []

    if event_type == EVENT_REMOVE:
        device["schedules"] = [
            schedule for schedule in schedules if schedule.get("id") != record_id
        ]
        _LOGGER.debug("Removed schedule from device %s", device_id)
        return

    if event_type in (EVENT_INSERT, EVENT_MODIFY):
        for idx, schedule in enumerate(schedules):
            if schedule.get("id") == record_id:
                schedules[idx] = record
                break
        else:
            schedules.append(record)
        return

    _LOGGER.warning("Received unknown update: %s", update)
