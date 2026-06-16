"""Pure-Python tests for protocol.py — no Home Assistant import path required.

Run with: `python -m unittest tests.test_protocol` from the repo root, OR
load directly via importlib so HA's package-style __init__ isn't a problem.
The latter is what this file does so the tests work from a clean checkout
with no `pip install` of the integration.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest


_PROTOCOL_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "petkit_fountain"
    / "protocol.py"
)


def _load_protocol():
    spec = importlib.util.spec_from_file_location("petkit_protocol", _PROTOCOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p = _load_protocol()


# Captured advertisement service_data, 00:00:01:02:03:04 (Petkit_W4XUVC),
# 2026-06-15. Byte 5 = 0xE4 = 228 = the MODEL_MAP key for the W4XUVC.
W4XUVC_SERVICE_DATA = {
    "0000c1a4-0000-1000-8000-00805f9b34fb": bytes.fromhex("0102030400e4"),
}


class TestExtractTypeCode(unittest.TestCase):
    def test_w4xuvc_captured_advertisement(self):
        """The blocker the v0.1.3 review flagged: assert what byte 5 of the
        real W4XUVC advertisement actually contains."""
        self.assertEqual(p.extract_type_code(W4XUVC_SERVICE_DATA), 228)

    def test_lookup_resolves_to_correct_sku(self):
        """And that the extracted value indexes MODEL_MAP correctly."""
        code = p.extract_type_code(W4XUVC_SERVICE_DATA)
        entry = p.MODEL_MAP[code]
        self.assertEqual(entry["name"], "Petkit_W4XUVC")
        self.assertEqual(entry["alias"], "W4X")
        self.assertEqual(entry["product_name"], "Eversweet 3 Pro UVC")

    def test_canonical_uvc_string_no_parens(self):
        """Step-1 (MODEL_MAP) and step-2 (string-match fallback) in
        coordinator._resolve_model must produce the SAME label. If someone
        re-adds the parens to MODEL_MAP, the two paths diverge and existing
        device cards shift on upgrade — guard against that drift."""
        for entry in p.MODEL_MAP.values():
            self.assertNotIn(
                "(",
                entry["product_name"],
                f"product_name {entry['product_name']!r} contains parens; the "
                "string-match fallback emits the no-parens form, so MODEL_MAP "
                "must match.",
            )

    def test_none_service_data(self):
        self.assertIsNone(p.extract_type_code(None))
        self.assertIsNone(p.extract_type_code({}))

    def test_too_short(self):
        """A payload under 6 bytes can't have byte 5; must return None."""
        short = {"0000c1a4-0000-1000-8000-00805f9b34fb": b"\x01\x02\x03"}
        self.assertIsNone(p.extract_type_code(short))

    def test_unknown_uuid_falls_back_to_concat(self):
        """If the PetKit-specific UUID isn't present but some other vendor
        UUID is, we still attempt the concat-and-index path so a future
        firmware with a different service UUID has a chance of being read."""
        data = {"0000ffff-0000-1000-8000-00805f9b34fb": bytes.fromhex("000000000099")}
        self.assertEqual(p.extract_type_code(data), 0x99)

    def test_prefers_petkit_uuid_when_both_present(self):
        """If both the PetKit UUID and some other UUID carry data, the
        PetKit one wins regardless of dict-iteration order."""
        data = {
            "0000ffff-0000-1000-8000-00805f9b34fb": bytes.fromhex("000000000011"),
            "0000c1a4-0000-1000-8000-00805f9b34fb": bytes.fromhex("0102030400e4"),
        }
        self.assertEqual(p.extract_type_code(data), 228)


class TestResolveAlias(unittest.TestCase):
    """The asymmetry guard: an unrecognized device must NOT default to W4X,
    because the W4X alias is also what gates write-entity registration. A
    truly unknown future PetKit SKU should be treated as read-only until
    verified, not have W4X write commands sent at it."""

    def test_type_code_lookup(self):
        # 228 = Petkit_W4XUVC
        self.assertEqual(p.resolve_alias(None, None, 228), "W4X")
        # 223 = Petkit_CTW3
        self.assertEqual(p.resolve_alias(None, None, 223), "CTW3")

    def test_name_substring_match(self):
        self.assertEqual(p.resolve_alias("Petkit_W4XUVC", None, None), "W4X")
        self.assertEqual(p.resolve_alias("Petkit_CTW3", None, None), "CTW3")
        self.assertEqual(p.resolve_alias("Petkit_W5C", None, None), "W5C")
        # Pinned name path also works
        self.assertEqual(p.resolve_alias(None, "Petkit_CTW2", None), "CTW2")

    def test_unknown_device_does_not_default_to_w4x(self):
        """Asymmetry guard: an unrecognized advert + name = UNKNOWN, NOT
        W4X. Write-entity platforms gate on `alias == 'W4X'`, so UNKNOWN
        correctly disables writes for safety."""
        self.assertEqual(
            p.resolve_alias("Petkit_FutureSKU", None, None), p.ALIAS_UNKNOWN
        )
        self.assertEqual(p.resolve_alias(None, None, None), p.ALIAS_UNKNOWN)
        # Unknown type_code (not in MODEL_MAP) AND unrecognized name
        self.assertEqual(
            p.resolve_alias("NotAPetkit", "Something", 999), p.ALIAS_UNKNOWN
        )

    def test_unknown_alias_still_reads_safely(self):
        """Read parsers route any non-CTW3 alias through the W4X branch, so
        an UNKNOWN device still gets sensible reads (just no writes)."""
        # Synthesize a W4X-shape frame; parsing with alias=UNKNOWN should
        # land in the W4X branch and produce real values.
        data = bytes([1, 2, 0, 0, 0, 0, 0, 0, 1, 244, 87, 1])
        result = p.parse_device_state(data, alias=p.ALIAS_UNKNOWN)
        self.assertEqual(result["power_status"], 1)
        self.assertEqual(result["filter_percentage"], 87)


class TestResolveModel(unittest.TestCase):
    def test_type_code_lookup_returns_canonical_no_parens(self):
        self.assertEqual(p.resolve_model(None, None, 228), "Eversweet 3 Pro UVC")
        self.assertEqual(p.resolve_model(None, None, 214), "Eversweet 3 Pro")

    def test_name_string_match(self):
        self.assertEqual(
            p.resolve_model("Petkit_W4XUVC", None, None), "Eversweet 3 Pro UVC"
        )
        self.assertEqual(p.resolve_model("Petkit_W4X", None, None), "Eversweet 3 Pro")

    def test_generic_fallback(self):
        """Unknown SKUs get the generic label — avoids mislabeling a future
        device as something specific we don't actually recognize."""
        self.assertEqual(p.resolve_model(None, None, None), "PetKit Fountain")
        self.assertEqual(
            p.resolve_model("NotAPetkit", "Something", 999), "PetKit Fountain"
        )


class TestFrameBuilder(unittest.TestCase):
    def test_round_trip(self):
        """build_command then parse_frame should round-trip the fields."""
        frame = p.build_command(seq=5, cmd=210, type_=1, data=[0, 0])
        parsed = p.parse_frame(frame)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["cmd"], 210)
        self.assertEqual(parsed["type"], 1)
        self.assertEqual(parsed["seq"], 5)
        self.assertEqual(parsed["data"], b"\x00\x00")

    def test_parse_frame_rejects_malformed(self):
        # Wrong header
        self.assertIsNone(p.parse_frame(b"\x00\x00\x00\xd2\x01\x01\x00\x00\xfb"))
        # Wrong trailer
        self.assertIsNone(p.parse_frame(b"\xfa\xfc\xfd\xd2\x01\x01\x00\x00\x00"))
        # Too short
        self.assertIsNone(p.parse_frame(b"\xfa\xfc\xfd"))


class TestComputeSecret(unittest.TestCase):
    def test_pads_to_eight_bytes(self):
        self.assertEqual(len(p.compute_secret([0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6])), 8)

    def test_applies_13_37_patch_on_trailing_zeroes(self):
        """If the reversed device_id ends in two zero bytes, PetKit's
        derivation patches them to (13, 37). Cover that branch."""
        # Original ends with [..., 0x12, 0x34, 0x00, 0x00]
        # Reversed: [0x00, 0x00, 0x34, 0x12, ...] — trailing two are NOT zero.
        # Want trailing two of REVERSED to be zero: original must START with two zeroes.
        result = p.compute_secret([0x00, 0x00, 0x12, 0x34, 0x00, 0x00])
        # Reversed: [0,0,0x34,0x12,0,0] → patch last two → [0,0,0x34,0x12,13,37]
        # Pad to 8: [0,0,0,0,0x34,0x12,13,37]
        self.assertEqual(result, [0, 0, 0, 0, 0x34, 0x12, 13, 37])


class TestParseDeviceState(unittest.TestCase):
    def test_w4x_minimum_frame(self):
        """A 12-byte W4X state frame: power=on, mode=smart, pump 500s, filter 87%."""
        data = bytes([1, 2, 0, 0, 0, 0, 0, 0, 1, 244, 87, 1])
        result = p.parse_device_state(data)
        self.assertEqual(result["power_status"], 1)
        self.assertEqual(result["mode"], 2)
        self.assertEqual(result["pump_runtime"], 500)
        self.assertEqual(result["filter_percentage"], 87)

    def test_ctw3_26byte_frame(self):
        """A 26-byte CTW3 state frame — synthesized from the field layout
        slespersen documents. Untested against real hardware; this test
        guards the field-extraction code from regressing while we wait for
        a Max-family owner to verify."""
        data = bytes([
            1,            # power_status = on
            0,            # suspend_status
            2,            # mode = smart
            1,            # electric_status
            0,            # dnd_state
            0, 0, 0, 0,   # warning_breakdown, water_missing, low_battery, warning_filter
            0, 0, 8, 64,  # pump_runtime (4 bytes BE) = 0x00000840 = 2112
            75,           # filter_percentage = 75
            1,            # running_status
            0, 0, 0, 30,  # pump_runtime_today = 30 seconds
            1,            # detect_status (cat present)
            0x14, 0xb4,   # supply_voltage = 0x14b4 mV = 5300 mV = 5.3 V
            0x10, 0x68,   # battery_voltage = 0x1068 mV = 4200 mV = 4.2 V
            85,           # battery_percentage = 85
            1,            # module_status
        ])
        result = p.parse_device_state(data, alias="CTW3")
        self.assertEqual(result["power_status"], 1)
        self.assertEqual(result["mode"], 2)
        self.assertEqual(result["pump_runtime"], 2112)
        self.assertEqual(result["filter_percentage"], 75)
        self.assertEqual(result["pump_runtime_today"], 30)
        self.assertAlmostEqual(result["supply_voltage"], 5.3, places=3)
        self.assertAlmostEqual(result["battery_voltage"], 4.2, places=3)
        self.assertEqual(result["battery_percentage"], 85)
        self.assertEqual(result["detect_status"], 1)

    def test_ctw3_rejects_short_frame(self):
        """A frame too short for the CTW3 26-byte layout should return {},
        not partially-populated nonsense."""
        result = p.parse_device_state(bytes(20), alias="CTW3")
        self.assertEqual(result, {})


class TestParseDeviceConfiguration(unittest.TestCase):
    def test_ctw3_10byte_frame(self):
        """CTW3 config swaps the LED+DND schedule slots for battery timings."""
        data = bytes([
            30,           # smart_time_on
            10,           # smart_time_off
            0x00, 0x3c,   # battery_working_time = 60 min
            0x00, 0x05,   # battery_sleep_time = 5 min
            1,            # led_switch
            2,            # led_brightness = medium
            0,            # dnd_switch
            0,            # is_locked
        ])
        result = p.parse_device_configuration(data, alias="CTW3")
        self.assertEqual(result["smart_time_on"], 30)
        self.assertEqual(result["smart_time_off"], 10)
        self.assertEqual(result["battery_working_time"], 60)
        self.assertEqual(result["battery_sleep_time"], 5)
        self.assertEqual(result["led_switch"], 1)
        self.assertEqual(result["led_brightness"], 2)
        self.assertEqual(result["do_not_disturb_switch"], 0)
        self.assertEqual(result["is_locked"], 0)


class TestCombinedStatus(unittest.TestCase):
    def test_w4x_30byte_combined(self):
        """W4X combined-status broadcast: 16 bytes state + 14 bytes config."""
        # Real frame from production capture — confirms the unified
        # parser extracts both portions.
        raw = bytes.fromhex(
            "010100000000000840534f010000c1983c010102000005a0000528016800"
        )
        result = p.parse_combined_status(raw)
        self.assertEqual(result["power_status"], 1)
        self.assertEqual(result["mode"], 1)
        self.assertEqual(result["filter_percentage"], 79)
        self.assertEqual(result["led_switch"], 1)
        self.assertEqual(result["led_brightness"], 2)

    def test_ctw3_alias_uses_ctw3_branches(self):
        """For CTW3, state portion is 26 bytes and config is 10 bytes."""
        state = bytes([
            1, 0, 2, 1, 0, 0, 0, 0, 0,
            0, 0, 8, 64, 75, 1,
            0, 0, 0, 30, 1,
            0x14, 0xb4, 0x10, 0x68, 85, 1,
        ])
        config = bytes([30, 10, 0, 60, 0, 5, 1, 2, 0, 0])
        result = p.parse_combined_status(state + config, alias="CTW3")
        self.assertEqual(result["power_status"], 1)
        self.assertEqual(result["pump_runtime_today"], 30)
        self.assertEqual(result["battery_percentage"], 85)
        self.assertEqual(result["smart_time_on"], 30)
        self.assertEqual(result["battery_working_time"], 60)


class TestWaterPurifiedMultipliers(unittest.TestCase):
    """Per-alias multipliers per slespersen calculate_water_purified."""

    def test_w4x(self):
        # (1.5 * 500 / 60) / 1.8 = 6.944
        self.assertAlmostEqual(
            p.calculate_water_purified_l("W4X", 500), 6.944, places=2
        )

    def test_w5c(self):
        # f2=1.0, f3=1.3 — (1.3 * 500 / 60) / 1.0 = 10.833
        self.assertAlmostEqual(
            p.calculate_water_purified_l("W5C", 500), 10.833, places=2
        )

    def test_ctw3(self):
        # f2=3.0, f3=1.5 — (1.5 * 500 / 60) / 3.0 = 4.167
        self.assertAlmostEqual(
            p.calculate_water_purified_l("CTW3", 500), 4.167, places=2
        )

    def test_unknown_alias_falls_back_to_default(self):
        # default (2.0, 1.5) — (1.5 * 500 / 60) / 2.0 = 6.25
        self.assertAlmostEqual(
            p.calculate_water_purified_l("W5", 500), 6.25, places=2
        )


class TestCalculateFilterDaysLeft(unittest.TestCase):
    def test_smart_mode_30_30(self):
        """At 87% in smart 30/30, expected ceil((0.87 * 30) * 60 / 30) = 53."""
        self.assertEqual(
            p.calculate_filter_days_left(87, mode=2, smart_time_on=30, smart_time_off=30),
            53,
        )

    def test_normal_mode_clamped_continuous(self):
        """Normal mode uses (1, 0) regardless of stored smart times."""
        self.assertEqual(
            p.calculate_filter_days_left(100, mode=1, smart_time_on=99, smart_time_off=99),
            30,
        )


if __name__ == "__main__":
    unittest.main()
