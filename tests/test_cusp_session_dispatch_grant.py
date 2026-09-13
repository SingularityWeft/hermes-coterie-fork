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


@pytest.fixture
def deferred_session(tmp_path, monkeypatch):
    import socket
    from tools.registry import registry
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network forbidden"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(json.dumps({"tools": {"tool_search": {"enabled": "on"}}}))
    calls = []
    names = ("mcp_session_grant_probe", "mcp_outside_grant_probe")
    for name, toolset in zip(names, ("mcp-session-grant", "mcp-outside-grant")):
        registry.register(name=name, toolset=toolset,
                          schema={"name": name, "description": "Local synthetic probe", "parameters": {
                              "type": "object", "properties": {"value": {"type": "integer"}},
                              "required": ["value"], "additionalProperties": False}},
                          handler=lambda args, **kw: calls.append(args) or json.dumps({"ok": True}))
    defs = model_tools.get_tool_definitions(enabled_toolsets=["mcp-session-grant"], quiet_mode=True)
    visible = {td["function"]["name"] for td in defs}
    assert "tool_call" in visible and not (set(names) & visible)
    agent = SimpleNamespace(valid_tool_names=visible, enabled_toolsets=["mcp-session-grant"],
                            disabled_toolsets=[], session_id="synthetic", _context_engine_tool_names=set(),
                            _memory_manager=None, quiet_mode=False)
    yield agent, calls
    for name in names:
        registry.deregister(name)


@pytest.mark.parametrize("route", ["bridge", "sequential", "concurrent"])
@pytest.mark.parametrize("name,args,allowed", [
    ("mcp_session_grant_probe", {"value": 1}, True),
    ("mcp_outside_grant_probe", {"value": 1}, False),
    ("mcp_session_grant_probe", {"value": "wrong type"}, False),
    ("terminal", {"command": "must not execute"}, False),
])
def test_deferred_catalog_grant_survives_dispatch(deferred_session, route, name, args, allowed):
    from agent.agent_runtime_helpers import invoke_tool
    from agent.tool_executor import _resolve_sequential_dispatch
    agent, calls = deferred_session
    before = set(agent.valid_tool_names)
    if route == "bridge":
        result = model_tools.handle_function_call("tool_call", {"calls": [{"name": name, "arguments": args}]},
                    enabled_tools=list(before), enabled_toolsets=agent.enabled_toolsets)
    elif route == "sequential":
        ref = SimpleNamespace(name=name, args=args, task_id="synthetic", call_id="synthetic", trace=[])
        result = _resolve_sequential_dispatch(agent, ref, []).execute(args)
    else:
        result = invoke_tool(agent, name, args, "synthetic", pre_tool_block_checked=True,
                             skip_tool_request_middleware=True, skip_tool_execution_middleware=True)
    assert bool(calls) is allowed
    assert (json.loads(result) == {"ok": True}) is allowed
    assert agent.valid_tool_names == before


@pytest.mark.parametrize("grant,connections,allowed", [(True, True, True), (True, False, False), (False, True, False)])
def test_connector_batch_reentry_keeps_scoped_grant(deferred_session, monkeypatch, grant, connections, allowed):
    from dataclasses import replace
    from tools.registry import registry
    import model_tools_connectors
    import tools.connections_tool
    from hermes_cli import plugins
    agent, _ = deferred_session
    # Availability is an external boundary; retain the native schema and selection.
    entry = registry._tools["manage_connections"]
    monkeypatch.setitem(registry._tools, "manage_connections", replace(entry, check_fn=lambda: True))
    enabled = agent.enabled_toolsets + (["connections"] if connections else [])
    visible = {td["function"]["name"] for td in model_tools.get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True)}
    sent, guarded = [], []
    monkeypatch.setattr(model_tools_connectors, "dispatch_connector_call", lambda name, args, call_id:
                        sent.append(name) or json.dumps({"response": {"ok": True}}))
    monkeypatch.setattr(plugins, "_dispatch_pre_tool_call_hooks", lambda name, args, **kw:
                        (guarded.append(name) or None, None))
    calls = [{"name": "connectors__synthetic__READ", "arguments": {}},
             {"name": "connectors__synthetic__LOOKUP", "arguments": {}}]
    result = json.loads(model_tools.handle_function_call("tool_call", {"calls": calls},
                        enabled_tools=list(visible) if grant else [], enabled_toolsets=enabled))
    assert sent == ([entry["name"] for entry in calls] if allowed else [])
    assert guarded == sent
    if allowed:
        assert all("response" in entry for entry in result["results"])
    else:
        assert "error" in result


def test_legacy_alias_respects_canonical_grant(monkeypatch):
    calls = []
    monkeypatch.setattr(model_tools.registry, "dispatch", lambda name, args, **kw: calls.append(name) or "{}")
    assert json.loads(model_tools.handle_function_call("process", {}, enabled_tools=["process_manage"])) == {}
    assert calls == ["process_manage"]
    assert "error" in json.loads(model_tools.handle_function_call("process", {}, enabled_tools=[]))
    assert calls == ["process_manage"]
