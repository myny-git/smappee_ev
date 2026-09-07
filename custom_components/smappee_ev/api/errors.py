"""Integration-specific API errors."""

from homeassistant.exceptions import ConfigEntryAuthFailed


class SmappeeError(Exception):
    """Base error for expected remote Smappee failures."""


class SmappeeMaintenanceError(SmappeeError):
    """Smappee explicitly reports Dashboard maintenance."""


class SmappeeAuthenticationError(ConfigEntryAuthFailed):
    """Smappee authentication failed."""


class SmappeeConnectionError(SmappeeError):
    """Smappee network or transport failed."""


class SmappeeServerError(SmappeeError):
    """Smappee is temporarily unable to serve a request (HTTP 5xx)."""


class SmappeeProtocolError(SmappeeError):
    """Smappee returned malformed or unsupported data."""
