"""A stopped private chat must wait for new user input after process recovery."""

from types import SimpleNamespace

import pytest

from gateway.run_startup import GatewayStartupMixin


def test_opt_out_leaves_session_store_and_scheduler_untouched():
    runner = SimpleNamespace(_ctx=SimpleNamespace(user_config={"gateway": {"auto_resume_interrupted": False}}))
    # No session store exists: any attempt to enumerate or schedule is a failure.
    assert GatewayStartupMixin._schedule_resume_pending_sessions(runner) == 0


@pytest.mark.parametrize("setting", [None, True])
def test_default_still_consults_pending_sessions(setting):
    calls = []
    gateway = {} if setting is None else {"auto_resume_interrupted": setting}
    runner = SimpleNamespace(_ctx=SimpleNamespace(user_config={"gateway": gateway}),
                             _resume_pending_candidates=lambda platform: calls.append(platform) or [])
    assert GatewayStartupMixin._schedule_resume_pending_sessions(runner) == 0
    assert calls == [None]
