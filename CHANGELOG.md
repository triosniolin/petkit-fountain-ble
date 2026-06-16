# Changelog

All notable changes to this project will be documented in this file. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project loosely adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-06-15

Broadens control coverage for non-W4X models where it can be done safely, exposes more CTW3 telemetry, and adds a one-line escape hatch for users who want to test the unverified config-block writes.

### Added

- **Power switch + mode select + reset filter button now register for all aliases.** CMD 220 (power/mode) and CMD 222 (reset filter) take fixed payloads that don't vary by alias per slespersen's source — these can be exposed safely on every PetKit fountain family without inferring byte positions.
- **CTW3 diagnostic sensors.** `electric_status`, `module_status`, `battery_working_time`, `battery_sleep_time` exposed as diagnostic-category entities so CTW3 owners can observe what values the device emits in different states. `suspend_status` exposed as a CTW3-only binary sensor. Slespersen documents the field positions; value semantics are inferred and the diagnostic category nudges users toward "watch these to figure out what they mean" rather than treating them as authoritative.
- **CTW3 set-config payload builder** in `protocol.py`. Inferred 10-byte layout matching slespersen's read parser. Untested against real hardware. Gated behind the experimental flag.
- **`ENABLE_EXPERIMENTAL_NON_W4X_WRITES` flag in `const.py`.** When `True`, the DND switch, LED switch, LED brightness select, and smart-mode timing entities register for non-W4X aliases and attempt writes via the appropriate alias-shaped payload (CTW3 → 10-byte, others → 14-byte). Default `False`. README describes the trade-off and asks adventurers to report findings via GitHub issues so untested rows can graduate.

### Changed

- **`build_config_payload` dispatches by alias** — W4X-family aliases route to the 14-byte layout, CTW3 to the 10-byte layout. `connection.set_config` passes `self.alias` through automatically; entity logic doesn't need to know which shape it's using.
- **`_current_config()` field set is alias-aware.** W4X cares about LED + DND time-of-day shorts; CTW3 cares about battery_working_time + battery_sleep_time instead. The completeness check that prevents partial-write corruption now reads the correct field list per alias.
- **CMD 222 (reset filter) payload aligned to slespersen's source** — now sends `[0]` instead of `[]`. W4X accepts both; the `[0]` form matches the upstream research and is the right shape for the untested aliases this release exposes the reset button to.

Extends discovery + parsing support to the rest of the PetKit fountain family that the upstream slespersen/Jezza34000 research covers — Eversweet Mini (W5/W5C/W5N), Eversweet Solo 2 (CTW2), and Eversweet Max (CTW3) — all explicitly marked **untested**. The verified-on-hardware set is still Eversweet 3 Pro UVC only.

### Added

- **Wider discovery filter.** `manifest.json` and config_flow now match `Petkit_*` instead of `Petkit_W4X*`. Any PetKit fountain advertising the W4X-style or CTW3-style payload will be offered in the discovery prompt.
- **Per-alias parser branches.** `parse_device_state`, `parse_device_configuration`, and `parse_combined_status` accept an `alias` argument that selects between the W4X 12-byte / 14-byte frames (shared with W5/W5C/W5N/CTW2) and the CTW3 26-byte / 10-byte frames. CTW3 carries fields W4X doesn't: battery voltage/percentage, pump runtime today, cat-presence detection, electric-status discrimination.
- **CTW3-only sensors + binary sensors.** Battery voltage, battery percentage, pump runtime today, low-battery warning, cat-detected. Registered only when the device's alias resolves to CTW3.
- **Per-alias water-purified multipliers.** Slespersen's `f2/f3` constants per device family: W5C `(1.0, 1.3)`, W4X `(1.8, 1.5)`, CTW3 `(3.0, 1.5)`, default `(2.0, 1.5)` for unspecified aliases.
- **Status table in README** spelling out verified vs untested model rows and an explicit "why write entities are disabled for non-W4X" note.
- **Options flow for connection mode + poll interval.** Settings → Devices & Services → PetKit Fountain → Configure now exposes two knobs:
  - **Connection mode** — *persistent* (default; one BLE adapter slot held continuously, real-time push frames captured) vs *on-demand* (slot freed between polls; push frames silent — updates only arrive at the poll interval). Useful when the BLE adapter is constrained and other connect-based devices are competing for slots.
  - **Poll interval** — 1–60 minutes, default 5. In persistent mode this is a backstop; in on-demand mode it's the only data path, so lower the interval (60–120s) when switching.
  Saving the form triggers a clean entry reload.
- **Expanded test suite to 30 tests.** Synthetic CTW3 frame fixtures (state + config + combined-status), short-frame rejection, per-alias water-purified math, and explicit regression coverage of the `resolve_alias` / `resolve_model` fallback chains — including a guard that unrecognized devices resolve to `ALIAS_UNKNOWN` rather than silently defaulting to a write-enabled alias. No real CTW3 hardware accessible, so parsers are guarded against regression even though they're unverified end-to-end.

### Changed

- **`coordinator.alias` is now derived** from `MODEL_MAP[type_code]["alias"]` (with name-substring fallbacks) instead of hardcoded `"W4X"`. The connection layer routes inbound frames through the correct parser branch based on this alias. **Unresolved devices return `ALIAS_UNKNOWN`, NOT `W4X`** — read parsers still route UNKNOWN through the W4X read branch (safe), but write entities gate on `alias == "W4X"` exactly, so an unrecognized future SKU never gets W4X write commands sent at it.
- **`coordinator.model` default**, when neither type_code nor name-string match anything we know, is now the generic `PetKit Fountain` (instead of `Eversweet 3 Pro`). Avoids actively mislabeling an unknown SKU as a specific model.

### Fixed

- **"Water purified" sensor now uses the device's actual alias** instead of hardcoding `"W4X"`. The per-alias multipliers (W5C / W4X / CTW3 each have different `(f2, f3)` constants per slespersen) were wired into the calculator but never reached the call site, so CTW3 / W5C users would have gotten W4X's constants applied to their pump runtime. Cosmetic-only — the value is a derived lifetime estimate, not control input — but the multipliers actually take effect now.
- **Config writes no longer silently zero out unread fields.** `async_patch_config` previously rebuilt the 14-byte CMD 221 payload from cached state with `or 0` fallbacks for any `None` field. If a write fired before the config block (CMD 211) had been read — for example, a service call hitting the integration programmatically before the first poll — unpatched fields like DND schedule times and smart-mode timings would be written as zeros, corrupting real device state. Now `_current_config()` returns `None` when the cache is incomplete and `async_patch_config` raises `HomeAssistantError` with an actionable message instead of writing partial garbage.
- **Entities now go unavailable when the fountain stops responding.** Previously the availability gate was effectively "any value was ever populated" — a powered-off or out-of-range fountain would keep reporting stale last-known values as live, indefinitely. The coordinator now stamps a `last_seen` timestamp on every advertisement, push frame, and successful poll, and entities are unavailable if nothing has updated it within 2.5× the configured poll interval (= 12.5 min on the 5-min default). Per-platform `available` overrides were removed in favor of one consistent freshness gate in the base entity.

### Breaking

- **Minimum Home Assistant version raised to 2024.11.0** (was 2024.1.0). The new options flow uses the no-arg `OptionsFlow` pattern that requires HA 2024.11+ — older versions raise `AttributeError: 'PetkitFountainOptionsFlow' object has no attribute 'config_entry'` when the user clicks Configure. Bumping the floor is the cleanest fix; carrying the legacy constructor-arg pattern would mean code that fights HA's deprecation path. If you're on an older HA, upgrade HA first.

### Notes — safety posture for untested SKUs

- **Write entities (switch / select / number / button) are not registered for non-W4X devices.** CMD 220 / 221 / 222 payload byte positions are verified on W4X but unverified on CTW3 and older W5 firmwares; sending a wrong-position payload could change unintended settings.
- **CMD 73 still runs unconditionally** on first connect for every alias — the destructive-pairing warning in the config_flow confirm dialog applies to every model, not just W4X. The pairing-secret derivation comes from slespersen's W5-originated code; behavior on other models is extrapolated, not verified.
- **The discovery filter widening means a user with an untested model can now install the integration.** They'll get read-only telemetry that's likely correct. If something is wrong, file an issue with the model + a brief description of what's broken.

## [0.1.3] — 2026-06-15

### Changed

- **Model detection is now authoritative.** v0.1.2 derived the model name from `ble_device.name`, which can be transiently None at boot — a UVC device could silently mislabel as non-UVC with no log or warning, and never re-evaluate without a reload. Now resolved in three steps, most authoritative first:
  1. The PetKit device-type code is captured at discovery time (from BLE advertisement service-data under `0000c1a4-0000-1000-8000-00805f9b34fb`, byte 5) and pinned into the config entry as `CONF_TYPE_CODE`. Coordinator looks it up in `protocol.MODEL_MAP` — comes from the device itself, doesn't drift. Verified empirically on a Petkit_W4XUVC: byte 5 = 0xE4 = 228 = the W4XUVC key.
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

[0.3.0]: https://github.com/triosniolin/petkit-fountain-ble/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/triosniolin/petkit-fountain-ble/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/triosniolin/petkit-fountain-ble/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/triosniolin/petkit-fountain-ble/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/triosniolin/petkit-fountain-ble/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/triosniolin/petkit-fountain-ble/releases/tag/v0.1.0
[connection.py]: custom_components/petkit_fountain/connection.py
