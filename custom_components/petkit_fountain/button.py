"""Button entities for the PetKit Fountain — filter reset."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PetkitFountainCoordinator
from .entity import PetkitFountainEntity


RESET_FILTER = ButtonEntityDescription(
    key="reset_filter",
    translation_key="reset_filter",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PetkitFountainCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.alias != "W4X":
        return  # write entities verified on W4X only — see switch.py for rationale
    async_add_entities([PetkitFountainResetFilterButton(coordinator)])


class PetkitFountainResetFilterButton(PetkitFountainEntity, ButtonEntity):
    def __init__(self, coordinator: PetkitFountainCoordinator) -> None:
        super().__init__(coordinator)
        self.entity_description = RESET_FILTER
        self._attr_unique_id = f"{coordinator.address}_reset_filter"

    # Availability inherited from PetkitFountainEntity (last_seen freshness).
    # Pressing a "reset filter" button on an offline device wouldn't do
    # anything anyway, so the freshness gate is the right semantic.

    async def async_press(self) -> None:
        await self.coordinator.async_reset_filter()
