"""Diagnostics support for the PetKit Fountain integration."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import PetkitFountainCoordinator

# Fields that contain hardware identifiers — not secret but worth redacting
# from public bug reports so users don't have to remember to scrub them.
REDACT_KEYS = {"serial", "device_id", "address"}


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
