"""Light platform for Vantage InFusion."""

from __future__ import annotations

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import VantageData
from .entity import VantageEntity
from .models import VantageDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vantage light entities."""
    data: VantageData = entry.runtime_data
    async_add_entities(VantageLight(data, device) for device in data.devices_of_type("dimmer", "rgb"))


class VantageLight(VantageEntity, LightEntity):
    """Vantage dimmer/RGB light."""

    def __init__(self, data: VantageData, device: VantageDevice) -> None:
        super().__init__(data, device)
        if device.type == "rgb":
            self._attr_supported_color_modes = {ColorMode.HS}
            self._attr_color_mode = ColorMode.HS
        else:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self.device.power

    @property
    def brightness(self) -> int:
        """Return Home Assistant brightness 0-255."""
        return round(self.device.brightness * 255 / 100)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return HS color."""
        if self.device.type != "rgb":
            return None
        return (self.device.hue, self.device.saturation)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn light on."""
        brightness = kwargs.get("brightness")
        if brightness is not None:
            self.device.brightness = round(int(brightness) * 100 / 255)
        elif self.device.brightness == 0:
            self.device.brightness = 100

        hs_color = kwargs.get("hs_color")
        if hs_color and self.device.type == "rgb":
            self.device.hue, self.device.saturation = hs_color
            await self.data.client.async_set_hue(self.device.address, self.device.hue)
            await self.data.client.async_set_saturation(self.device.address, self.device.saturation)

        self.device.power = True
        await self.data.client.async_set_brightness(self.device.address, self.device.brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn light off."""
        self.device.power = False
        await self.data.client.async_set_brightness(self.device.address, 0)
        self.async_write_ha_state()
