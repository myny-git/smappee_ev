"""Device-registry helpers for Smappee EV runtime data."""

from __future__ import annotations

from inspect import signature
import logging
from typing import Any

from homeassistant.const import ATTR_RESTORED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, MANUFACTURER
from .helpers import connector_device_identifier, site_device_identifier, station_device_identifier
from .models.runtime_data import (
    RuntimeData,
    RuntimeMode,
    SmappeeConnectorRuntime,
    SmappeeEvConfigEntry,
)

_LOGGER = logging.getLogger(__name__)

_SUPPORTS_VIA_DEVICE_ID = (
    "via_device_id" in signature(dr.DeviceRegistry.async_get_or_create).parameters
)


def _via_device_kwargs(device: dr.DeviceEntry, identifier: tuple[str, str]) -> dict[str, Any]:
    """Use registry IDs when supported, otherwise the legacy identifier API."""
    if _SUPPORTS_VIA_DEVICE_ID:
        return {"via_device_id": device.id}
    return {"via_device": identifier}


def _remove_entry_device(
    registry: dr.DeviceRegistry, device: dr.DeviceEntry, entry: SmappeeEvConfigEntry
) -> None:
    """Remove a device this entry owns, or only this entry's claim on a shared one."""
    config_entry_id = getattr(device, "config_entry_id", None)
    if config_entry_id is not None:
        if config_entry_id == entry.entry_id:
            registry.async_remove_device(device.id)
        return

    # Before HA 2026.8 devices could be shared by several config entries.
    if device.config_entries == {entry.entry_id}:
        registry.async_remove_device(device.id)
    else:
        registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


def _connector_device_key(connector_uuid: str, info: SmappeeConnectorRuntime) -> tuple[str, str]:
    """Return the registry key and display label for a connector device."""
    position = info.connector_position or getattr(info.connector_client, "connector_number", None)
    key = connector_uuid or (f"position:{position}" if position else "unknown")
    return str(key), str(position) if position is not None else str(key)


def _remove_legacy_led_controller_devices(
    registry: dr.DeviceRegistry,
    entry: SmappeeEvConfigEntry,
) -> None:
    """Remove old standalone LED Controller devices for this config entry."""
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not any(
            domain == DOMAIN and identifier.startswith("led:")
            for domain, identifier in device.identifiers
        ):
            continue
        _remove_entry_device(registry, device, entry)


def _register_runtime_devices(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> set[str]:
    """Ensure the registry contains the real Smappee hierarchy; return its device IDs."""
    registered: set[str] = set()
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        return registered
    registry = dr.async_get(hass)
    _remove_legacy_led_controller_devices(registry, entry)
    rd = entry.runtime_data
    for site_sid, site in (rd.sites or {}).items():
        site_identifier = site_device_identifier(site_sid)
        site_name = site.site_name or f"Smappee {site_sid}"
        gateway_serial = site.gateway_serial
        gateway_type = site.gateway_type
        site_device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={site_identifier},
            manufacturer=MANUFACTURER,
            name=f"Smappee {site_name}",
            model=f"{gateway_type} / Service Location" if gateway_type else "Service Location",
            serial_number=str(gateway_serial) if gateway_serial else None,
        )
        registered.add(site_device.id)

        for station_uuid, bucket in site.stations.items():
            station_serial = bucket.charging_station_serial
            if not station_serial:
                continue
            control_sid = bucket.control_location_id
            station_identifier = station_device_identifier(site_sid, control_sid, station_serial)
            station_identifiers = {station_identifier}
            station_client = bucket.station_client
            legacy_serial = getattr(station_client, "serial_id", None) or station_serial
            station_identifiers.add((DOMAIN, f"{site_sid}:{legacy_serial}:{station_uuid}"))
            station_device = registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers=station_identifiers,
                manufacturer=MANUFACTURER,
                name=bucket.station_name or f"Smappee EV {station_serial}",
                model=bucket.charging_station_model or "EV Wall",
                serial_number=str(station_serial),
                **_via_device_kwargs(site_device, site_identifier),
            )
            registered.add(station_device.id)

            for connector_uuid, info in bucket.connectors.items():
                connector_key, label = _connector_device_key(connector_uuid, info)
                connector_device = registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={
                        connector_device_identifier(
                            site_sid, control_sid, station_serial, connector_key
                        )
                    },
                    manufacturer=MANUFACTURER,
                    name=f"Smappee EV {station_serial} | Connector {label}",
                    model="Connector",
                    **_via_device_kwargs(station_device, station_identifier),
                )
                registered.add(connector_device.id)
    return registered


def _device_has_live_entity(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, device_id: str
) -> bool:
    """Return True when an entity created during this setup is attached to the device."""
    for entity in er.async_entries_for_device(entity_registry, device_id):
        state = hass.states.get(entity.entity_id)
        if state is not None and not state.attributes.get(ATTR_RESTORED):
            return True
    return False


@callback
def async_remove_stale_devices(
    hass: HomeAssistant, entry: SmappeeEvConfigEntry, registered_device_ids: set[str]
) -> None:
    """Remove devices that the completed Dashboard discovery no longer reports.

    Call only after the platforms are set up. Cached MQTT-only runtimes describe
    an older topology and therefore never prove that a device has disappeared.
    """
    runtime = entry.runtime_data
    if runtime.mode is not RuntimeMode.NORMAL or not registered_device_ids:
        return
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    current_identifiers = _current_device_identifiers(entry)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if device.id in registered_device_ids:
            continue
        domain_identifiers = {
            identifier for domain, identifier in device.identifiers if domain == DOMAIN
        }
        if not domain_identifiers or not domain_identifiers.isdisjoint(current_identifiers):
            continue
        # Entities may describe a device slightly differently than discovery does;
        # a device that just received an entity is in use, whatever its identifier.
        if _device_has_live_entity(hass, entity_registry, device.id):
            continue
        _LOGGER.info("Removing stale Smappee EV device %s", device.name_by_user or device.name)
        _remove_entry_device(device_registry, device, entry)


def _current_station_device_identifiers(entry: SmappeeEvConfigEntry) -> set[str]:
    """Return Smappee EV device identifiers currently known for this entry."""
    try:
        rd = entry.runtime_data
    except AttributeError:
        return set()
    if not isinstance(rd, RuntimeData):
        return set()  # type: ignore[unreachable]

    identifiers: set[str] = set()
    for sid, site in (rd.sites or {}).items():
        for station_uuid, bucket in site.stations.items():
            serial: Any = bucket.charging_station_serial
            if not serial:
                station_client = bucket.station_client
                serial = getattr(station_client, "serial_id", None) or getattr(
                    station_client, "serial", None
                )
            if serial:
                control_sid = bucket.control_location_id or sid
                identifiers.add(f"station:{sid}:{control_sid}:{serial}")
                legacy_serial = getattr(bucket.station_client, "serial_id", None)
                if not isinstance(legacy_serial, str) or not legacy_serial.strip():
                    legacy_serial = serial
                identifiers.add(f"{sid}:{legacy_serial}:{station_uuid}")
    return identifiers


def _current_device_identifiers(entry: SmappeeEvConfigEntry) -> set[str]:
    """Return every device identifier in use: sites, stations, connectors and status."""
    identifiers = _current_station_device_identifiers(entry)
    if not identifiers:
        return identifiers
    rd = entry.runtime_data
    if rd.status_coordinator is not None:
        identifiers.add(rd.status_coordinator.unique_id_prefix)
    for sid, site in rd.sites.items():
        identifiers.add(site_device_identifier(sid)[1])
        for bucket in site.stations.values():
            serial = bucket.charging_station_serial
            if not serial:
                continue
            control_sid = bucket.control_location_id or sid
            for connector_uuid, info in bucket.connectors.items():
                connector_key, _label = _connector_device_key(connector_uuid, info)
                identifiers.add(
                    connector_device_identifier(sid, control_sid, serial, connector_key)[1]
                )
    return identifiers
