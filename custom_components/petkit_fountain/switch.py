"""Switch entities for the PetKit Fountain — power, DND, LED."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PetkitFountainCoordinator, PetkitFountainData
from .entity import PetkitFountainEntity


@dataclass(kw_only=True)
class PetkitSwitchDescription(SwitchEntityDescription):
    value_fn: Callable[[PetkitFountainData], bool | None]
    turn_on_fn: Callable[[PetkitFountainCoordinator], Awaitable[None]]
    turn_off_fn: Callable[[PetkitFountainCoordinator], Awaitable[None]]


SWITCHES: tuple[PetkitSwitchDescription, ...] = (
    PetkitSwitchDescription(
        key="power",
        translation_key="power",
        device_class=SwitchDeviceClass.SWITCH,
        value_fn=lambda d: bool(d.power_status) if d.power_status is not None else None,
        turn_on_fn=lambda c: c.async_set_power(True),
        turn_off_fn=lambda c: c.async_set_power(False),
    ),
    PetkitSwitchDescription(
        key="dnd",
        translation_key="dnd",
        device_class=SwitchDeviceClass.SWITCH,
        value_fn=lambda d: (
            bool(d.do_not_disturb_switch)
            if d.do_not_disturb_switch is not None
            else None
        ),
        turn_on_fn=lambda c: c.async_patch_config(do_not_disturb_switch=1),
        turn_off_fn=lambda c: c.async_patch_config(do_not_disturb_switch=0),
    ),
    PetkitSwitchDescription(
        key="led",
        translation_key="led",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda d: bool(d.led_switch) if d.led_switch is not None else None,
        turn_on_fn=lambda c: c.async_patch_config(led_switch=1),
        turn_off_fn=lambda c: c.async_patch_config(led_switch=0),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PetkitFountainCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PetkitFountainSwitch(coordinator, description) for description in SWITCHES
    )


class PetkitFountainSwitch(PetkitFountainEntity, SwitchEntity):
    entity_description: PetkitSwitchDescription

    def __init__(
        self,
        coordinator: PetkitFountainCoordinator,
        description: PetkitSwitchDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.is_on is not None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.entity_description.turn_on_fn(self.coordinator)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.entity_description.turn_off_fn(self.coordinator)
