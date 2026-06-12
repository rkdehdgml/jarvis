"""ClaudeCodeWrapper: stream-json 라인 파싱 / 실패 분류 / env·cmd 구성."""

from __future__ import annotations

import json

from app.services.claude_code.schema import (
    CCInit, CCResult, CCStatusEvent, CCTextDelta, CCToolResult, CCToolUse,
    ClaudeCodeSettings, ClaudeStatus,
)
from app.services.claude_code.wrapper import ClaudeCodeWrapper, ENV_WHITELIST


def make_wrapper(store, tracker, detector):
    return ClaudeCodeWrapper(store=store, tracker=tracker, detector=detector)


# ══════════════════════════════════════════════════════════════════════════════
# _dispatch_line — stream-json 한 줄 → 이벤트
# ══════════════════════════════════════════════════════════════════════════════

def test_dispatch_system_init_sets_session_and_notes_api_key_source(
    store, tracker, installed_detector,
):
    w = make_wrapper(store, tracker, installed_detector)
    line = {
        "type": "system", "subtype": "init",
        "session_id": "sess-123", "model": "claude-sonnet-4-6",
        "apiKeySource": "none", "tools": ["Read", "WebSearch"],
    }

    events = w._dispatch_line(line)

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, CCInit)
    assert ev.session_id == "sess-123"
    assert ev.api_key_source == "none"
    assert w.session_id == "sess-123"
    assert installed_detector.note_api_key_source_calls == ["none"]


def test_dispatch_text_delta(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    line = {
        "type": "stream_event",
        "event": {"type": "content_block_delta",
                  "delta": {"type": "text_delta", "text": "안녕하세요"}},
    }

    events = w._dispatch_line(line)

    assert events == [CCTextDelta("안녕하세요")]


def test_dispatch_tool_use(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    line = {
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/a.txt"}},
        ]},
    }

    events = w._dispatch_line(line)

    assert len(events) == 1
    assert isinstance(events[0], CCToolUse)
    assert events[0].name == "Read"
    assert "a.txt" in events[0].input_preview


def test_dispatch_tool_result(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    line = {
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "is_error": True},
        ]},
    }

    events = w._dispatch_line(line)

    assert events == [CCToolResult(tool_use_id="tu_1", is_error=True)]


def test_dispatch_result_success(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    line = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": "완료했습니다", "total_cost_usd": 0.01234,
        "num_turns": 2, "duration_ms": 1500, "session_id": "sess-456",
        "usage": {"input_tokens": 100, "output_tokens": 50,
                  "cache_read_input_tokens": 5, "cache_creation_input_tokens": 0},
    }

    events = w._dispatch_line(line)

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, CCResult)
    assert ev.is_error is False
    assert ev.total_cost_usd == 0.01234
    assert ev.input_tokens == 100
    assert ev.session_id == "sess-456"
    assert w.session_id == "sess-456"


def test_dispatch_rate_limit_event(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    line = {"type": "rate_limit_event", "status": "rejected", "resetsAt": 1700000000}

    events = w._dispatch_line(line)

    assert events == [("__limit__", 1700000000)]


def test_dispatch_unknown_type_is_ignored(store, tracker, installed_detector, capsys):
    w = make_wrapper(store, tracker, installed_detector)
    events = w._dispatch_line({"type": "future_event_type"})

    assert events == []
    # 1회만 로그되는지 확인 — 두 번째 호출은 추가 출력 없음
    w._dispatch_line({"type": "future_event_type"})
    captured = capsys.readouterr()
    assert captured.out.count("future_event_type") == 1


# ══════════════════════════════════════════════════════════════════════════════
# 실패 분류 — 한도 도달 / 로그인 필요 / 일반 오류
# ══════════════════════════════════════════════════════════════════════════════

def test_classify_failure_limit_reached(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    result = CCResult(
        subtype="error_max_turns", is_error=True,
        result_text="usage limit reached|1700003600",
        total_cost_usd=0.0, num_turns=10, duration_ms=100,
        input_tokens=0, output_tokens=0, cache_read_tokens=0,
        cache_creation_tokens=0, session_id="sess-x",
    )

    ev = w._classify_failure(result, limit_reset_at=None)

    assert isinstance(ev, CCStatusEvent)
    assert ev.status == ClaudeStatus.LIMIT_REACHED
    assert ev.reset_at == 1700003600


def test_classify_failure_not_logged_in(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    result = CCResult(
        subtype="error", is_error=True,
        result_text="Invalid API key · Please run /login",
        total_cost_usd=0.0, num_turns=0, duration_ms=10,
        input_tokens=0, output_tokens=0, cache_read_tokens=0,
        cache_creation_tokens=0, session_id="",
    )

    ev = w._classify_failure(result, limit_reset_at=None)

    assert ev.status == ClaudeStatus.NOT_LOGGED_IN
    assert installed_detector.note_login_failure_calls == 1


def test_classify_failure_generic_error(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    result = CCResult(
        subtype="error_during_execution", is_error=True,
        result_text="something unexpected happened",
        total_cost_usd=0.0, num_turns=1, duration_ms=10,
        input_tokens=0, output_tokens=0, cache_read_tokens=0,
        cache_creation_tokens=0, session_id="",
    )

    ev = w._classify_failure(result, limit_reset_at=None)

    assert ev.status == ClaudeStatus.ERROR
    assert "something unexpected happened" in ev.message


def test_classify_no_result_limit_reached_from_stderr(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    ev = w._classify_no_result(rc=1, stderr_text="Error: usage limit reached|1700003600",
                                limit_reset_at=None)

    assert ev.status == ClaudeStatus.LIMIT_REACHED
    assert ev.reset_at == 1700003600


def test_classify_no_result_not_logged_in_from_stderr(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    ev = w._classify_no_result(rc=1, stderr_text="OAuth token expired", limit_reset_at=None)

    assert ev.status == ClaudeStatus.NOT_LOGGED_IN


def test_classify_no_result_generic_error(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    ev = w._classify_no_result(rc=2, stderr_text="boom", limit_reset_at=None)

    assert ev.status == ClaudeStatus.ERROR
    assert "exit=2" in ev.message
    assert "boom" in ev.message


# ══════════════════════════════════════════════════════════════════════════════
# env / cmd 구성
# ══════════════════════════════════════════════════════════════════════════════

def test_build_env_excludes_anthropic_key_by_default(
    store, tracker, installed_detector, monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("TEST_OTHER_API_KEY", "other-service-secret")

    w = make_wrapper(store, tracker, installed_detector)
    env = w._build_env(ClaudeCodeSettings(allow_api_key_billing=False))

    assert "ANTHROPIC_API_KEY" not in env
    assert "TEST_OTHER_API_KEY" not in env
    for key in env:
        assert key in ENV_WHITELIST


def test_build_env_allows_anthropic_key_only_when_billing_enabled(
    store, tracker, installed_detector, monkeypatch, capsys,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-billing-ok")
    monkeypatch.setenv("TEST_OTHER_API_KEY", "other-service-secret")

    w = make_wrapper(store, tracker, installed_detector)
    env = w._build_env(ClaudeCodeSettings(allow_api_key_billing=True))

    assert env["ANTHROPIC_API_KEY"] == "sk-ant-billing-ok"
    assert "TEST_OTHER_API_KEY" not in env
    captured = capsys.readouterr()
    assert "경고" in captured.out


def test_build_cmd_includes_max_turns_and_allowed_tools(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    s = ClaudeCodeSettings(max_turns=7, allowed_tools=["Read", "WebSearch"])

    cmd = w._build_cmd(["claude"], s, system=None, resume_id=None)

    assert "--max-turns" in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "7"
    assert "--allowedTools" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,WebSearch"


def test_build_cmd_includes_resume_and_model(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    s = ClaudeCodeSettings(model="claude-sonnet-4-6")

    cmd = w._build_cmd(["claude"], s, system="페르소나", resume_id="sess-789")

    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "sess-789"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"
    assert "--append-system-prompt" in cmd
    assert cmd[cmd.index("--append-system-prompt") + 1] == "페르소나"


def test_build_cmd_without_model_omits_model_flag(store, tracker, installed_detector):
    w = make_wrapper(store, tracker, installed_detector)
    s = ClaudeCodeSettings(model=None)

    cmd = w._build_cmd(["claude"], s, system=None, resume_id=None)

    assert "--model" not in cmd
