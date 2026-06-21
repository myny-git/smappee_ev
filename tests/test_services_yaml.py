from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.services import register_services


class _RecordingServices:
    def __init__(self):
        self.registered = set()

    def async_register(self, domain, service, handler, schema=None):
        self.registered.add((domain, service))


@pytest.mark.asyncio
async def test_services_yaml_matches_registered_services():
    yaml_path = Path(__file__).parents[1] / "custom_components" / DOMAIN / "services.yaml"
    yaml_services = set(yaml.safe_load(yaml_path.read_text(encoding="utf-8")))
    recording_services = _RecordingServices()
    hass = SimpleNamespace(services=recording_services)

    await register_services(hass)

    registered_services = {
        service for domain, service in recording_services.registered if domain == DOMAIN
    }
    assert registered_services == yaml_services
