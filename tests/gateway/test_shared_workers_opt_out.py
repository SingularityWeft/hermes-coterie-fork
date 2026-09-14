"""Profile-only gateways may opt out of shared Kanban and hosted-room workers."""

import asyncio
import hashlib
import json
import socket
import sqlite3
from pathlib import Path

import pytest

from gateway.config import GatewayConfig, load_gateway_config
from gateway.run import GatewayRunner


@pytest.mark.parametrize("raw, expected", [
    ({}, True),
    ({"gateway": {"kanban_enabled": False, "hosted_rooms_enabled": False}}, False),
    ({"kanban_enabled": False, "hosted_rooms_enabled": False,
      "gateway": {"kanban_enabled": True, "hosted_rooms_enabled": True}}, False),
])
def test_shared_worker_policy_survives_real_yaml_loading(tmp_path, monkeypatch, raw, expected):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(json.dumps(raw))
    loaded = load_gateway_config()
    for config in (loaded, GatewayConfig.from_dict(raw), GatewayConfig.from_dict(loaded.to_dict())):
        assert config.kanban_enabled is expected
        assert config.hosted_rooms_enabled is expected


@pytest.mark.asyncio
async def test_profile_only_start_and_stop_leave_shared_root_untouched(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    home = root / "profiles" / "private-chat"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "900000002")
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network forbidden"))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *a, **k: pytest.fail("network forbidden"))
    (home / "config.yaml").write_text(json.dumps({
        "gateway": {"kanban_enabled": False, "hosted_rooms_enabled": False,
                    "auto_resume_interrupted": False, "multiplex_profiles": False},
        "agent": {"gateway_startup_warmup_timeout": 0},
        "sessions_dir": str(home / "sessions"),
        "nous": {"guest": False}, "relay": {"enabled": False},
        "model_catalog": {"enabled": False}, "security": {"allow_lazy_installs": False},
        "kanban": {"dispatch_in_gateway": False, "auto_decompose": False, "review_dispatch": False},
        "hooks": {}, "mcp_servers": {}, "curator": {"enabled": False},
    }))
    # Optional-tool installer is outside this lifecycle test. Native startup,
    # config parsing, background services, stores and shutdown remain real.
    monkeypatch.setattr(GatewayRunner, "_init_startup_checks", lambda self: None)
    def shared_inventory():
        return {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_relative_to(home)
        }

    stores = []
    for name in ("kanban.db", "shared-state.db"):
        con = sqlite3.connect(root / name)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE preserved_marker(value TEXT)")
        con.execute("INSERT INTO preserved_marker VALUES ('synthetic preserved value')")
        con.commit()
        stores.append(con)  # Keep genuine WAL/SHM alive throughout the lifecycle.
    (root / "install_id").write_text("a" * 32 + "\n")
    before = shared_inventory()
    runner = GatewayRunner()
    try:
        assert await runner.start()
        assert runner._running
        # Also cover direct supervisor/worker entry points, not only boot routing.
        assert await runner._ensure_hosted_room_worker() is None
        await runner._hosted_room_worker_watcher(interval=0)
        assert runner._kanban_dispatcher_boot() is None
        await runner._kanban_dispatcher_watcher()
        await runner._kanban_notifier_watcher()
        assert shared_inventory() == before
    finally:
        await runner.stop()
        for task in tuple(runner._background_tasks):
            task.cancel()
        await asyncio.gather(*tuple(runner._background_tasks), return_exceptions=True)
        try:
            assert shared_inventory() == before
        finally:
            for con in stores:
                con.close()
