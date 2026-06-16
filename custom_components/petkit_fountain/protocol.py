"""PetKit fountain BLE protocol — frame format, command builders, parsers.

Adapted from slespersen/PetkitW5BLEMQTT (MIT, Copyright 2024 slespersen)
and the Jezza34000 fork extending it for CTW2/CTW3/W4X model families.
Original upstream source: https://github.com/slespersen/PetkitW5BLEMQTT
Jezza fork:               https://github.com/Jezza34000/PetkitW5BLEMQTT

MIT License — preserved per upstream terms.

This module covers the W4X protocol branch (Eversweet 3 Pro and 3 Pro UVC).
CTW3 (Eversweet Max 2) frame layouts differ and are not implemented here;
add a separate parser branch if/when another model is supported.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

# Frame header / trailer (per slespersen utils.build_command)
FRAME_HEADER = [0xFA, 0xFC, 0xFD]
FRAME_END = 0xFB

# Command codes (slespersen commands.py)
CMD_BATTERY = 66
CMD_INIT_DEVICE = 73          # Sets device secret — disrupts official app pairing
CMD_SET_DATETIME = 84
CMD_DEVICE_SYNC = 86
CMD_DEVICE_INFO = 200          # Firmware version
CMD_DEVICE_TYPE = 201
CMD_DEVICE_STATE = 210         # Power / mode / warnings / filter %
CMD_DEVICE_CONFIG = 211        # LED / DND / schedules
CMD_DEVICE_DETAILS = 213       # device_id + serial
CMD_SET_LIGHT = 215
CMD_SET_DND = 216
CMD_SET_MODE = 220             # on/off + normal/smart
CMD_SET_CONFIG = 221           # batch update of LED/DND/schedules
CMD_RESET_FILTER = 222

# Type field — 1 = outbound (client→device), 2 = inbound (device→client)
TYPE_SEND = 1
TYPE_RECV = 2

# Model registry — type_code → metadata. From slespersen utils.get_device_properties.
# Only models with W4X alias are handled by this module's parsers.
MODEL_MAP: dict[int, dict[str, Any]] = {
    205: {"name": "Petkit_W5C", "alias": "W5C", "product_name": "Eversweet Mini",
          "device_type": 14, "type_code": 2},
    206: {"name": "Petkit_W5", "alias": "W5", "product_name": "Eversweet Mini",
          "device_type": 14, "type_code": 1},
    213: {"name": "Petkit_W5N", "alias": "W5N", "product_name": "Eversweet Mini",
          "device_type": 14, "type_code": 3},
    214: {"name": "Petkit_W4X", "alias": "W4X", "product_name": "Eversweet 3 Pro",
          "device_type": 14, "type_code": 4},
    217: {"name": "Petkit_CTW2", "alias": "CTW2", "product_name": "Eversweet Solo 2",
          "device_type": 14, "type_code": 5},
    223: {"name": "Petkit_CTW3", "alias": "CTW3", "product_name": "Eversweet Max",
          "device_type": 24, "type_code": 0},
    228: {"name": "Petkit_W4XUVC", "alias": "W4X", "product_name": "Eversweet 3 Pro UVC",
          "device_type": 14, "type_code": 6},
}


# ─────────────────────────── byte/array utilities ───────────────────────────


def byte_to_int(b: int) -> int:
    return b & 0xFF


def bytes_to_int(data: bytes | list[int], byteorder: str = "big") -> int:
    """Unsigned multi-byte integer."""
    return int.from_bytes(bytes(data), byteorder=byteorder)


def bytes_to_short(data: bytes | list[int], byteorder: str = "big") -> int:
    """Signed 16-bit short."""
    return int.from_bytes(bytes(data), byteorder=byteorder, signed=True)


def pad_array(data: list[int], target_length: int) -> list[int]:
    return [0] * (target_length - len(data)) + list(data)


def reverse_array(data: list[int]) -> list[int]:
    return list(reversed(data))


def replace_last_two_if_zero(data: list[int]) -> list[int]:
    """If the trailing two bytes are both zero, set them to (13, 37). PetKit-magic."""
    if len(data) >= 2 and data[-1] == 0 and data[-2] == 0:
        data[-2] = 13
        data[-1] = 37
    return data


# ─────────────────────────── frame builder / parser ──────────────────────────


def build_command(seq: int, cmd: int, type_: int, data: list[int]) -> bytes:
    """Pack a request frame: [FA FC FD] [cmd] [type] [seq] [len] [0] [data...] [FB]."""
    length = len(data)
    start_data = 0
    frame = (
        FRAME_HEADER
        + [cmd, type_, seq, length, start_data]
        + list(data)
        + [FRAME_END]
    )
    return bytes(frame)


def parse_frame(raw: bytes) -> dict[str, Any] | None:
    """Unpack an inbound notification frame. Returns None if not a valid frame."""
    if len(raw) < 9:
        return None
    if list(raw[0:3]) != FRAME_HEADER or raw[-1] != FRAME_END:
        return None
    return {
        "cmd": raw[3],
        "type": raw[4],
        "seq": raw[5],
        "length": raw[6],
        "start": raw[7],
        "data": bytes(raw[8:-1]),
    }


# ──────────────────────────── time / datetime ───────────────────────────────

_REF_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _seconds_since_2000() -> int:
    return int((datetime.now(timezone.utc) - _REF_EPOCH).total_seconds())


def time_in_bytes() -> list[int]:
    """Encode current UTC time as the 6-byte payload CMD 84 expects.
    Layout: [0, sec>>24, sec>>16, sec>>8, sec, 13]. Last byte is constant '13'
    per slespersen — purpose unclear but required."""
    seconds = _seconds_since_2000()
    return [
        0,
        (seconds >> 24) & 0xFF,
        (seconds >> 16) & 0xFF,
        (seconds >> 8) & 0xFF,
        seconds & 0xFF,
        13,
    ]


# ─────────────────────────────── parsers ─────────────────────────────────────
#
# All parsers below operate on the `data` portion of a parsed frame (i.e. the
# bytes between the header [FA FC FD ... seq, len, 0] and the trailer [FB]).
# Field layouts are W4X-specific (matches slespersen's "else" branches).


def parse_supply(data: bytes) -> dict[str, Any]:
    """CMD 66 response for W4X — the field PetKit labels 'battery' is actually
    the USB supply voltage, and the second byte (nominally battery %) is
    always 0 on this model line (no battery hardware). For models that DO
    have a battery (CTW3 family per slespersen), parse separately when
    that branch is implemented.
    """
    if len(data) < 2:
        return {}
    voltage = ((data[0] * 256) + (data[1] & 0xFF)) / 1000.0
    return {"supply_voltage": voltage}


def parse_firmware(data: bytes) -> dict[str, Any]:
    """CMD 200 response — firmware version as 'major.minor'."""
    if len(data) < 2:
        return {}
    return {"firmware": float(f"{data[0]}.{data[1]}")}


def parse_device_identifiers(data: bytes) -> dict[str, Any]:
    """CMD 213 response — 2 byte prefix + 6 byte device_id + variable serial.

    Observed frame length on a W4XUVC is 22 bytes (slespersen comments assume
    23+ but their code's slicing is lenient). We require only the 8 bytes
    that contain device_id_bytes; serial may be shorter than the slespersen
    comment suggests.
    """
    if len(data) < 8:
        return {}
    device_id_bytes = list(data[2:8])
    device_id = int.from_bytes(bytes(device_id_bytes), byteorder="big")
    # Serial is the remaining ASCII tail. Some bytes are non-ASCII (firmware
    # quirk — observed leading 0xB0) so we keep the raw chars and let users
    # see what's there rather than stripping.
    serial = "".join(chr(b) for b in data[8:]).rstrip("\x00")
    return {
        "device_id_bytes": device_id_bytes,
        "device_id": device_id,
        "serial": serial,
    }


def parse_device_state(data: bytes, alias: str = "W4X") -> dict[str, Any]:
    """CMD 210 response — frame layout differs by alias.

    W4X / W5* / CTW2 share the slespersen "else" branch: 12-byte frame with
    power/mode/warnings/pump_runtime/filter/running_status. CTW3 is a 26+
    byte frame with extra fields (electric_status, low_battery,
    pump_runtime_today, detect_status, supply/battery voltages in mV,
    battery_percentage, module_status). Untested for non-W4X — see README's
    verified-models table.
    """
    if alias == "CTW3":
        if len(data) < 26:
            return {}
        return {
            "power_status": data[0],                      # 0=off, 1=on
            "suspend_status": data[1],
            "mode": data[2],                              # 1=normal, 2=smart
            "electric_status": data[3],
            "dnd_state": data[4],
            "warning_breakdown": data[5],
            "warning_water_missing": data[6],
            "low_battery": data[7],
            "warning_filter": data[8],
            "pump_runtime": bytes_to_int(data[9:13]),     # seconds, lifetime
            "filter_percentage": byte_to_int(data[13]),   # 0-100
            "running_status": byte_to_int(data[14]),
            "pump_runtime_today": bytes_to_int(data[15:19]),
            "detect_status": data[19],                    # cat presence
            # Voltages on the wire are mV per slespersen; expose in volts.
            "supply_voltage": bytes_to_short(data[20:22]) / 1000.0,
            "battery_voltage": bytes_to_short(data[22:24]) / 1000.0,
            "battery_percentage": byte_to_int(data[24]),
            "module_status": data[25],
        }
    # W4X family (and unverified W5*/CTW2 — same 12-byte layout per slespersen).
    if len(data) < 12:
        return {}
    return {
        "power_status": data[0],              # 0=off, 1=on
        "mode": data[1],                      # 1=normal, 2=smart
        "dnd_state": data[2],                 # DND active flag
        "warning_breakdown": data[3],
        "warning_water_missing": data[4],
        "warning_filter": data[5],
        "pump_runtime": bytes_to_int(data[6:10]),       # seconds, lifetime
        "filter_percentage": byte_to_int(data[10]),     # 0-100
        "running_status": byte_to_int(data[11]),
    }


def parse_device_configuration(data: bytes, alias: str = "W4X") -> dict[str, Any]:
    """CMD 211 response — frame layout differs by alias.

    W4X / W5* / CTW2 share the 14-byte "else" branch: schedules, LED config,
    DND config, is_locked. CTW3 is a 10-byte frame with different fields —
    notably battery_working_time / battery_sleep_time instead of LED+DND
    schedules. Untested for non-W4X.
    """
    if alias == "CTW3":
        if len(data) < 9:
            return {}
        return {
            "smart_time_on": data[0],                       # minutes (1-60)
            "smart_time_off": data[1],                      # minutes (1-60)
            "battery_working_time": bytes_to_short(data[2:4]),  # minutes
            "battery_sleep_time": bytes_to_short(data[4:6]),    # minutes
            "led_switch": data[6],
            "led_brightness": data[7],
            "do_not_disturb_switch": data[8],
            "is_locked": data[9] if len(data) > 9 else None,
        }
    # W4X family (and unverified W5*/CTW2 — same 14-byte layout per slespersen).
    if len(data) < 14:
        return {}
    return {
        "smart_time_on": data[0],             # minutes (1-60)
        "smart_time_off": data[1],            # minutes (1-60)
        "led_switch": data[2],                # 0/1
        "led_brightness": data[3],            # 1=low, 2=med, 3=high
        "led_light_time_on": bytes_to_short(data[4:6]),   # minute-of-day
        "led_light_time_off": bytes_to_short(data[6:8]),
        "do_not_disturb_switch": data[8],     # 0/1
        "do_not_disturb_time_on": bytes_to_short(data[9:11]),
        "do_not_disturb_time_off": bytes_to_short(data[11:13]),
        "is_locked": data[13] if len(data) > 13 else None,
    }


# ────────────────────────── derived calculations ─────────────────────────────


def parse_combined_status(data: bytes, alias: str = "W4X") -> dict[str, Any]:
    """CMD 230 (0xE6) unsolicited broadcast.

    W4X family: 16 bytes state + 14 bytes config (30 total observed).
    CTW3: 26 bytes state + 10 bytes config (36 total per slespersen layouts).

    The fountain emits these frames unsolicited in bursts — observed pattern
    on the W4XUVC is ~4 frames at ~3-second intra-burst spacing, with the
    next burst about one minute later. CTW3 cadence is untested.

    Returns the merged field dict; callers pour it into PetkitFountainData.
    """
    out: dict[str, Any] = {}
    if alias == "CTW3":
        if len(data) >= 26:
            out.update(parse_device_state(data[:26], alias="CTW3"))
        if len(data) >= 36:
            out.update(parse_device_configuration(data[26:36], alias="CTW3"))
        return out
    # W4X family.
    if len(data) >= 12:
        out.update(parse_device_state(data[:16] if len(data) >= 16 else data))
    if len(data) >= 30:
        out.update(parse_device_configuration(data[16:30]))
    return out


# Per-alias multipliers for the water-purified calculation. Constants match
# slespersen utils.calculate_water_purified.
_WATER_MULTIPLIERS: dict[str, tuple[float, float]] = {
    "W5C": (1.0, 1.3),
    "W4X": (1.8, 1.5),
    "CTW3": (3.0, 1.5),
}
_WATER_MULTIPLIERS_DEFAULT = (2.0, 1.5)


def calculate_water_purified_l(alias: str, pump_runtime_seconds: int) -> float:
    """Liters purified, per slespersen calculate_water_purified.

    Formula: (f3 * pump_runtime_seconds / 60.0) / f2, where (f2, f3) are
    alias-specific constants. Defaults to slespersen's generic (2.0, 1.5)
    for aliases not explicitly mapped (W5, W5N, CTW2).
    """
    f2, f3 = _WATER_MULTIPLIERS.get(alias, _WATER_MULTIPLIERS_DEFAULT)
    return (f3 * pump_runtime_seconds / 60.0) / f2


# NOTE: slespersen's calculate_energy_usage (Wh-vs-kWh ambiguity in the
# constant divisor) was ported here in 0.1.0 but never wired to a sensor,
# so it was removed in 0.1.2 as dead code. Resurrect from git history if a
# future release adds an energy sensor — and validate the unit empirically
# with a kill-a-watt meter before exposing the value.


def calculate_filter_days_left(
    filter_percentage: int, mode: int, smart_time_on: int, smart_time_off: int
) -> int:
    """Days of filter life remaining (ceiling).

    filter_percentage is 0-100 (the raw byte from CMD 210). Internally
    normalized to 0.0-1.0 to match slespersen's formula:
        ((fraction * 30) * (time_on + time_off)) / time_on
    Max output is 30 days at 100% and continuous mode.
    """
    if mode == 1:  # normal: continuous
        time_on, time_off = 1, 0
    else:          # smart: cycle
        time_on, time_off = smart_time_on, smart_time_off
    if time_on == 0:
        return 0
    fraction = filter_percentage / 100.0
    return math.ceil(((fraction * 30.0) * (time_on + time_off)) / time_on)


# ────────────────────────── secret derivation ────────────────────────────────


ADVERT_SERVICE_UUID = "0000c1a4-0000-1000-8000-00805f9b34fb"


def extract_type_code(service_data: dict[str, bytes] | None) -> int | None:
    """Pull PetKit's device-type identifier out of a BLE advertisement's
    service_data block. The identifier indexes into MODEL_MAP and tells us
    the SKU authoritatively (UVC vs non-UVC, W4X vs CTW3 family, etc.).

    The W4X family advertises a 6-byte payload under
    ``0000c1a4-0000-1000-8000-00805f9b34fb`` with the type identifier at
    offset 5 (empirically verified on a Petkit_W4XUVC: the first 4 bytes
    are the device's MAC suffix, byte 4 is reserved/zero, byte 5 is the
    type code — e.g. a synthetic payload of ``0102030400e4`` would carry
    type 0xE4 = 228 = W4XUVC).

    Resolution order: look up the PetKit service UUID directly; if absent,
    fall back to concatenating *all* service_data values in dict order
    (slespersen's pattern) so a hypothetical future firmware that uses a
    different UUID still has a chance of being read. Returns None if the
    buffer is too short to contain offset 5.
    """
    if not service_data:
        return None
    # Preferred: the specific PetKit service UUID. Stable, unambiguous.
    primary = service_data.get(ADVERT_SERVICE_UUID)
    if primary is not None and len(primary) >= 6:
        return primary[5]
    # Fallback: concat-and-index (matches slespersen's original approach).
    # Only relevant if PetKit ever changes the advertisement service UUID.
    combined = bytearray()
    for chunk in service_data.values():
        combined.extend(chunk)
    if len(combined) < 6:
        return None
    return combined[5]


def _split_short(val: int) -> tuple[int, int]:
    """Split a 16-bit integer into (high_byte, low_byte) for big-endian wire
    encoding. Inverse of bytes_to_short over a 2-byte slice."""
    val &= 0xFFFF
    return (val >> 8) & 0xFF, val & 0xFF


def build_config_payload(config: dict[str, int]) -> list[int]:
    """Pack a 14-byte CMD 221 payload from the current+patched config.

    The fountain accepts the entire config block per write; partial updates
    aren't possible. Callers should read the current values from the
    coordinator and patch the fields they want to change.

    Required keys (matches parse_device_configuration outputs):
      smart_time_on, smart_time_off, led_switch, led_brightness,
      led_light_time_on, led_light_time_off, do_not_disturb_switch,
      do_not_disturb_time_on, do_not_disturb_time_off, is_locked
    """
    led_on_hi, led_on_lo = _split_short(config.get("led_light_time_on") or 0)
    led_off_hi, led_off_lo = _split_short(config.get("led_light_time_off") or 0)
    dnd_on_hi, dnd_on_lo = _split_short(config.get("do_not_disturb_time_on") or 0)
    dnd_off_hi, dnd_off_lo = _split_short(config.get("do_not_disturb_time_off") or 0)
    return [
        config.get("smart_time_on", 0) & 0xFF,
        config.get("smart_time_off", 0) & 0xFF,
        config.get("led_switch", 0) & 0xFF,
        config.get("led_brightness", 0) & 0xFF,
        led_on_hi, led_on_lo,
        led_off_hi, led_off_lo,
        config.get("do_not_disturb_switch", 0) & 0xFF,
        dnd_on_hi, dnd_on_lo,
        dnd_off_hi, dnd_off_lo,
        config.get("is_locked", 0) & 0xFF,
    ]


# Sentinel alias for devices we couldn't identify. Read parsers fall through
# to W4X behavior for any non-CTW3 alias (UNKNOWN included), so reads are
# still safe. The asymmetry matters at the *entity* layer: write platforms
# in switch/select/number/button gate on `coordinator.alias == "W4X"` and
# therefore correctly skip registration when the device is UNKNOWN.
ALIAS_UNKNOWN = "UNKNOWN"


def resolve_alias(
    ble_local_name: str | None, pinned_name: str | None, type_code: int | None
) -> str:
    """Pick the protocol-parser branch identifier. W4X covers Eversweet 3 Pro
    variants; CTW3 covers Eversweet Max. Other aliases (W5/W5C/W5N/CTW2) share
    the W4X read-path frame layout per slespersen's "else" branch.

    Returns ALIAS_UNKNOWN when neither type_code nor a name substring matches
    a known SKU. Callers should NOT default UNKNOWN to W4X for write
    decisions — a write to an unknown SKU could land on the wrong byte
    positions. Reading is safe (parsers tolerate the W4X default).
    """
    if type_code is not None and type_code in MODEL_MAP:
        return MODEL_MAP[type_code]["alias"]
    combined = f"{ble_local_name or ''} {pinned_name or ''}".upper()
    for alias in ("CTW3", "CTW2", "W4X", "W5N", "W5C", "W5"):
        if alias in combined:
            return alias
    return ALIAS_UNKNOWN


def resolve_model(
    ble_local_name: str | None, pinned_name: str | None, type_code: int | None
) -> str:
    """Derive the human-readable model name. Three-step resolution, most
    authoritative first: type-code → MODEL_MAP (verified by the device
    itself); then string match on either name source; then a generic
    fallback. The string-match branch only knows about W4X-family markers
    since that's all we can label confidently."""
    # 1. Authoritative: type-code lookup.
    if type_code is not None and type_code in MODEL_MAP:
        return MODEL_MAP[type_code]["product_name"]
    # 2. String-match defense in depth.
    combined = f"{ble_local_name or ''} {pinned_name or ''}".upper()
    if "UVC" in combined:
        return "Eversweet 3 Pro UVC"
    if "W4X" in combined:
        return "Eversweet 3 Pro"
    # 3. Generic fallback — we don't know which SKU it is.
    return "PetKit Fountain"


def compute_secret(device_id_bytes: list[int]) -> list[int]:
    """Derive the 8-byte 'secret' the device expects in CMD 73 + CMD 86 payloads.

    Per slespersen: reverse(device_id_bytes), if trailing two bytes are zero
    replace with (13, 37), then zero-pad to 8 bytes.
    """
    reversed_id = reverse_array(list(device_id_bytes))
    patched = replace_last_two_if_zero(reversed_id)
    return pad_array(patched, 8)
