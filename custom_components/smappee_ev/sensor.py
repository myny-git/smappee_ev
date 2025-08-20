from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV sensors from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SmappeeCoordinator = data["coordinator"]
    connector_clients: dict[str, SmappeeApiClient] = data["connector_clients"]

    entities: list[SensorEntity] = []
    for uuid, client in connector_clients.items():
        entities.append(
            SmappeeChargingStateSensor(coordinator=coordinator, api_client=client, uuid=uuid)
        )
        entities.append(
            SmappeeEVCCStateSensor(coordinator=coordinator, api_client=client, uuid=uuid)
        )
        entities.append(
            SmappeeEvseStatusSensor(coordinator=coordinator, api_client=client, uuid=uuid)
        )

    async_add_entities(entities, update_before_add=True)


class _Base(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    """Base class for Smappee EV sensors."""

    _attr_should_poll = False  # Event-driven, no polling

    def __init__(
        self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client  # kept only for device_info
        self._uuid = uuid

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._uuid) if data else None


class SmappeeChargingStateSensor(_Base):
    """Raw charging/session state reported by the connector."""

    def __init__(
        self, *, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str
    ) -> None:
        super().__init__(coordinator=coordinator, api_client=api_client, uuid=uuid)
        self._attr_name = f"Charging state {api_client.connector_number}"
        self._attr_unique_id = (
            f"{api_client.serial_id}_connector{api_client.connector_number}_charging_state"
        )
        self._attr_icon = "mdi:ev-station"

    @property
    def native_value(self):
        st = self._state()
        return st.session_state if st else None


class SmappeeEVCCStateSensor(_Base, RestoreEntity):
    """EVCC A/B/C/E mapping derived from the session state."""

    def __init__(
        self, *, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str
    ) -> None:
        super().__init__(coordinator=coordinator, api_client=api_client, uuid=uuid)
        self._attr_name = f"EVCC state {api_client.connector_number}"
        self._attr_unique_id = (
            f"{api_client.serial_id}_connector{api_client.connector_number}_evcc_state"
        )
        self._attr_icon = "mdi:car-electric"
        self._restored: str | None = None

    @property
    def native_value(self):
        st = self._state()
        # 1) live from coordinator
        if st and st.evcc_state:
            return st.evcc_state
        # 2) fallback: last known value
        return self._restored

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state and last.state not in ("unknown", "unavailable"):
            self._restored = last.state

    @property
    def extra_state_attributes(self):
        st = self._state()
        if not st:
            return None
        return {
            "iec_status": st.iec_status,  # example C2
            "session_state": st.session_state,  # STARTED/STOPPED/...
            "charging_mode": st.raw_charging_mode,  # NORMAL/SMART/PAUSED
            "optimization_strategy": st.optimization_strategy,
            "paused": st.paused,
            "status_current": st.session_cause,  # AP-status
        }


class SmappeeEvseStatusSensor(_Base):
    """Smappee Dashboard connector status."""

    def __init__(
        self, *, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str
    ) -> None:
        super().__init__(coordinator=coordinator, api_client=api_client, uuid=uuid)
        self._attr_name = f"EVSE status {api_client.connector_number}"
        self._attr_unique_id = (
            f"{api_client.serial_id}_connector{api_client.connector_number}_evse_status"
        )
        self._attr_icon = "mdi:information-outline"

    @property
    def native_value(self):
        st = self._state()
        return st.status_current if st else None
