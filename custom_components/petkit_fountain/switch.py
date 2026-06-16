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

from .const import DOMAIN, ENABLE_EXPERIMENTAL_NON_W4X_WRITES
from .coordinator import PetkitFountainCoordinator, PetkitFountainData
from .entity import PetkitFountainEntity


@dataclass(kw_only=True)
class PetkitSwitchDescription(SwitchEntityDescription):
    value_fn: Callable[[PetkitFountainData], bool | None]
    turn_on_fn: Callable[[PetkitFountainCoordinator], Awaitable[None]]
    turn_off_fn: Callable[[PetkitFountainCoordinator], Awaitable[None]]
    # True if this entity sends CMD 221 (set_config) whose payload byte
    # positions are only verified on W4X. False means the underlying
    # command is alias-agnostic (CMD 220 power, CMD 222 reset filter, etc.)
    # and is safe to register on all aliases.
    requires_w4x: bool = True


SWITCHES: tuple[PetkitSwitchDescription, ...] = (
    PetkitSwitchDescription(
        key="power",
        translation_key="power",
        device_class=SwitchDeviceClass.SWITCH,
        value_fn=lambda d: bool(d.power_status) if d.power_status is not None else None,
        turn_on_fn=lambda c: c.async_set_power(True),
        turn_off_fn=lambda c: c.async_set_power(False),
        requires_w4x=False,  # CMD 220 — alias-agnostic payload
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
    # Power switch (CMD 220) is alias-agnostic and registers everywhere.
    # DND + LED switches use CMD 221 (set_config) whose payload is only
    # verified on W4X; for other aliases we gate them behind the
    # ENABLE_EXPERIMENTAL_NON_W4X_WRITES flag in const.py.
    descriptions = [
        d for d in SWITCHES
        if not d.requires_w4x
        or coordinator.alias == "W4X"
        or ENABLE_EXPERIMENTAL_NON_W4X_WRITES
    ]
    async_add_entities(
        PetkitFountainSwitch(coordinator, description) for description in descriptions
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

    # Availability inherited from PetkitFountainEntity (last_seen freshness).

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.entity_description.turn_on_fn(self.coordinator)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.entity_description.turn_off_fn(self.coordinator)
