"""Integration-specific API errors."""

from homeassistant.exceptions import ConfigEntryAuthFailed


class SmappeeError(Exception):
    """Base error for expected remote Smappee failures."""


class SmappeeMaintenanceError(SmappeeError):
    """Smappee explicitly reports Dashboard maintenance."""


class SmappeeAuthenticationError(ConfigEntryAuthFailed):
    """Smappee authentication failed."""


class SmappeeTransientError(SmappeeError):
    """Temporary transport or server failure eligible for MQTT fallback."""


class SmappeeConnectionError(SmappeeTransientError):
    """Smappee network or transport failed."""


class SmappeeServerError(SmappeeTransientError):
    """Smappee is temporarily unable to serve a request (HTTP 5xx)."""


class SmappeeProtocolError(SmappeeError):
    """Smappee returned malformed or unsupported data."""


class SmappeeRateLimitError(SmappeeTransientError):
    """Dashboard rate limit, including the minimum delay before another request."""

    def __init__(self, retry_after: float = 30.0) -> None:
        super().__init__("Dashboard rate limit reached (HTTP 429)")
        self.retry_after = retry_after
