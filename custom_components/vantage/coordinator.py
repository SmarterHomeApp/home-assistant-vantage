"""Runtime data holder for Vantage config entries."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .client import VantageClient
from .const import CONF_FORCE_SSL, CONF_OMIT, CONF_RANGE, CONF_USE_CACHE, DEFAULT_RANGE, DOMAIN
from .models import VantageDevice, VantageRealtimeEvent


class VantageData:
    """Runtime data for a Vantage controller config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.devices: list[VantageDevice] = []
        self.devices_by_vid: dict[str, VantageDevice] = {}
        self.listeners: dict[str, list[Callable[[VantageRealtimeEvent], None]]] = {}

        cache_dir = Path(hass.config.path("vantage"))
        self.client = VantageClient(
            host=entry.data["host"],
            username=entry.data.get("username"),
            password=entry.data.get("password"),
            cache_dir=cache_dir,
            use_cache=entry.options.get(CONF_USE_CACHE, entry.data.get(CONF_USE_CACHE, True)),
            omit=entry.options.get(CONF_OMIT, entry.data.get(CONF_OMIT, "")),
            vid_range=entry.options.get(CONF_RANGE, entry.data.get(CONF_RANGE, DEFAULT_RANGE)),
            force_ssl=entry.options.get(CONF_FORCE_SSL, entry.data.get(CONF_FORCE_SSL, False)),
        )

    async def async_setup(self) -> None:
        """Connect and discover devices."""
        await self.client.async_start()
        self.client.add_realtime_callback(self._handle_realtime_event)
        try:
            self.devices = await self.client.async_discover_devices()
        except Exception:
            await self.client.async_close()
            raise
        self.devices_by_vid = {device.vid: device for device in self.devices}
        await self.client.async_refresh_status(self.devices)
        if self.client.host != self.entry.data["host"]:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, "host": self.client.host},
            )

    async def async_close(self) -> None:
        """Close client."""
        await self.client.async_close()

    @callback
    def listen_vid(self, vid: str, listener: Callable[[VantageRealtimeEvent], None]) -> Callable[[], None]:
        """Register a listener for realtime updates for one VID."""
        listeners = self.listeners.setdefault(vid, [])
        listeners.append(listener)

        def remove() -> None:
            listeners.remove(listener)

        return remove

    @callback
    def _handle_realtime_event(self, event: VantageRealtimeEvent) -> None:
        """Dispatch realtime event to entity listeners."""
        device = self.devices_by_vid.get(event.vid)
        if device is not None:
            self._update_device_state(device, event)

        for listener in self.listeners.get(event.vid, []):
            listener(event)

    @callback
    def _update_device_state(self, device: VantageDevice, event: VantageRealtimeEvent) -> None:
        """Update cached device state from realtime event."""
        if event.event_type == "load":
            device.brightness = int(event.value)
            device.power = device.brightness > 0
            if device.type == "rgb" and event.command is not None:
                if event.command == 0:
                    device.hue = event.value
                elif event.command == 1:
                    device.saturation = event.value
                elif event.command == 2:
                    device.brightness = int(event.value)
        elif event.event_type == "blind":
            device.position = int(event.value)
            device.position_state = 2
        elif event.event_type == "thermostat_temperature":
            device.temperature = event.value
        elif event.event_type == "thermostat_mode":
            if event.command is not None:
                device.mode = event.command
            if event.value >= 0:
                device.target_temperature = event.value

    def devices_of_type(self, *device_types: str) -> list[VantageDevice]:
        """Return devices matching one or more Vantage types."""
        return [device for device in self.devices if device.type in device_types]

    @property
    def device_info_base(self) -> dict[str, Any]:
        """Device info shared by Vantage entities."""
        return {
            "manufacturer": "Vantage Controls",
            "configuration_url": f"http://{self.entry.data['host']}",
        }
