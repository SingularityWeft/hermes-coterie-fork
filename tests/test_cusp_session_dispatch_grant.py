"""An explicit per-session grant survives both ordinary dispatch paths."""

import json
from types import SimpleNamespace

import pytest

import model_tools


@pytest.mark.parametrize("grant,allowed", [(None, True), ([], False), (["cusp_probe"], True), (["other_probe"], False)])
def test_dispatch_uses_explicit_grant(monkeypatch, grant, allowed):
    calls = []

    def benign_handler(name, args, **kwargs):
        calls.append((name, args))
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", benign_handler)
    result = json.loads(model_tools.handle_function_call("cusp_probe", {}, enabled_tools=grant))
    assert bool(calls) is allowed
    assert (result == {"ok": True}) is allowed
    if not allowed:
        assert "not enabled for this session" in result["error"]


def test_concurrent_invoke_preserves_empty_grant(monkeypatch):
    from agent.agent_runtime_helpers import invoke_tool

    calls = []
    monkeypatch.setattr(model_tools.registry, "dispatch", lambda *args, **kwargs: calls.append(args) or '{"ok":true}')
    agent = SimpleNamespace(valid_tool_names=set(), session_id="synthetic", _context_engine_tool_names=set(), _memory_manager=None)
    result = json.loads(invoke_tool(agent, "cusp_probe", {}, "synthetic", pre_tool_block_checked=True,
                                    skip_tool_request_middleware=True, skip_tool_execution_middleware=True))
    assert "not enabled for this session" in result["error"]
    assert calls == []


def test_sequential_invoke_preserves_empty_grant(monkeypatch):
    from agent.tool_executor import _resolve_sequential_dispatch

    calls = []
    monkeypatch.setattr(model_tools.registry, "dispatch", lambda *args, **kwargs: calls.append(args) or '{"ok":true}')
    agent = SimpleNamespace(valid_tool_names=set(), session_id="synthetic", _context_engine_tool_names=set(),
                            _memory_manager=None, quiet_mode=False)
    ref = SimpleNamespace(name="cusp_probe", args={}, task_id="synthetic", call_id="synthetic", trace=[])
    result = json.loads(_resolve_sequential_dispatch(agent, ref, []).execute({}))
    assert "not enabled for this session" in result["error"]
    assert calls == []
