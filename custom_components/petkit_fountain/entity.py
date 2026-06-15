"""Base entity for PetKit Fountain entities."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import PetkitFountainCoordinator, signal_update


class PetkitFountainEntity(Entity):
    """Common attributes + dispatcher hookup for all entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: PetkitFountainCoordinator) -> None:
        self.coordinator = coordinator

    @property
    def device_info(self) -> DeviceInfo:
        """Built lazily so serial/firmware appear on the device card as soon
        as the first poll populates them — not None at startup."""
        d = self.coordinator.data
        info = DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            name=self.coordinator.name,
            manufacturer="PetKit",
            model="Eversweet 3 Pro UVC",
            connections={("bluetooth", self.coordinator.address)},
        )
        if d.serial:
            info["serial_number"] = d.serial
        if d.firmware is not None:
            info["sw_version"] = str(d.firmware)
        return info

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_update(self.coordinator.entry.entry_id),
                self.async_write_ha_state,
            )
        )

    @property
    def available(self) -> bool:
        return self.coordinator.data.rssi is not None
