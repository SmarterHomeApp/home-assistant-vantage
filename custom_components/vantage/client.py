"""Async Vantage InFusion controller client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import ipaddress
import logging
from pathlib import Path
import re
import socket
import ssl
import xml.etree.ElementTree as ET

from .const import DEFAULT_RANGE
from .models import VantageConfigValidation, VantageDevice, VantageRealtimeEvent

_LOGGER = logging.getLogger(__name__)

TYPE_THERMO = [
    "Thermostat",
    "Vantage.HVAC-Interface_Point_Zone_CHILD",
    "Vantage.VirtualThermostat_PORT",
    "Tekmar.tN4_Gateway_482_Zone_-_Slab_Only_CHILD",
    "Tekmar.tN4_Gateway_482_Zone_CHILD",
    "Legrand.MH_HVAC_Control_CHILD",
    "Vantage.HVAC-IU-Zone_CHILD",
]

TYPE_BLIND = [
    "Blind",
    "RelayBlind",
    "QISBlind",
    "Lutron.Shade_x2F_Blind_Child_CHILD",
    "QubeBlind",
    "ESI.RQShadeChannel_CHILD",
    "QMotion.QIS_Channel_CHILD",
    "Somfy.UAI-RS485-Motor_CHILD",
]

OBJECT_TYPES = [
    "Area",
    "Load",
    "Vantage.DDGColorLoad",
    "Legrand.MH_Relay_CHILD",
    "Legrand.MH_Dimmer_CHILD",
    "Jandy.Aqualink_RS_Pump_CHILD",
    "Jandy.Aqualink_RS_Auxiliary_CHILD",
    *TYPE_THERMO,
    *TYPE_BLIND,
]

DISCOVERABLE_OBJECT_TYPES = [item for item in OBJECT_TYPES if item != "Area"]

RealtimeCallback = Callable[[VantageRealtimeEvent], Awaitable[None] | None]

# Virtual interfaces are not the LAN the controller is on, unless they own the default route.
_VIRTUAL_IFACE_PREFIXES = (
    "lo",
    "awdl",
    "llw",
    "utun",
    "bridge",
    "gif",
    "stf",
    "docker",
    "br-",
    "veth",
    "vmnet",
    "anpi",
)


def _default_ipv4() -> str | None:
    """Return the local IPv4 address used for outbound traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def local_ipv4_subnets() -> list[str]:
    """Return this machine's IPv4 subnets, with the default-route subnet first."""
    import psutil

    default_ip = _default_ipv4()
    ranked: list[tuple[bool, str]] = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        iface_stats = stats.get(name)
        if iface_stats is not None and not iface_stats.isup:
            continue
        for addr in addrs:
            if addr.family != socket.AF_INET or not addr.address or not addr.netmask:
                continue
            ip = ipaddress.ip_address(addr.address)
            if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                continue
            if name.startswith(_VIRTUAL_IFACE_PREFIXES) and addr.address != default_ip:
                continue
            network = ipaddress.ip_network(f"{addr.address}/{addr.netmask}", strict=False)
            if network.prefixlen < 20:
                network = ipaddress.ip_network(f"{addr.address}/24", strict=False)
            ranked.append((addr.address == default_ip, str(network)))

    ranked.sort(key=lambda item: not item[0])
    subnets: list[str] = []
    for _preferred, cidr in ranked:
        if cidr not in subnets:
            subnets.append(cidr)
    return subnets


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    """Close a socket. Vantage often sends data after its SSL close notice."""
    writer.close()
    try:
        await writer.wait_closed()
    except (OSError, asyncio.CancelledError):
        pass


async def scan_local_for_vantage_controller() -> tuple[str, int] | None:
    """Scan this machine's local subnets for a Vantage controller."""
    subnets = local_ipv4_subnets()
    if not subnets:
        raise VantageError("no local IPv4 network found to scan")
    for subnet in subnets:
        result = await scan_for_vantage_controller(subnet)
        if result is not None:
            return result
    return None


class VantageError(Exception):
    """Base Vantage client error."""


class VantageConfigError(VantageError):
    """Raised when controller configuration is invalid or incomplete."""


async def scan_for_vantage_controller(
    subnet: str,
    ports: tuple[int, ...] = (3001, 3010),
    concurrency: int = 32,
    timeout: float = 1,
) -> tuple[str, int] | None:
    """Scan a subnet for the first host with an open Vantage command port."""
    network = ipaddress.ip_network(subnet, strict=False)
    semaphore = asyncio.Semaphore(concurrency)

    async def check(host: str, port: int) -> tuple[str, int] | None:
        async with semaphore:
            try:
                _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
                writer.close()
                await writer.wait_closed()
                return host, port
            except Exception:
                return None

    tasks = [
        asyncio.create_task(check(str(host), port))
        for host in network.hosts()
        for port in ports
    ]

    try:
        for task in asyncio.as_completed(tasks):
            result = await task
            if result is not None:
                for pending in tasks:
                    pending.cancel()
                return result
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)

    return None


class VantageClient:
    """Client for Vantage InFusion discovery, realtime events, and commands."""

    def __init__(
        self,
        host: str,
        username: str | None,
        password: str | None,
        cache_dir: Path,
        use_cache: bool = True,
        omit: str = "",
        vid_range: str = DEFAULT_RANGE,
        force_ssl: bool = False,
    ) -> None:
        self.host = host
        self.username = username or ""
        self.password = password or ""
        self.cache_dir = cache_dir
        self.use_cache = use_cache
        self.omit = omit
        self.vid_range = vid_range
        self.force_ssl = force_ssl

        self._command_reader: asyncio.StreamReader | None = None
        self._command_writer: asyncio.StreamWriter | None = None
        self._realtime_task: asyncio.Task | None = None
        self._callbacks: list[RealtimeCallback] = []
        self._use_ssl_command = force_ssl
        self._use_ssl_config = force_ssl

    @property
    def cache_path(self) -> Path:
        """Persistent controller-level last-known-good cache path."""
        key = re.sub(r"[^a-zA-Z0-9.-]", "_", self.host)
        return self.cache_dir / f"vantage-{key}.dc"

    def add_realtime_callback(self, callback: RealtimeCallback) -> None:
        """Register callback for realtime events."""
        self._callbacks.append(callback)

    async def async_start(self) -> None:
        """Probe ports and open command socket."""
        if not self.host:
            await self.async_scan_and_update_host()
        if not self.force_ssl:
            self._use_ssl_command = not await self._port_usable(3001, b"STATUS ALL\n")
            self._use_ssl_config = not await self._port_usable(
                2001,
                b"<IIntrospection><GetInterfaces><call></call></GetInterfaces></IIntrospection>\n",
            )
        try:
            await self._open_command_socket()
        except OSError:
            await self.async_scan_and_update_host()
            await self._open_command_socket()

    async def async_scan_and_update_host(self) -> str:
        """Scan this machine's local subnets and update host to the controller that answers."""
        subnets = local_ipv4_subnets()
        result = await scan_local_for_vantage_controller()
        if result is None:
            raise VantageError(f"no Vantage controller found on {', '.join(subnets) or 'any local network'}")

        self.host = result[0]
        self._use_ssl_command = result[1] == 3010
        self._use_ssl_config = result[1] == 3010
        return self.host

    async def async_close(self) -> None:
        """Close command socket."""
        task = self._realtime_task
        self._realtime_task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        writer = self._command_writer
        self._command_writer = None
        self._command_reader = None
        if writer:
            await _close_writer(writer)

    async def async_discover_devices(self) -> list[VantageDevice]:
        """Download fresh configuration first, falling back to persistent cache."""
        if self.use_cache:
            await self._migrate_legacy_cache_if_needed()

        try:
            xml = await self._pull_configuration_xml()
            validation = self._parse_and_validate_configuration_xml(xml, "downloaded configuration")
            if self.use_cache:
                try:
                    await self._write_cache_atomically(xml, validation)
                    await self._write_legacy_tmp_mirror(xml, validation)
                except VantageConfigError:
                    raise
                except OSError:
                    # Live config is still usable for this run if persistence fails.
                    pass
            return self._process_configuration_xml(xml, "downloaded configuration")
        except Exception as error:
            if not self.use_cache:
                raise
            try:
                return self._load_persistent_cache()
            except Exception as cache_error:
                raise VantageConfigError(
                    f"fresh discovery failed and persistent cache is unavailable: {error}; cache: {cache_error}",
                ) from cache_error

    async def async_set_brightness(self, vid: str, level: int) -> None:
        """Set dimmer/RGB brightness."""
        level = max(0, min(100, int(level)))
        await self._write_command(f"INVOKE {vid} Load.Ramp 6 1 {level}\n")

    async def async_set_relay(self, vid: str, on: bool) -> None:
        """Set relay on/off."""
        await self._write_command(f"LOAD {vid} {100 if on else 0}\n")

    async def async_set_cover_position(self, vid: str, position: int) -> None:
        """Set blind position."""
        position = max(0, min(100, int(position)))
        await self._write_command(f"BLIND {vid} POS {position}\n")

    async def async_set_hue(self, vid: str, hue: float) -> None:
        """Best-effort RGB hue command."""
        await self._write_command(f"INVOKE {vid} RGBLoad.SetHSL {round(hue)} 100 100\n")

    async def async_set_saturation(self, vid: str, saturation: float) -> None:
        """Best-effort RGB saturation command."""
        await self._write_command(f"INVOKE {vid} RGBLoad.SetHSL 0 {round(saturation)} 100\n")

    async def async_set_thermostat_mode(self, vid: str, mode: int) -> None:
        """Set thermostat mode."""
        out = "OFF" if mode == 0 else "HEAT" if mode == 1 else "COOL" if mode == 2 else "AUTO"
        await self._write_command(f"THERMOP {vid} {out}\n")

    async def async_set_thermostat_target(self, vid: str, value: float, mode: int | None = None) -> None:
        """Set thermostat target temperature."""
        rounded = round(value)
        if mode == 1:
            await self._write_command(f"THERMTEMP {vid} HEAT {rounded}\n")
        elif mode == 2:
            await self._write_command(f"THERMTEMP {vid} COOL {rounded}\n")
        else:
            await self._write_command(f"THERMTEMP {vid} HEAT {rounded}\n")
            await self._write_command(f"THERMTEMP {vid} COOL {rounded}\n")

    async def async_refresh_thermostat(self, vid: str) -> None:
        """Refresh thermostat state."""
        await self._write_command(f"INVOKE {vid} Thermostat.GetIndoorTemperature\n")
        await self._write_command(f"GETTHERMOP {vid}\n")
        await self._write_command(f"GETTHERMTEMP {vid} HEAT\n")
        await self._write_command(f"GETTHERMTEMP {vid} COOL\n")

    async def async_refresh_status(self, devices: list[VantageDevice], timeout: float = 20) -> None:
        """Read current load, blind, and thermostat state from the controller."""
        pending = {
            device.vid
            for device in devices
            if device.type in {"relay", "dimmer", "rgb", "blind", "thermostat"}
        }
        if not pending:
            return

        done = asyncio.Event()

        def on_event(event: VantageRealtimeEvent) -> None:
            if event.event_type not in {"load", "blind", "thermostat_temperature", "thermostat_mode"}:
                return
            pending.discard(event.vid)
            if not pending:
                done.set()

        self._callbacks.append(on_event)
        try:
            for device in devices:
                if device.type in {"relay", "dimmer", "rgb"}:
                    await self._write_command(f"GETLOAD {device.vid}\n")
                elif device.type == "blind":
                    await self._write_command(f"GETBLIND {device.vid}\n")
                elif device.type == "thermostat":
                    await self.async_refresh_thermostat(device.vid)
            remaining = len(pending)
            await asyncio.wait_for(done.wait(), timeout)
        except TimeoutError:
            _LOGGER.warning(
                "Timed out reading Vantage status; %s of %s devices did not answer",
                len(pending),
                remaining,
            )
        else:
            _LOGGER.info("Read current status for %s Vantage devices", remaining)
        finally:
            self._callbacks.remove(on_event)

    async def _port_usable(self, port: int, probe: bytes) -> bool:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, port), timeout=8)
            writer.write(probe)
            await writer.drain()
            data = await asyncio.wait_for(reader.read(256), timeout=8)
            writer.close()
            await writer.wait_closed()
            return bool(data)
        except Exception:
            return False

    async def _open_command_socket(self) -> None:
        port = 3010 if self._use_ssl_command else 3001
        ssl_context = self._ssl_context() if self._use_ssl_command else None
        self._command_reader, self._command_writer = await asyncio.open_connection(
            self.host,
            port,
            ssl=ssl_context,
        )
        if self.username and self.password:
            await self._write_command(f"Login {self.username} {self.password}\n")
        await self._write_command("STATUS ALL\n")
        await self._write_command(
            "ELENABLE 1 AUTOMATION ON\n"
            "ELENABLE 1 EVENT ON\n"
            "ELENABLE 1 STATUS ON\n"
            "ELENABLE 1 STATUSEX ON\n"
            "ELENABLE 1 SYSTEM ON\n"
            "ELLOG AUTOMATION ON\n"
            "ELLOG EVENT ON\n"
            "ELLOG STATUS ON\n"
            "ELLOG STATUSEX ON\n"
            "ELLOG SYSTEM ON\n"
        )
        self._realtime_task = asyncio.create_task(self._realtime_loop())

    async def _write_command(self, command: str) -> None:
        if not self._command_writer:
            raise VantageError("command socket is not connected")
        self._command_writer.write(command.encode())
        await self._command_writer.drain()

    async def _realtime_loop(self) -> None:
        assert self._command_reader is not None
        while True:
            data = await self._command_reader.readline()
            if not data:
                await asyncio.sleep(5)
                await self._open_command_socket()
                return
            for event in self._parse_realtime(data.decode(errors="ignore")):
                for callback in self._callbacks:
                    result = callback(event)
                    if result is not None:
                        await result

    def _parse_realtime(self, buffer: str) -> list[VantageRealtimeEvent]:
        events: list[VantageRealtimeEvent] = []
        for line in buffer.splitlines():
            parts = line.split(" ")
            if len(parts) < 3:
                continue
            try:
                if line.startswith(("S:BLIND", "R:GETBLIND")):
                    events.append(VantageRealtimeEvent("blind", parts[1], float(parts[2])))
                elif line.startswith(("S:LOAD ", "R:GETLOAD ")):
                    events.append(VantageRealtimeEvent("load", parts[1], float(parts[2])))
                elif parts[0] == "R:INVOKE" and len(parts) > 4 and "RGBLoad.GetHSL" in parts[3]:
                    events.append(VantageRealtimeEvent("load", parts[1], float(parts[2]), int(parts[4])))
                elif parts[0] == "S:TEMP":
                    events.append(VantageRealtimeEvent("thermostat_refresh", parts[1], float(parts[2])))
                elif parts[0] == "R:INVOKE" and len(parts) > 3 and "Thermostat.GetIndoorTemperature" in parts[3]:
                    events.append(VantageRealtimeEvent("thermostat_temperature", parts[1], float(parts[2])))
                elif parts[0] in {"S:THERMOP", "R:GETTHERMOP", "R:THERMTEMP"}:
                    token = parts[2] if len(parts) > 2 else ""
                    mode = 0 if "OFF" in token else 1 if "HEAT" in token else 2 if "COOL" in token else 3
                    value = -1 if parts[0] != "R:THERMTEMP" or len(parts) < 4 else float(parts[3])
                    events.append(VantageRealtimeEvent("thermostat_mode", parts[1], value, mode))
            except (ValueError, IndexError):
                continue
        return events

    async def _pull_configuration_xml(self) -> str:
        port = 2010 if self._use_ssl_config else 2001
        ssl_context = self._ssl_context() if self._use_ssl_config else None
        try:
            reader, writer = await asyncio.open_connection(self.host, port, ssl=ssl_context)
        except OSError:
            await self.async_scan_and_update_host()
            port = 2010 if self._use_ssl_config else 2001
            ssl_context = self._ssl_context() if self._use_ssl_config else None
            reader, writer = await asyncio.open_connection(self.host, port, ssl=ssl_context)
        read_objects: list[dict] = []
        controller = 1

        try:
            if self.username and self.password:
                writer.write(
                    f"<ILogin><Login><call><User>{self.username}</User><Password>{self.password}</Password></call></Login></ILogin>\n".encode(),
                )
                await writer.drain()
                await asyncio.sleep(0.25)

            for object_type in OBJECT_TYPES:
                writer.write(
                    (
                        f"<?Master {controller}?>"
                        f"<IConfiguration><OpenFilter><call><Objects><ObjectType>{object_type}</ObjectType></Objects>"
                        "</call></OpenFilter></IConfiguration>\n"
                    ).encode(),
                )
                await writer.drain()
                open_response = await self._read_xml_message(reader)
                h_filter = self._extract_filter_handle(open_response)
                if not h_filter:
                    continue
                writer.write(
                    (
                        "<IConfiguration><GetFilterResults><call><Count>1000</Count><WholeObject>true</WholeObject>"
                        f"<hFilter>{h_filter}</hFilter></call></GetFilterResults></IConfiguration>\n"
                    ).encode(),
                )
                await writer.drain()
                response = await self._read_xml_message(reader)
                read_objects.extend(self._extract_filter_results(response, object_type))
        finally:
            await _close_writer(writer)

        if not read_objects:
            raise VantageConfigError("controller returned no configuration objects")
        return self._build_cache_xml(read_objects)

    async def _read_xml_message(self, reader: asyncio.StreamReader, timeout: float = 30) -> str:
        """Read until the buffer is one complete XML document.

        Vantage pretty-prints responses, so the first line is only ``<?Master 1?>``.
        """
        buffer = ""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise VantageConfigError("timed out waiting for a complete configuration response")
            chunk = await asyncio.wait_for(reader.read(65536), timeout=remaining)
            if not chunk:
                break
            buffer += chunk.decode(errors="ignore").replace("\ufeff", "")
            if self._parse_xml_fragment(buffer) is not None:
                return buffer
        if self._parse_xml_fragment(buffer) is not None:
            return buffer
        raise VantageConfigError("configuration response ended before a complete XML document")

    def _extract_filter_handle(self, xml: str) -> str | None:
        root = self._parse_xml_fragment(xml)
        if root is None:
            return None
        node = root.find(".//OpenFilter/return")
        return node.text if node is not None else None

    def _extract_filter_results(self, xml: str, object_type: str) -> list[dict]:
        root = self._parse_xml_fragment(xml)
        if root is None:
            return []
        results: list[dict] = []
        for object_node in root.findall(".//Object"):
            child = next(iter(object_node), None)
            if child is None:
                continue
            values = {**child.attrib, **{item.tag: item.text or "" for item in child}}
            values["ObjectType"] = object_type
            results.append({object_type: values})
        return results

    def _parse_xml_fragment(self, xml: str) -> ET.Element | None:
        sanitized = self._sanitize_xml(xml).strip()
        if sanitized.startswith("<?Master"):
            sanitized = sanitized[sanitized.find("?>") + 2 :]
        try:
            return ET.fromstring(sanitized)
        except ET.ParseError:
            return None

    def _process_configuration_xml(self, xml: str, source: str) -> list[VantageDevice]:
        validation = self._parse_and_validate_configuration_xml(xml, source)
        objects = validation.objects
        areas = {item["Area"].get("VID"): item["Area"] for item in objects if "Area" in item}

        omit = {item for item in re.sub(r"\s+", "", self.omit or "").split(",") if item}
        min_vid, max_vid = self._parse_range()
        devices: list[VantageDevice] = []
        seen_names: set[str] = set()
        blind_open_close: set[str] = set()

        for raw in objects:
            key = next(iter(raw.keys()))
            item = raw[key]
            vid = str(item.get("VID", ""))
            if not vid or vid in omit:
                continue
            vid_number = int(vid) if vid.isdigit() else -1
            if vid_number < min_vid or vid_number > max_vid:
                continue
            object_type = item.get("ObjectType") or key
            if object_type not in OBJECT_TYPES:
                continue

            name = str(item.get("DName") or item.get("Name") or f"VID{vid}")
            area_ref = str(item.get("AreaID") or item.get("Area") or "")
            area_record = areas.get(area_ref)
            area_name = ""
            if area_record:
                area_name = str(area_record.get("Name") or area_record.get("DName") or "").strip()
            name = name.replace("-", "") or f"VID{vid}"
            name_key = f"{area_name.lower()}|{name.lower()}"
            if name_key in seen_names:
                name = f"{name} VID{vid}"
            seen_names.add(name_key)

            if item.get("DeviceCategory") == "HVAC" or object_type in TYPE_THERMO:
                devices.append(VantageDevice(name, vid, vid, object_type, "thermostat", area=area_name or None))
                continue

            if key == "Load" or object_type in {
                "Load",
                "Legrand.MH_Dimmer_CHILD",
                "Legrand.MH_Relay_CHILD",
                "Vantage.DDGColorLoad",
                "Jandy.Aqualink_RS_Auxiliary_CHILD",
                "Jandy.Aqualink_RS_Pump_CHILD",
            }:
                is_relay = object_type in {
                    "Jandy.Aqualink_RS_Pump_CHILD",
                    "Jandy.Aqualink_RS_Auxiliary_CHILD",
                    "Legrand.MH_Relay_CHILD",
                } or item.get("LoadType") in {
                    "Fluor. Mag non-Dim",
                    "LED non-Dim",
                    "Fluor. Electronic non-Dim",
                    "Low Voltage Relay",
                    "Motor",
                    "High Voltage Relay",
                }
                device_type = "rgb" if object_type == "Vantage.DDGColorLoad" else "relay" if is_relay else "dimmer"
                devices.append(
                    VantageDevice(
                        name,
                        vid,
                        vid,
                        object_type,
                        device_type,
                        area=area_name or None,
                        load_type=item.get("LoadType"),
                    ),
                )
                continue

            if object_type in TYPE_BLIND:
                if object_type == "RelayBlind":
                    blind_open_close.update({str(item.get("OpenLoad", "")), str(item.get("CloseLoad", ""))})
                devices.append(VantageDevice(name, vid, vid, object_type, "blind", area=area_name or None))

        return [device for device in devices if device.address not in blind_open_close]

    def _parse_and_validate_configuration_xml(self, xml: str, source: str) -> VantageConfigValidation:
        try:
            root = ET.fromstring(self._sanitize_xml(xml))
        except ET.ParseError as error:
            raise VantageConfigError(f"{source} rejected: XML parsing failed: {error}") from error
        object_nodes = root.findall("./Objects/Object")
        if not object_nodes:
            raise VantageConfigError(f"{source} rejected: missing Project.Objects.Object configuration structure")
        objects: list[dict] = []
        for node in object_nodes:
            child = next(iter(node), None)
            if child is None:
                continue
            fields = {**child.attrib, **{item.tag: item.text or "" for item in child}}
            objects.append({child.tag: fields})
        relevant = sum(1 for item in objects if self._is_relevant_config_object(item))
        if relevant == 0:
            raise VantageConfigError(f"{source} rejected: configuration contains no discoverable Vantage objects")
        return VantageConfigValidation(objects, relevant)

    def _is_relevant_config_object(self, raw: dict) -> bool:
        key = next(iter(raw.keys()))
        value = raw[key]
        object_type = value.get("ObjectType") or key
        return key in DISCOVERABLE_OBJECT_TYPES or object_type in DISCOVERABLE_OBJECT_TYPES

    def _load_persistent_cache(self) -> list[VantageDevice]:
        if not self.cache_path.exists():
            raise VantageConfigError(f"persistent cache not found at {self.cache_path}")
        return self._process_configuration_xml(self.cache_path.read_text(), f"persistent cache {self.cache_path}")

    async def _write_cache_atomically(self, xml: str, validation: VantageConfigValidation) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._validate_candidate_against_existing_cache(validation)
        tmp_path = self.cache_path.with_name(f"{self.cache_path.name}.{id(self)}.tmp")
        tmp_path.write_text(xml)
        tmp_validation = self._parse_and_validate_configuration_xml(tmp_path.read_text(), f"temporary cache {tmp_path}")
        self._validate_candidate_against_existing_cache(tmp_validation)
        tmp_path.replace(self.cache_path)

    async def _write_legacy_tmp_mirror(self, xml: str, validation: VantageConfigValidation) -> None:
        cache_path = Path("/tmp/vantage.dc")
        tmp_path = cache_path.with_name(f"{cache_path.name}.{id(self)}.tmp")
        try:
            tmp_path.write_text(xml)
            tmp_validation = self._parse_and_validate_configuration_xml(tmp_path.read_text(), f"legacy mirror {tmp_path}")
            if tmp_validation.relevant_object_count != validation.relevant_object_count:
                raise VantageConfigError("legacy mirror validation count mismatch")
            tmp_path.replace(cache_path)
        except (OSError, VantageConfigError):
            tmp_path.unlink(missing_ok=True)

    async def _migrate_legacy_cache_if_needed(self) -> None:
        if self.cache_path.exists():
            return
        legacy_path = Path("/tmp") / f"vantage-{re.sub(r'[^a-zA-Z0-9.-]', '_', self.host)}.dc"
        if not legacy_path.exists():
            return
        validation = self._parse_and_validate_configuration_xml(legacy_path.read_text(), f"legacy cache {legacy_path}")
        await self._write_cache_atomically(legacy_path.read_text(), validation)

    def _validate_candidate_against_existing_cache(self, validation: VantageConfigValidation) -> None:
        if not self.cache_path.exists():
            return
        try:
            existing = self._parse_and_validate_configuration_xml(self.cache_path.read_text(), f"existing cache {self.cache_path}")
        except VantageConfigError:
            return
        if existing.relevant_object_count >= 20 and validation.relevant_object_count < existing.relevant_object_count * 0.5:
            raise VantageConfigError(
                "downloaded configuration appears incomplete: "
                f"{validation.relevant_object_count} relevant objects vs {existing.relevant_object_count} in persistent cache",
            )

    def _parse_range(self) -> tuple[int, int]:
        raw = re.sub(r"\s+", "", self.vid_range or DEFAULT_RANGE)
        separator = "-" if "-" in raw else ","
        parts = raw.split(separator)
        return int(parts[0]), int(parts[1])

    def _sanitize_xml(self, xml: str) -> str:
        xml = re.sub(r"&(?!amp;|lt;|gt;|apos;|quot;)", "&amp;", xml)
        return re.sub(r"<Area>(\d+)</Area>", r"<AreaID>\1</AreaID>", xml)

    def _build_cache_xml(self, read_objects: list[dict]) -> str:
        fragments: list[str] = []
        for item in read_objects:
            key = next(iter(item.keys()))
            values = item[key]
            body = "".join(f"<{name}>{self._escape_xml(str(value))}</{name}>" for name, value in values.items())
            fragments.append(f"<Object><{key}>{body}</{key}></Object>")
        return f"<Project><Objects>{''.join(fragments)}</Objects></Project>"

    def _escape_xml(self, value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    def _ssl_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
