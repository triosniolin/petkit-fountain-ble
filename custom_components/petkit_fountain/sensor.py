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
        # alias passed from PetkitFountainData so non-W4X devices get the
        # right slespersen multiplier — W5C uses (1.0, 1.3), CTW3 uses
        # (3.0, 1.5), W4X uses (1.8, 1.5), unknown falls to (2.0, 1.5).
        value_fn=lambda d: (
            round(calculate_water_purified_l(d.alias or "W4X", d.pump_runtime), 2)
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

# CTW3-only sensors. The Eversweet Max family advertises a 26-byte state
# frame with battery telemetry + daily pump runtime that W4X doesn't carry.
# Untested — slespersen documents these fields but we have no hardware to
# verify against. Field shapes are likely correct; values may need empirical
# tuning. Only registered when the device's alias is CTW3.
CTW3_SENSORS: tuple[PetkitSensorDescription, ...] = (
    PetkitSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.battery_voltage,
    ),
    PetkitSensorDescription(
        key="battery_percentage",
        translation_key="battery_percentage",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.battery_percentage,
    ),
    PetkitSensorDescription(
        key="pump_runtime_today",
        translation_key="pump_runtime_today",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=1,
        value_fn=lambda d: (
            round(d.pump_runtime_today / 60, 2)
            if d.pump_runtime_today is not None
            else None
        ),
    ),
    # Diagnostic bytes from the CTW3 state/config frames. Slespersen
    # documents the field positions but doesn't fully document the value
    # semantics — exposing them as raw integers so CTW3 owners can
    # observe what values their device emits in different states and
    # report back. Once we know what the numbers mean, these can graduate
    # to enum-style sensors with proper labels.
    PetkitSensorDescription(
        key="electric_status",
        translation_key="electric_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.electric_status,
    ),
    PetkitSensorDescription(
        key="module_status",
        translation_key="module_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.module_status,
    ),
    PetkitSensorDescription(
        key="battery_working_time",
        translation_key="battery_working_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.battery_working_time,
    ),
    PetkitSensorDescription(
        key="battery_sleep_time",
        translation_key="battery_sleep_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.battery_sleep_time,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PetkitFountainCoordinator = hass.data[DOMAIN][entry.entry_id]
    descriptions = list(SENSORS)
    if coordinator.alias == "CTW3":
        descriptions.extend(CTW3_SENSORS)
    async_add_entities(
        PetkitFountainSensor(coordinator, description) for description in descriptions
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

    # Availability inherited from PetkitFountainEntity: keyed on
    # last_seen freshness across the whole device. Individual sensors
    # whose field hasn't been populated yet just render as "unknown"
    # (state=None) until the next poll/push fills them in — that's
    # the right HA semantic for "we're talking to the device but this
    # value hasn't arrived yet."
