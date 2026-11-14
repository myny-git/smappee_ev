"""Device-registry helpers for Smappee EV runtime data."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONFIGURATION_URL, DOMAIN, MANUFACTURER
from .helpers import (
    build_connector_id,
    connector_device_identifier,
    connector_device_name,
    site_device_identifier,
    site_device_model,
    site_device_name,
    station_device_identifier,
    station_device_model,
    station_device_name,
)
from .models.runtime_data import RuntimeData, SmappeeConnectorRuntime, SmappeeEvConfigEntry


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
        registry.async_remove_device(device.id)


def _register_runtime_devices(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> None:
    """Ensure the registry contains the real Smappee hierarchy."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        return
    registry = dr.async_get(hass)
    _remove_legacy_led_controller_devices(registry, entry)
    rd = entry.runtime_data
    for site_sid, site in (rd.sites or {}).items():
        site_identifier = site_device_identifier(site_sid)
        gateway_serial = site.gateway_serial
        site_device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={site_identifier},
            manufacturer=MANUFACTURER,
            configuration_url=CONFIGURATION_URL,
            name=site_device_name(site_sid, site.site_name),
            model=site_device_model(site.gateway_type),
            serial_number=str(gateway_serial) if gateway_serial else None,
        )

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
                configuration_url=CONFIGURATION_URL,
                name=station_device_name(station_serial, bucket.station_name),
                model=station_device_model(bucket.charging_station_model),
                serial_number=str(station_serial),
                via_device_id=site_device.id,
            )

            for connector_uuid, info in bucket.connectors.items():
                connector_key, label = _connector_device_key(connector_uuid, info)
                if connector_uuid:
                    # Entities label a connector by its number, else its UUID tail.
                    label = build_connector_id(info.connector_client, connector_uuid)
                registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={
                        connector_device_identifier(
                            site_sid, control_sid, station_serial, connector_key
                        )
                    },
                    manufacturer=MANUFACTURER,
                    configuration_url=CONFIGURATION_URL,
                    name=connector_device_name(station_serial, label, bucket.station_name),
                    model="Connector",
                    via_device_id=station_device.id,
                )


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
