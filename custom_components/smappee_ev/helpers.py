# custom_components/smappee_ev/helpers.py

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta
import logging
from typing import Any, Final, Literal

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONFIGURATION_URL, DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT, DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)

# Home Assistant's statistics treat a drop below 90% of the previous total as a reset.
TOTAL_RESET_RATIO: Final = 0.9
# Separate measurements that must report such a drop before it is shown.
TOTAL_RESET_CONFIRMATIONS: Final = 3


def charging_session_phase(
    session_state: str | None,
    status_current: str | None,
    session_cause: str | None,
) -> Literal["active", "paused", "finished"] | None:
    """Resolve explicit session signals in order of authority."""
    for value in (session_state, status_current, session_cause):
        state = (value or "").strip().upper()
        if state in {"STOPPED", "CHARGING_FINISHED", "FINISHED", "COMPLETED", "IDLE"}:
            return "finished"
        if state in {"STARTED", "CHARGING", "CHARGING_STARTED", "RUNNING"}:
            return "active"
        if state in {"SUSPENDED", "PAUSED"} or state.startswith("SUSPENDED_EVSE"):
            return "paused"
    return None


def charging_session_paused(
    raw_mode: str | None,
    session_state: str | None,
    session_cause: str | None,
    status_current: str | None = None,
    *,
    fallback: bool = False,
) -> bool:
    """Prefer session state, then status/cause, over a possibly stale mode."""
    phase = charging_session_phase(session_state, status_current, session_cause)
    if phase is not None:
        return phase == "paused"
    return (raw_mode or "").strip().upper() == "PAUSED" or fallback


def dashboard_property_value(prop: object) -> Any:
    """Return a scalar from Dashboard ``value`` or typed ``values`` payloads."""
    if not isinstance(prop, dict):
        return None

    if "value" in prop:
        raw = prop.get("value")
    else:
        values = prop.get("values")
        raw = values[0] if isinstance(values, list) and values else None

    if not isinstance(raw, dict):
        return raw

    quantity = raw.get("Quantity")
    if isinstance(quantity, dict):
        return quantity.get("value")
    if "Integer" in raw:
        return raw["Integer"]
    if "value" in raw:
        return raw["value"]
    return None


def dashboard_mode(mode: str | None) -> str | None:
    """Return a Dashboard v10 charging mode, accepting legacy/restored labels."""
    mode_up = str(mode or "").upper()
    if mode_up in {"STANDARD", "NORMAL"}:
        return "STANDARD"
    if mode_up in {"SMART", "SOLAR"}:
        return mode_up
    return None


def station_action_error(method_name: str, err: BaseException) -> HomeAssistantError:
    """Return a translated station action failure."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="station_service_failed",
        translation_placeholders={"method_name": method_name, "error": str(err)},
    )


def site_device_identifier(site_sid: int | str) -> tuple[str, str]:
    """Return the registry identifier for a site/gateway device."""
    return (DOMAIN, f"site:{site_sid}")


def station_device_identifier(
    site_sid: int | str,
    control_sid: int | str,
    charging_station_serial: str,
) -> tuple[str, str]:
    """Return the registry identifier for a charging station device."""
    return (DOMAIN, f"station:{site_sid}:{control_sid}:{charging_station_serial}")


def connector_device_identifier(
    site_sid: int | str,
    control_sid: int | str,
    charging_station_serial: str,
    connector_key: str,
) -> tuple[str, str]:
    """Return the registry identifier for a connector device."""
    return (
        DOMAIN,
        f"connector:{site_sid}:{control_sid}:{charging_station_serial}:{connector_key}",
    )


# Device metadata is written twice: by runtime device registration at setup and
# by every entity's device_info. Both use these functions, so neither overwrites
# the other with a different name or model.


def site_device_name(site_sid: int | str, site_name: str | None) -> str:
    """Return the device name of a site/service location."""
    return site_name or f"{MANUFACTURER} {site_sid}"


def site_device_model(gateway_type: str | None) -> str:
    """Return the device model of a site/service location."""
    return f"{gateway_type} / Service Location" if gateway_type else "Service Location"


def station_device_name(charging_station_serial: str, station_name: str | None) -> str:
    """Return the device name of a charging station."""
    return station_name or f"{MANUFACTURER} EV {charging_station_serial}"


def station_device_model(station_model: str | None) -> str:
    """Return the device model of a charging station."""
    return station_model or "EV Wall"


def connector_device_name(
    charging_station_serial: str, connector_label: str, station_name: str | None
) -> str:
    """Return the device name of a connector, prefixed by its station's name."""
    station = station_device_name(charging_station_serial, station_name)
    return f"{station} | Connector {connector_label}"


def make_site_device_info(
    site_sid: int | str,
    site_name: str | None,
    gateway_serial: str | None = None,
    gateway_type: str | None = None,
) -> DeviceInfo:
    """Return Home Assistant device_info for a site/service location."""
    name = site_device_name(site_sid, site_name)
    model = site_device_model(gateway_type)
    # Keyword construction works for both the TypedDict (HA < 2026.11) and the
    # dataclass; optional fields are passed only when known, never assigned later.
    if gateway_serial:
        return DeviceInfo(
            identifiers={site_device_identifier(site_sid)},
            name=name,
            manufacturer=MANUFACTURER,
            configuration_url=CONFIGURATION_URL,
            model=model,
            serial_number=gateway_serial,
        )
    return DeviceInfo(
        identifiers={site_device_identifier(site_sid)},
        name=name,
        manufacturer=MANUFACTURER,
        configuration_url=CONFIGURATION_URL,
        model=model,
    )


def make_station_device_info(
    site_sid: int | str,
    control_sid: int | str,
    charging_station_serial: str,
    *,
    station_name: str | None = None,
    station_model: str | None = None,
    legacy_identifier: str | None = None,
) -> DeviceInfo:
    """Return station metadata; runtime registration owns the device hierarchy."""
    identifiers = {station_device_identifier(site_sid, control_sid, charging_station_serial)}
    if legacy_identifier:
        identifiers.add((DOMAIN, legacy_identifier))
    return DeviceInfo(
        identifiers=identifiers,
        name=station_device_name(charging_station_serial, station_name),
        manufacturer=MANUFACTURER,
        configuration_url=CONFIGURATION_URL,
        model=station_device_model(station_model),
        serial_number=charging_station_serial,
    )


def make_connector_device_info(
    site_sid: int | str,
    control_sid: int | str,
    charging_station_serial: str,
    connector_key: str,
    connector_label: str | None = None,
    station_name: str | None = None,
) -> DeviceInfo:
    """Return connector metadata; runtime registration owns the device hierarchy."""
    label = connector_label or connector_key
    return DeviceInfo(
        identifiers={
            connector_device_identifier(
                site_sid, control_sid, charging_station_serial, connector_key
            )
        },
        name=connector_device_name(charging_station_serial, label, station_name),
        manufacturer=MANUFACTURER,
        configuration_url=CONFIGURATION_URL,
        model="Connector",
    )


def make_device_info(
    sid: int,
    serial: str,
    station_uuid: str,
    connector_label: str | None = None,
    *,
    scope: str = "station",
    site_name: str | None = None,
    gateway_serial: str | None = None,
    gateway_type: str | None = None,
    control_sid: int | str | None = None,
    charging_station_serial: str | None = None,
    station_name: str | None = None,
    station_model: str | None = None,
    connector_key: str | None = None,
) -> DeviceInfo:
    """Return a Home Assistant device_info dict for a station or connector."""
    if scope == "site":
        # The station serial is not the site's: registration leaves it empty too.
        return make_site_device_info(sid, site_name, gateway_serial, gateway_type)

    resolved_control_sid = control_sid or sid
    resolved_station_serial = charging_station_serial or serial
    legacy_identifier = f"{sid}:{serial}:{station_uuid}"

    if scope == "connector":
        resolved_connector_key = connector_key or connector_label or "unknown"
        return make_connector_device_info(
            sid,
            resolved_control_sid,
            resolved_station_serial,
            resolved_connector_key,
            connector_label,
            station_name,
        )

    return make_station_device_info(
        sid,
        resolved_control_sid,
        resolved_station_serial,
        station_name=station_name,
        station_model=station_model,
        legacy_identifier=legacy_identifier,
    )


def make_unique_id(
    sid: int,
    serial: str,
    station_uuid: str,
    connector_uuid: str | None,
    metric: str,
) -> str:
    """
    Generate a globally unique ID for any entity.

    Args:
        sid: service location ID
        serial: station serial
        station_uuid: UUID of the station
        connector_uuid: UUID of the connector (None for station-wide entities)
        metric: suffix for the entity type, e.g. "mqtt_connected", "charging_mode"
    """
    if connector_uuid:
        return f"{sid}:{serial}:{station_uuid}:{connector_uuid}:{metric}"
    return f"{sid}:{serial}:{station_uuid}:{metric}"


# ----------------------------------------------------------------------------------
# Additional helpers to reduce duplication across entity platforms
# ----------------------------------------------------------------------------------


def station_serial(coord: Any) -> str:
    """Return the station serial from a coordinator (fallback 'unknown')."""
    station_client = getattr(coord, "station_client", None)
    if station_client is None:
        return "unknown"
    return (
        getattr(station_client, "charging_station_serial", None)
        or getattr(station_client, "serial_id", None)
        or "unknown"
    )


def resolve_connector_current_range(
    *,
    previous_min: int | None,
    previous_max: int | None,
    reported_min: int | None,
    reported_max: int | None,
) -> tuple[int, int]:
    """Resolve external connector bounds without storing an invalid range.

    Missing reported bounds are combined with the last valid range. If the
    resulting pair is invalid, the last valid range is retained; when no valid
    previous pair exists, the integration defaults are used instead. A usable
    charging range must have a nonnegative minimum and a positive maximum,
    including fixed ranges.
    """
    if (
        previous_min is not None
        and previous_max is not None
        and previous_min >= 0
        and previous_max > 0
        and previous_max >= previous_min
    ):
        fallback_min, fallback_max = int(previous_min), int(previous_max)
    else:
        fallback_min, fallback_max = DEFAULT_MIN_CURRENT, DEFAULT_MAX_CURRENT

    candidate_min = fallback_min if reported_min is None else int(reported_min)
    candidate_max = fallback_max if reported_max is None else int(reported_max)
    if candidate_min >= 0 and candidate_max > 0 and candidate_max >= candidate_min:
        return candidate_min, candidate_max

    _LOGGER.debug(
        "Ignoring invalid connector current range %d-%d A; keeping valid range %d-%d A",
        candidate_min,
        candidate_max,
        fallback_min,
        fallback_max,
    )
    return fallback_min, fallback_max


def percentage_to_current(percentage: int, min_current: int, max_current: int) -> float:
    """Convert a Dashboard ``percentageLimit`` to Ampere within the connector range."""
    if max_current <= min_current:
        return float(min_current)
    rng = int(max_current) - int(min_current)
    return round((int(percentage) / 100.0) * rng + float(min_current), 1)


def current_to_percentage(current: float, min_current: int, max_current: int) -> int:
    """Convert Ampere to the nearest Dashboard ``percentageLimit`` in 0-100."""
    rng = max(int(max_current) - int(min_current), 1)
    pct = int(round((float(current) - float(min_current)) * 100.0 / rng))
    return max(0, min(100, pct))


def connector_percentage_setpoint(state: Any) -> int | None:
    """Return the connector's charging setpoint as a Dashboard percentage.

    Prefers the percentage the API reported and falls back to deriving it from
    the Ampere setpoint. Returns ``None`` when neither representation is known.
    """
    if state is None:
        return None
    pct = getattr(state, "selected_percentage_limit", None)
    if pct is not None:
        return max(0, min(100, int(pct)))
    current = getattr(state, "selected_current_limit", None)
    if current is None:
        return None
    return current_to_percentage(
        current,
        getattr(state, "min_current", DEFAULT_MIN_CURRENT),
        getattr(state, "max_current", DEFAULT_MAX_CURRENT),
    )


def build_connector_id(api_client: Any, connector_uuid: str) -> str:
    """Return a human friendly connector label (prefers numeric connector number)."""
    num = getattr(api_client, "connector_number", None)
    return str(num) if num is not None else str(connector_uuid[-4:])


def next_total_increasing(
    last: float | None,
    candidate: float | None,
    *,
    received: datetime | None,
    reset_evidence: frozenset[datetime],
) -> tuple[float | None, frozenset[datetime]]:
    """Return the total to expose and the updated evidence of a meter reset.

    Rules:
      * A missing candidate keeps the last total.
      * A candidate at or above the last total is accepted.
      * Zero, and a dip to 90% or more of the last total, keep the last total.
        Those are incomplete or corrected readings, not resets.
      * A drop below 90% is a meter reset, as Home Assistant's statistics define
        it. It is accepted once ``TOTAL_RESET_CONFIRMATIONS`` separate
        measurements (distinct receive times) have reported it without a normal
        reading in between. One glitch cannot reset the statistics, but a real
        reset is no longer ignored until the new meter passes the old total.
    """
    if candidate is None:
        return last, reset_evidence
    if last is None or candidate >= last:
        return candidate, frozenset()
    if candidate == 0 or candidate >= last * TOTAL_RESET_RATIO or received is None:
        return last, reset_evidence
    evidence = reset_evidence | {received}
    if len(evidence) < TOTAL_RESET_CONFIRMATIONS:
        return last, evidence
    return candidate, frozenset()


def safe_sum(values: object) -> float | None:
    """
    Best effort sum of an iterable of numeric-like values, returning float or None.

    Accepts any list/tuple of values coercible to float. Returns None if empty or any
    element cannot be converted.
    """
    if not isinstance(values, list | tuple) or not values:
        return None
    with suppress(TypeError, ValueError):
        return float(sum(float(v) for v in values))
    return None


__all__ = [
    "make_device_info",
    "make_site_device_info",
    "make_station_device_info",
    "make_connector_device_info",
    "make_unique_id",
    "station_serial",
    "next_total_increasing",
    "safe_sum",
]


def format_as_hms(td: timedelta) -> str:
    """
    Format a timedelta object into a human-readable HH:MM:SS string.

    This function calculates the total hours, minutes, and seconds from a
    given timedelta object and returns them as a zero-padded string,
    even if the duration exceeds 24 hours.

    Args:
        td (timedelta): The duration object to format.

    Returns:
        str: The formatted duration in HH:MM:SS format.
    """
    total_seconds = int(td.total_seconds())

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    return f"{hours:02}:{minutes:02}:{seconds:02}"


def anonymize_uuid(uuid: str | None) -> str:
    """Mask a UUID while keeping it consistent for debugging."""
    if uuid is None:
        return "none"
    if not uuid or len(uuid) < 8:
        return "****"

    return f"{uuid[:4]}****{uuid[-4:]}"
