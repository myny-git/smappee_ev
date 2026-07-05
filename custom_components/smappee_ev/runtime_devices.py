"""Device-registry helpers for Smappee EV runtime data."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN, MANUFACTURER
from .helpers import connector_device_identifier, site_device_identifier, station_device_identifier
from .models.runtime_data import RuntimeData, SmappeeEvConfigEntry


def _remove_legacy_led_controller_devices(
    registry,
    entry: SmappeeEvConfigEntry,
) -> None:
    """Remove old standalone LED Controller devices for this config entry."""
    from . import dr

    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not any(
            domain == DOMAIN and identifier.startswith("led:")
            for domain, identifier in device.identifiers
        ):
            continue

        if device.config_entries == {entry.entry_id}:
            registry.async_remove_device(device.id)
        else:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


def _register_runtime_devices(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> None:
    """Ensure the HA device registry contains the real Smappee hierarchy."""
    from . import _remove_legacy_led_controller_devices, dr

    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        return
    registry = dr.async_get(hass)
    _remove_legacy_led_controller_devices(registry, entry)
    rd = entry.runtime_data
    for site_sid, site in (rd.sites or {}).items():
        site_identifier = site_device_identifier(site_sid)
        site_name = site.site_name or f"Smappee {site_sid}"
        gateway_serial = site.gateway_serial
        gateway_type = site.gateway_type
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={site_identifier},
            manufacturer=MANUFACTURER,
            name=f"Smappee {site_name}",
            model=f"{gateway_type} / Service Location" if gateway_type else "Service Location",
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
            registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers=station_identifiers,
                manufacturer=MANUFACTURER,
                name=bucket.station_name or f"Smappee EV {station_serial}",
                model=bucket.charging_station_model or "EV Wall",
                serial_number=str(station_serial),
                via_device=site_identifier,
            )

            for connector_uuid, info in bucket.connectors.items():
                client = info.connector_client
                position = info.connector_position or getattr(client, "connector_number", None)
                connector_key = connector_uuid or (
                    f"position:{position}" if position else "unknown"
                )
                label = str(position) if position is not None else str(connector_key)
                registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={
                        connector_device_identifier(
                            site_sid, control_sid, station_serial, str(connector_key)
                        )
                    },
                    manufacturer=MANUFACTURER,
                    name=f"Smappee EV {station_serial} | Connector {label}",
                    model="Connector",
                    via_device=station_identifier,
                )


def _current_station_device_identifiers(entry: SmappeeEvConfigEntry) -> set[str]:
    """Return Smappee EV device identifiers currently known for this entry."""
    try:
        rd = entry.runtime_data
    except AttributeError:
        return set()
    if not isinstance(rd, RuntimeData):
        return set()

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
