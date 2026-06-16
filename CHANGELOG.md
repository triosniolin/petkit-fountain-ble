# Changelog

All notable changes to this project will be documented in this file. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project loosely adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] — 2026-06-15

### Changed

- **Model detection is now authoritative.** v0.1.2 derived the model name from `ble_device.name`, which can be transiently None at boot — a UVC device could silently mislabel as non-UVC with no log or warning, and never re-evaluate without a reload. Now resolved in three steps, most authoritative first:
  1. The PetKit device-type code is captured at discovery time (from BLE advertisement service-data under `0000c1a4-0000-1000-8000-00805f9b34fb`, byte 5) and pinned into the config entry as `CONF_TYPE_CODE`. Coordinator looks it up in `protocol.MODEL_MAP` — comes from the device itself, doesn't drift. Verified empirically on a Petkit_W4XUVC: payload `0102030400e4`, byte 5 = 0xE4 = 228 = the W4XUVC key.
  2. String match against the BLE local_name AND the pinned config-entry name combined — defense in depth if discovery missed service_data.
  3. Conservative default to `Eversweet 3 Pro` (non-UVC) so we never falsely promote a unit to a higher SKU.
- **Auto-backfill of `type_code`** on entries created before v0.1.3 — `async_setup_entry` extracts from the most recent advertisement and persists it back into the entry. The backfill is keyed on *key absence* and only persists when extraction succeeds, so a legacy entry that boots during a BLE blind spot will retry the migration on the next boot rather than pinning a stale None. Heals legacy entries without user action, and can't reintroduce an every-boot loop on fresh 0.1.3 entries because those always have the key written by config_flow.

### Fixed

- **Backfill no longer re-runs on every boot.** The earlier draft gated on `type_code is None`, which couldn't distinguish key-not-present (legacy entry) from key-present-but-None (discovery wrote None into a fresh 0.1.3 entry). Now gates on key presence (`CONF_TYPE_CODE not in entry.data`); coupled with persist-on-success-only, legacy entries retry their one-shot migration until it lands, and 0.1.3 entries are stable from the start.
- **Canonical model string normalized.** The MODEL_MAP `product_name` for the UVC variant was `Eversweet 3 Pro (UVC)` (parens, from slespersen's table), while the string-match fallback produced `Eversweet 3 Pro UVC` (no parens) and v0.1.2 hardcoded the no-parens form. The two resolution paths now produce identical strings, and the device-card label matches what v0.1.2 users were seeing.
- **`extract_type_code` reads from the specific PetKit service UUID** (`0000c1a4-...`) by preference, with the original concat-all-values approach as a fallback for hypothetical future firmwares using a different UUID. Robust to multi-service-data advertisements.

### Added

- **First test suite** at `tests/test_protocol.py`. Covers `extract_type_code` (including a fixture from a real W4XUVC advertisement asserting byte 5 = 228), MODEL_MAP product_name normalization, frame round-trip, secret derivation, state parser, and the filter-days calculator. 14 tests, runs with stdlib `unittest` (no pip install needed).

## [0.1.2] — 2026-06-15

### Fixed

- **`_on_push_update` docstring** in `coordinator.py` said "every ~30s" — stale from before the v0.1.1 cadence correction. Now matches reality: "~1 burst/min, 4 frames at ~3s intra-burst spacing."
- **Hardcoded UVC model on every device.** `entity.py` always reported `Eversweet 3 Pro UVC` regardless of which W4X SKU was connected. Model is now derived from the BLE local_name at coordinator init — `Petkit_W4XUVC` → "Eversweet 3 Pro UVC", `Petkit_W4X` → "Eversweet 3 Pro". The coordinator exposes a `model` attribute so the logic lives in one place.

### Removed

- **Dead `calculate_energy_wh` function** in `protocol.py`. Carried an acknowledged kWh-vs-Wh unit ambiguity from upstream slespersen but was never wired to any entity, so dropping it. A comment marker is left in place explaining where to find it in git history if a future release adds an energy sensor.

## [0.1.1] — 2026-06-15

### Changed

- **Notification de-duplication is now bytewise, not seq-based.** The W4X firmware delivers each BLE notification twice in quick succession; we suppress the duplicate by comparing the full raw frame bytes per command code. Previously we keyed dedup on the `seq` byte, which would silently drop legitimate broadcasts on any future firmware that emits unsolicited frames with a constant `seq`. ([connection.py])
- **Config-flow consent dialog now surfaces the CMD 73 warning.** The README disclosed that first-connect pairing irreversibly severs the official PetKit app's control of the fountain, but the in-UI confirm step did not repeat the warning. The Bluetooth-discovery confirm and the manual-setup descriptions now both include the irreversible-pairing notice so users see it at the moment of consent.
- **Push-cadence documentation corrected to "~1 burst/min".** Earlier comments said "~3s" (which was actually the intra-burst spacing of a single observed burst) or "~30s" (an interim guess). Live capture pattern is a 4-frame burst at ~3s intra-burst spacing, with the next burst about one minute later — so the user-observable update cadence is roughly one per minute under steady operation. Updated `README.md`, `coordinator.py`, `protocol.py`.

### Notes

These three changes were driven by an external code review. Net result: no user-visible regressions, but the integration is now more robust against firmware-revision drift, more honest about device behavior in docs, and gates the destructive-pairing step behind a more visible warning.

## [0.1.0] — 2026-06-15

### Added

Initial public release. Native Home Assistant integration for PetKit Eversweet 3 Pro / 3 Pro UVC fountains over Bluetooth LE — no PetKit cloud and no MQTT broker required.

Built on protocol research from [slespersen/PetkitW5BLEMQTT](https://github.com/slespersen/PetkitW5BLEMQTT) (MIT, 2024) and the Jezza34000 fork extending it for the W4X family. The W4X protocol branch is ported into a self-contained HA custom_components integration.

Entity catalog (21 total):

- 9 sensors: filter life %, filter days remaining, pump runtime, water purified (lifetime), mode, firmware, serial, supply voltage, RSSI
- 4 binary sensors: power state, hardware fault, low water, filter due
- 3 switches: power, do-not-disturb, LED
- 2 selects: operating mode (Normal / Smart), LED brightness
- 2 numbers: smart-mode on/off durations
- 1 button: reset filter

Other features:

- BLE discovery + manual config flow
- Persistent BLE connection via `bleak_retry_connector`
- Unsolicited CMD 230 push parser for near-real-time updates
- 5-minute backstop poll for static fields (firmware, supply voltage)
- Diagnostics export with serial/device_id/address redacted

Tested on the Eversweet 3 Pro UVC (`Petkit_W4XUVC`). The non-UVC Eversweet 3 Pro (`Petkit_W4X`) uses the same parser branch and should work but is unverified. Other PetKit model families (W5 / CTW2 / CTW3) are recognized in the model map but not parsed — extend `protocol.py` to add support.

[0.1.3]: https://github.com/triosniolin/petkit-fountain-ble/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/triosniolin/petkit-fountain-ble/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/triosniolin/petkit-fountain-ble/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/triosniolin/petkit-fountain-ble/releases/tag/v0.1.0
[connection.py]: custom_components/petkit_fountain/connection.py
