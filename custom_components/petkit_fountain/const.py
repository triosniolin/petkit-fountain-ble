"""Constants for the PetKit Fountain BLE integration.

GATT UUIDs and command codes are borrowed from slespersen/PetkitW5BLEMQTT
(MIT-licensed, Copyright 2024 slespersen) and the Jezza34000 fork that
extended it. See protocol.py header for full attribution.
"""

DOMAIN = "petkit_fountain"

# Config entry data keys (in addition to HA's CONF_ADDRESS/CONF_NAME).
# Pinning the device-type code at discovery lets us look up the SKU in
# protocol.MODEL_MAP without depending on later BLE advertisements.
CONF_TYPE_CODE = "type_code"

# The 8-byte device secret, stored hex-encoded. Persisted so the pairing
# command (CMD 73) runs only ONCE per device rather than on every connect:
# at runtime we authenticate (CMD 86) with the stored value instead of
# re-deriving + re-pairing. New devices get a random secret provisioned at
# config-flow time; entries created before this key existed self-migrate by
# deriving the legacy device_id-based secret on first connect (see
# connection._authenticate) and persisting it here — no re-pairing.
CONF_DEVICE_SECRET = "device_secret"

# Options-flow keys + defaults. Connection mode trades BLE slot residency
# for update freshness; poll interval is the periodic GATT read cadence.
CONF_CONNECTION_MODE = "connection_mode"
CONF_POLL_INTERVAL_MINUTES = "poll_interval_minutes"

CONNECTION_MODE_PERSISTENT = "persistent"
CONNECTION_MODE_ON_DEMAND = "on_demand"

DEFAULT_CONNECTION_MODE = CONNECTION_MODE_PERSISTENT
DEFAULT_POLL_INTERVAL_MINUTES = 5

# ─── Experimental flag — set True ONLY if you have a non-W4X PetKit ───
#
# Controls (CMD 220 "set mode/power" and CMD 222 "reset filter") work the
# same on every model — they take fixed payloads with no alias-specific
# byte positions — so power switch, mode select, and reset-filter button
# register for every device by default.
#
# But CMD 221 "set config" (the multi-byte block that drives the DND
# switch, LED switch, LED brightness, and smart-mode timings) sends
# differently-shaped payloads for W4X vs CTW3. The W4X shape is verified.
# The CTW3 shape is INFERRED from slespersen's read parser, never tested
# against real hardware. Other aliases (W5/W5C/W5N/CTW2) share the W4X
# payload shape per the same read research, but are also untested.
#
# Set this to True if you own a non-W4X fountain and want to test the
# inferred write path. The DND/LED/brightness/smart-time entities will
# register and attempt writes via the appropriate alias-shaped payload.
# If a setting flips to a wrong value or stops responding, factory-reset
# the fountain and open a GitHub issue — you're the first person past
# this gate, and your report is how the table moves forward.
ENABLE_EXPERIMENTAL_NON_W4X_WRITES = False

# GATT characteristics (per slespersen/PetkitW5BLEMQTT constants.py)
WRITE_UUID = "0000aaa2-0000-1000-8000-00805f9b34fb"
READ_UUID = "0000aaa1-0000-1000-8000-00805f9b34fb"

# Local-name prefix the integration matches on. Catches every PetKit BLE
# fountain — W4X (verified), W5/W5C/W5N/CTW2 (unverified — share the W4X
# read-path frame layout), and CTW3 (unverified — uses different frame
# layouts handled by an explicit CTW3 parser branch).
NAME_PREFIX = "Petkit_"
