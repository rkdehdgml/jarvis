"""/api/claude 라우터: 설정 저장/로드, 사용량, 상태, 세션 초기화."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import claude_code as claude_code_router
from app.services.claude_code.schema import ClaudeCodeSettings
from app.services.claude_code.settings_store import SettingsStore
from app.services.claude_code.usage_tracker import UsageTracker


class FakeWrapper:
    def __init__(self):
        self.reset_called = False
        self.forced = False

    async def status(self, force: bool = False) -> dict:
        self.forced = self.forced or force
        return {
            "installed": True,
            "path": "/usr/bin/claude",
            "version": "1.2.3",
            "logged_in": True,
            "auth_method": "subscription_oauth",
            "via_wsl": False,
            "error": None,
            "model": None,
            "session_id": None,
            "allow_api_key_billing": False,
        }

    def reset_session(self) -> None:
        self.reset_called = True


@pytest.fixture
def client(data_dir, monkeypatch):
    store = SettingsStore(path=data_dir / "claude_settings.json")
    tracker = UsageTracker(log_path=data_dir / "claude_usage.jsonl")
    fake_wrapper = FakeWrapper()

    monkeypatch.setattr(claude_code_router, "get_store", lambda: store)
    monkeypatch.setattr(claude_code_router, "get_tracker", lambda: tracker)
    monkeypatch.setattr(claude_code_router, "get_wrapper", lambda: fake_wrapper)

    app = FastAPI()
    app.include_router(claude_code_router.router, prefix="/api/claude")

    test_client = TestClient(app)
    test_client.fake_wrapper = fake_wrapper
    test_client.fake_store = store
    test_client.fake_tracker = tracker
    return test_client


def test_get_settings_returns_defaults_without_api_key_field(client):
    res = client.get("/api/claude/settings")
    assert res.status_code == 200
    body = res.json()
    assert "api_key" not in body
    assert body["allow_api_key_billing"] is False
    assert body["allowed_tools"] == ["Read", "WebSearch"]


def test_put_settings_updates_and_persists(client):
    res = client.put("/api/claude/settings", json={"model": "claude-sonnet-4-6", "max_turns": 5})
    assert res.status_code == 200
    body = res.json()
    assert body["model"] == "claude-sonnet-4-6"
    assert body["max_turns"] == 5

    # 다시 로드해도 유지
    assert client.fake_store.load().model == "claude-sonnet-4-6"


def test_put_settings_rejects_api_key_field(client):
    res = client.put("/api/claude/settings", json={"api_key": "sk-ant-xxx"})
    assert res.status_code == 422


def test_get_usage_includes_limits_and_warn_flag(client):
    client.fake_tracker.record_call()
    client.fake_tracker.record_result(cost_usd=1.0, input_tokens=10, output_tokens=5)

    res = client.get("/api/claude/usage")
    assert res.status_code == 200
    body = res.json()
    assert body["calls"] == 1
    assert body["cost_usd"] == 1.0
    assert body["hourly_limit"] == 30
    assert body["daily_limit"] == 200
    assert "over_warn" in body


def test_get_status_returns_wrapper_status(client):
    res = client.get("/api/claude/status")
    assert res.status_code == 200
    body = res.json()
    assert body["installed"] is True
    assert body["auth_method"] == "subscription_oauth"
    assert client.fake_wrapper.forced is False


def test_post_status_refresh_forces_redetect(client):
    res = client.post("/api/claude/status/refresh")
    assert res.status_code == 200
    assert client.fake_wrapper.forced is True


def test_post_session_reset(client):
    res = client.post("/api/claude/session/reset")
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert client.fake_wrapper.reset_called is True
