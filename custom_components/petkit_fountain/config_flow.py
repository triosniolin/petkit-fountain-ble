"""Config flow for the PetKit Fountain BLE integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME

from .const import DOMAIN, NAME_PREFIX


class PetkitFountainConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discovery + manual flow for the PetKit Fountain."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_service_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

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
            return self.async_create_entry(
                title=info.name or info.address,
                data={
                    CONF_ADDRESS: info.address,
                    CONF_NAME: info.name or "PetKit Fountain",
                },
            )
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
            return self.async_create_entry(
                title=info.name or address,
                data={
                    CONF_ADDRESS: address,
                    CONF_NAME: info.name or "PetKit Fountain",
                },
            )

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
