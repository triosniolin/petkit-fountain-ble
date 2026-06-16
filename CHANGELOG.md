# Changelog

All notable changes to this project will be documented in this file. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project loosely adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.1]: https://github.com/triosniolin/petkit-fountain-ble/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/triosniolin/petkit-fountain-ble/releases/tag/v0.1.0
[connection.py]: custom_components/petkit_fountain/connection.py
