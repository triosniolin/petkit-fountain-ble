"""Select entities for the PetKit Fountain — operating mode + LED brightness."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PetkitFountainCoordinator, PetkitFountainData
from .entity import PetkitFountainEntity

# Mappings between the integer values the firmware uses and the user-friendly
# strings exposed to HA. select.py must round-trip cleanly between them.
MODE_VALUES = {"normal": 1, "smart": 2}
MODE_LABELS = {v: k for k, v in MODE_VALUES.items()}

BRIGHTNESS_VALUES = {"low": 1, "medium": 2, "high": 3}
BRIGHTNESS_LABELS = {v: k for k, v in BRIGHTNESS_VALUES.items()}


@dataclass(kw_only=True)
class PetkitSelectDescription(SelectEntityDescription):
    value_fn: Callable[[PetkitFountainData], str | None]
    select_fn: Callable[[PetkitFountainCoordinator, str], Awaitable[None]]


SELECTS: tuple[PetkitSelectDescription, ...] = (
    PetkitSelectDescription(
        key="mode",
        translation_key="mode_select",
        options=list(MODE_VALUES.keys()),
        value_fn=lambda d: MODE_LABELS.get(d.mode) if d.mode is not None else None,
        select_fn=lambda c, opt: c.async_set_mode(MODE_VALUES[opt]),
    ),
    PetkitSelectDescription(
        key="led_brightness",
        translation_key="led_brightness",
        options=list(BRIGHTNESS_VALUES.keys()),
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda d: (
            BRIGHTNESS_LABELS.get(d.led_brightness)
            if d.led_brightness is not None
            else None
        ),
        select_fn=lambda c, opt: c.async_patch_config(
            led_brightness=BRIGHTNESS_VALUES[opt]
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PetkitFountainCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PetkitFountainSelect(coordinator, description) for description in SELECTS
    )


class PetkitFountainSelect(PetkitFountainEntity, SelectEntity):
    entity_description: PetkitSelectDescription

    def __init__(
        self,
        coordinator: PetkitFountainCoordinator,
        description: PetkitSelectDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def current_option(self) -> str | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.current_option is not None

    async def async_select_option(self, option: str) -> None:
        await self.entity_description.select_fn(self.coordinator, option)
