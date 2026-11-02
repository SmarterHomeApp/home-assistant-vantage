"""Config flow for Vantage InFusion."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from .client import VantageClient, VantageError, local_ipv4_subnets, scan_local_for_vantage_controller
from .const import (
    CONF_FORCE_SSL,
    CONF_OMIT,
    CONF_RANGE,
    CONF_SCAN_SUBNET,
    CONF_USE_CACHE,
    DEFAULT_RANGE,
    DEFAULT_USE_CACHE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate that the controller can be reached."""
    resolved = dict(data)
    resolved.pop(CONF_SCAN_SUBNET, None)
    if not resolved.get(CONF_HOST):
        try:
            result = await scan_local_for_vantage_controller()
        except VantageError as error:
            raise CannotConnect from error
        if result is None:
            raise CannotConnect
        resolved[CONF_HOST] = result[0]

    client = VantageClient(
        host=resolved[CONF_HOST],
        username=resolved.get(CONF_USERNAME),
        password=resolved.get(CONF_PASSWORD),
        cache_dir=Path(hass.config.path("vantage")),
        use_cache=resolved.get(CONF_USE_CACHE, DEFAULT_USE_CACHE),
        omit=resolved.get(CONF_OMIT, ""),
        vid_range=resolved.get(CONF_RANGE, DEFAULT_RANGE),
        force_ssl=resolved.get(CONF_FORCE_SSL, False),
    )
    try:
        await client.async_start()
    except Exception as error:
        raise CannotConnect from error
    finally:
        try:
            await client.async_close()
        except Exception:
            _LOGGER.debug("Failed to close Vantage client after setup check", exc_info=True)
    resolved[CONF_HOST] = client.host
    return resolved


class CannotConnect(Exception):
    """Raised when the controller cannot be reached."""


class VantageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Vantage InFusion."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                resolved = await _validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error connecting to Vantage")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(resolved[CONF_HOST])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Vantage {resolved[CONF_HOST]}",
                    data=resolved,
                )

        subnet = ", ".join(local_ipv4_subnets()) or "No local network detected"
        schema = vol.Schema(
            {
                vol.Optional(CONF_HOST, default=""): str,
                vol.Required(CONF_SCAN_SUBNET, default=subnet): TextSelector(TextSelectorConfig(read_only=True)),
                vol.Optional(CONF_USERNAME, default=""): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Optional(CONF_USE_CACHE, default=DEFAULT_USE_CACHE): bool,
                vol.Optional(CONF_OMIT, default=""): str,
                vol.Optional(CONF_RANGE, default=DEFAULT_RANGE): str,
                vol.Optional(CONF_FORCE_SSL, default=False): bool,
            },
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
