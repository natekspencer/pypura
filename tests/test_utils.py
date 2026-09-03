"""Tests utils."""

import copy
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest

from pypura.utils import (
    EVENT_INSERT,
    EVENT_MODIFY,
    EVENT_REMOVE,
    RECORD_TYPE_DEVICE,
    RECORD_TYPE_SCHEDULE,
    RECORD_TYPE_TIMER,
    decode,
    dig,
    get_device_name,
    get_fragrance_name,
    get_fragrance_remaining,
    get_model_name,
    has_fragrance,
    merge_websocket_update,
    parse_intensity,
)


def test_decode() -> None:
    """Test `decode` function."""
    assert decode("ZGVjb2RlZA==") == "decoded"


@pytest.mark.parametrize(
    ("path", "expected"),
    [("a", "a"), ("b.b1", "b1"), ("c", ["c0", "c1", "c2"]), ("c.c2", None)],
)
def test_dig(path: str, expected: Any) -> None:
    """Test `dig` function."""
    data = {"a": "a", "b": {"b1": "b1"}, "c": ["c0", "c1", "c2"]}
    assert dig(data, path) == expected


@pytest.mark.parametrize(
    ("device", "name"),
    [
        ({"displayName": {"name": "Bedroom"}}, "Bedroom"),
        ({"displayName": {"name": "Bedroom"}, "roomName": "Main Bedroom"}, "Bedroom"),
        ({"roomName": "Upstairs Bedroom"}, "Upstairs Bedroom"),
        ({"model": 2}, "Car"),
        ({"model": 3}, "Home Plus"),
        ({}, "Pura"),
    ],
)
def test_get_device_name(device: dict[str, Any], name: str) -> None:
    """Test get device name."""
    assert get_device_name(device) == name


@pytest.mark.parametrize(
    ("bay_data", "remaining"),
    [
        ({}, None),
        ({"remaining": {"percent": 23}}, 23),
        ({"remaining": {"percent": 45.5}}, 45.5),
        ({"fragrance": {"expectedLifeHours": 100}}, 100),
        ({"fragrance": {"expectedLifeHours": 100}, "wearingTime": 90000}, 75),
        (
            {
                "fragrance": {"expectedLifeHours": 100},
                "wearingTime": 90000,
                "activeAt": 1788449400,
            },
            74.5,
        ),
    ],
)
def test_get_fragrance_remaining(
    freezer: FrozenDateTimeFactory, bay_data: dict[str, Any], remaining: float | None
) -> None:
    """Test get fragrance remaining."""
    freezer.move_to("2026-09-03 10:00:00 -06:00")
    device = _wall_device(KITCHEN_ID, bay1=bay_data)
    assert get_fragrance_remaining(device, 1) == remaining


@pytest.mark.parametrize(
    ("device", "model"),
    [
        ({"deviceVer": "v48"}, "Home v4"),
        ({"deviceVer": "wall_5"}, "Home v5"),
        ({"deviceVer": "unknown"}, "Pura"),
        ({"model": 2}, "Car"),
        ({"model": 3}, "Home Plus"),
        ({"model": 4}, "Home Mini"),
        ({"model": -1}, "Pura"),
        ({}, "Pura"),
    ],
)
def test_get_model_name(device: dict[str, Any], model: str) -> None:
    """Test get model name."""
    assert get_model_name(device) == model


@pytest.mark.parametrize(
    ("intensity", "name"),
    [
        (0, "off"),
        (1, "subtle"),
        (3, "light"),
        (5, "medium"),
        (7, "pronounced"),
        (10, "strong"),
    ],
)
def test_parse_intensity(intensity: int | str, name: str) -> None:
    """Test get model name."""
    assert parse_intensity(intensity) == name


KITCHEN_ID = "AAAAAAAAAA10"  # wall, dual-bay, oscillation-capable
HALLWAY_ID = "AAAAAAAAAA03"  # wall, single active bay, sees timer + fw update
LIVING_ROOM_ID = "AAAAAAAAAA05"  # wall, sees repeated timer insert/remove
CAR_ID = "AAAAAAAAAA01"  # car diffuser
NEW_DEVICE_ID = "AAAAAAAAAA14"  # plus device that gets onboarded mid-stream
UNKNOWN_ID = "FFFFFFFFFFFF"  # never present in the device map


def _wall_device(device_id: str, **overrides: Any) -> dict[str, Any]:
    """Build a trimmed but structurally faithful wall-diffuser record."""
    base: dict[str, Any] = {
        "deviceId": device_id,
        "deviceVer": "v48",
        "model": 1,
        "modelNumber": "1",
        "fwVersion": "7.5.3",
        "hwVersion": "4.12",
        "connected": True,
        "setupComplete": True,
        "diffusionMode": "standard",
        "controller": "default",
        "position": 1,
        "roomName": "Test Room",
        "roomType": "custom",
        "ssid": "TestNetwork",
        "otaVer": 223592179,
        "ota": {"status": "Finished", "percent": 100},
        "awayMode": {"away": False, "enabled": False},
        "ambientMode": False,
        "deviceStatus": "normal",
        "serialNumber": "UNKNOWN",
        "deviceLocation": {"timezone": "America/Denver"},
        "deviceDefaults": {
            "bay": 0,
            "bay1Intensity": 3,
            "bay2Intensity": 3,
            "nightlight": {"active": False, "brightness": 10, "color": "FFFFFF"},
        },
        "deviceActiveState": {
            "activeBay": 0,
            "activeBayIntensity": 1,
            "activeNightlight": {"active": False, "brightness": 10, "color": "FFFFFF"},
        },
        "bay1": {
            "id": 1000001,
            "code": "TEST",
            "vialId": "vial-0001",
            "isSmartVial": True,
            "activeAt": 0,
            "wearingTime": 1000,
            "max": 0,
            "min": 0,
            "msg": "",
            "fragrance": {"name": "Test"},
        },
        "bay2": {
            "id": 1000002,
            "code": "TEST",
            "vialId": "vial-0002",
            "isSmartVial": True,
            "activeAt": 0,
            "wearingTime": 1000,
            "max": 0,
            "min": 0,
            "msg": "",
            "fragrance": {"name": "Test"},
        },
        "schedules": [],
        "timer": None,
        "scheduleId": "",
    }
    base.update(overrides)
    return base


def _car_device(device_id: str, **overrides: Any) -> dict[str, Any]:
    """Build a trimmed but structurally faithful car-diffuser record."""
    base: dict[str, Any] = {
        "deviceId": device_id,
        "deviceVer": "2",
        "model": 2,
        "fwVersion": "9.1.16",
        "hwVersion": "27.5",
        "deviceName": "Test Car",
        "displayName": {"name": "Test Car", "type": "custom"},
        "batteryRemaining": 70,
        "lowBattery": False,
        "needsFwUpdate": False,
        "position": 0,
        "serialId": None,
        "onboardedAt": 1682646885,
        "lastConnectedAt": 1787018828,
        "accelerometerSettings": {"automaticModeEnabled": True, "wakeUpSensitivity": 1},
        "bay1": {
            "id": 1,
            "code": "TEST",
            "fanIntensity": "high",
            "lowFragrance": False,
            "msg": "",
            "refillId": None,
            "wearingTime": 88298,
            "activeAt": 0,
            "fragrance": {"name": "Test"},
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def devices() -> dict[str, dict[str, Any]]:
    """Return a small, representative slice of get_devices()'s output."""
    return {
        KITCHEN_ID: _wall_device(
            KITCHEN_ID,
            roomName="Kitchen",
            roomType="kitchen",
            diffusionMode="oscillation-multi-bay",
            position=3,
            modelType="wall",
        ),
        HALLWAY_ID: _wall_device(
            HALLWAY_ID,
            roomName="Upstairs Hallway",
            roomType="hallway",
            position=1,
            modelType="wall",
        ),
        LIVING_ROOM_ID: _wall_device(
            LIVING_ROOM_ID,
            roomName="Living Room",
            roomType="livingRoom",
            deviceVer="v3l",
            fwVersion="2.7.0",
            hwVersion="3.0",
            position=4,
            modelType="wall",
            schedules=[
                {"id": "sched-existing-1", "name": "Old schedule", "bay": 1},
            ],
        ),
        CAR_ID: _car_device(CAR_ID, modelType="car"),
    }


def _device_update(
    device_id: str,
    event_type: str,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a DEVICE-record websocket update payload."""
    update: dict[str, Any] = {
        "uid": "test-uid",
        "deviceId": device_id,
        "eventType": event_type,
        "recordType": RECORD_TYPE_DEVICE,
    }
    if record is not None:
        update["deviceRecord"] = record
    return update


def _timer_update(
    device_id: str,
    event_type: str,
    timer_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a TIMER-record websocket update payload."""
    update: dict[str, Any] = {
        "uid": "test-uid",
        "deviceId": device_id,
        "eventType": event_type,
        "recordType": RECORD_TYPE_TIMER,
    }
    if timer_record is not None:
        update["timerRecord"] = timer_record
    return update


def _schedule_update(
    device_id: str,
    event_type: str,
    schedule_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a SCHEDULE-record websocket update payload."""
    update: dict[str, Any] = {
        "uid": "test-uid",
        "deviceId": device_id,
        "eventType": event_type,
        "recordType": RECORD_TYPE_SCHEDULE,
    }
    if schedule_record is not None:
        update["scheduleRecord"] = schedule_record
    return update


def test_update_missing_device_id_is_noop(devices: dict[str, dict[str, Any]]) -> None:
    """An update with no deviceId leaves the device map untouched."""
    before = copy.deepcopy(devices)
    update = {
        "uid": "test-uid",
        "eventType": EVENT_MODIFY,
        "recordType": RECORD_TYPE_DEVICE,
    }

    merge_websocket_update(devices, update)

    assert devices == before


def test_device_remove_existing(devices: dict[str, dict[str, Any]]) -> None:
    """A DEVICE REMOVE for a known device deletes it from the map."""
    update = _device_update(KITCHEN_ID, EVENT_REMOVE)

    merge_websocket_update(devices, update)

    assert KITCHEN_ID not in devices
    assert len(devices) == 3


def test_device_remove_nonexistent_is_noop(devices: dict[str, dict[str, Any]]) -> None:
    """A DEVICE REMOVE for an unknown device is a no-op, not an error."""
    before = copy.deepcopy(devices)
    update = _device_update(UNKNOWN_ID, EVENT_REMOVE)

    merge_websocket_update(devices, update)

    assert devices == before


def test_device_modify_existing_deep_merges_single_field(
    devices: dict[str, dict[str, Any]],
) -> None:
    """A DEVICE MODIFY deep-merges into the existing record in place."""
    # Mirrors the real pattern of a MODIFY carrying the full record but
    # only one nested field actually differing (e.g. bay1Intensity).
    record = _wall_device(
        KITCHEN_ID,
        roomName="Kitchen",
        roomType="kitchen",
        diffusionMode="oscillation-multi-bay",
        position=3,
    )
    record["deviceDefaults"]["bay1Intensity"] = 7

    update = _device_update(KITCHEN_ID, EVENT_MODIFY, record)
    merge_websocket_update(devices, update)

    assert devices[KITCHEN_ID]["deviceDefaults"]["bay1Intensity"] == 7
    # Untouched sibling fields survive the merge.
    assert devices[KITCHEN_ID]["deviceDefaults"]["bay2Intensity"] == 3
    assert devices[KITCHEN_ID]["roomName"] == "Kitchen"


def test_device_modify_nonexistent_is_noop(devices: dict[str, dict[str, Any]]) -> None:
    """A DEVICE MODIFY for an unknown device does not insert it."""
    before = copy.deepcopy(devices)
    record = _wall_device(UNKNOWN_ID)
    update = _device_update(UNKNOWN_ID, EVENT_MODIFY, record)

    merge_websocket_update(devices, update)

    assert devices == before
    assert UNKNOWN_ID not in devices


def test_device_insert_existing_deep_merges(devices: dict[str, dict[str, Any]]) -> None:
    """A DEVICE INSERT for an already-known device behaves like MODIFY."""
    record = _wall_device(
        HALLWAY_ID, roomName="Upstairs Hallway", roomType="hallway", position=1
    )
    record["deviceDefaults"]["nightlight"]["active"] = True

    update = _device_update(HALLWAY_ID, EVENT_INSERT, record)
    merge_websocket_update(devices, update)

    assert devices[HALLWAY_ID]["deviceDefaults"]["nightlight"]["active"] is True
    assert len(devices) == 4  # no new device added


def test_device_insert_new_device_sets_model_type(
    devices: dict[str, dict[str, Any]],
) -> None:
    """A DEVICE INSERT for a brand-new device adds it and backfills deviceId."""
    # Mirrors the real onboarding sequence: INSERT arrives with an empty
    # deviceId on the record itself (the top-level deviceId is authoritative).
    record: dict[str, Any] = {
        "deviceId": "",
        "model": 3,
        "modelNumber": "3",
        "hwVersion": "22.8",
        "fwVersion": "",
        "connected": False,
        "setupComplete": False,
        "position": 0,
        "roomName": "",
        "deviceLocation": {"timezone": ""},
        "bay1": {
            "id": 0,
            "code": "",
            "vialId": "",
            "isSmartVial": False,
            "wearingTime": 0,
            "activeAt": 0,
        },
        "bay2": {
            "id": 0,
            "code": "",
            "vialId": "",
            "isSmartVial": False,
            "wearingTime": 0,
            "activeAt": 0,
        },
        "deviceDefaults": {"bay": 0, "bay1Intensity": 1, "bay2Intensity": 1},
        "deviceActiveState": {"activeBay": 0, "activeBayIntensity": 1},
        "awayMode": {"away": False, "enabled": False},
    }
    update = _device_update(NEW_DEVICE_ID, EVENT_INSERT, record)

    merge_websocket_update(devices, update)

    assert NEW_DEVICE_ID in devices
    assert devices[NEW_DEVICE_ID]["deviceId"] == NEW_DEVICE_ID  # backfilled
    assert devices[NEW_DEVICE_ID]["model"] == 3
    assert devices[NEW_DEVICE_ID]["modelType"] == "plus"
    assert len(devices) == 5


def test_device_missing_record_is_noop(devices: dict[str, dict[str, Any]]) -> None:
    """A DEVICE update with no deviceRecord key at all is a no-op."""
    before = copy.deepcopy(devices)
    update = _device_update(KITCHEN_ID, EVENT_MODIFY, record=None)

    merge_websocket_update(devices, update)

    assert devices == before


def test_device_record_not_a_dict_is_noop(devices: dict[str, dict[str, Any]]) -> None:
    """A DEVICE update whose deviceRecord isn't a dict is a no-op."""
    before = copy.deepcopy(devices)
    update = _device_update(KITCHEN_ID, EVENT_MODIFY)
    update["deviceRecord"] = "not-a-dict"

    merge_websocket_update(devices, update)

    assert devices == before


def test_device_unknown_event_type_is_noop(devices: dict[str, dict[str, Any]]) -> None:
    """A DEVICE update with an unrecognized eventType is a no-op."""
    before = copy.deepcopy(devices)
    record = _wall_device(KITCHEN_ID)
    update = _device_update(KITCHEN_ID, "SOMETHING_NEW", record)

    merge_websocket_update(devices, update)

    assert devices == before


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"code": "UNKNOWN"}, "Fragrance: UNKNOWN"),
        ({"code": "NEW"}, "Fragrance: NEW"),
        ({"code": "NEW", "fragrance": {"name": "New"}}, "New"),
        ({"code": ""}, None),
    ],
)
def test_device_fragrance_swap(
    devices: dict[str, dict[str, Any]], data: dict[str, Any], expected: str | None
) -> None:
    """A device fragrance swap is updated successfully."""
    kitchen = devices[KITCHEN_ID]
    assert has_fragrance(kitchen, 1)
    assert get_fragrance_name(kitchen, 1) == "Test"

    record = _wall_device(KITCHEN_ID, bay1=data)
    update = _device_update(KITCHEN_ID, EVENT_MODIFY, record)
    merge_websocket_update(devices, update)

    assert has_fragrance(kitchen, 1) == bool(expected)
    assert get_fragrance_name(kitchen, 1) == expected


def test_timer_insert_sets_timer(devices: dict[str, dict[str, Any]]) -> None:
    """A TIMER INSERT sets the device's timer field."""
    timer_record = {"bay": 1, "start": 1786338878, "end": 1786340678, "intensity": 3}
    update = _timer_update(HALLWAY_ID, EVENT_INSERT, timer_record)

    merge_websocket_update(devices, update)

    assert devices[HALLWAY_ID]["timer"] == timer_record


def test_timer_modify_replaces_timer(devices: dict[str, dict[str, Any]]) -> None:
    """A TIMER MODIFY replaces the existing timer wholesale."""
    devices[HALLWAY_ID]["timer"] = {"bay": 1, "start": 1, "end": 2, "intensity": 1}
    new_timer = {"bay": 2, "start": 100, "end": 200, "intensity": 5}
    update = _timer_update(HALLWAY_ID, EVENT_MODIFY, new_timer)

    merge_websocket_update(devices, update)

    assert devices[HALLWAY_ID]["timer"] == new_timer


def test_timer_remove_clears_timer(devices: dict[str, dict[str, Any]]) -> None:
    """A TIMER REMOVE clears the device's timer field to None."""
    devices[LIVING_ROOM_ID]["timer"] = {"bay": 2, "start": 1, "end": 2, "intensity": 4}
    update = _timer_update(LIVING_ROOM_ID, EVENT_REMOVE)

    merge_websocket_update(devices, update)

    assert devices[LIVING_ROOM_ID]["timer"] is None


def test_timer_unknown_device_is_noop(devices: dict[str, dict[str, Any]]) -> None:
    """A TIMER update for an unknown device is a no-op."""
    before = copy.deepcopy(devices)
    update = _timer_update(
        UNKNOWN_ID, EVENT_INSERT, {"bay": 1, "start": 1, "end": 2, "intensity": 1}
    )

    merge_websocket_update(devices, update)

    assert devices == before


def test_timer_unknown_event_type_is_noop(devices: dict[str, dict[str, Any]]) -> None:
    """A TIMER update with an unrecognized eventType is a no-op."""
    devices[HALLWAY_ID]["timer"] = None
    before = copy.deepcopy(devices)
    update = _timer_update(
        HALLWAY_ID, "SOMETHING_NEW", {"bay": 1, "start": 1, "end": 2, "intensity": 1}
    )

    merge_websocket_update(devices, update)

    assert devices == before


def test_schedule_insert_appends(devices: dict[str, dict[str, Any]]) -> None:
    """A SCHEDULE INSERT for a new id appends to the schedules list."""
    record = {
        "id": "sched-new-1",
        "name": "Morning",
        "bay": 1,
        "start": "0600",
        "end": "1000",
        "intensity": 5,
    }
    update = _schedule_update(LIVING_ROOM_ID, EVENT_INSERT, record)

    merge_websocket_update(devices, update)

    ids = [s["id"] for s in devices[LIVING_ROOM_ID]["schedules"]]
    assert ids == ["sched-existing-1", "sched-new-1"]


def test_schedule_modify_replaces_in_place_without_duplicating(
    devices: dict[str, dict[str, Any]],
) -> None:
    """A SCHEDULE MODIFY replaces the matching entry, not append-alongside it.

    Regression test: modifying an existing schedule must replace it,
    not replace-and-also-append a duplicate.
    """
    record = {
        "id": "sched-existing-1",
        "name": "Renamed schedule",
        "bay": 1,
        "start": "0700",
        "end": "1100",
        "intensity": 7,
    }
    update = _schedule_update(LIVING_ROOM_ID, EVENT_MODIFY, record)

    merge_websocket_update(devices, update)

    schedules = devices[LIVING_ROOM_ID]["schedules"]
    assert len(schedules) == 1
    assert schedules[0]["name"] == "Renamed schedule"
    assert schedules[0]["intensity"] == 7


def test_schedule_modify_unknown_id_appends(devices: dict[str, dict[str, Any]]) -> None:
    """A SCHEDULE MODIFY for an id we don't have yet falls through to append."""
    # Matches the real "for...else" behavior of the implementation.
    record = {"id": "sched-never-seen", "name": "Ghost", "bay": 1}
    update = _schedule_update(LIVING_ROOM_ID, EVENT_MODIFY, record)

    merge_websocket_update(devices, update)

    ids = [s["id"] for s in devices[LIVING_ROOM_ID]["schedules"]]
    assert ids == ["sched-existing-1", "sched-never-seen"]


def test_schedule_remove_existing(devices: dict[str, dict[str, Any]]) -> None:
    """A SCHEDULE REMOVE deletes the matching entry from the list."""
    update = _schedule_update(LIVING_ROOM_ID, EVENT_REMOVE, {"id": "sched-existing-1"})

    merge_websocket_update(devices, update)

    assert devices[LIVING_ROOM_ID]["schedules"] == []


def test_schedule_remove_does_not_warn_about_unknown_update(
    devices: dict[str, dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A successful SCHEDULE REMOVE must not also log an "unknown update" warning.

    Regression test for the missing `return` after the REMOVE branch.
    """
    update = _schedule_update(LIVING_ROOM_ID, EVENT_REMOVE, {"id": "sched-existing-1"})

    with caplog.at_level("WARNING"):
        merge_websocket_update(devices, update)

    assert "unknown update" not in caplog.text.lower()


def test_schedule_remove_nonexistent_id_is_noop(
    devices: dict[str, dict[str, Any]],
) -> None:
    """A SCHEDULE REMOVE for an id not present in the list changes nothing."""
    before_schedules = copy.deepcopy(devices[LIVING_ROOM_ID]["schedules"])
    update = _schedule_update(LIVING_ROOM_ID, EVENT_REMOVE, {"id": "sched-not-present"})

    merge_websocket_update(devices, update)

    assert devices[LIVING_ROOM_ID]["schedules"] == before_schedules


def test_schedule_missing_record_is_noop(devices: dict[str, dict[str, Any]]) -> None:
    """A SCHEDULE update with no scheduleRecord key at all is a no-op."""
    before = copy.deepcopy(devices)
    update = _schedule_update(LIVING_ROOM_ID, EVENT_INSERT, schedule_record=None)

    merge_websocket_update(devices, update)

    assert devices == before


def test_schedule_unknown_device_is_noop(devices: dict[str, dict[str, Any]]) -> None:
    """A SCHEDULE update for an unknown device is a no-op."""
    before = copy.deepcopy(devices)
    update = _schedule_update(UNKNOWN_ID, EVENT_INSERT, {"id": "sched-x", "bay": 1})

    merge_websocket_update(devices, update)

    assert devices == before


def test_schedule_unknown_event_type_warns(
    devices: dict[str, dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A SCHEDULE update with an unrecognized eventType logs a warning and no-ops."""
    before = copy.deepcopy(devices)
    update = _schedule_update(
        LIVING_ROOM_ID, "SOMETHING_NEW", {"id": "sched-existing-1", "bay": 1}
    )

    with caplog.at_level("WARNING"):
        merge_websocket_update(devices, update)

    assert devices == before
    assert "unknown update" in caplog.text.lower()


def test_schedule_initializes_missing_schedules_list() -> None:
    """A device missing the "schedules" key gets one created rather than raising."""
    device_id = "AAAAAAAAAA99"
    devices: dict[str, dict[str, Any]] = {device_id: {"deviceId": device_id}}
    record = {"id": "sched-first", "name": "First", "bay": 1}
    update = _schedule_update(device_id, EVENT_INSERT, record)

    merge_websocket_update(devices, update)

    assert devices[device_id]["schedules"] == [record]


def test_unknown_record_type_warns_and_is_noop(
    devices: dict[str, dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An update with an unrecognized recordType logs a warning and no-ops."""
    before = copy.deepcopy(devices)
    update = {
        "uid": "test-uid",
        "deviceId": KITCHEN_ID,
        "eventType": EVENT_MODIFY,
        "recordType": "SOMETHING_NEW",
    }

    with caplog.at_level("WARNING"):
        merge_websocket_update(devices, update)

    assert devices == before
    assert "unknown update" in caplog.text.lower()


def test_unknown_record_type_for_unknown_device_is_noop(
    devices: dict[str, dict[str, Any]],
) -> None:
    """An unrecognized recordType for an unknown device hits the device-not-found branch."""
    # recordType isn't DEVICE, and the device isn't in the map either:
    # should hit the early "device does not exist" branch, not the
    # bottom-of-function warning.
    before = copy.deepcopy(devices)
    update = {
        "uid": "test-uid",
        "deviceId": UNKNOWN_ID,
        "eventType": EVENT_MODIFY,
        "recordType": "SOMETHING_NEW",
    }

    merge_websocket_update(devices, update)

    assert devices == before


def test_schedule_realistic_sequence_ends_empty(
    devices: dict[str, dict[str, Any]],
) -> None:
    """A realistic insert/modify/remove burst leaves an empty schedules list."""
    device_id = LIVING_ROOM_ID
    devices[device_id]["schedules"] = []  # start clean for this scenario

    sequence: list[dict[str, Any]] = [
        _schedule_update(
            device_id,
            EVENT_INSERT,
            {"id": "sched-a", "name": "Rise and shine", "bay": 1},
        ),
        _schedule_update(
            device_id, EVENT_INSERT, {"id": "sched-b", "name": "Wind down", "bay": 1}
        ),
        _schedule_update(
            device_id, EVENT_INSERT, {"id": "sched-c", "name": "All day", "bay": 1}
        ),
        _schedule_update(device_id, EVENT_REMOVE, {"id": "sched-a"}),
        _schedule_update(
            device_id,
            EVENT_MODIFY,
            {"id": "sched-c", "name": "All day", "bay": 1, "intensity": 7},
        ),
        _schedule_update(
            device_id,
            EVENT_MODIFY,
            {"id": "sched-b", "name": "Wind down", "bay": 1, "intensity": 7},
        ),
        _schedule_update(device_id, EVENT_REMOVE, {"id": "sched-b"}),
        _schedule_update(device_id, EVENT_REMOVE, {"id": "sched-c"}),
        _schedule_update(device_id, EVENT_REMOVE, {"id": "sched-never-existed"}),
        _schedule_update(device_id, EVENT_INSERT, {"key": "no-id-field"}),
    ]

    for update in sequence:
        merge_websocket_update(devices, update)

    assert devices[device_id]["schedules"] == []
