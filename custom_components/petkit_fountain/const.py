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

# Options-flow keys + defaults. Connection mode trades BLE slot residency
# for update freshness; poll interval is the periodic GATT read cadence.
CONF_CONNECTION_MODE = "connection_mode"
CONF_POLL_INTERVAL_MINUTES = "poll_interval_minutes"

CONNECTION_MODE_PERSISTENT = "persistent"
CONNECTION_MODE_ON_DEMAND = "on_demand"

DEFAULT_CONNECTION_MODE = CONNECTION_MODE_PERSISTENT
DEFAULT_POLL_INTERVAL_MINUTES = 5

# GATT characteristics (per slespersen/PetkitW5BLEMQTT constants.py)
WRITE_UUID = "0000aaa2-0000-1000-8000-00805f9b34fb"
READ_UUID = "0000aaa1-0000-1000-8000-00805f9b34fb"

# Local-name prefix the integration matches on. Catches every PetKit BLE
# fountain — W4X (verified), W5/W5C/W5N/CTW2 (unverified — share the W4X
# read-path frame layout), and CTW3 (unverified — uses different frame
# layouts handled by an explicit CTW3 parser branch).
NAME_PREFIX = "Petkit_"
