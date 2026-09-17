"""Keep per-test socket cleanup active for Home Assistant's network guard."""

import asyncio
import socket
import sys

import pytest


def test_socket_cleanup_hook_is_registered(pytestconfig):
    """HA installs a guard per test, so pytest-socket must remove it per test."""
    plugin = pytestconfig.pluginmanager.get_plugin("socket")
    assert plugin is not None
    assert any(
        hook.plugin is plugin and hook.function is plugin.pytest_runtest_teardown
        for hook in pytestconfig.hook.pytest_runtest_teardown.get_hookimpls()
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses the socketpair exception")
@pytest.mark.parametrize("iteration", range(3))
def test_linux_socket_guard_does_not_accumulate(iteration):
    """Repeated test setup must keep one guard and allow asyncio's self-pipe."""
    assert sum(cls.__name__ == "GuardedSocket" for cls in socket.socket.__mro__) == 1
    loop = asyncio.new_event_loop()
    loop.close()
