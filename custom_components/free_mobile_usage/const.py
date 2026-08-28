"""Constants for Free Mobile Usage."""

from datetime import timedelta

DOMAIN = "free_mobile_usage"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TRUSTED_UUID = "trusted_uuid"

DEFAULT_SCAN_INTERVAL = timedelta(hours=2)
