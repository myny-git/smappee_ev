"""Rejected credentials during an entity action start reauthentication."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
import pytest

from custom_components.smappee_ev import switch
from custom_components.smappee_ev.coordinator import SmappeeCoordinator


def _charging_switch(platform):
    api_client = MagicMock()
    api_client.set_charging_mode = AsyncMock(side_effect=ConfigEntryAuthFailed("rejected"))
    with patch(
        "custom_components.smappee_ev.helpers.build_connector_label",
        return_value="Connector 1",
    ):
        entity = switch.SmappeeChargingSwitch(
            coordinator=MagicMock(spec=SmappeeCoordinator),
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
            connector_uuid="connector_uuid1",
        )
    entity.hass = MagicMock()
    entity.platform = platform
    entity.parallel_updates = None
    return entity


@pytest.mark.asyncio
async def test_entity_action_auth_failure_starts_reauth_for_its_entry():
    """Home Assistant only reacts to ConfigEntryAuthFailed from setup or a coordinator."""
    entry = MagicMock()
    entity = _charging_switch(MagicMock(config_entry=entry))

    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_request_call(entity.async_turn_on())

    assert not isinstance(caught.value, ConfigEntryAuthFailed)
    assert caught.value.translation_key == "reauth_required"
    assert isinstance(caught.value.__cause__, ConfigEntryAuthFailed)
    entry.async_start_reauth_if_available.assert_called_once_with(entity.hass)


@pytest.mark.asyncio
async def test_entity_action_auth_failure_without_platform_still_explains():
    entity = _charging_switch(None)

    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_request_call(entity.async_turn_on())

    assert caught.value.translation_key == "reauth_required"


@pytest.mark.asyncio
async def test_entity_action_other_errors_pass_unchanged():
    entity = _charging_switch(MagicMock())

    async def fails():
        raise HomeAssistantError("other")

    with pytest.raises(HomeAssistantError, match="other"):
        await entity.async_request_call(fails())
    entity.platform.config_entry.async_start_reauth_if_available.assert_not_called()
