"""Exercise restart policy through the real config loader and GatewayRunner."""

import asyncio
import json
import socket
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.config import GatewayConfig, Platform, load_gateway_config
from gateway.run import GatewayRunner, _prepare_resume_pending_message
from gateway.session import SessionSource


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [False, True, None])
@pytest.mark.parametrize("reconnect", [False, True])
async def test_real_runner_waits_for_input_only_when_opted_out(tmp_path, monkeypatch, setting, reconnect):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "900000002")
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network forbidden"))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *args: pytest.fail("network forbidden"))
    gateway = {} if setting is None else {"auto_resume_interrupted": setting}
    (tmp_path / "config.yaml").write_text(json.dumps({
        "gateway": gateway, "sessions_dir": str(tmp_path / "sessions"),
        "security": {"allow_lazy_installs": False}, "nous": {"guest": False},
    }))
    # Constructor preflight can install optional tools; this test never starts a gateway.
    monkeypatch.setattr(GatewayRunner, "_init_startup_checks", lambda self: None)
    runner = GatewayRunner()
    tasks = []
    try:
        source = SessionSource(platform=Platform.TELEGRAM, chat_id="900000002", user_id="900000002")
        entry = runner.session_store.get_or_create_session(source)
        assert runner.session_store.mark_resume_pending(entry.session_key, reason="restart_timeout")
        enumerate_pending = Mock(wraps=runner._resume_pending_candidates)
        monkeypatch.setattr(runner, "_resume_pending_candidates", enumerate_pending)
        monkeypatch.setattr(runner, "_adapter_for_source", lambda source: object())
        process_turn = AsyncMock()
        monkeypatch.setattr(runner, "_run_startup_resume_event", process_turn)
        monkeypatch.setattr(runner, "_retain_background_task", lambda task: tasks.append(task) or task)
        scheduled = runner._schedule_resume_pending_sessions(Platform.TELEGRAM if reconnect else None)
        await asyncio.gather(*tasks)
        expected = 0 if setting is False else 1
        assert scheduled == expected
        assert process_turn.await_count == expected
        assert enumerate_pending.call_count == expected
        assert runner.session_store.get_or_create_session(source).session_id == entry.session_id
        assert entry.resume_pending is True
        # A fresh user turn retains the pending session and persists the user's own text.
        prompt, persisted = _prepare_resume_pending_message(entry.resume_reason, "Continue my request")
        assert "Continue my request" in prompt and persisted == "Continue my request"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for handle in runner._session_db_handles.values():
            handle._db.close()


@pytest.mark.parametrize("raw,expected", [
    ({}, True),
    ({"gateway": {"auto_resume_interrupted": False}}, False),
    ({"auto_resume_interrupted": False, "gateway": {"auto_resume_interrupted": True}}, False),
    ({"auto_resume_interrupted": True, "gateway": {"auto_resume_interrupted": False}}, True),
])
def test_resume_policy_survives_yaml_loading_and_config_round_trip(tmp_path, monkeypatch, raw, expected):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    loaded = load_gateway_config()
    assert loaded.auto_resume_interrupted is expected
    assert GatewayConfig.from_dict(raw).auto_resume_interrupted is expected
    assert GatewayConfig.from_dict(loaded.to_dict()).auto_resume_interrupted is expected
