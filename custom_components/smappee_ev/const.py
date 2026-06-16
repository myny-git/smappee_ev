from datetime import timedelta
from typing import Final

from homeassistant import const as ha_const

DOMAIN = "smappee_ev"
MANUFACTURER = "Smappee"
CONFIGURATION_URL: Final = "https://dashboard.smappee.net"

ATTRIBUTION: Final = f"Data provided by {MANUFACTURER}"

CONF_NEEDS_DASHBOARD_REAUTH = "needs_dashboard_reauth"

# Coordinator polling interval (seconds) – kept internal, no user option anymore
UPDATE_INTERVAL_DEFAULT: Final = 30

# Common numeric defaults / ranges
DEFAULT_MIN_CURRENT: Final = 6
DEFAULT_MAX_CURRENT: Final = 32
DEFAULT_LED_BRIGHTNESS: Final = 70
DEFAULT_MIN_SURPLUS_PERCENT: Final = 100

# Charging percentages
FULL_PERCENTAGE: Final = 100
CHARGING_MODES: Final = ("standard", "smart", "solar")

# MQTT reconnect/backoff timings
MQTT_RECONNECT_INITIAL_BACKOFF: Final = 1.0
MQTT_RECONNECT_MAX_BACKOFF: Final = 60.0
MQTT_QOS_AT_LEAST_ONCE: Final = 1

# MQTT tracking / payload constants
MQTT_TRACKING_TYPE_RT_VALUES: Final = "RT_VALUES"
MQTT_HEARTBEAT_TOPIC_SUFFIX: Final = "/homeassistant/heartbeat"

# Config keys
CONF_USERNAME: Final = ha_const.CONF_USERNAME
CONF_PASSWORD: Final = ha_const.CONF_PASSWORD
CONF_SERVICE_LOCATION_ID: str = "service_location_id"
CONF_SERVICE_LOCATION_UUID: str = "service_location_uuid"
CONF_SMART_DEVICE_UUID: str = "smart_device_uuid"
CONF_SMART_DEVICE_ID: str = "smart_device_id"
CONF_DASHBOARD_REFRESH_TOKEN: str = "dashboard_refresh_token"  # noqa: S105
CONF_DASHBOARD_TOKEN_EXPIRES_AT: str = "dashboard_token_expires_at"  # noqa: S105

# Service names
SERVICE_SET_CHARGING_MODE = "set_charging_mode"
SERVICE_PAUSE_CHARGING = "pause_charging"
SERVICE_STOP_CHARGING = "stop_charging"
SERVICE_START_CHARGING = "start_charging"
SERVICE_SET_AVAILABLE = "set_available"
SERVICE_SET_UNAVAILABLE = "set_unavailable"
SERVICE_RELOAD = "reload"

# Base URLs
DASHAPI_URL = "https://dashboard.smappee.net/dashapi"
DASHBOARD_API_URL = "https://dashboard.smappee.net/api"
DASHBOARD_REFRESH_INTERVAL: Final = timedelta(minutes=30)
DASHBOARD_REFRESH_AFTER_WRITE_DELAY: Final = 2 * 60

# MQTT for connect
MQTT_HOST = "mqtt.smappee.net"
MQTT_PORT_TLS = 443
MQTT_TRACK_INTERVAL_SEC = 60

# Shared HTTP timeout (aiohttp.ClientTimeout) parameters
HTTP_CONNECT_TIMEOUT: Final = 5
HTTP_TOTAL_TIMEOUT: Final = 15
