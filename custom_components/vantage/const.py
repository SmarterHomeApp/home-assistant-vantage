"""Constants for the Vantage InFusion integration."""

from __future__ import annotations

DOMAIN = "vantage"

CONF_FORCE_SSL = "force_ssl"
CONF_OMIT = "omit"
CONF_RANGE = "range"
CONF_SCAN_SUBNET = "scan_subnet"
CONF_USE_CACHE = "use_cache"

DEFAULT_RANGE = "0-999999999"
DEFAULT_USE_CACHE = True

PLATFORMS = ["light", "switch", "cover", "climate"]
