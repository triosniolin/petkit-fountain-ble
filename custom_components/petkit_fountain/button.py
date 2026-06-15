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
    async_add_entities([PetkitFountainResetFilterButton(coordinator)])


class PetkitFountainResetFilterButton(PetkitFountainEntity, ButtonEntity):
    def __init__(self, coordinator: PetkitFountainCoordinator) -> None:
        super().__init__(coordinator)
        self.entity_description = RESET_FILTER
        self._attr_unique_id = f"{coordinator.address}_reset_filter"

    @property
    def available(self) -> bool:
        # Button is always usable once the coordinator has any data
        # established; no per-value freshness gate.
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_reset_filter()
