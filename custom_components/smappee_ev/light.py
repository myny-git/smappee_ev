from __future__ import annotations

from typing import Any, cast

from aiohttp import ClientError
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entities import SmappeeLedEntity
from .const import DEFAULT_LED_BRIGHTNESS, DOMAIN
from .coordinator import SmappeeCoordinator
from .data import IntegrationData, SmappeeEvConfigEntry
from .device_handle import SmappeeDeviceHandle

PARALLEL_UPDATES = 1


def _station_action_error(method_name: str, err: BaseException) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="station_service_failed",
        translation_placeholders={"method_name": method_name, "error": str(err)},
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV light entities."""
    runtime = config_entry.runtime_data

    entities: list[LightEntity] = []
    for sid, site in (runtime.sites or {}).items():
        sid_int = int(sid)
        for st_uuid, bucket in site.stations.items():
            coord = cast(SmappeeCoordinator | None, bucket.station_coordinator)
            st_client = cast(SmappeeDeviceHandle | None, bucket.station_client)
            if coord is None or st_client is None:
                continue
            if bucket.connectors:
                led_key, led_info = next(iter(bucket.led_devices.items()), (None, None))
                entities.append(
                    SmappeeLedLight(
                        coordinator=coord,
                        api_client=st_client,
                        sid=sid_int,
                        station_uuid=st_uuid,
                        led_device_id=led_key,
                        led_name=led_info.led_device_name if led_info else None,
                    )
                )

    async_add_entities(entities, False)


class SmappeeLedLight(SmappeeLedEntity, LightEntity):
    """Dimmable LED ring on the Smappee EV charger."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_icon = "mdi:led-on"
    _attr_translation_key = "led"

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        led_device_id: str | None = None,
        led_name: str | None = None,
    ) -> None:
        SmappeeLedEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="light:led",
            led_device_id=led_device_id,
            led_name=led_name,
        )
        self.api_client = api_client
        self._last_nonzero_brightness: int | None = None

    def _station_brightness(self) -> int | None:
        data: IntegrationData | None = self.coordinator.data
        if not data or not data.station or data.station.led_brightness is None:
            return None
        brightness = max(0, min(100, int(data.station.led_brightness)))
        if brightness > 0:
            self._last_nonzero_brightness = brightness
        return brightness

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
            current = self._station_brightness()
            brightness = (
                current
                if current and current > 0
                else self._last_nonzero_brightness or DEFAULT_LED_BRIGHTNESS
            )

        await self._set_brightness(max(1, min(100, brightness)))

    async def async_turn_off(self, **kwargs: Any) -> None:
        brightness = self._station_brightness()
        if brightness and brightness > 0:
            self._last_nonzero_brightness = brightness
        await self._set_brightness(0)

    async def _set_brightness(self, brightness: int) -> None:
        try:
            await self.api_client.set_brightness(brightness)
        except (ClientError, TimeoutError, RuntimeError, ValueError) as err:
            raise _station_action_error("set_brightness", err) from err
        if brightness > 0:
            self._last_nonzero_brightness = brightness
        data: IntegrationData | None = self.coordinator.data
        if data and data.station:
            data.station.led_brightness = brightness
            self.coordinator.async_set_updated_data(data)
        self.coordinator.async_schedule_dashboard_refresh()
