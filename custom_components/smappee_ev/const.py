from typing import Final

DOMAIN = "smappee_ev"

UPDATE_INTERVAL_DEFAULT: Final = 30

# Config keys
CONF_CLIENT_ID: str = "client_id"
CONF_CLIENT_SECRET: str = "client_secret"  # noqa: S105 - config field name, not a secret
CONF_USERNAME: str = "username"
CONF_PASSWORD: str = "password"  # noqa: S105 - config field name, not a secret
CONF_SERIAL: str = "serial"
CONF_SERVICE_LOCATION_ID: str = "service_location_id"
CONF_SMART_DEVICE_UUID: str = "smart_device_uuid"
CONF_SMART_DEVICE_ID: str = "smart_device_id"
CONF_UPDATE_INTERVAL: str = "update_interval"

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
