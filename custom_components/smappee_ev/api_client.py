import aiohttp
import logging
import random
import asyncio
import aiohttp

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
        self._loop = asyncio.get_event_loop()
        #self._latest_session_counter = 0
        self._session_state = "Initialize"
        self._timer = datetime.now() - timedelta(seconds=self.update_interval)
        self._set_mode_select_callback = None        
        self._charging_point_session_state = None
        
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
        self._loop.create_task(self.delayed_update())
    
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

        headers = {
            "Authorization": f"Bearer {self.oauth_client.access_token}",
            "Content-Type": "application/json",
        }
        
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
                       
                # Device session state
                resp_state = await session.get(url_session_state, headers=headers)
                if resp_state.status != 200:
                    text = await resp_state.text()
                    _LOGGER.error("Failed to get charging point session state: %s", text)
                    raise Exception(f"Charging point session state error: {text}")

                # retrieve propoerties and search for chargingState
                session_state_data = await resp_state.json() 
                self._session_state = next(
                    (prop.get("value") for prop in session_state_data.get("properties", [])
                     if prop.get("spec", {}).get("name") == "chargingState"),
                    "unknown"
                )
                _LOGGER.debug("Charging point session state updated: %s", self._session_state)
                
        except Exception as exc:
            _LOGGER.error("Exception during delayed_update: %s", exc)
            raise
        
        await self.publish_updates()
        _LOGGER.info("Delayed update done.")

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
        if self._timer + timedelta(seconds=self.update_interval) < datetime.now():
            self._timer = datetime.now()
            self._loop.create_task(self.delayed_update())
        return self._session_state
  
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
                response = await method(url, json=payload, headers=headers)
                if response.status != 200:
                    text = await response.text()
                    _LOGGER.error("Failed to set charging mode: %s", text)
                    raise Exception(f"Set charging mode error: {text}")
                _LOGGER.debug("Charging mode set successfully")
        except Exception as exc:
            _LOGGER.error("Exception in set_charging_mode: %s", exc)
            raise       
       
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
                _LOGGER.debug("Changed brightness successfully")
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


