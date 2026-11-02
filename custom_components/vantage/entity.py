"""Base Vantage entity."""

from __future__ import annotations

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
        )

    async def async_added_to_hass(self) -> None:
        """Register realtime listener."""
        self._remove_listener = self.data.listen_vid(self.device.vid, self._handle_realtime_event)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister realtime listener."""
        if self._remove_listener:
            self._remove_listener()

    def _handle_realtime_event(self, event: VantageRealtimeEvent) -> None:
        """Handle realtime update."""
        self.async_write_ha_state()
