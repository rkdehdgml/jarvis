"""tests/claude_code 공용 fixture.

JARVIS_DATA_DIR을 임시 디렉터리로 돌려 claude_settings.json / claude_usage.jsonl이
실제 사용자 데이터 디렉터리를 건드리지 않도록 한다.
"""

from __future__ import annotations

import pytest

from app.services.claude_code.detector import ClaudeDetector, DetectResult
from app.services.claude_code.schema import ClaudeCodeSettings
from app.services.claude_code.settings_store import SettingsStore
from app.services.claude_code.usage_tracker import UsageTracker


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """claude_code 패키지의 사용자 데이터 디렉터리를 임시 경로로 격리."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(data_dir):
    return SettingsStore(path=data_dir / "claude_settings.json")


@pytest.fixture
def tracker(data_dir):
    return UsageTracker(log_path=data_dir / "claude_usage.jsonl")


class FakeDetector:
    """탐지 결과를 고정값으로 반환하는 더블 — wrapper 테스트용."""

    def __init__(self, result: DetectResult) -> None:
        self._result = result
        self.note_api_key_source_calls: list[str] = []
        self.note_login_failure_calls = 0

    async def detect(self, force: bool = False) -> DetectResult:
        return self._result

    def note_api_key_source(self, source: str) -> None:
        self.note_api_key_source_calls.append(source)

    def note_login_failure(self) -> None:
        self.note_login_failure_calls += 1

    @staticmethod
    def base_cmd_for(path: str, via_wsl: bool) -> list[str]:
        return [path]


@pytest.fixture
def installed_detector() -> FakeDetector:
    return FakeDetector(DetectResult(
        installed=True, path="/usr/bin/claude", version="1.2.3",
        logged_in=True, auth_method="subscription_oauth", via_wsl=False,
    ))


@pytest.fixture
def not_installed_detector() -> FakeDetector:
    return FakeDetector(DetectResult(installed=False, error="claude CLI를 찾을 수 없습니다."))


@pytest.fixture
def not_logged_in_detector() -> FakeDetector:
    return FakeDetector(DetectResult(
        installed=True, path="/usr/bin/claude", version="1.2.3",
        logged_in=False, auth_method="unknown", via_wsl=False,
    ))
