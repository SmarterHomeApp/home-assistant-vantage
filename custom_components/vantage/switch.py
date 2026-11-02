"""Switch platform for Vantage InFusion."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import VantageData
from .entity import VantageEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vantage switch entities."""
    data: VantageData = entry.runtime_data
    async_add_entities(VantageSwitch(data, device) for device in data.devices_of_type("relay"))


class VantageSwitch(VantageEntity, SwitchEntity):
    """Vantage relay switch."""

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self.device.power

    async def async_turn_on(self, **kwargs) -> None:
        """Turn switch on."""
        self.device.power = True
        await self.data.client.async_set_relay(self.device.address, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn switch off."""
        self.device.power = False
        await self.data.client.async_set_relay(self.device.address, False)
        self.async_write_ha_state()
