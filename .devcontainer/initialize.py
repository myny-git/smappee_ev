"""Refresh Home Assistant before opening a devcontainer, preserving active sessions."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys

IMAGE = "ghcr.io/home-assistant/home-assistant:stable"
_LOGGER = logging.getLogger(__name__)


def docker(*args: str, capture: bool = True) -> str:
    executable = shutil.which("docker")
    if executable is None:
        raise RuntimeError("Docker is required to open the Home Assistant devcontainer")
    result = subprocess.run(  # noqa: S603 - fixed executable from PATH, arguments without a shell
        [executable, *args],
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def same_path(actual: str, expected: Path) -> bool:
    return os.path.normcase(os.path.normpath(actual)) == os.path.normcase(str(expected))


def refresh(workspace: Path) -> None:
    workspace = workspace.resolve()
    config = workspace / ".devcontainer" / "devcontainer.json"
    try:
        docker("pull", IMAGE, capture=False)
    except subprocess.CalledProcessError:
        # Opening the existing environment must remain possible while offline.
        _LOGGER.warning(
            "Could not refresh Home Assistant stable; keeping the cached image and containers."
        )
        return

    latest = json.loads(docker("image", "inspect", IMAGE))[0]
    version = latest.get("Config", {}).get("Labels", {}).get("io.hass.version", "stable")
    _LOGGER.info("Home Assistant stable: %s", version)
    candidates = docker(
        "ps",
        "--all",
        "--quiet",
        "--filter",
        "label=devcontainer.local_folder",
        "--filter",
        "label=devcontainer.config_file",
    ).splitlines()
    for candidate in candidates:
        container = json.loads(docker("inspect", candidate))[0]
        labels = container["Config"].get("Labels", {})
        if not (
            same_path(labels.get("devcontainer.local_folder", ""), workspace)
            and same_path(labels.get("devcontainer.config_file", ""), config)
        ):
            continue
        if container["Image"] == latest["Id"]:
            continue
        if container["State"]["Running"]:
            _LOGGER.info(
                "An updated image is ready. The active devcontainer is kept; rebuild when ready."
            )
            continue
        # No --force or volume deletion: even if it starts between inspect and
        # rm, Docker refuses to remove a running container. Bind mounts persist.
        docker("rm", container["Id"])
        _LOGGER.info("Removed the outdated stopped devcontainer; Dev Containers will recreate it.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    refresh(Path(sys.argv[1]))
