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
from datetime import datetime, timedelta
import logging
from typing import Any

from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .connection import PetkitFountainConnection
from .const import (
    CONNECTION_MODE_ON_DEMAND,
    CONNECTION_MODE_PERSISTENT,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_POLL_INTERVAL_MINUTES,
    DOMAIN,
)
from .protocol import resolve_alias, resolve_model

_LOGGER = logging.getLogger(__name__)

# When a poll fails (typically a transient BLE race after a reload),
# schedule a follow-up attempt this many seconds later instead of waiting
# for the next periodic interval. ~10s is long enough for bluez to settle
# after a failed connect, short enough that the entity-unavailable window
# stays in "blink" territory rather than "wait for next poll cycle".
_FAIL_RETRY_SECONDS = 10

# Poll cadence is configurable via the options flow (CONF_POLL_INTERVAL_MINUTES,
# default 5 minutes). In persistent connection mode the poll is a backstop —
# the fountain's CMD 230 push frames (observed: ~4 frames at ~3s intra-burst
# spacing, ~once/min overall) carry most fresh data. In on-demand mode the
# connection is closed between polls, so push frames are functionally inert
# and the poll IS the data path; users on on-demand probably want a lower
# interval.


@dataclass
class PetkitFountainData:
    """All live fountain state. Fields are populated incrementally as the
    coordinator's poll cycles return data; entities should treat any None as
    'not yet known'."""

    # Bluetooth-level
    rssi: int | None = None
    # Stamped on each advertisement, push frame, and successful poll. Drives
    # entity availability — if we haven't heard from the device in a while,
    # everything goes unavailable rather than showing stale values as live.
    last_seen: datetime | None = None

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

    # Configuration (CMD 211 — W4X family layout)
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

    # CTW3-only state fields (Eversweet Max family — untested).
    # CTW3's state frame is 26 bytes vs W4X's 12 and carries hardware that
    # W4X doesn't have (a real battery, supply/battery voltage telemetry,
    # cat-presence detect, electric-status discrimination).
    suspend_status: int | None = None
    electric_status: int | None = None
    low_battery: int | None = None
    pump_runtime_today: int | None = None   # seconds, since midnight
    detect_status: int | None = None        # cat presence sensor
    module_status: int | None = None

    # CTW3-only configuration fields. CTW3 swaps the LED+DND schedule slots
    # for battery-management timings.
    battery_working_time: int | None = None  # minutes (battery uptime budget)
    battery_sleep_time: int | None = None    # minutes (battery sleep budget)

    def update_from_poll(self, poll_result: dict[str, Any]) -> None:
        """Merge fields returned by PetkitFountainConnection.poll() into self."""
        for key, value in poll_result.items():
            if hasattr(self, key):
                setattr(self, key, value)


def signal_update(entry_id: str) -> str:
    return f"{DOMAIN}_update_{entry_id}"


# Fields that compose the CMD 221 set-config payload — per alias, since
# W4X and CTW3 carry different fields. All must be present (non-None)
# before we're willing to write; otherwise unpatched fields would
# silently collapse to 0 in the wire payload and overwrite real device
# state.
_CONFIG_BLOCK_FIELDS_W4X: tuple[str, ...] = (
    "smart_time_on",
    "smart_time_off",
    "led_switch",
    "led_brightness",
    "led_light_time_on",
    "led_light_time_off",
    "do_not_disturb_switch",
    "do_not_disturb_time_on",
    "do_not_disturb_time_off",
    "is_locked",
)
_CONFIG_BLOCK_FIELDS_CTW3: tuple[str, ...] = (
    "smart_time_on",
    "smart_time_off",
    "battery_working_time",
    "battery_sleep_time",
    "led_switch",
    "led_brightness",
    "do_not_disturb_switch",
    "is_locked",
)


def _config_block_fields_for(alias: str) -> tuple[str, ...]:
    return _CONFIG_BLOCK_FIELDS_CTW3 if alias == "CTW3" else _CONFIG_BLOCK_FIELDS_W4X


class PetkitFountainCoordinator:
    """Owns the connection + data; drives the poll loop."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        ble_device: BLEDevice,
        name: str,
        type_code: int | None,
        connection_mode: str = DEFAULT_CONNECTION_MODE,
        poll_interval_minutes: int = DEFAULT_POLL_INTERVAL_MINUTES,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = ble_device.address
        self.name: str = name
        self.connection_mode = connection_mode
        self.poll_interval = timedelta(minutes=poll_interval_minutes)
        # Entity availability gate. If we haven't seen the device (adv,
        # push frame, or successful poll) within this window, entities go
        # unavailable. 2.5× poll_interval tolerates one missed cycle of
        # whatever the user configured, generous enough to avoid flapping
        # but tight enough that a powered-off / out-of-range fountain
        # actually surfaces as offline rather than showing stale values
        # forever.
        self.stale_after = timedelta(seconds=poll_interval_minutes * 60 * 2.5)
        # Resolve alias + friendly model in one go. The alias drives parser
        # branch selection (W4X / CTW3 frames are decoded differently); the
        # model is the human-readable label on the device card. An
        # unresolved alias becomes "UNKNOWN" — read parsers treat that as
        # the W4X default, but write entities never register for it.
        self.alias = resolve_alias(ble_device.name, name, type_code)
        self.model = resolve_model(ble_device.name, name, type_code)
        self.data = PetkitFountainData(alias=self.alias)

        self._connection = PetkitFountainConnection(
            ble_device,
            self.name,
            alias=self.alias,
            on_unsolicited_status=self._on_push_update,
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
        self.data.last_seen = dt_util.utcnow()
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    # ─────────────────────── unsolicited push updates ────────────────────────

    @callback
    def _on_push_update(self, parsed: dict[str, Any]) -> None:
        """Fires on every CMD 230 broadcast — observed cadence is roughly one
        burst per minute (4 frames at ~3s intra-burst spacing). Updates the
        data block and notifies entities without waiting for the next poll."""
        self.data.update_from_poll(parsed)
        self.data.last_seen = dt_util.utcnow()
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    # ──────────────────────────────── controls ──────────────────────────────
    #
    # Each control method patches one field and re-sends the appropriate full
    # command (CMD 220 for power/mode, CMD 221 for the config block). The
    # fountain doesn't accept partial-field updates, so we read the current
    # cached state + apply the patch. After the write succeeds, we schedule
    # a poll to refresh entities — UI lag should be ≤2s.

    def _current_config(self) -> dict[str, int] | None:
        """Snapshot the config-block fields from the most recent poll.

        Returns None if any field is still None (i.e. no successful config
        read since startup). Callers must NOT synthesize zeros for missing
        fields — the fountain only accepts the full config block per CMD
        221 write, so a partial snapshot with `or 0` fallbacks would clobber
        real device state with zeros on the unpatched fields (e.g. flipping
        the LED switch could simultaneously rewrite DND schedule times to
        00:00 if those hadn't been read yet). Field set varies by alias —
        W4X has 10 fields, CTW3 has 8 (different LED+DND vs battery slots).
        """
        d = self.data
        fields = _config_block_fields_for(self.alias)
        if any(getattr(d, field) is None for field in fields):
            return None
        return {field: getattr(d, field) for field in fields}

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
        """Apply a partial config patch and send the full CMD 221 payload.

        Refuses to write if the cached config block is incomplete — the
        device only accepts whole-block writes, so a partial snapshot would
        corrupt unread fields. Caller should retry after the next poll.
        """
        config = self._current_config()
        if config is None:
            raise HomeAssistantError(
                "PetKit Fountain config block hasn't been read from the device "
                "yet — cannot safely write a partial update. Wait for the next "
                "poll to populate the cache, then retry."
            )
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
        """Run one poll cycle. Suppress overlapping invocations. In on-demand
        connection mode, disconnect after each successful poll so the BLE
        adapter slot is freed between cycles."""
        if self._poll_in_progress:
            _LOGGER.debug("Skipping poll — previous still in flight")
            return
        self._poll_in_progress = True
        try:
            result = await self._connection.poll()
            self.data.update_from_poll(result)
            self.data.last_seen = dt_util.utcnow()
            async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))
            if self.connection_mode == CONNECTION_MODE_ON_DEMAND:
                # Free the BLE slot between polls. Push frames are
                # functionally inert in this mode anyway.
                try:
                    await self._connection.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as err:  # noqa: BLE001 — coordinator must never crash
            _LOGGER.warning(
                "Poll failed (%s): %s", type(err).__name__, err, exc_info=True
            )
            # Drop the (possibly half-open) connection so the retry
            # re-establishes cleanly.
            try:
                await self._connection.disconnect()
            except Exception:  # noqa: BLE001
                pass
            # Schedule a near-term retry instead of waiting for the next
            # periodic interval. Matters most after an options-flow reload
            # in on-demand mode: the first poll can race the old
            # connection's teardown and fail, and without this retry the
            # user would see entities `unavailable` for the entire
            # configured poll interval. _FAIL_RETRY_SECONDS is short enough
            # to recover before the user notices but long enough to let
            # bluez settle.
            async_call_later(self.hass, _FAIL_RETRY_SECONDS, self._async_poll)
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
            self.hass, self._async_poll, self.poll_interval
        )

        async def _stop() -> None:
            if self._unsub_adv is not None:
                self._unsub_adv()
                self._unsub_adv = None
            if self._unsub_poll is not None:
                self._unsub_poll()
                self._unsub_poll = None
            # Await the disconnect so the old GATT session is fully torn
            # down before HA's reload pipeline moves on to setup_entry.
            # If we fire-and-forget here (as an earlier draft did), the
            # new coordinator's first connect races the in-progress
            # disconnect and bleak raises BleakDBusError [NotConnected]
            # from start_notify, causing the initial poll to fail and the
            # entities to stay unavailable until the NEXT scheduled poll —
            # exactly the user-visible "unavailable for the full poll
            # interval after options change" bug.
            try:
                await self._connection.disconnect()
            except Exception:  # noqa: BLE001
                pass

        return _stop
