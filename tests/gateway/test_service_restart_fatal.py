"""Fatal transport recovery must preserve the service supervisor's retry budget."""
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from gateway.run import GatewayRunner
from gateway.run_shutdown import _resolve_gateway_exit_verdict


class Adapter(BasePlatformAdapter):
    fatal_error_restart_required = False

    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="synthetic"), Platform.TELEGRAM)

    async def connect(self, *, is_reconnect=False):
        return True

    async def disconnect(self):
        self._mark_disconnected()

    async def send(self, *args, **kwargs):
        raise AssertionError("no sends in recovery qualification")

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable,restart_required", [(False, True), (True, True), (False, False), (True, False)])
async def test_fatal_routes_to_service_or_background_or_terminal_stop(tmp_path, monkeypatch, retryable, restart_required):
    runner = GatewayRunner(GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="synthetic")},
        sessions_dir=tmp_path / "sessions",
    ))
    adapter = Adapter()
    adapter.fatal_error_restart_required = restart_required
    adapter._set_fatal_error("synthetic_transport", "Synthetic transport failure", retryable=retryable)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.delivery_router.adapters = runner.adapters
    runner.stop = AsyncMock(side_effect=runner._shutdown_event.set)
    monkeypatch.setattr(runner, "_ensure_reconnect_watcher_running", lambda: None)
    await runner._handle_adapter_fatal_error(adapter)
    queued = retryable and not restart_required
    assert (Platform.TELEGRAM in runner._failed_platforms) is queued
    assert runner.stop.await_count == (0 if queued else 1)
    assert _resolve_gateway_exit_verdict(runner, False) is (not restart_required)
