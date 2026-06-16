# PetKit Fountain (Local BLE)

A Home Assistant custom integration that controls a **PetKit Eversweet 3 Pro / 3 Pro UVC** water fountain over Bluetooth LE — locally, with **no PetKit cloud and no MQTT broker**. The fountain talks directly to your HA host's BT adapter.

## Status

| Model | BLE local_name | Parser branch | Status |
|---|---|---|---|
| Eversweet 3 Pro UVC | `Petkit_W4XUVC` | W4X | ✅ Verified — sensors + write controls |
| Eversweet 3 Pro | `Petkit_W4X` | W4X | 🟡 Shares verified branch; unverified end-to-end |
| Eversweet Mini | `Petkit_W5`, `Petkit_W5C`, `Petkit_W5N` | W4X (read-only) | 🟠 **Untested** — discovery + read-path likely work, write entities disabled |
| Eversweet Solo 2 | `Petkit_CTW2` | W4X (read-only) | 🟠 **Untested** — discovery + read-path likely work, write entities disabled |
| Eversweet Max | `Petkit_CTW3` | CTW3 (read-only) | 🟠 **Untested** — distinct parser branch, write entities disabled |

**Verified** means I personally own the unit, the integration has been running on it for a meaningful period, and write commands (mode change, DND toggle, etc.) actually do what they say. Other rows are based on protocol research from upstream slespersen/Jezza34000 work but have **never been run against real hardware** by this maintainer.

If you own one of the untested models and want to help, install, file an issue with the model + a brief description of what works, and the verified/untested table moves forward.

### Why write entities are disabled for non-W4X

Read parsers for the other PetKit fountain families share the W4X byte layout (slespersen's "else" branch) or have an explicit CTW3 branch — those are mechanical to port and likely correct. **Write commands** (CMD 220 / CMD 221 / CMD 222), however, expect payloads with byte positions that have been verified on W4X but may differ on CTW3 or older W5 firmwares. Sending a wrong-position payload could change unintended settings or destabilize the device. To keep the experimental support safe, switches/selects/numbers/buttons are simply not registered for non-W4X devices.

## Features

- 9 sensors: filter life % / filter days remaining, pump runtime, water purified (lifetime), mode, firmware, serial, supply voltage, RSSI
- 4 binary sensors: power state, hardware fault, low water, filter due
- 3 switches: power, do-not-disturb, LED
- 2 selects: operating mode (Normal / Smart), LED brightness
- 2 number entities: smart-mode on/off durations
- 1 button: filter reset
- State updates via the fountain's own unsolicited push broadcasts — typically a burst of frames roughly once per minute under steady operation, so most monitoring is push-driven without aggressive polling
- Diagnostics export with sensitive fields redacted

## Important caveat — pairing breaks the official PetKit app

The integration sends `CMD 73 (init_device)` on first connect, which sets a device-side secret derived from the fountain's `device_id`. **After this runs, the official PetKit app can no longer control this fountain** (the cloud-side session binding is invalidated). If you install this integration, you are committing to local-only operation. There is no documented way back without a factory reset.

## Installation

### HACS (recommended)

1. In HACS, open the Integrations section
2. Three-dot menu → "Custom repositories"
3. Add `https://github.com/triosniolin/petkit-fountain-ble` as category `Integration`
4. Install "PetKit Fountain (Local BLE)"
5. Restart Home Assistant
6. Settings → Devices & Services → Add Integration → search "PetKit Fountain (BLE)"

### Manual

1. Copy `custom_components/petkit_fountain/` into your HA `config/custom_components/` directory
2. Restart Home Assistant
3. Add the integration via Settings → Devices & Services

## Configuration

No YAML required. Once installed, HA's Bluetooth integration auto-discovers the fountain (it advertises as `Petkit_W4XUVC`, `Petkit_W4X`, `Petkit_CTW3`, etc.); confirm in the discovery prompt.

### Options

After install, **Settings → Devices & Services → PetKit Fountain → Configure** exposes:

- **Connection mode**
  - *Persistent* (default): one BLE adapter slot is held continuously. Real-time push frames (~1 burst/min) deliver state changes near-instantly, and control commands fire in <1s.
  - *On-demand*: BLE slot is freed between polls. Updates only arrive at the poll interval, and control commands incur ~2–3s of reconnect overhead. Use this when other connect-based BLE devices are competing for adapter slots.
- **Poll interval** (1–60 minutes, default 5): how often the integration reads the full state set. In persistent mode this is a backstop; in on-demand mode it's the only data path — lower it (60–120s) when switching modes.

Saving the form triggers a clean integration reload — expect entities to show `unavailable` for ~5–15 seconds while the new connection is established.

## Requirements

- A Home Assistant instance with the Bluetooth integration set up and a working BT adapter (or an ESPHome BT proxy with range to the fountain)
- Home Assistant **2024.11 or newer** (the options flow uses the no-arg `OptionsFlow` pattern introduced in 2024.11; older versions raise `AttributeError` when opening the Configure dialog)

## Acknowledgements

Built on protocol research by:

- **slespersen** — original W5 BLE protocol reverse-engineering, MIT-licensed at [slespersen/PetkitW5BLEMQTT](https://github.com/slespersen/PetkitW5BLEMQTT)
- **Jezza34000** — extended slespersen's library for CTW2/CTW3/W4X model families ([Jezza34000/PetkitW5BLEMQTT](https://github.com/Jezza34000/PetkitW5BLEMQTT)) and maintains the cloud-based PetKit HACS integration ([Jezza34000/homeassistant_petkit](https://github.com/Jezza34000/homeassistant_petkit))

This integration ports the W4X path of that protocol research into a native HA `custom_components` package with no MQTT broker dependency. The `protocol.py` module preserves the original MIT copyright notice per upstream terms.

## License

MIT — see [LICENSE](LICENSE).
