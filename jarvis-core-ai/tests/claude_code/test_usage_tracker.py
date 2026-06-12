"""UsageTracker: 호출 예산 게이트 + 사용량 로그/집계."""

from __future__ import annotations


def test_record_call_and_today_counts(tracker):
    tracker.record_call()
    tracker.record_call()

    today = tracker.today()
    assert today["calls"] == 2
    assert today["hourly_calls"] == 2
    assert today["cost_usd"] == 0.0


def test_record_result_accumulates_cost_and_tokens(tracker):
    tracker.record_call()
    cost_after = tracker.record_result(
        cost_usd=0.012, input_tokens=100, output_tokens=50,
        cache_read_tokens=10, cache_creation_tokens=0,
        num_turns=1, duration_ms=1000, session_id="sess-1",
    )

    assert cost_after == 0.012
    today = tracker.today()
    assert today["cost_usd"] == 0.012
    assert today["input_tokens"] == 100
    assert today["output_tokens"] == 50


def test_check_budget_allows_within_limits(tracker):
    ok, why = tracker.check_budget(hourly_limit=30, daily_limit=200)
    assert ok is True
    assert why == ""


def test_check_budget_rejects_when_hourly_limit_exceeded(tracker):
    for _ in range(3):
        tracker.record_call()

    ok, why = tracker.check_budget(hourly_limit=3, daily_limit=200)
    assert ok is False
    assert "시간당" in why


def test_check_budget_rejects_when_daily_limit_exceeded(tracker):
    for _ in range(3):
        tracker.record_call()

    ok, why = tracker.check_budget(hourly_limit=100, daily_limit=3)
    assert ok is False
    assert "일일" in why


def test_crossed_warn_threshold_only_on_crossing(tracker):
    # 5.0 임계값을 4.0 -> 6.0 으로 넘는 경우에만 True
    assert tracker.crossed_warn_threshold(4.0, 6.0, 5.0) is True
    # 이미 넘은 상태에서 추가 호출은 다시 경고하지 않음
    assert tracker.crossed_warn_threshold(6.0, 7.0, 5.0) is False
    # 임계값 미달은 False
    assert tracker.crossed_warn_threshold(1.0, 2.0, 5.0) is False


def test_seeding_restores_recent_calls_from_log(data_dir):
    """프로세스 재시작 시 최근 24시간 호출 기록이 예산 계산에 복원되어야 한다."""
    from app.services.claude_code.usage_tracker import UsageTracker

    log_path = data_dir / "claude_usage.jsonl"
    tracker1 = UsageTracker(log_path=log_path)
    for _ in range(5):
        tracker1.record_call()

    tracker2 = UsageTracker(log_path=log_path)
    ok, _ = tracker2.check_budget(hourly_limit=5, daily_limit=200)
    assert ok is False   # 새 인스턴스도 기존 5건을 인지해야 함
