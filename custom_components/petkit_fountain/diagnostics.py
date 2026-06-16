"""Diagnostics support for the PetKit Fountain integration."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_SECRET, DOMAIN
from .coordinator import PetkitFountainCoordinator

# Keys redacted from the diagnostics export. Hardware identifiers (serial,
# device_id, address) aren't secret but are scrubbed so users don't have to
# remember to before attaching diagnostics to a public issue. CONF_DEVICE_SECRET
# IS secret — it's the sole credential for controlling the device — and entry.data
# carries it, so it must be redacted here too. (coordinator.data holds no secret;
# it lives on the connection, so entry.data is the only vector.)
REDACT_KEYS = {"serial", "device_id", "address", CONF_DEVICE_SECRET}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: PetkitFountainCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            REDACT_KEYS,
        ),
        "coordinator": async_redact_data(
            {
                "name": coordinator.name,
                "address": coordinator.address,
                "data": asdict(coordinator.data),
            },
            REDACT_KEYS,
        ),
    }
