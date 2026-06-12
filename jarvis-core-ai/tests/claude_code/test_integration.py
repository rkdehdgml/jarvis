"""실제 claude CLI를 1회 호출하는 end-to-end 통합 테스트.

기본적으로 skip된다. 실행하려면:
    RUN_CLAUDE_INTEGRATION=1 pytest tests/claude_code/test_integration.py -m integration -s

claude 구독 호출이 실제로 발생하므로(소량) 반복 실행하지 않도록 주의.
"""

from __future__ import annotations

import os

import pytest

from app.services.claude_code.schema import CCInit, CCResult, CCStatusEvent, ClaudeCodeSettings
from app.services.claude_code.settings_store import SettingsStore
from app.services.claude_code.usage_tracker import UsageTracker
from app.services.claude_code.wrapper import ClaudeCodeWrapper

pytestmark = pytest.mark.integration

requires_opt_in = pytest.mark.skipif(
    os.environ.get("RUN_CLAUDE_INTEGRATION") != "1",
    reason="RUN_CLAUDE_INTEGRATION=1 일 때만 실행 (실제 claude -p 1회 호출)",
)


@requires_opt_in
@pytest.mark.asyncio
async def test_single_real_call_and_usage_log(data_dir):
    store = SettingsStore(path=data_dir / "claude_settings.json")
    store.save(ClaudeCodeSettings(
        allowed_tools=[],
        max_turns=1,
        timeout_sec=120.0,
    ))
    tracker = UsageTracker(log_path=data_dir / "claude_usage.jsonl")
    wrapper = ClaudeCodeWrapper(store=store, tracker=tracker)

    events = []
    async for ev in wrapper.stream("딱 한 단어로만 답해: 'pong'"):
        events.append(ev)

    statuses = [ev for ev in events if isinstance(ev, CCStatusEvent)]
    assert not statuses, f"비정상 상태 이벤트 발생: {statuses}"

    assert any(isinstance(ev, CCInit) for ev in events)
    results = [ev for ev in events if isinstance(ev, CCResult)]
    assert len(results) == 1
    assert results[0].is_error is False

    # 사용량 로그가 기록됐는지 확인
    today = tracker.today()
    assert today["calls"] == 1
    assert today["cost_usd"] >= 0
