"""Binary sensors for the PetKit Fountain — power state + warnings."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PetkitFountainCoordinator, PetkitFountainData
from .entity import PetkitFountainEntity


@dataclass(kw_only=True)
class PetkitBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[PetkitFountainData], bool | None]


BINARY_SENSORS: tuple[PetkitBinarySensorDescription, ...] = (
    PetkitBinarySensorDescription(
        key="power_status",
        translation_key="power_status",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda d: bool(d.power_status) if d.power_status is not None else None,
    ),
    PetkitBinarySensorDescription(
        key="warning_breakdown",
        translation_key="warning_breakdown",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: (
            bool(d.warning_breakdown) if d.warning_breakdown is not None else None
        ),
    ),
    PetkitBinarySensorDescription(
        key="warning_water_missing",
        translation_key="warning_water_missing",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: (
            bool(d.warning_water_missing)
            if d.warning_water_missing is not None
            else None
        ),
    ),
    PetkitBinarySensorDescription(
        key="warning_filter",
        translation_key="warning_filter",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: (
            bool(d.warning_filter) if d.warning_filter is not None else None
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
        PetkitFountainBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class PetkitFountainBinarySensor(PetkitFountainEntity, BinarySensorEntity):
    entity_description: PetkitBinarySensorDescription

    def __init__(
        self,
        coordinator: PetkitFountainCoordinator,
        description: PetkitBinarySensorDescription,
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
