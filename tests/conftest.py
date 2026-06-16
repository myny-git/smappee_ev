"""Pytest configuration and lightweight stubs.

We stub both ``aiomqtt`` and (if necessary) ``pytest_socket``. The latter is
installed transitively via pytest-homeassistant-custom-component and replaces
``socket.socket`` causing Windows event loop creation to fail (needs
``socketpair``). A benign stub prevents the real plugin from loading.
"""

from __future__ import annotations

import logging
import sys
import types


def _install_pytest_socket_stub() -> None:
    if "pytest_socket" in sys.modules:  # already provided
        return
    mod = types.ModuleType("pytest_socket")

    def enable_socket():
        """No-op stub: sockets already allowed."""

    def disable_socket():
        """No-op stub: do not actually disable sockets."""

    # Provide attributes the real plugin might look for
    mod.enable_socket = enable_socket  # type: ignore[attr-defined]
    mod.disable_socket = disable_socket  # type: ignore[attr-defined]
    mod.SocketBlockedError = RuntimeError  # type: ignore[attr-defined]
    sys.modules["pytest_socket"] = mod


_install_pytest_socket_stub()


def _install_aiomqtt_stub() -> None:
    if "aiomqtt" in sys.modules:
        return

    mod = types.ModuleType("aiomqtt")

    class MqttError(Exception):
        """Generic MQTT error stub."""

    class _EmptyMessages:
        def __aiter__(self) -> _EmptyMessages:  # pragma: no cover - trivial
            return self

        async def __anext__(self):  # pragma: no cover - trivial
            raise StopAsyncIteration

    class Client:
        def __init__(self, *_, **__):
            self.messages = _EmptyMessages()

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *exc):
            return False

        async def subscribe(self, *_args, **_kwargs):  # pragma: no cover - trivial
            return None

        async def publish(self, *_args, **_kwargs):  # pragma: no cover - trivial
            return None

    mod.Client = Client  # type: ignore[attr-defined]
    mod.MqttError = MqttError  # type: ignore[attr-defined]
    sys.modules["aiomqtt"] = mod


_install_aiomqtt_stub()


def _restore_real_socket() -> None:
    try:  # pragma: no cover - defensive
        import socket

        real = getattr(socket, "__real_socket__", None)
        if real and isinstance(socket.socket, type):
            socket.socket = real  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).debug("restore socket failed: %s", exc)


import pytest  # type: ignore  # noqa: E402


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):  # type: ignore[override]
    if fixturedef.argname == "event_loop":  # ensure sockets available for loop creation
        _restore_real_socket()
        try:  # pragma: no cover - optional
            from pytest_socket import enable_socket as _en  # type: ignore

            _en()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).debug("enable_socket call failed: %s", exc)
    yield
