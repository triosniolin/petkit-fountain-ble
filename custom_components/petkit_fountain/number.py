"""Number entities for the PetKit Fountain — smart mode on/off durations."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENABLE_EXPERIMENTAL_NON_W4X_WRITES
from .coordinator import PetkitFountainCoordinator, PetkitFountainData
from .entity import PetkitFountainEntity


@dataclass(kw_only=True)
class PetkitNumberDescription(NumberEntityDescription):
    value_fn: Callable[[PetkitFountainData], int | None]
    set_fn: Callable[[PetkitFountainCoordinator, int], Awaitable[None]]


NUMBERS: tuple[PetkitNumberDescription, ...] = (
    PetkitNumberDescription(
        key="smart_time_on",
        translation_key="smart_time_on",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda d: d.smart_time_on,
        set_fn=lambda c, v: c.async_patch_config(smart_time_on=v),
    ),
    PetkitNumberDescription(
        key="smart_time_off",
        translation_key="smart_time_off",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda d: d.smart_time_off,
        set_fn=lambda c, v: c.async_patch_config(smart_time_off=v),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PetkitFountainCoordinator = hass.data[DOMAIN][entry.entry_id]
    # All number entities currently use CMD 221 (set_config). Gate them
    # to W4X unless the experimental flag is set — see switch.py for the
    # rationale.
    if coordinator.alias != "W4X" and not ENABLE_EXPERIMENTAL_NON_W4X_WRITES:
        return
    async_add_entities(
        PetkitFountainNumber(coordinator, description) for description in NUMBERS
    )


class PetkitFountainNumber(PetkitFountainEntity, NumberEntity):
    entity_description: PetkitNumberDescription

    def __init__(
        self,
        coordinator: PetkitFountainCoordinator,
        description: PetkitNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def native_value(self) -> int | None:
        return self.entity_description.value_fn(self.coordinator.data)

    # Availability inherited from PetkitFountainEntity (last_seen freshness).

    async def async_set_native_value(self, value: float) -> None:
        await self.entity_description.set_fn(self.coordinator, int(value))
