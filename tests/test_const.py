"""Contract tests for integration constants."""

from datetime import timedelta
import importlib

from homeassistant import const as ha_const


def test_const_values_are_stable():
    const = importlib.import_module("custom_components.smappee_ev.const")
    const = importlib.reload(const)

    assert const.DOMAIN == "smappee_ev"
    assert const.MANUFACTURER == "Smappee"
    assert const.CONFIGURATION_URL == "https://dashboard.smappee.net"
    assert const.CONF_USERNAME == ha_const.CONF_USERNAME
    assert const.CONF_PASSWORD == ha_const.CONF_PASSWORD
    assert const.CONF_DASHBOARD_REFRESH_TOKEN == "dashboard_refresh_token"  # noqa: S105
    assert const.CONF_DASHBOARD_TOKEN_EXPIRES_AT == "dashboard_token_expires_at"  # noqa: S105

    assert const.DEFAULT_MIN_CURRENT == 6
    assert const.DEFAULT_MAX_CURRENT == 32
    assert const.DEFAULT_LED_BRIGHTNESS == 70
    assert const.DEFAULT_MIN_SURPLUS_PERCENT == 100
    assert const.FULL_PERCENTAGE == 100
    assert const.CHARGING_MODES == ("standard", "smart", "solar")

    assert const.MQTT_HOST == "mqtt.smappee.net"
    assert const.MQTT_PORT_TLS == 443
    assert const.MQTT_QOS_AT_LEAST_ONCE == 1
    assert const.MQTT_TRACKING_TYPE_RT_VALUES == "RT_VALUES"
    assert const.MQTT_HEARTBEAT_TOPIC_SUFFIX == "/homeassistant/heartbeat"

    assert const.DASHAPI_URL == "https://dashboard.smappee.net/dashapi"
    assert const.DASHBOARD_API_URL == "https://dashboard.smappee.net/api"
    assert timedelta(minutes=30) == const.DASHBOARD_REFRESH_INTERVAL
    assert const.DASHBOARD_REFRESH_AFTER_WRITE_DELAY == 120

    assert const.HTTP_CONNECT_TIMEOUT == 5
    assert const.HTTP_TOTAL_TIMEOUT == 15
