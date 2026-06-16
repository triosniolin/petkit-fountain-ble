"""GATT connection + command/response handling for PetKit fountain.

Manages a persistent BLE connection via bleak_retry_connector, sends
protocol commands to the WRITE characteristic, and decodes notifications
on the READ characteristic. Response demultiplexing is by command-byte:
each command's response carries the same `cmd` value as the request, so
callers awaiting a specific response register a future keyed by cmd.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from .const import READ_UUID, WRITE_UUID
from .protocol import (
    CMD_BATTERY,
    CMD_DEVICE_CONFIG,
    CMD_DEVICE_DETAILS,
    CMD_DEVICE_INFO,
    CMD_DEVICE_STATE,
    CMD_DEVICE_SYNC,
    CMD_INIT_DEVICE,
    CMD_RESET_FILTER,
    CMD_SET_CONFIG,
    CMD_SET_DATETIME,
    CMD_SET_MODE,
    TYPE_SEND,
    build_command,
    build_config_payload,
    compute_secret,
    pad_array,
    parse_device_configuration,
    parse_device_identifiers,
    parse_device_state,
    parse_combined_status,
    parse_firmware,
    parse_frame,
    parse_supply,
    time_in_bytes,
)

# CMD code the fountain uses for unsolicited combined-status broadcasts.
CMD_COMBINED_STATUS = 230

_LOGGER = logging.getLogger(__name__)

# How long to wait for a command's response before giving up on it.
_RESPONSE_TIMEOUT = 3.0

# Inter-command pacing — preserved from slespersen. The fountain's firmware
# doesn't tolerate back-to-back writes well; an explicit gap between commands
# avoids the "frame interleave" failure mode.
_INTER_CMD_DELAY = 0.5


class PetkitFountainConnection:
    """Encapsulates one BLE connection to a fountain + its protocol state."""

    def __init__(
        self,
        ble_device: BLEDevice,
        name: str,
        alias: str = "W4X",
        on_unsolicited_status: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.ble_device = ble_device
        self.name = name
        # Alias selects which parser branch is applied to inbound frames.
        # Defaults to W4X (the only verified branch) so callers that haven't
        # been updated don't accidentally route CTW3 frames through W4X
        # parsers — they'd just see "untested" results, not crashes.
        self.alias = alias
        # Coordinator-provided callback for CMD 230 push frames. Fires on the
        # event loop thread (bleak calls _on_notify synchronously from the
        # async loop), so callback must be a plain sync function that doesn't
        # block — typically just updates a dataclass + fires a dispatcher.
        self._on_unsolicited_status = on_unsolicited_status

        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._sequence = 0
        self._waiters: dict[int, asyncio.Future[bytes]] = {}
        self._initialized = False
        # The fountain's BLE stack delivers duplicate notifications on the
        # notify channel — every frame arrives twice in quick succession. We
        # de-dup by full raw-frame bytes per cmd-code, NOT by seq. Some
        # firmwares use a global incrementing seq (current W4X behavior), but
        # trusting that for dedup would silently drop legitimate broadcasts if
        # a future firmware sent unsolicited frames with a constant seq.
        # Bytewise equality is safe in either case: identical bytes within the
        # flush window = same frame; different bytes = different frame.
        self._last_raw: dict[int, bytes] = {}

        # Populated by the init sequence:
        self.device_id_bytes: list[int] | None = None
        self.device_id: int | None = None
        self.serial: str | None = None
        self._secret: list[int] | None = None

    # ────────────────────────── connection lifecycle ─────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def _ensure_connected(self) -> None:
        """Open the connection if needed; run init sequence on first connect."""
        if self.is_connected:
            return
        _LOGGER.debug("Establishing BLE connection to %s", self.ble_device.address)
        self._client = await establish_connection(
            BleakClient,
            self.ble_device,
            self.name,
            disconnected_callback=self._on_disconnect,
        )
        # Log discovered services + characteristics so UUID mismatches surface
        # in logs rather than silently timing out.
        try:
            for svc in self._client.services:
                _LOGGER.debug("GATT service %s", svc.uuid)
                for ch in svc.characteristics:
                    _LOGGER.debug(
                        "  char %s  props=%s", ch.uuid, ",".join(ch.properties)
                    )
        except Exception as svc_err:  # noqa: BLE001
            _LOGGER.warning("Could not enumerate services: %s", svc_err)

        await self._client.start_notify(READ_UUID, self._on_notify)
        _LOGGER.debug("Subscribed to notifications on %s", READ_UUID)
        # Give the GATT stack a beat to settle before flooding commands.
        await asyncio.sleep(0.5)
        # Init sequence runs once per connection. On reconnect (bleak drops the
        # GATT session), we run it again — the device is stateless about
        # in-memory sequence counters but persists the secret across reboots.
        await self._run_init_sequence()
        self._initialized = True

    def _on_disconnect(self, _client: BleakClient) -> None:
        """Bleak callback fired when the connection drops. Resolve any waiters
        with an exception so callers don't hang indefinitely."""
        _LOGGER.debug("BLE connection to %s lost", self.ble_device.address)
        self._initialized = False
        for fut in self._waiters.values():
            if not fut.done():
                fut.set_exception(ConnectionError("BLE disconnected mid-command"))
        self._waiters.clear()

    async def disconnect(self) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
        self._initialized = False

    # ────────────────────────── notification routing ─────────────────────────

    def _on_notify(self, _sender, data: bytearray) -> None:
        """Decode an inbound frame and resolve the awaiting future, if any.

        The W4X firmware delivers each notification twice on the notify
        channel — we suppress the duplicate by bytewise equality of the raw
        frame, keyed on cmd-code. See the comment on self._last_raw above for
        why we don't trust the seq byte for this.
        """
        raw = bytes(data)
        _LOGGER.debug("BLE notify received: %s", raw.hex())
        frame = parse_frame(raw)
        if frame is None:
            _LOGGER.warning("Dropping malformed BLE frame: %s", raw.hex())
            return
        cmd = frame["cmd"]
        if self._last_raw.get(cmd) == raw:
            # Duplicate copy of the same frame; ignore.
            return
        self._last_raw[cmd] = raw
        fut = self._waiters.pop(cmd, None)
        if fut is not None and not fut.done():
            fut.set_result(frame["data"])
            return
        # Unsolicited path. CMD 230 is the fountain's combined state+config
        # broadcast — parse + hand off so entities update without waiting for
        # the next poll. Other unsolicited cmd codes fall through to debug.
        if cmd == CMD_COMBINED_STATUS and self._on_unsolicited_status is not None:
            parsed = parse_combined_status(frame["data"], alias=self.alias)
            if parsed:
                try:
                    self._on_unsolicited_status(parsed)
                except Exception as cb_err:  # noqa: BLE001
                    _LOGGER.warning(
                        "on_unsolicited_status callback raised: %s", cb_err
                    )
            return
        _LOGGER.debug(
            "Unsolicited frame cmd=%d data=%s", cmd, frame["data"].hex()
        )

    # ─────────────────────────── command send/recv ───────────────────────────

    async def _send(self, cmd: int, data: list[int]) -> None:
        """Write a command without waiting for response."""
        seq = self._sequence
        self._sequence = (self._sequence + 1) % 256
        frame = build_command(seq, cmd, TYPE_SEND, data)
        assert self._client is not None
        _LOGGER.debug("BLE write cmd=%d seq=%d frame=%s", cmd, seq, frame.hex())
        await self._client.write_gatt_char(WRITE_UUID, frame, response=False)

    async def _send_and_wait(
        self, cmd: int, data: list[int], timeout: float = _RESPONSE_TIMEOUT
    ) -> bytes:
        """Write a command and wait for the matching response. Returns the
        frame's data payload (header/trailer stripped)."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bytes] = loop.create_future()
        # Register waiter BEFORE writing, to avoid races on fast responders.
        self._waiters[cmd] = fut
        try:
            await self._send(cmd, data)
            return await asyncio.wait_for(fut, timeout=timeout)
        except (asyncio.TimeoutError, ConnectionError):
            self._waiters.pop(cmd, None)
            raise

    # ─────────────────────────── init / polling ─────────────────────────────

    async def _run_init_sequence(self) -> None:
        """Pair / re-handshake. CMD 73 sets a secret derived from the device's
        own ID — it's idempotent on re-runs but the *first* time it runs it
        invalidates whatever secret the official PetKit app set.

        Order matches slespersen's init_device_connection() but with explicit
        response awaits where possible (replacing some fixed sleeps).
        """
        _LOGGER.debug("Running init sequence on %s", self.ble_device.address)

        # 1. Get device identifiers (device_id_bytes + serial).
        details = await self._send_and_wait(CMD_DEVICE_DETAILS, [0, 0])
        _LOGGER.debug(
            "Device identifiers raw response (len=%d): %s",
            len(details),
            details.hex(),
        )
        ids = parse_device_identifiers(details)
        _LOGGER.debug("Parsed ids: %s", ids)
        if not ids:
            raise RuntimeError(
                f"Empty device_identifiers response (data was {len(details)} bytes)"
            )
        self.device_id_bytes = ids["device_id_bytes"]
        self.device_id = ids["device_id"]
        self.serial = ids["serial"]
        self._secret = compute_secret(self.device_id_bytes)
        await asyncio.sleep(_INTER_CMD_DELAY)

        # 2. CMD 73 — set device secret. Fire-and-forget (no useful response).
        device_id_padded = pad_array(self.device_id_bytes, 8)
        await self._send(CMD_INIT_DEVICE, [0, 0] + device_id_padded + self._secret)
        await asyncio.sleep(1.0)

        # 3. CMD 86 — sync. Fire-and-forget.
        await self._send(CMD_DEVICE_SYNC, [0, 0] + self._secret)
        await asyncio.sleep(_INTER_CMD_DELAY)

        # 4. CMD 84 — set datetime.
        await self._send(CMD_SET_DATETIME, time_in_bytes())
        await asyncio.sleep(_INTER_CMD_DELAY)

    # ──────────────────────────── controls ──────────────────────────────────
    #
    # Each control method below acquires the same lock the poll uses, so
    # commands are serialized end-to-end. After mutating state on the device
    # we don't immediately re-read here — the coordinator schedules a fresh
    # poll after the control completes so entity state catches up within a
    # second or two.

    async def set_mode(self, power_on: int, mode: int) -> None:
        """CMD 220 — set power (0/1) + mode (1=normal, 2=smart)."""
        async with self._lock:
            await self._ensure_connected()
            await self._send(CMD_SET_MODE, [power_on & 1, mode & 0xFF])
            await asyncio.sleep(_INTER_CMD_DELAY)

    async def set_config(self, config: dict[str, int]) -> None:
        """CMD 221 — write the full 14-byte config block. Caller is
        responsible for passing the current config + their patches; this
        method does not read state first."""
        async with self._lock:
            await self._ensure_connected()
            payload = build_config_payload(config)
            await self._send(CMD_SET_CONFIG, payload)
            await asyncio.sleep(_INTER_CMD_DELAY)

    async def reset_filter(self) -> None:
        """CMD 222 — reset the filter wear counter to 100%."""
        async with self._lock:
            await self._ensure_connected()
            await self._send(CMD_RESET_FILTER, [])
            await asyncio.sleep(_INTER_CMD_DELAY)

    # ─────────────────────────────── polling ────────────────────────────────

    async def poll(self) -> dict[str, Any]:
        """Single poll cycle: ensure connected, read state/config/battery/fw."""
        async with self._lock:
            await self._ensure_connected()
            out: dict[str, Any] = {
                "device_id": self.device_id,
                "serial": self.serial,
                "alias": self.alias,
            }

            # Each read is independently fallible (timeout, disconnect mid-poll).
            # We accumulate what we got and let the rest stay stale.
            # Parsers that vary by alias receive it; ones that don't take it
            # are wrapped to keep the loop uniform.
            def _parse_state(raw: bytes) -> dict[str, Any]:
                return parse_device_state(raw, alias=self.alias)

            def _parse_config(raw: bytes) -> dict[str, Any]:
                return parse_device_configuration(raw, alias=self.alias)

            for cmd, parser, label in (
                (CMD_DEVICE_INFO, parse_firmware, "firmware"),
                (CMD_BATTERY, parse_supply, "supply"),
                (CMD_DEVICE_STATE, _parse_state, "state"),
                (CMD_DEVICE_CONFIG, _parse_config, "config"),
            ):
                payload = [] if cmd == CMD_DEVICE_INFO else [0, 0]
                try:
                    raw = await self._send_and_wait(cmd, payload)
                    out.update(parser(raw))
                    await asyncio.sleep(_INTER_CMD_DELAY)
                except asyncio.TimeoutError:
                    _LOGGER.debug("Timeout reading %s (cmd=%d)", label, cmd)
                except ConnectionError:
                    _LOGGER.debug("Disconnected during %s read", label)
                    raise  # propagate so caller can decide

            return out
