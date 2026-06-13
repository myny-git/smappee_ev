from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api_client import SmappeeApiClient
from .base_entities import SmappeeStationRestEntity
from .const import DEFAULT_LED_BRIGHTNESS
from .coordinator import SmappeeCoordinator
from .data import IntegrationData, SmappeeEvConfigEntry

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV light entities."""
    runtime = config_entry.runtime_data
    sites = runtime.sites

    entities: list[LightEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            entities.append(
                SmappeeLedLight(
                    coordinator=bucket["coordinator"],
                    api_client=bucket["station_client"],
                    sid=sid,
                    station_uuid=st_uuid,
                )
            )

    async_add_entities(entities, False)


class SmappeeLedLight(SmappeeStationRestEntity, LightEntity):
    """Dimmable LED ring on the Smappee EV charger."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_icon = "mdi:led-on"

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeStationRestEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="light:led",
            name="LED",
        )
        self.api_client = api_client

    def _station_brightness(self) -> int | None:
        data: IntegrationData | None = self.coordinator.data
        if not data or not data.station or data.station.led_brightness is None:
            return None
        return max(0, min(100, int(data.station.led_brightness)))

    @property
    def is_on(self) -> bool | None:
        brightness = self._station_brightness()
        if brightness is None:
            return None
        return brightness > 0

    @property
    def brightness(self) -> int | None:
        brightness = self._station_brightness()
        if not brightness:
            return None
        return round(brightness / 100 * 255)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            brightness = int(round((int(kwargs[ATTR_BRIGHTNESS]) / 255) * 100))
        else:
            brightness = self._station_brightness() or DEFAULT_LED_BRIGHTNESS

        await self._set_brightness(max(1, min(100, brightness)))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_brightness(0)

    async def _set_brightness(self, brightness: int) -> None:
        await self.api_client.set_brightness(brightness)
        data: IntegrationData | None = self.coordinator.data
        if data and data.station:
            data.station.led_brightness = brightness
            self.coordinator.async_set_updated_data(data)
