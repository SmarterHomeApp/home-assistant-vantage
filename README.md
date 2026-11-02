# Home Assistant Vantage

Custom Home Assistant integration for Vantage InFusion controllers.

This project is a Python/Home Assistant port of the working controller behavior from
`@smarterhomeapp/homebridge-vantage`. It is intended to live in its own repository.

## Current Status

Early custom integration scaffold:

- Fresh controller configuration download on startup
- Controller scan of this machine's local network on ports 3001 and 3010
- Persistent last-known-good controller cache
- VID range and omit filtering
- Basic entity mapping for lights, switches, covers, and thermostats
- Realtime socket listener for Vantage status events

## Installation For Development

Copy or symlink `custom_components/vantage` into your Home Assistant config directory:

```bash
ln -s /path/to/home-assistant-vantage/custom_components/vantage /config/custom_components/vantage
```

Restart Home Assistant, then add the integration from:

```text
Settings -> Devices & services -> Add integration -> Vantage
```

Enter the controller IP manually, or leave the host blank. A blank host scans the
local network detected from this machine's address. That network is shown on the form
and cannot be edited. If a later connection fails, the client rescans the current
local network and updates the stored controller IP.

## Notes

This is not a direct TypeScript port. Home Assistant integrations run in Python and use
entities/config entries instead of Homebridge platform accessories.

The integration keeps a persistent full-controller cache keyed by controller IP. VID range
filtering happens after the full controller configuration is loaded, so multiple config
entries can divide one controller by VID range while using the same last-known-good cache.
