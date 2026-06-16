"""Config flow for the PetKit Fountain BLE integration."""
from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback

from .connection import PetkitFountainConnection
from .const import (
    CONF_CONNECTION_MODE,
    CONF_DEVICE_SECRET,
    CONF_POLL_INTERVAL_MINUTES,
    CONF_TYPE_CODE,
    CONNECTION_MODE_ON_DEMAND,
    CONNECTION_MODE_PERSISTENT,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_POLL_INTERVAL_MINUTES,
    DOMAIN,
    NAME_PREFIX,
)
from .protocol import extract_type_code, resolve_alias

_LOGGER = logging.getLogger(__name__)


class PetkitFountainConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discovery + manual flow for the PetKit Fountain."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PetkitFountainOptionsFlow()

    def __init__(self) -> None:
        self._discovered_service_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        # Set once a device is confirmed; carried through the init / re-pair
        # steps so they don't depend on the discovery context.
        self._pending_info: BluetoothServiceInfoBleak | None = None
        self._pending_data: dict[str, Any] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a BLE discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovered_service_info = discovery_info
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered device."""
        assert self._discovered_service_info is not None
        info = self._discovered_service_info
        if user_input is not None:
            self._set_pending(info, info.address)
            return await self.async_step_init_device()
        self._set_confirm_only()
        placeholders = {"name": info.name or info.address, "address": info.address}
        return self.async_show_form(
            step_id="bluetooth_confirm", description_placeholders=placeholders
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a manual setup flow — pick from already-seen devices."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            info = self._discovered_devices[address]
            self._set_pending(info, address)
            return await self.async_step_init_device()

        current_addresses = self._async_current_ids()
        for info in bluetooth.async_discovered_service_info(self.hass):
            address = info.address
            if address in current_addresses or address in self._discovered_devices:
                continue
            if not (info.name or "").startswith(NAME_PREFIX):
                continue
            self._discovered_devices[address] = info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        labels = {
            addr: f"{info.name or 'PetKit Fountain'} ({addr})"
            for addr, info in self._discovered_devices.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(labels)}),
        )

    # ───────────────────────── pairing / re-pair ─────────────────────────────

    def _set_pending(self, info: BluetoothServiceInfoBleak, address: str) -> None:
        """Stash the base config-entry data for the init / re-pair steps."""
        self._pending_info = info
        self._pending_data = {
            CONF_ADDRESS: address,
            CONF_NAME: info.name or "PetKit Fountain",
            # Pin the type code from the discovery advertisement so the model
            # is authoritative even if later advertisements arrive empty.
            CONF_TYPE_CODE: extract_type_code(info.service_data),
        }

    async def async_step_init_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check pairing state, then provision a fresh secret (uninitialized
        device) or offer the re-pair recovery menu (already bound)."""
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._pending_data[CONF_ADDRESS], connectable=True
        )
        if ble_device is None:
            return self.async_abort(reason="cannot_connect")

        alias = self._pending_alias()
        name = self._pending_data[CONF_NAME]
        try:
            already_paired = await PetkitFountainConnection(
                ble_device, name, alias=alias
            ).async_check_initialized()
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Could not read pairing state for %s",
                self._pending_data[CONF_ADDRESS],
            )
            return self.async_abort(reason="cannot_connect")

        if already_paired:
            # Bound already (PetKit app, or a prior install whose secret was
            # lost). The firmware accepts a fresh CMD 73 without a factory
            # reset, so offer re-pair recovery instead of dead-ending (#75).
            return await self.async_step_confirm_repair()

        # Uninitialized — register a fresh random secret straight away.
        secret_hex = await self._async_init_with_secret(ble_device, name, alias)
        if secret_hex is None:
            return self.async_abort(reason="init_failed")
        return self._create_entry(secret_hex)

    async def async_step_confirm_repair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user re-pair an already-bound device or cancel."""
        return self.async_show_menu(
            step_id="confirm_repair",
            menu_options=["repair_confirm", "repair_cancel"],
            description_placeholders={"name": self._pending_data[CONF_NAME]},
        )

    async def async_step_repair_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-pair the device with a fresh secret, overwriting the old one."""
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._pending_data[CONF_ADDRESS], connectable=True
        )
        if ble_device is None:
            return self.async_abort(reason="repair_failed")
        secret_hex = await self._async_init_with_secret(
            ble_device, self._pending_data[CONF_NAME], self._pending_alias()
        )
        if secret_hex is None:
            return self.async_abort(reason="repair_failed")
        return self._create_entry(secret_hex)

    async def async_step_repair_cancel(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Abort the flow, leaving the already-paired device untouched."""
        return self.async_abort(reason="repair_cancelled")

    def _pending_alias(self) -> str:
        info = self._pending_info
        name = info.name if info is not None else None
        return resolve_alias(name, name, self._pending_data.get(CONF_TYPE_CODE))

    def _create_entry(self, secret_hex: str | None) -> ConfigFlowResult:
        data = dict(self._pending_data)
        if secret_hex is not None:
            data[CONF_DEVICE_SECRET] = secret_hex
        title = (
            (self._pending_info.name if self._pending_info else None)
            or self._pending_data[CONF_ADDRESS]
        )
        return self.async_create_entry(title=title, data=data)

    async def _async_init_with_secret(
        self, ble_device, name: str, alias: str
    ) -> str | None:
        """One-time pairing: install a fresh random secret via CMD 73 and
        return it hex-encoded, or None on failure.

        A *random* secret (vs. the device_id-derived one) can't be recomputed
        by anyone who can read the device_id over BLE. Running CMD 73 is
        destructive — it invalidates the official-app pairing — which is
        exactly what the confirm dialog / re-pair menu warned about.

        NOTE: untested against real W4X hardware from the config-flow context.
        """
        secret = secrets.token_bytes(8)
        connection = PetkitFountainConnection(
            ble_device, name, alias=alias, secret=secret
        )
        try:
            await asyncio.wait_for(connection.async_init_device(), timeout=30)
            _LOGGER.info("Paired %s with a fresh secret", ble_device.address)
            return secret.hex()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not pair %s (%s)", ble_device.address, err)
            return None
        finally:
            try:
                await connection.disconnect()
            except Exception:  # noqa: BLE001
                pass


class PetkitFountainOptionsFlow(OptionsFlow):
    """Lets users trade BLE slot residency for update freshness without
    re-running discovery. Two knobs: connection mode (persistent vs
    on-demand) and the periodic-poll interval. Saving changes triggers a
    config-entry reload via the update-listener registered in __init__."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_mode = self.config_entry.options.get(
            CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE
        )
        current_interval = self.config_entry.options.get(
            CONF_POLL_INTERVAL_MINUTES, DEFAULT_POLL_INTERVAL_MINUTES
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_CONNECTION_MODE, default=current_mode): vol.In(
                    [CONNECTION_MODE_PERSISTENT, CONNECTION_MODE_ON_DEMAND]
                ),
                vol.Required(
                    CONF_POLL_INTERVAL_MINUTES, default=current_interval
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
