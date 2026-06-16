# PetKit Fountain (Local BLE)

A Home Assistant custom integration that controls a **PetKit Eversweet 3 Pro / 3 Pro UVC** water fountain over Bluetooth LE — locally, with **no PetKit cloud and no MQTT broker**. The fountain talks directly to your HA host's BT adapter.

## Status

Tested on **Eversweet 3 Pro UVC** (BLE local_name `Petkit_W4XUVC`, internal alias `W4X`). The W4X protocol code path also covers the non-UVC Eversweet 3 Pro (`Petkit_W4X`); both should work, only the UVC variant has been verified.

Other PetKit fountain families (W5 / CTW2 / CTW3) are **not** supported — only their model codes are recognized in the model map. Add a new parser branch if you want to extend support.

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

No YAML required. Once installed, HA's Bluetooth integration auto-discovers the fountain (it advertises as `Petkit_W4XUVC` or `Petkit_W4X`); confirm in the discovery prompt.

## Requirements

- A Home Assistant instance with the Bluetooth integration set up and a working BT adapter (or an ESPHome BT proxy with range to the fountain)
- Home Assistant 2024.1 or newer

## Acknowledgements

Built on protocol research by:

- **slespersen** — original W5 BLE protocol reverse-engineering, MIT-licensed at [slespersen/PetkitW5BLEMQTT](https://github.com/slespersen/PetkitW5BLEMQTT)
- **Jezza34000** — extended slespersen's library for CTW2/CTW3/W4X model families ([Jezza34000/PetkitW5BLEMQTT](https://github.com/Jezza34000/PetkitW5BLEMQTT)) and maintains the cloud-based PetKit HACS integration ([Jezza34000/homeassistant_petkit](https://github.com/Jezza34000/homeassistant_petkit))

This integration ports the W4X path of that protocol research into a native HA `custom_components` package with no MQTT broker dependency. The `protocol.py` module preserves the original MIT copyright notice per upstream terms.

## License

MIT — see [LICENSE](LICENSE).
