"""Charging-state presentation through HA translations, MQTT and entry reload."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
import pytest

from tests import test_mqtt_bootstrap as bootstrap, test_recovery_integration as recovery

entry = bootstrap.entry
recovery_dashboard = recovery.recovery_dashboard

CHARGING_STATE_TOPIC = (
    "servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/devices/"
    "connector-uuid-1/property/chargingstate"
)
SMART_PAYLOAD = {
    "available": True,
    "percentageLimit": 0,
    "chargingState": "SMART",
    "chargingMode": "SMART",
    "optimizationStrategy": "SCHEDULES_FIRST_THEN_EXCESS",
    "iecStatus": {"current": "A2"},
    "status": {"current": "AVAILABLE", "stoppedByCloud": False, "errors": []},
}


@pytest.mark.parametrize("language", ["en", "nl"])
async def test_smart_charging_state_translation(hass, enable_custom_integrations, language):
    """HA loads the display label from the bundled language files."""
    translations = await async_get_translations(hass, language, "entity", {"smappee_ev"})
    assert translations["component.smappee_ev.entity.sensor.charging_state.state.smart"] == "Smart"

    source = Path(__file__).parents[1] / "custom_components/smappee_ev/strings.json"
    strings = json.loads(source.read_text(encoding="utf-8"))
    assert strings["entity"]["sensor"]["charging_state"]["state"]["smart"] == "Smart"


async def test_smart_charging_state_updates_after_mqtt_and_entry_reload(
    hass, entry, recovery_dashboard, enable_custom_integrations
):
    """A new entity after real HA unload/setup accepts SMART again on MQTT."""
    recovery_dashboard.async_get_recent_sessions = AsyncMock(return_value=[])
    with (
        patch(
            "custom_components.smappee_ev._create_dashboard_client", return_value=recovery_dashboard
        ),
        patch("custom_components.smappee_ev.api.mqtt_gateway.SmappeeMqtt.start", new=AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        entity_id = next(
            item.entity_id
            for item in er.async_entries_for_config_entry(registry, entry.entry_id)
            if item.translation_key == "charging_state"
        )
        unique_id = registry.async_get(entity_id).unique_id
        old_runtime = entry.runtime_data

        for reload_entry in (False, True):
            if reload_entry:
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                assert old_runtime.stopping
                assert entry.runtime_data is not old_runtime
                assert registry.async_get(entity_id).unique_id == unique_id

            mqtt = entry.runtime_data.mqtt[317418][1]
            mqtt._on_conn(True)
            for payload, expected in (
                (SMART_PAYLOAD, "smart"),
                (SMART_PAYLOAD, "smart"),
                ({"chargingState": "FUTURE_FIRMWARE_STATUS"}, "unknown"),
                ({"chargingState": "Started"}, "started"),
                (SMART_PAYLOAD, "smart"),
            ):
                mqtt._on_properties(CHARGING_STATE_TOPIC, payload)
                await hass.async_block_till_done()
                state = hass.states.get(entity_id)
                assert state.state == expected
                assert state.attributes["raw_status"] == payload["chargingState"]

        assert await hass.config_entries.async_unload(entry.entry_id)
