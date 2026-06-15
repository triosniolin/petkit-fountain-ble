"""Bluetooth coordinator for the PetKit Fountain.

Manages two parallel data flows:

1. *Passive*: subscribes to BLE advertisements (via HA's bluetooth integration)
   to track RSSI in real time without holding the GATT connection open.
2. *Active*: maintains a GATT connection via PetkitFountainConnection and runs
   a periodic poll cycle that reads state/config/battery/firmware.

After each update (passive or active), entities are notified via the
dispatcher signal_update(entry_id).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .connection import PetkitFountainConnection
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Poll cadence. The fountain pushes a complete state+config frame (CMD 230)
# every ~3 seconds without prompting, so most "fresh data" comes through the
# unsolicited path (see PetkitFountainConnection._on_unsolicited_status).
# The active poll only serves to read fields the push doesn't carry — supply
# voltage (CMD 66), firmware (CMD 200) — and as a watchdog that detects
# connection drops the disconnect callback might miss. 5 minutes is generous
# for both.
POLL_INTERVAL = timedelta(minutes=5)


@dataclass
class PetkitFountainData:
    """All live fountain state. Fields are populated incrementally as the
    coordinator's poll cycles return data; entities should treat any None as
    'not yet known'."""

    # Bluetooth-level
    rssi: int | None = None

    # Device identity
    device_id: int | None = None
    serial: str | None = None
    alias: str | None = None
    firmware: float | None = None

    # State (CMD 210)
    power_status: int | None = None         # 0=off, 1=on
    mode: int | None = None                 # 1=normal, 2=smart
    dnd_state: int | None = None
    warning_breakdown: int | None = None
    warning_water_missing: int | None = None
    warning_filter: int | None = None
    pump_runtime: int | None = None         # seconds, lifetime
    filter_percentage: int | None = None
    running_status: int | None = None

    # Supply / battery (CMD 66 — interpretation depends on device family)
    #
    # W4X (Eversweet 3 Pro and 3 Pro UVC): no battery hardware. The "voltage"
    # field PetKit returns here is the USB supply voltage feeding the pump;
    # the "battery percentage" byte is always 0. Exposed as supply_voltage.
    #
    # CTW3 (Eversweet Max family): has internal battery. When that parser
    # branch is implemented it should populate battery_voltage and
    # battery_percentage separately. Kept here as scaffolding so the
    # dataclass is one shared shape across device families.
    supply_voltage: float | None = None
    battery_voltage: float | None = None
    battery_percentage: int | None = None

    # Configuration (CMD 211)
    smart_time_on: int | None = None        # minutes
    smart_time_off: int | None = None
    led_switch: int | None = None
    led_brightness: int | None = None
    led_light_time_on: int | None = None    # minutes-of-day
    led_light_time_off: int | None = None
    do_not_disturb_switch: int | None = None
    do_not_disturb_time_on: int | None = None
    do_not_disturb_time_off: int | None = None
    is_locked: int | None = None

    def update_from_poll(self, poll_result: dict[str, Any]) -> None:
        """Merge fields returned by PetkitFountainConnection.poll() into self."""
        for key, value in poll_result.items():
            if hasattr(self, key):
                setattr(self, key, value)


def signal_update(entry_id: str) -> str:
    return f"{DOMAIN}_update_{entry_id}"


class PetkitFountainCoordinator:
    """Owns the connection + data; drives the poll loop."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        ble_device: BLEDevice,
        name: str,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = ble_device.address
        self.name: str = name
        self.data = PetkitFountainData()

        self._connection = PetkitFountainConnection(
            ble_device, self.name, on_unsolicited_status=self._on_push_update
        )
        self._unsub_adv: CALLBACK_TYPE | None = None
        self._unsub_poll: CALLBACK_TYPE | None = None
        self._poll_in_progress = False

    # ─────────────────────── passive advertisement path ──────────────────────

    @callback
    def _handle_advertisement(
        self,
        service_info: BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        self.data.rssi = service_info.rssi
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    # ─────────────────────── unsolicited push updates ────────────────────────

    @callback
    def _on_push_update(self, parsed: dict[str, Any]) -> None:
        """Fires on every CMD 230 broadcast — typically every ~30s. Updates
        the data block and notifies entities without waiting for the poll."""
        self.data.update_from_poll(parsed)
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    # ──────────────────────────────── controls ──────────────────────────────
    #
    # Each control method patches one field and re-sends the appropriate full
    # command (CMD 220 for power/mode, CMD 221 for the config block). The
    # fountain doesn't accept partial-field updates, so we read the current
    # cached state + apply the patch. After the write succeeds, we schedule
    # a poll to refresh entities — UI lag should be ≤2s.

    def _current_config(self) -> dict[str, int]:
        """Snapshot the config-block fields from the most recent poll."""
        d = self.data
        return {
            "smart_time_on": d.smart_time_on or 0,
            "smart_time_off": d.smart_time_off or 0,
            "led_switch": d.led_switch or 0,
            "led_brightness": d.led_brightness or 0,
            "led_light_time_on": d.led_light_time_on or 0,
            "led_light_time_off": d.led_light_time_off or 0,
            "do_not_disturb_switch": d.do_not_disturb_switch or 0,
            "do_not_disturb_time_on": d.do_not_disturb_time_on or 0,
            "do_not_disturb_time_off": d.do_not_disturb_time_off or 0,
            "is_locked": d.is_locked or 0,
        }

    async def _trigger_refresh(self) -> None:
        """Kick a fresh poll so entities reflect the new state quickly."""
        # Use create_task so the control call returns immediately; the user's
        # service call doesn't block on the post-write read.
        self.hass.async_create_background_task(
            self._async_poll(), name=f"{DOMAIN}_refresh_after_write"
        )

    async def async_set_power(self, on: bool) -> None:
        mode = self.data.mode or 1  # default normal if mode unknown
        await self._connection.set_mode(1 if on else 0, mode)
        self.data.power_status = 1 if on else 0
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))
        await self._trigger_refresh()

    async def async_set_mode(self, mode: int) -> None:
        """mode: 1=normal, 2=smart."""
        power = self.data.power_status if self.data.power_status is not None else 1
        await self._connection.set_mode(power, mode)
        self.data.mode = mode
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))
        await self._trigger_refresh()

    async def async_patch_config(self, **patches: int) -> None:
        """Apply a partial config patch and send the full CMD 221 payload."""
        config = self._current_config()
        config.update(patches)
        await self._connection.set_config(config)
        # Optimistic update: write the patched fields back into self.data so
        # the next dispatcher_send shows them immediately.
        for key, value in patches.items():
            if hasattr(self.data, key):
                setattr(self.data, key, value)
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))
        await self._trigger_refresh()

    async def async_reset_filter(self) -> None:
        await self._connection.reset_filter()
        await self._trigger_refresh()

    # ─────────────────────────── active poll path ────────────────────────────

    async def _async_poll(self, _now=None) -> None:
        """Run one poll cycle. Suppress overlapping invocations."""
        if self._poll_in_progress:
            _LOGGER.debug("Skipping poll — previous still in flight")
            return
        self._poll_in_progress = True
        try:
            result = await self._connection.poll()
            self.data.update_from_poll(result)
            async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))
        except Exception as err:  # noqa: BLE001 — coordinator must never crash
            _LOGGER.warning(
                "Poll failed (%s): %s", type(err).__name__, err, exc_info=True
            )
            # Drop the (possibly half-open) connection so the next poll
            # re-establishes cleanly.
            try:
                await self._connection.disconnect()
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._poll_in_progress = False

    # ──────────────────────────── start / stop ───────────────────────────────

    def async_start(self) -> CALLBACK_TYPE:
        """Subscribe to advertisements + schedule polling. Returns an
        unsubscribe callable that stops both."""

        self._unsub_adv = bluetooth.async_register_callback(
            self.hass,
            self._handle_advertisement,
            {"address": self.address, "connectable": False},
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        # Seed RSSI from any existing last-seen advertisement.
        last = bluetooth.async_last_service_info(
            self.hass, self.address, connectable=False
        )
        if last is not None:
            self.data.rssi = last.rssi

        # Fire one poll immediately (background), then on interval.
        self.hass.async_create_background_task(
            self._async_poll(), name=f"{DOMAIN}_initial_poll"
        )
        self._unsub_poll = async_track_time_interval(
            self.hass, self._async_poll, POLL_INTERVAL
        )

        def _stop() -> None:
            if self._unsub_adv is not None:
                self._unsub_adv()
                self._unsub_adv = None
            if self._unsub_poll is not None:
                self._unsub_poll()
                self._unsub_poll = None
            # Best-effort connection close. Coordinator stop is async-context
            # so we schedule the disconnect rather than awaiting it here.
            self.hass.async_create_background_task(
                self._connection.disconnect(), name=f"{DOMAIN}_disconnect"
            )

        return _stop
