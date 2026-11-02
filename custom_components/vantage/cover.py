"""Cover platform for Vantage InFusion."""

from __future__ import annotations

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
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
    """Set up Vantage cover entities."""
    data: VantageData = entry.runtime_data
    async_add_entities(VantageCover(data, device) for device in data.devices_of_type("blind"))


class VantageCover(VantageEntity, CoverEntity):
    """Vantage blind/cover."""

    _attr_supported_features = CoverEntityFeature.SET_POSITION

    @property
    def current_cover_position(self) -> int:
        """Return current cover position."""
        return self.device.position

    @property
    def is_closed(self) -> bool:
        """Return true if cover is closed."""
        return self.device.position <= 0

    async def async_set_cover_position(self, **kwargs) -> None:
        """Set cover position."""
        position = int(kwargs["position"])
        self.device.position = position
        await self.data.client.async_set_cover_position(self.device.address, position)
        self.async_write_ha_state()
