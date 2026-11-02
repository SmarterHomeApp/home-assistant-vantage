"""Climate platform for Vantage InFusion."""

from __future__ import annotations

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.components.climate.const import HVACAction
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import VantageData
from .entity import VantageEntity

VANTAGE_TO_HVAC = {
    0: HVACMode.OFF,
    1: HVACMode.HEAT,
    2: HVACMode.COOL,
    3: HVACMode.HEAT_COOL,
}

HVAC_TO_VANTAGE = {value: key for key, value in VANTAGE_TO_HVAC.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vantage climate entities."""
    data: VantageData = entry.runtime_data
    entities = [VantageClimate(data, device) for device in data.devices_of_type("thermostat")]
    async_add_entities(entities)
    for entity in entities:
        await data.client.async_refresh_thermostat(entity.device.address)


class VantageClimate(VantageEntity, ClimateEntity):
    """Vantage thermostat."""

    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self) -> float:
        """Return current temperature."""
        return self.device.temperature

    @property
    def target_temperature(self) -> float:
        """Return target temperature."""
        return self.device.target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        return VANTAGE_TO_HVAC.get(self.device.mode, HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action."""
        if self.device.current == 1:
            return HVACAction.HEATING
        if self.device.current == 2:
            return HVACAction.COOLING
        return HVACAction.IDLE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        mode = HVAC_TO_VANTAGE[hvac_mode]
        self.device.mode = mode
        await self.data.client.async_set_thermostat_mode(self.device.address, mode)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs) -> None:
        """Set target temperature."""
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        self.device.target_temperature = float(temperature)
        await self.data.client.async_set_thermostat_target(
            self.device.address,
            self.device.target_temperature,
            self.device.mode,
        )
        self.async_write_ha_state()
