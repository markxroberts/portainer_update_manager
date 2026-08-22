"""Config flow for Portainer Update Manager."""

from __future__ import annotations

from typing import Any, override

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    BridgeAuthenticationError,
    BridgeConnectionError,
    BridgeProtocolError,
    DockerEventBridgeClient,
)
from .const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_PORTAINER_ENTRY_ID,
    CONF_SCAN_INTERVAL,
    CONF_UPDATE_DELAY_HOURS,
    CONF_VERIFY_SSL,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_BRIDGE_URL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UPDATE_DELAY_HOURS,
    DEFAULT_VERIFY_SSL,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MAX_UPDATE_DELAY_HOURS,
    MIN_SCAN_INTERVAL,
    PORTAINER_DOMAIN,
)


def _settings_schema(values: dict[str, Any]) -> vol.Schema:
    """Build the update-policy settings schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=values.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            ),
            vol.Required(
                CONF_UPDATE_DELAY_HOURS,
                default=values.get(
                    CONF_UPDATE_DELAY_HOURS, DEFAULT_UPDATE_DELAY_HOURS
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=MAX_UPDATE_DELAY_HOURS,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="h",
                )
            ),
            vol.Required(
                CONF_WINDOW_START,
                default=values.get(CONF_WINDOW_START, DEFAULT_WINDOW_START),
            ): selector.TimeSelector(),
            vol.Required(
                CONF_WINDOW_END,
                default=values.get(CONF_WINDOW_END, DEFAULT_WINDOW_END),
            ): selector.TimeSelector(),
        }
    )


def _bridge_schema(values: dict[str, Any]) -> vol.Schema:
    """Build bridge connection schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_BRIDGE_URL,
                default=values.get(CONF_BRIDGE_URL, DEFAULT_BRIDGE_URL),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
            ),
            vol.Required(
                CONF_BRIDGE_TOKEN,
                default=values.get(CONF_BRIDGE_TOKEN, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_VERIFY_SSL,
                default=values.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): selector.BooleanSelector(),
        }
    )


class PortainerUpdateManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Portainer Update Manager."""

    VERSION = 5

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> PortainerUpdateManagerOptionsFlow:
        """Return the options flow."""
        return PortainerUpdateManagerOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        portainer_entries = self.hass.config_entries.async_entries(PORTAINER_DOMAIN)
        configured = {
            entry.data.get(CONF_PORTAINER_ENTRY_ID)
            for entry in self._async_current_entries()
        }
        available_entries = [
            entry for entry in portainer_entries if entry.entry_id not in configured
        ]

        if not available_entries:
            return self.async_abort(reason="no_available_portainer_entries")

        errors: dict[str, str] = {}
        if user_input is not None:
            selected_id = user_input[CONF_PORTAINER_ENTRY_ID]
            selected_entry = next(
                (entry for entry in available_entries if entry.entry_id == selected_id),
                None,
            )
            if selected_entry is None:
                return self.async_abort(reason="portainer_entry_missing")
            error = await self._async_validate_bridge(user_input)
            if error is None:
                await self.async_set_unique_id(selected_entry.entry_id)
                self._abort_if_unique_id_configured()
                options = _options_from_input(user_input)
                return self.async_create_entry(
                    title=f"{selected_entry.title} update manager",
                    data={
                        CONF_PORTAINER_ENTRY_ID: selected_entry.entry_id,
                        **_bridge_from_input(user_input),
                    },
                    options=options,
                )
            errors["base"] = error

        portainer_options = [
            selector.SelectOptionDict(label=entry.title, value=entry.entry_id)
            for entry in available_entries
        ]
        schema = _settings_schema(user_input or {}).extend(
            _bridge_schema(user_input or {}).schema
        ).extend(
            {
                vol.Required(CONF_PORTAINER_ENTRY_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=portainer_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @override
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the bridge connection without deleting policy state."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_validate_bridge(user_input)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=_bridge_from_input(user_input),
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_bridge_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    @override
    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start bridge token reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a replacement bridge token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._get_reauth_entry()
            values = dict(entry.data)
            values[CONF_BRIDGE_TOKEN] = user_input[CONF_BRIDGE_TOKEN]
            error = await self._async_validate_bridge(values)
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_BRIDGE_TOKEN: user_input[CONF_BRIDGE_TOKEN]},
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_TOKEN): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def _async_validate_bridge(self, values: dict[str, Any]) -> str | None:
        session = async_get_clientsession(
            self.hass,
            verify_ssl=bool(values.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
        )
        client = DockerEventBridgeClient(
            session,
            str(values[CONF_BRIDGE_URL]),
            str(values[CONF_BRIDGE_TOKEN]),
        )
        try:
            await client.async_get_snapshot()
        except BridgeAuthenticationError:
            return "invalid_auth"
        except BridgeConnectionError:
            return "cannot_connect"
        except BridgeProtocolError:
            return "invalid_response"
        return None


class PortainerUpdateManagerOptionsFlow(OptionsFlowWithReload):
    """Handle Portainer Update Manager options."""

    @override
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage update-policy options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=_options_from_input(user_input))

        return self.async_show_form(
            step_id="init",
            data_schema=_settings_schema(dict(self.config_entry.options)),
        )


def _bridge_from_input(values: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_BRIDGE_URL: str(values[CONF_BRIDGE_URL]).rstrip("/"),
        CONF_BRIDGE_TOKEN: str(values[CONF_BRIDGE_TOKEN]),
        CONF_VERIFY_SSL: bool(values[CONF_VERIFY_SSL]),
    }


def _options_from_input(values: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_SCAN_INTERVAL: int(values[CONF_SCAN_INTERVAL]),
        CONF_UPDATE_DELAY_HOURS: int(values[CONF_UPDATE_DELAY_HOURS]),
        CONF_WINDOW_START: values[CONF_WINDOW_START],
        CONF_WINDOW_END: values[CONF_WINDOW_END],
    }
