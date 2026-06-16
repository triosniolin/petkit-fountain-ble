"""Select entities for the PetKit Fountain — operating mode + LED brightness."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENABLE_EXPERIMENTAL_NON_W4X_WRITES
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
    # Same gate semantic as switch.py: False means alias-agnostic command,
    # True means CMD 221 (set_config) which is W4X-verified only.
    requires_w4x: bool = True


SELECTS: tuple[PetkitSelectDescription, ...] = (
    PetkitSelectDescription(
        key="mode",
        translation_key="mode_select",
        options=list(MODE_VALUES.keys()),
        value_fn=lambda d: MODE_LABELS.get(d.mode) if d.mode is not None else None,
        select_fn=lambda c, opt: c.async_set_mode(MODE_VALUES[opt]),
        requires_w4x=False,  # CMD 220 — alias-agnostic
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
    descriptions = [
        d for d in SELECTS
        if not d.requires_w4x
        or coordinator.alias == "W4X"
        or ENABLE_EXPERIMENTAL_NON_W4X_WRITES
    ]
    async_add_entities(
        PetkitFountainSelect(coordinator, description) for description in descriptions
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

    # Availability inherited from PetkitFountainEntity (last_seen freshness).

    async def async_select_option(self, option: str) -> None:
        await self.entity_description.select_fn(self.coordinator, option)
