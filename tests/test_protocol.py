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
