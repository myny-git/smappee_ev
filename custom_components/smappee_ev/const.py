from typing import Final

DOMAIN = "smappee_ev"

UPDATE_INTERVAL_DEFAULT: Final = 30 

# Config keys
CONF_CLIENT_ID: "client_id"
CONF_CLIENT_SECRET: "client_secret"
CONF_USERNAME: "username"
CONF_PASSWORD: "password"
CONF_SERIAL: "serial"
CONF_SERVICE_LOCATION_ID = "service_location_id"      
CONF_SMART_DEVICE_UUID = "smart_device_uuid"              
CONF_SMART_DEVICE_ID = "smart_device_id"   
CONF_UPDATE_INTERVAL = "update_interval"

# Service names
SERVICE_SET_CHARGING_MODE = "set_charging_mode"
SERVICE_PAUSE_CHARGING = "pause_charging"
SERVICE_STOP_CHARGING = "stop_charging"
SERVICE_START_CHARGING = "start_charging"
SERVICE_SET_BRIGHTNESS = "set_brightness"
SERVICE_SET_AVAILABLE = "set_available"
SERVICE_SET_UNAVAILABLE = "set_unavailable"
SERVICE_RELOAD = "reload"

# Base URL of the API
BASE_URL = "https://app1pub.smappee.net/dev/v3"