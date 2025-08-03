import aiohttp
import logging
import random
import asyncio

from datetime import datetime, timedelta
from typing import Callable, Optional, Set
from .const import BASE_URL, UPDATE_INTERVAL_DEFAULT

_LOGGER = logging.getLogger(__name__)

class SmappeeApiClient:
    """Client to interact with the Smappee API."""

    def __init__(
        self, 
        oauth_client, 
        serial: str, 
        smart_device_uuid: str, 
        smart_device_id: str, 
        service_location_id: str,
        update_interval: Optional[int] = None,
    ):

        """Initialize the API client."""
        self.oauth_client = oauth_client
        self.serial = serial
        self.smart_device_uuid = smart_device_uuid
        self.smart_device_id = smart_device_id        
        self.service_location_id = service_location_id
        self.update_interval = update_interval if update_interval is not None else UPDATE_INTERVAL_DEFAULT

        self._callbacks: Set[Callable] = set()
        #self._loop = asyncio.get_event_loop()
        #self._latest_session_counter = 0
        self._session_state = "Initialize"
        self._timer = datetime.now() - timedelta(seconds=self.update_interval)
        self._set_mode_select_callback = None        
        self._charging_point_session_state = None
        self.led_brightness = 70
        self.min_current = 6  # default fallback
        self.max_current = 32  # default fallback
        self._value_callbacks = {} 

       
        _LOGGER.info(
            "SmappeeApiClient initialized for serial: %s with update interval: %s seconds",
            self.serial, self.update_interval
        )

    @property
    def serial_id(self) -> str:
        """Return the serial number (ID) for this Smappee device."""
        return self.serial

    def enable(self) -> None:
        """Enable the client (may trigger updates)."""
        #self._latest_session_counter = random.randint(1, 10)
        _LOGGER.info("SmappeeApiClient enabled for serial: %s", self.serial)
        asyncio.create_task(self.delayed_update())
        asyncio.create_task(self.polling_loop())

    async def polling_loop(self):
        while True:
            await self.delayed_update()
            await asyncio.sleep(self.update_interval)        
    
    async def delayed_update(self) -> None:
        """Refresh the session state and related info from the API."""
        _LOGGER.info("Performing delayed update...")
        await self.oauth_client.ensure_token_valid()

        # ----------sensor charging energy data is commented -----------
        #now = datetime.now()
        #startsession = int(datetime(now.year-1, 6, 1).timestamp())

        #url = f"{BASE_URL}/chargingstations/{self.serial}/sessions?active=false&range={startsession}"
        # ------------ TILL HERE ----------------------
        ## new API-call for a better up to date charging poitn session state
        url_session_state = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_id}"
        #url_all_devices = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices"

        headers = {
            "Authorization": f"Bearer {self.oauth_client.access_token}",
            "Content-Type": "application/json",
        }

        update_required = False
        
        try:
            async with aiohttp.ClientSession() as session:
                # ---------------  Charging session state (commented) ---------------------
                #resp = await session.get(url, headers=headers)
                #if response.status != 200:
                #    text = await resp.text()
                #    _LOGGER.error("Failed to get charging sessions: %s", text)
                #    raise Exception(f"Charging sessions error: {text}")
                #sessions = await response.json()

                #self._session_state = sessions[0].get("status", "unknown")
                #self._latest_session_counter = sessions[0].get("startReading", 0) + sessions[0].get("energy", 0)
                # ------------ TILL HERE ----------------------
                       
                # --- Charging session state ---
                resp_state = await session.get(url_session_state, headers=headers)
                if resp_state.status != 200:
                    text = await resp_state.text()
                    _LOGGER.error("Failed to get charging point session state: %s", text)
                    raise Exception(f"Charging point session state error: {text}")

                data = await resp_state.json()

                # --- chargingState & percentageLimit (from "properties") ---
                for prop in data.get("properties", []):
                    name = prop.get("spec", {}).get("name")
                    value = prop.get("value")

                    if name == "chargingState":
                        if value != self._session_state:
                            _LOGGER.debug("Charging session state changed: %s → %s", self._session_state, value)
                            self._session_state = value
                            update_required = True

                    elif name == "percentageLimit":
                        new_percentage = int(value)
                        if new_percentage != getattr(self, "selected_percentage_limit", None):
                            _LOGGER.debug("Percentage limit changed: %s → %s", self.selected_percentage_limit, new_percentage)
                            self.selected_percentage_limit = new_percentage
                            self.push_value_update("percentage_limit", new_percentage)
                            update_required = True

                # --- Configuration properties (e.g. max/min current) ---
                for prop in data.get("configurationProperties", []):
                    name = prop.get("spec", {}).get("name")
                    raw_value = prop.get("value")
                    value = raw_value.get("value") if isinstance(raw_value, dict) else raw_value

                    if name == "etc.smart.device.type.car.charger.config.max.current":
                        new_max = int(value)
                        if new_max != self.max_current:
                            _LOGGER.debug("Max current changed: %s → %s", self.max_current, new_max)
                            self.max_current = new_max
                            update_required = True

                    elif name == "etc.smart.device.type.car.charger.config.min.current":
                        new_min = int(value)
                        if new_min != self.min_current:
                            _LOGGER.debug("Min current changed: %s → %s", self.min_current, new_min)
                            self.min_current = new_min
                            update_required = True

                    elif name == "etc.smart.device.type.car.charger.led.config.brightness":
                        new_brightness = int(value)
                        if new_brightness != getattr(self, "led_brightness", 70):
                            _LOGGER.debug("LED brightness changed: %s → %s", self.led_brightness, new_brightness)
                            self.led_brightness = new_brightness
                            update_required = True

        except Exception as exc:
            _LOGGER.error("Exception during delayed_update: %s", exc)
            raise

        if update_required:
            await self.publish_updates()
            _LOGGER.info("Published updates to Home Assistant.")
        else:
            _LOGGER.debug("No update needed.")

        _LOGGER.info("Delayed update done.")

                # new_session_state = next(
                #     (prop.get("value") for prop in session_state_data.get("properties", [])
                #      if prop.get("spec", {}).get("name") == "chargingState"),
                #     "unknown"
                # )
                # if new_session_state != self._session_state:
                #     _LOGGER.debug("Charging session state changed: %s → %s", self._session_state, new_session_state)
                #     self._session_state = new_session_state
                #     update_required = True

                # --- Smart device configuration + properties ---
                # resp_devices = await session.get(url_all_devices, headers=headers)
                # if resp_devices.status == 200:
                #     _LOGGER.warning("Failed to fetch smartdevices: %s", resp_devices.status)
                #     return

                # data = await resp_devices.json()

                # for device in data:
                #      # --- Configuration properties: min/max current, brightness ---
                #     for prop in device.get("configurationProperties", []):
                #         spec = prop.get("spec", {})
                #         name = spec.get("name")
                #         raw_value = prop.get("value")
                #         value = raw_value.get("value") if isinstance(raw_value, dict) else raw_value

                #         if name == "etc.smart.device.type.car.charger.led.config.brightness":
                #             new_brightness = int(value)
                #             if new_brightness != getattr(self, "led_brightness", 70):
                #                 _LOGGER.debug("LED brightness changed: %s → %s", self.led_brightness, new_brightness)
                #                 self.led_brightness = new_brightness
                #                 update_required = True

                #         elif name == "etc.smart.device.type.car.charger.config.max.current":
                #             new_max = int(value)
                #             if new_max != self.max_current:
                #                 _LOGGER.debug("Max current changed: %s → %s", self.max_current, new_max)
                #                 self.max_current = new_max
                #                 update_required = True

                #         elif name == "etc.smart.device.type.car.charger.config.min.current":
                #             new_min = int(value)
                #             if new_min != self.min_current:
                #                 _LOGGER.debug("Min current changed: %s → %s", self.min_current, new_min)
                #                 self.min_current = new_min
                #                 update_required = True
                        
                #         # --- Properties: percentageLimit ---
                #         elif name == "percentageLimit":
                #             new_percentage = int(value)
                #             if new_percentage != getattr(self, "selected_percentage_limit", None):
                #                 _LOGGER.debug("Percentage limit changed: %s → %s", self.selected_percentage_limit, new_percentage)
                #                 self.selected_percentage_limit = new_percentage
                #                 self.push_value_update("percentage_limit", new_percentage)
                #                 update_required = True                                                

                    # for device in data:
                    #     for prop in device.get("configurationProperties", []):
                    #         spec = prop.get("spec", {})
                    #         if spec.get("name") == "etc.smart.device.type.car.charger.led.config.brightness":
                    #             new_brightness = int(prop.get("value", 70))
                    #             if new_brightness != getattr(self, "led_brightness", 70):
                    #                 _LOGGER.debug("LED brightness changed: %s → %s", self.led_brightness, new_brightness)
                    #                 self.led_brightness = new_brightness
                    #                 update_required = True
                    #             break
        #         else:
        #             _LOGGER.warning("Failed to fetch smartdevices: %s", resp_devices.status)
                
        # except Exception as exc:
        #     _LOGGER.error("Exception during delayed_update: %s", exc)
        #     raise
        
        # #await self.publish_updates()

        # if update_required:
        #     await self.publish_updates()
        #     _LOGGER.info("Published updates to Home Assistant.")
        # else:
        #     _LOGGER.debug("No update needed.")

        # _LOGGER.info("Delayed update done.")

    async def publish_updates(self) -> None:
        """Notify all registered callbacks of an update."""
        for callback in self._callbacks:
            callback()
            
    def set_mode_select_callback(self, callback: Callable) -> None:
        """Set a callback function for mode select changes."""
        self._set_mode_select_callback = callback

    def register_callback(self, callback: Callable) -> None:
        """Register a callback to notify on updates."""
        self._callbacks.add(callback)       

    def remove_callback(self, callback: Callable) -> None:
        """Remove a previously registered callback."""
        self._callbacks.discard(callback)

    def register_value_callback(self, key: str, callback: Callable[[int], None]) -> None:
        self._value_callbacks[key] = callback

    def push_value_update(self, key: str, value: int) -> None:
        if callback := self._value_callbacks.get(key):
            callback(value)

    # --- REACTIVATE IF NECESSARY ---
    #@property
    #def latest_session_counter(self) -> int:
    #    """Return latest session counter, triggers update if timer expired."""
    #    if self._timer + timedelta(seconds=self.update_interval) < datetime.now():
    #        self._timer = datetime.now()
    #        self._loop.create_task(self.delayed_update())
    #    return self._latest_session_counter
    # ------------------------------------------------------------------------
    @property
    def session_state(self) -> str:
        """Return current session state, triggers update if timer expired."""
        # if self._timer + timedelta(seconds=self.update_interval) < datetime.now():
        #     self._timer = datetime.now()
        #     self._loop.create_task(self.delayed_update())
        return self._session_state
  
    # --- API-calls (set_charging_mode, start/pause/stop charging, set_brightness, ...) ---

    async def set_charging_mode(self, mode: str, limit: Optional[int] = None) -> None:
        """Set the charging mode for the charger."""
        await self.oauth_client.ensure_token_valid()
        _LOGGER.debug("Setting charging mode: %s, limit: %s", mode, limit)

        if mode in ["SMART", "SOLAR"]:
            url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setChargingMode"
            payload = [{"spec": {"name": "mode", "species": "String"}, "value": mode}]
            async_method = "post"
        else:
            url = f"{BASE_URL}/chargingstations/{self.serial}/connectors/1/mode"
            if mode == "NORMAL_PERCENTAGE":
                payload = {"mode": "NORMAL", "limit": {"unit": "PERCENTAGE", "value": limit}}
            elif mode == "NORMAL":
                payload = {"mode": mode, "limit": {"unit": "AMPERE", "value": limit}}
            else:
                payload = {"mode": mode}
            async_method = "put"
        headers = {
            "Authorization": f"Bearer {self.oauth_client.access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                method = getattr(session, async_method)
                resp = await method(url, json=payload, headers=headers)
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error("Failed to set charging mode: %s", text)
                    raise Exception(f"Set charging mode error: {text}")
                _LOGGER.debug("Charging mode set successfully")
        except Exception as exc:
            _LOGGER.error("Exception in set_charging_mode: %s", exc)
            raise     

        if self._set_mode_select_callback:
            self._set_mode_select_callback(mode)

        if mode == "NORMAL" and limit is not None:
            self.selected_current_limit = limit
            self.push_value_update("current_limit", limit)

        elif mode == "NORMAL_PERCENTAGE" and limit is not None:
            self.selected_percentage_limit = limit
            self.push_value_update("percentage_limit", limit)              
       
    async def pause_charging(self) -> None:
        """Pause charging via the Smappee API."""
        await self.oauth_client.ensure_token_valid()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/pauseCharging"
        headers = {"Authorization": f"Bearer {self.oauth_client.access_token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=[], headers=headers)
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error("Failed to pause charging: %s", text)
                    raise Exception(f"Pause charging error: {text}")
                if self._set_mode_select_callback:
                    self._set_mode_select_callback("NORMAL")
                _LOGGER.debug("Paused charging successfully")
        except Exception as exc:
            _LOGGER.error("Exception in pause_charging: %s", exc)
            raise
            
    async def stop_charging(self) -> None:
        """Stop charging via the Smappee API."""
        await self.oauth_client.ensure_token_valid()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/stopCharging"
        headers = {"Authorization": f"Bearer {self.oauth_client.access_token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=[], headers=headers)
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error("Failed to stop charging: %s", text)
                    raise Exception(f"Stop charging error: {text}")
                _LOGGER.debug("Stopped charging successfully")
        except Exception as exc:
            _LOGGER.error("Exception in stop_charging: %s", exc)
            raise

    async def start_charging(self, percentage: int = 100) -> None:
        """Start charging via the Smappee API (optionally with percentage limit)."""
        await self.oauth_client.ensure_token_valid()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/startCharging"
        payload = [{
            "spec": {"name": "percentageLimit", "species": "Integer"},
            "value": percentage
        }]
        headers = {"Authorization": f"Bearer {self.oauth_client.access_token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=payload, headers=headers)
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error("Failed to start charging: %s", text)
                    raise Exception(f"Start charging error: {text}")
                _LOGGER.debug("Started charging successfully")
        except Exception as exc:
            _LOGGER.error("Exception in start_charging: %s", exc)
            raise     

        self.push_value_update("percentage_limit", percentage)
        
        if self._set_mode_select_callback:
            self._set_mode_select_callback("NORMAL_PERCENTAGE")        

    async def start_charging_current(self, current: int) -> None:
        """Start charging by specifying a current (in Amps).
        Internally converts to percentage and uses the existing start_charging() API.
        """
        # Make sure min/max current values are available
        if not hasattr(self, "min_current") or not hasattr(self, "max_current"):
            raise ValueError("min_current and max_current must be set before calling start_charging_current")

        # Validate that the input current is within the allowed range
        if current < self.min_current or current > self.max_current:
            raise ValueError(f"Current {current}A is outside allowed range: {self.min_current}–{self.max_current}A")

        # Convert current (A) to percentage for the API
        range_current = self.max_current - self.min_current
        percentage = round(((current - self.min_current) / range_current) * 100)

        _LOGGER.debug("Converted current %d A → %d %% for start_charging()", current, percentage)

        self.selected_current_limit = current
        self.selected_percentage_limit = percentage
        self.push_value_update("percentage_limit", percentage)

        # Use existing API call that supports percentage-based charging
        await self.start_charging(percentage)
     

    async def set_brightness(self, brightness: int) -> None:
        """Set LED brightness via the Smappee API."""
        await self.oauth_client.ensure_token_valid()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.serial}/actions/setBrightness"
        payload = [{
            "spec": {
                "name": "etc.smart.device.type.car.charger.led.config.brightness",
                "species": "Integer"
            },
            "value": brightness
        }]
        headers = {"Authorization": f"Bearer {self.oauth_client.access_token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=payload, headers=headers)
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error("Failed to set brightness: %s", text)
                    raise Exception(f"Set brightness error: {text}")
                _LOGGER.info("LED brightness set successfully to %d%%", brightness)
            
        except Exception as exc:
            _LOGGER.error("Exception in set_brightness: %s", exc)
            raise

    async def set_available(self) -> None:
        """Make charger available via the Smappee API."""
        await self.oauth_client.ensure_token_valid()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.serial}/actions/setAvailable"
        headers = {"Authorization": f"Bearer {self.oauth_client.access_token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=[], headers=headers)
                if resp.status != 0 and resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error("Failed to set available: %s", text)
                    raise Exception(f"Set available error: {text}")
                _LOGGER.debug("Set charger available successfully")
        except Exception as exc:
            _LOGGER.error("Exception in set_available: %s", exc)
            raise

    async def set_unavailable(self) -> None:
        """Make charger unavailable via the Smappee API."""
        await self.oauth_client.ensure_token_valid()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.serial}/actions/setUnavailable"
        headers = {"Authorization": f"Bearer {self.oauth_client.access_token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=[], headers=headers)
                if resp.status != 0 and resp.status != 200:
                    text = await resp.text()
                    _LOGGER.error("Failed to set unavailable: %s", text)
                    raise Exception(f"Set unavailable error: {text}")
                _LOGGER.debug("Set charger unavailable successfully")
        except Exception as exc:
            _LOGGER.error("Exception in set_unavailable: %s", exc)
            raise


