"""Models used by the Vantage InFusion integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VantageDeviceType = Literal["relay", "dimmer", "rgb", "blind", "thermostat"]


@dataclass(slots=True)
class VantageDevice:
    """A Vantage object exposed to Home Assistant."""

    name: str
    address: str
    vid: str
    object_type: str
    type: VantageDeviceType
    load_type: str | None = None
    brightness: int = 100
    power: bool = False
    hue: float = 0
    saturation: float = 0
    position: int = 100
    position_state: int = 2
    temperature: float = 20
    target_temperature: float = 20
    heating: float = 20
    cooling: float = 25
    mode: int = 0
    current: int = 0


@dataclass(slots=True)
class VantageConfigValidation:
    """Validated XML configuration details."""

    objects: list[dict]
    relevant_object_count: int


@dataclass(slots=True)
class VantageRealtimeEvent:
    """Realtime event emitted by the controller command socket."""

    event_type: str
    vid: str
    value: float
    command: int | None = None
