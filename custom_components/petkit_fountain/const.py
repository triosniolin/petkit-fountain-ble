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

# GATT characteristics (per slespersen/PetkitW5BLEMQTT constants.py)
WRITE_UUID = "0000aaa2-0000-1000-8000-00805f9b34fb"
READ_UUID = "0000aaa1-0000-1000-8000-00805f9b34fb"

# Local-name prefixes the integration matches on. The W4X family covers both
# the Eversweet 3 Pro (Petkit_W4X) and 3 Pro UVC (Petkit_W4XUVC) — same parser
# branch, different product names.
NAME_PREFIX = "Petkit_W4X"
