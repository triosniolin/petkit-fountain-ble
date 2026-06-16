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
    build_ctw3_mode_payload,
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
        secret: bytes | None = None,
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

        # Device identifiers, populated by _authenticate() on first connect.
        self.device_id_bytes: list[int] | None = None
        self.device_id: int | None = None
        self.serial: str | None = None
        # The 8-byte secret. Supplied by the caller (read from the config
        # entry). When None — a legacy entry created before secrets were
        # persisted — _authenticate() derives the legacy device_id-based value,
        # runs CMD 73 once to (re)pair with it, and sets secret_was_derived so
        # the coordinator persists it. After that, the stored secret is used
        # and CMD 73 never runs again for this entry.
        self._secret: list[int] | None = list(secret) if secret is not None else None
        self.secret_was_derived: bool = False

    # ────────────────────────── connection lifecycle ─────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def secret_bytes(self) -> bytes | None:
        """The active 8-byte secret, for the coordinator to persist into the
        config entry. None until the first authenticate/init populates it."""
        return bytes(self._secret) if self._secret is not None else None

    async def _open(self) -> None:
        """Open the GATT connection + subscribe to notifications. No auth."""
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
        # Give the GATT stack a beat to settle before sending commands.
        await asyncio.sleep(0.5)

    async def _ensure_connected(self) -> None:
        """Open the connection if needed, then authenticate once per session.

        Authentication (CMD 213 → 86 → 84) runs once per GATT session; on
        reconnect _on_disconnect clears _initialized so we re-auth. The
        destructive pairing command (CMD 73) is NOT on this path — it runs at
        most once, either via the legacy-migration branch in _authenticate or
        explicitly at setup via async_init_device.
        """
        if self.is_connected:
            return
        await self._open()
        if not self._initialized:
            await self._authenticate()
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
        # CMD 73 (init) and CMD 86 (sync) embed the secret; don't log the raw
        # frame for those so it can't leak into a pasted debug log.
        if cmd in (CMD_INIT_DEVICE, CMD_DEVICE_SYNC):
            _LOGGER.debug("BLE write cmd=%d seq=%d (payload redacted: secret)", cmd, seq)
        else:
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

    async def _read_identifiers(self) -> None:
        """CMD 213 — read device_id_bytes + serial into self. Raises on empty."""
        details = await self._send_and_wait(CMD_DEVICE_DETAILS, [0, 0])
        # NOTE: the raw response carries device_id + serial — do NOT log its
        # hex, so identifiers can't leak into a pasted debug log.
        _LOGGER.debug("Device identifiers response received (len=%d)", len(details))
        ids = parse_device_identifiers(details)
        if not ids:
            raise RuntimeError(
                f"Empty device_identifiers response (data was {len(details)} bytes)"
            )
        self.device_id_bytes = ids["device_id_bytes"]
        self.device_id = ids["device_id"]
        self.serial = ids["serial"]

    async def _authenticate(self) -> None:
        """Authenticate to an already-paired device, once per GATT session:
        read identifiers (CMD 213), verify with the stored secret (CMD 86),
        sync the clock (CMD 84).

        Migration: entries created before the secret was persisted have
        self._secret is None. We then derive the legacy device_id-based secret
        (the value the old every-connect CMD 73 installed) and run CMD 73 ONCE
        to (re)pair with it — idempotent on an already-paired device. The
        coordinator persists the derived secret after the first successful
        poll, so this branch — and CMD 73 — never run again for this entry.
        """
        _LOGGER.debug("Authenticating to %s", self.ble_device.address)

        await self._read_identifiers()
        assert self.device_id_bytes is not None

        if self._secret is None:
            self._secret = compute_secret(self.device_id_bytes)
            self.secret_was_derived = True
            device_id_padded = pad_array(self.device_id_bytes, 8)
            await asyncio.sleep(_INTER_CMD_DELAY)
            # CMD 73 — one-time (re)pairing with the derived secret.
            await self._send(
                CMD_INIT_DEVICE, [0, 0] + device_id_padded + self._secret
            )
            await asyncio.sleep(1.0)
            _LOGGER.info(
                "Migrated %s to stored-secret model (one-time pairing sent)",
                self.ble_device.address,
            )
        await asyncio.sleep(_INTER_CMD_DELAY)

        # CMD 86 — sync/verify with the secret. Fire-and-forget (no useful
        # response observed on this firmware).
        await self._send(CMD_DEVICE_SYNC, [0, 0] + self._secret)
        await asyncio.sleep(_INTER_CMD_DELAY)

        # CMD 84 — set datetime.
        await self._send(CMD_SET_DATETIME, time_in_bytes())
        await asyncio.sleep(_INTER_CMD_DELAY)

    async def async_check_initialized(self) -> bool:
        """Connect, read the device id, and report whether the device is
        already paired (has a secret registered — by the PetKit app or a prior
        install). A non-zero device_id means paired. Disconnects before
        returning. Used by the config flow to decide whether to offer the
        re-pair recovery menu vs. provision a fresh secret directly.

        Heuristic note: mirrors aavdberg's `device_id != 0` check, validated on
        CTW3/W5. If a fresh W4X ever reports non-zero while unpaired, the only
        effect is that the user sees the re-pair menu and confirms — the
        outcome is still correct.
        """
        try:
            if not self.is_connected:
                await self._open()
            await self._read_identifiers()
            return bool(self.device_id)
        finally:
            await self.disconnect()

    async def async_init_device(self) -> None:
        """One-time pairing for an UNINITIALIZED device, with the secret the
        caller already set (a fresh random secret). Sends CMD 73, which
        installs that secret device-side and INVALIDATES any official-app
        pairing. Called once from the config flow; never at runtime.
        """
        assert self._secret is not None, "async_init_device requires a secret"
        async with self._lock:
            if not self.is_connected:
                await self._open()
            await self._read_identifiers()
            assert self.device_id_bytes is not None
            device_id_padded = pad_array(self.device_id_bytes, 8)
            await asyncio.sleep(_INTER_CMD_DELAY)
            # CMD 73 — set device secret (destructive: breaks app pairing).
            await self._send(
                CMD_INIT_DEVICE, [0, 0] + device_id_padded + self._secret
            )
            await asyncio.sleep(1.0)
            # CMD 86 — verify/sync with the freshly-set secret.
            await self._send(CMD_DEVICE_SYNC, [0, 0] + self._secret)
            await asyncio.sleep(_INTER_CMD_DELAY)
            self._initialized = True

    # ──────────────────────────── controls ──────────────────────────────────
    #
    # Each control method below acquires the same lock the poll uses, so
    # commands are serialized end-to-end. After mutating state on the device
    # we don't immediately re-read here — the coordinator schedules a fresh
    # poll after the control completes so entity state catches up within a
    # second or two.

    async def set_mode(self, power_on: int, mode: int) -> None:
        """CMD 220 — set power (0/1) + mode (1=normal, 2=smart).

        W4X/W5/CTW2 take a 2-byte [power, mode] payload. CTW3 takes a 3-byte
        [power, suspend, mode] payload — the suspend byte must be 1 for the
        pump to actually run in normal mode (aavdberg issue #57), so we
        dispatch by alias here rather than treating CMD 220 as fully
        alias-agnostic.
        """
        async with self._lock:
            await self._ensure_connected()
            if self.alias == "CTW3":
                payload = build_ctw3_mode_payload(power_on & 1, mode & 0xFF)
            else:
                payload = [power_on & 1, mode & 0xFF]
            await self._send(CMD_SET_MODE, payload)
            await asyncio.sleep(_INTER_CMD_DELAY)

    async def set_config(self, config: dict[str, int]) -> None:
        """CMD 221 — write the full config block. Dispatches to the W4X
        (14-byte) or CTW3 (10-byte) payload shape based on `self.alias`.
        Caller is responsible for passing the current config + their
        patches; this method does not read state first."""
        async with self._lock:
            await self._ensure_connected()
            payload = build_config_payload(config, alias=self.alias)
            await self._send(CMD_SET_CONFIG, payload)
            await asyncio.sleep(_INTER_CMD_DELAY)

    async def reset_filter(self) -> None:
        """CMD 222 — reset the filter wear counter to 100%. Payload `[0]`
        per slespersen's set_reset_filter (the W4X device also accepts
        `[]`, which is what earlier versions sent — but the [0] form
        matches the original research and is the right shape to send on
        untested models per the "per slespersen" safety claim in the
        README's write-command table)."""
        async with self._lock:
            await self._ensure_connected()
            await self._send(CMD_RESET_FILTER, [0])
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
