"""ClaudeCodeWrapper.stream(): 사전 게이트 + end-to-end 스트리밍(가짜 subprocess)."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services.claude_code.schema import (
    CCInit, CCResult, CCStatusEvent, CCTextDelta, ClaudeCodeSettings, ClaudeStatus,
)
from app.services.claude_code.wrapper import ClaudeCodeWrapper


class FakeStdout:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class FakeStdin:
    def __init__(self):
        self.written = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeStderr:
    def __init__(self, data: bytes = b""):
        self._data = data

    async def read(self) -> bytes:
        return self._data


class FakeProcess:
    def __init__(self, lines: list[bytes], stderr: bytes = b"", returncode: int = 0):
        self.stdout = FakeStdout(lines)
        self.stdin = FakeStdin()
        self.stderr = FakeStderr(stderr)
        self.returncode = returncode
        self.killed = False

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _ndjson(*objs: dict) -> list[bytes]:
    return [(json.dumps(o) + "\n").encode("utf-8") for o in objs]


async def _collect(agen):
    return [ev async for ev in agen]


# ══════════════════════════════════════════════════════════════════════════════
# 사전 게이트 — spawn 없이 상태 반환
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stream_not_installed_skips_spawn(store, tracker, not_installed_detector, monkeypatch):
    w = ClaudeCodeWrapper(store=store, tracker=tracker, detector=not_installed_detector)

    called = False

    async def fail_spawn(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("spawn되지 않아야 함")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_spawn)

    events = await _collect(w.stream("안녕"))

    assert called is False
    assert len(events) == 1
    assert events[0].status == ClaudeStatus.NOT_INSTALLED


@pytest.mark.asyncio
async def test_stream_not_logged_in_skips_spawn(store, tracker, not_logged_in_detector, monkeypatch):
    w = ClaudeCodeWrapper(store=store, tracker=tracker, detector=not_logged_in_detector)

    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawn 안 됨")))

    events = await _collect(w.stream("안녕"))

    assert len(events) == 1
    assert events[0].status == ClaudeStatus.NOT_LOGGED_IN


@pytest.mark.asyncio
async def test_stream_budget_exceeded_skips_spawn(store, tracker, installed_detector, monkeypatch):
    store.save(ClaudeCodeSettings(hourly_call_limit=1, daily_call_limit=200))
    tracker.record_call()   # 이미 1회 호출 — 한도(1) 도달

    w = ClaudeCodeWrapper(store=store, tracker=tracker, detector=installed_detector)

    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawn 안 됨")))

    events = await _collect(w.stream("안녕"))

    assert len(events) == 1
    assert events[0].status == ClaudeStatus.BUDGET_EXCEEDED


# ══════════════════════════════════════════════════════════════════════════════
# 성공 흐름 — 텍스트 스트리밍 + 사용량 기록
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stream_success_yields_text_and_records_usage(
    store, tracker, installed_detector, monkeypatch,
):
    lines = _ndjson(
        {"type": "system", "subtype": "init", "session_id": "sess-1",
         "model": "claude-sonnet-4-6", "apiKeySource": "none", "tools": []},
        {"type": "stream_event",
         "event": {"type": "content_block_delta",
                   "delta": {"type": "text_delta", "text": "안녕하세요"}}},
        {"type": "result", "subtype": "success", "is_error": False,
         "result": "안녕하세요", "total_cost_usd": 0.01, "num_turns": 1,
         "duration_ms": 500, "session_id": "sess-1",
         "usage": {"input_tokens": 10, "output_tokens": 5}},
    )
    fake_proc = FakeProcess(lines)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    w = ClaudeCodeWrapper(store=store, tracker=tracker, detector=installed_detector)
    events = await _collect(w.stream("안녕"))

    types = [type(ev) for ev in events]
    assert CCInit in types
    assert CCTextDelta("안녕하세요") in events
    result_events = [ev for ev in events if isinstance(ev, CCResult)]
    assert len(result_events) == 1
    assert result_events[0].total_cost_usd == 0.01

    # 세션 ID가 다음 호출의 --resume에 쓰이도록 보존됨
    assert w.session_id == "sess-1"

    # 사용량이 기록되어 오늘 집계에 반영됨
    today = tracker.today()
    assert today["calls"] == 1
    assert today["cost_usd"] == 0.01
    assert today["input_tokens"] == 10


@pytest.mark.asyncio
async def test_stream_prompt_is_sent_via_stdin(store, tracker, installed_detector, monkeypatch):
    lines = _ndjson({"type": "result", "subtype": "success", "is_error": False,
                     "result": "ok", "total_cost_usd": 0.0, "num_turns": 1,
                     "duration_ms": 1, "session_id": "s", "usage": {}})
    fake_proc = FakeProcess(lines)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    w = ClaudeCodeWrapper(store=store, tracker=tracker, detector=installed_detector)
    await _collect(w.stream("테스트 프롬프트"))

    assert fake_proc.stdin.written.decode("utf-8") == "테스트 프롬프트"
    assert fake_proc.stdin.closed is True


# ══════════════════════════════════════════════════════════════════════════════
# 한도 도달 / 일반 오류 — result 이벤트 기반
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stream_limit_reached_result(store, tracker, installed_detector, monkeypatch):
    lines = _ndjson({"type": "result", "subtype": "error_max_turns", "is_error": True,
                     "result": "usage limit reached|1700003600", "total_cost_usd": 0.0,
                     "num_turns": 10, "duration_ms": 1, "session_id": "s", "usage": {}})
    fake_proc = FakeProcess(lines)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    w = ClaudeCodeWrapper(store=store, tracker=tracker, detector=installed_detector)
    events = await _collect(w.stream("안녕"))

    status_events = [ev for ev in events if isinstance(ev, CCStatusEvent)]
    assert len(status_events) == 1
    assert status_events[0].status == ClaudeStatus.LIMIT_REACHED
    assert status_events[0].reset_at == 1700003600


# ══════════════════════════════════════════════════════════════════════════════
# 타임아웃 — 강제 종료 후 TIMEOUT 상태
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stream_timeout_kills_process(store, tracker, installed_detector, monkeypatch):
    store.save(ClaudeCodeSettings(timeout_sec=0.05))

    class HangingStdout:
        async def readline(self) -> bytes:
            await asyncio.sleep(10)
            return b""

    fake_proc = FakeProcess([])
    fake_proc.stdout = HangingStdout()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    w = ClaudeCodeWrapper(store=store, tracker=tracker, detector=installed_detector)
    events = await _collect(w.stream("안녕"))

    assert len(events) == 1
    assert events[0].status == ClaudeStatus.TIMEOUT
    assert fake_proc.killed is True
