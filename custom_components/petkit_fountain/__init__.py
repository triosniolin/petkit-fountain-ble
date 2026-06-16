"""PetKit Fountain BLE integration."""
from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_CONNECTION_MODE,
    CONF_DEVICE_SECRET,
    CONF_POLL_INTERVAL_MINUTES,
    CONF_TYPE_CODE,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_POLL_INTERVAL_MINUTES,
    DOMAIN,
)
from .coordinator import PetkitFountainCoordinator
from .protocol import extract_type_code

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PetKit Fountain from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), connectable=True
    )
    if ble_device is None:
        raise ConfigEntryNotReady(
            f"PetKit Fountain {address} not advertising; check power + range"
        )

    # Use the name + type-code pinned at setup time over whatever the
    # current BLE advertisement says — advertisement fields can be
    # transiently None at boot, which would cause device-name drift in HA's
    # device registry and silently mislabel the model.
    name = entry.data.get(CONF_NAME) or ble_device.name or "PetKit Fountain"
    type_code = entry.data.get(CONF_TYPE_CODE)
    # Self-heal entries created before CONF_TYPE_CODE existed by extracting
    # from the most recent advertisement and persisting the result. Only
    # legacy entries (key absent) take this branch — fresh 0.1.3+ entries
    # always have the key written by config_flow, so the every-boot loop
    # bug from the previous draft can't recur here.
    #
    # We only PERSIST when extraction succeeds. If a legacy entry happens to
    # boot during a BLE blind spot and extract returns None, leave the key
    # absent so the next boot retries the migration. (A None pin would
    # be cosmetically harmless thanks to the string-match fallback, but
    # leaves a stale None in storage forever.)
    if CONF_TYPE_CODE not in entry.data:
        last_info = bluetooth.async_last_service_info(
            hass, address, connectable=True
        )
        backfilled = (
            extract_type_code(last_info.service_data) if last_info else None
        )
        if backfilled is not None:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_TYPE_CODE: backfilled}
            )
            _LOGGER.info(
                "Backfilled type_code=%d into legacy config entry", backfilled
            )
        type_code = backfilled
    connection_mode = entry.options.get(CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE)
    poll_interval = entry.options.get(
        CONF_POLL_INTERVAL_MINUTES, DEFAULT_POLL_INTERVAL_MINUTES
    )
    # Stored device secret (hex). Absent on entries created before the
    # stored-secret model — those self-migrate on first connect by deriving
    # the legacy value and persisting it (see coordinator._maybe_persist_secret).
    secret_hex = entry.data.get(CONF_DEVICE_SECRET)
    try:
        secret = bytes.fromhex(secret_hex) if secret_hex else None
    except ValueError:
        _LOGGER.warning("Stored device secret is malformed; will re-derive")
        secret = None
    coordinator = PetkitFountainCoordinator(
        hass,
        entry,
        ble_device,
        name,
        type_code,
        secret=secret,
        connection_mode=connection_mode,
        poll_interval_minutes=poll_interval,
    )
    entry.async_on_unload(coordinator.async_start())
    # Reload the entry on options-flow saves so connection mode / poll
    # interval changes take effect cleanly.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
