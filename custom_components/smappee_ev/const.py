from typing import Final

DOMAIN = "smappee_ev"

# Coordinator polling interval (seconds) – kept internal, no user option anymore
UPDATE_INTERVAL_DEFAULT: Final = 30

# Common numeric defaults / ranges
DEFAULT_MIN_CURRENT: Final = 6
DEFAULT_MAX_CURRENT: Final = 32
DEFAULT_LED_BRIGHTNESS: Final = 70
DEFAULT_MIN_SURPLUS_PERCENT: Final = 100

# OAuth / auth related constants
OAUTH_EARLY_RENEW_SKEW: Final = 60  # seconds before expiry to refresh
OAUTH_MAX_REFRESH_ATTEMPTS: Final = 3
TOKEN_DEFAULT_EXPIRES_IN: Final = 3600

# Charging percentages
FULL_PERCENTAGE: Final = 100

# MQTT reconnect/backoff timings
MQTT_RECONNECT_INITIAL_BACKOFF: Final = 1.0
MQTT_RECONNECT_MAX_BACKOFF: Final = 60.0
MQTT_QOS_AT_LEAST_ONCE: Final = 1

# MQTT tracking / payload constants
MQTT_TRACKING_TYPE_RT_VALUES: Final = "RT_VALUES"
MQTT_HEARTBEAT_TOPIC_SUFFIX: Final = "/homeassistant/heartbeat"

# Config keys
CONF_CLIENT_ID: str = "client_id"
CONF_CLIENT_SECRET: str = "client_secret"  # noqa: S105 - config field name, not a secret
CONF_USERNAME: str = "username"
CONF_PASSWORD: str = "password"  # noqa: S105 - config field name, not a secret
CONF_SERIAL: str = "serial"
CONF_SERVICE_LOCATION_ID: str = "service_location_id"
CONF_SERVICE_LOCATION_UUID: str = "service_location_uuid"
CONF_SMART_DEVICE_UUID: str = "smart_device_uuid"
CONF_SMART_DEVICE_ID: str = "smart_device_id"

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

# MQTT for connect
MQTT_HOST = "mqtt.smappee.net"
MQTT_PORT_TLS = 443
MQTT_TRACK_INTERVAL_SEC = 60

# Shared HTTP timeout (aiohttp.ClientTimeout) parameters
HTTP_CONNECT_TIMEOUT: Final = 5
HTTP_TOTAL_TIMEOUT: Final = 15

# OAuth timeouts / retry constants
OAUTH_CONNECT_TIMEOUT: Final = 5
OAUTH_TOTAL_TIMEOUT: Final = 10
OAUTH_REFRESH_RETRY_BASE_DELAY: Final = 2  # seconds base (multiplied by attempt)
