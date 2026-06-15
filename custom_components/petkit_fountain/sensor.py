"""Sensor entities for the PetKit Fountain.

Phase 2 read-only set: identity (firmware, serial), battery, filter life,
pump runtime + derived water/energy, current mode, RSSI. All values come
from the coordinator's PetkitFountainData; entities are pure views.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PetkitFountainCoordinator, PetkitFountainData
from .entity import PetkitFountainEntity
from .protocol import calculate_filter_days_left, calculate_water_purified_l

# Mode value → label. PetKit firmware uses 1=normal, 2=smart.
MODE_LABELS = {1: "normal", 2: "smart"}


@dataclass(kw_only=True)
class PetkitSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor pulling from
    PetkitFountainData."""

    value_fn: Callable[[PetkitFountainData], Any]


SENSORS: tuple[PetkitSensorDescription, ...] = (
    PetkitSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.rssi,
    ),
    # NOTE: no battery sensors exposed — the W4X / 3 Pro UVC has no battery
    # hardware. The PetKit "battery" field carries the USB supply voltage
    # only. If we add CTW3 (Eversweet Max) support later, add battery_voltage
    # and battery_percentage descriptions here gated by device family.
    PetkitSensorDescription(
        key="supply_voltage",
        translation_key="supply_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.supply_voltage,
    ),
    PetkitSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: str(d.firmware) if d.firmware is not None else None,
    ),
    PetkitSensorDescription(
        key="serial",
        translation_key="serial",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.serial,
    ),
    PetkitSensorDescription(
        key="filter_percentage",
        translation_key="filter_percentage",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.filter_percentage,
    ),
    PetkitSensorDescription(
        key="filter_days_left",
        translation_key="filter_days_left",
        native_unit_of_measurement=UnitOfTime.DAYS,
        value_fn=lambda d: (
            calculate_filter_days_left(
                d.filter_percentage,
                d.mode,
                d.smart_time_on,
                d.smart_time_off,
            )
            if None
            not in (d.filter_percentage, d.mode, d.smart_time_on, d.smart_time_off)
            else None
        ),
    ),
    PetkitSensorDescription(
        key="pump_runtime",
        translation_key="pump_runtime",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        value_fn=lambda d: (
            round(d.pump_runtime / 3600, 2) if d.pump_runtime is not None else None
        ),
    ),
    PetkitSensorDescription(
        key="purified_water",
        translation_key="purified_water",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=1,
        value_fn=lambda d: (
            round(calculate_water_purified_l("W4X", d.pump_runtime), 2)
            if d.pump_runtime is not None
            else None
        ),
    ),
    PetkitSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(MODE_LABELS.values()),
        value_fn=lambda d: MODE_LABELS.get(d.mode) if d.mode is not None else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PetkitFountainCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PetkitFountainSensor(coordinator, description) for description in SENSORS
    )


class PetkitFountainSensor(PetkitFountainEntity, SensorEntity):
    entity_description: PetkitSensorDescription

    def __init__(
        self,
        coordinator: PetkitFountainCoordinator,
        description: PetkitSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        # Per-sensor availability — diagnostic sensors (rssi, firmware, serial)
        # can show as soon as we have advertisement / init data; others require
        # at least one successful poll. Override the base entity's RSSI-only
        # check by considering this sensor's own value.
        return self.native_value is not None
