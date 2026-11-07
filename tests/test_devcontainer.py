"""An image refresh may only replace this workspace's outdated stopped container."""

import importlib.util
import json
from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def initializer():
    script = Path(__file__).parents[1] / ".devcontainer" / "initialize.py"
    spec = importlib.util.spec_from_file_location("devcontainer_initialize", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refresh_replaces_only_own_outdated_stopped_container(initializer, tmp_path, monkeypatch):
    workspace = tmp_path.resolve()
    labels = {
        "devcontainer.local_folder": str(workspace),
        "devcontainer.config_file": str(workspace / ".devcontainer" / "devcontainer.json"),
    }

    def container(key, *, running=False, image="old", own_labels=None):
        return {
            "Id": key,
            "Image": image,
            "State": {"Running": running},
            "Config": {"Labels": labels if own_labels is None else own_labels},
        }

    containers = {
        "old-stopped": container("old-stopped"),
        "old-running": container("old-running", running=True),
        "current": container("current", image="latest"),
        "other-workspace": container(
            "other-workspace",
            own_labels={**labels, "devcontainer.local_folder": str(workspace / "another-project")},
        ),
        "other-config": container(
            "other-config",
            own_labels={
                **labels,
                "devcontainer.config_file": str(workspace / "another-config.json"),
            },
        ),
    }

    def command(*args, capture=True):
        if args[0] == "pull":
            return ""
        if args[:2] == ("image", "inspect"):
            return json.dumps(
                [{"Id": "latest", "Config": {"Labels": {"io.hass.version": "2026.11.0"}}}]
            )
        if args[0] == "ps":
            return "\n".join(containers)
        if args[0] == "inspect":
            return json.dumps([containers[args[1]]])
        if args[0] == "rm":
            return args[1]
        pytest.fail(f"Unexpected docker command: {args}")

    docker = MagicMock(side_effect=command)
    monkeypatch.setattr(initializer, "docker", docker)
    initializer.refresh(workspace)
    docker.assert_any_call("pull", initializer.IMAGE, capture=False)
    assert [call.args for call in docker.call_args_list if call.args[0] == "rm"] == [
        ("rm", "old-stopped")
    ]


def test_offline_refresh_keeps_cached_containers(initializer, tmp_path, monkeypatch):
    docker = MagicMock(side_effect=subprocess.CalledProcessError(1, ["docker", "pull"]))
    monkeypatch.setattr(initializer, "docker", docker)
    initializer.refresh(tmp_path)
    docker.assert_called_once_with("pull", initializer.IMAGE, capture=False)
