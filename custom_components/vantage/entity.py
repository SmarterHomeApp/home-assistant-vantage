"""Base Vantage entity."""

from __future__ import annotations

from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN
from .coordinator import VantageData
from .models import VantageDevice, VantageRealtimeEvent


class VantageEntity(Entity):
    """Base class for Vantage entities."""

    _attr_has_entity_name = True

    def __init__(self, data: VantageData, device: VantageDevice) -> None:
        self.data = data
        self.device = device
        self._attr_name = device.name
        self._attr_unique_id = f"{data.entry.entry_id}_{device.vid}"
        self._remove_listener = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.data.entry.data['host']}_{self.device.vid}")},
            name=self.device.name,
            manufacturer="Vantage Controls",
            model=self.device.object_type,
            serial_number=f"VID {self.device.vid}",
            suggested_area=self.device.area,
        )

    async def async_added_to_hass(self) -> None:
        """Register realtime listener and place the device in its Vantage area."""
        self._remove_listener = self.data.listen_vid(self.device.vid, self._handle_realtime_event)
        self._assign_area()

    def _assign_area(self) -> None:
        """Create the Vantage area and add this device when it has no area yet."""
        if not self.device.area:
            return
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, f"{self.data.entry.data['host']}_{self.device.vid}")},
        )
        if device is None or device.area_id is not None:
            return
        area = ar.async_get(self.hass).async_get_or_create(self.device.area)
        device_registry.async_update_device(device.id, area_id=area.id)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister realtime listener."""
        if self._remove_listener:
            self._remove_listener()

    def _handle_realtime_event(self, event: VantageRealtimeEvent) -> None:
        """Handle realtime update."""
        self.async_write_ha_state()
