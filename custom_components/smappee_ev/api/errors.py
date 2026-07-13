"""Integration-specific API errors."""

from homeassistant.exceptions import ConfigEntryAuthFailed


class SmappeeError(Exception):
    """Base error for expected remote Smappee failures."""


class SmappeeAuthenticationError(ConfigEntryAuthFailed):
    """Smappee authentication failed."""


class SmappeeConnectionError(SmappeeError):
    """Smappee network or transport failed."""


class SmappeeProtocolError(SmappeeError):
    """Smappee returned malformed or unsupported data."""
