"""SettingsStore: claude_settings.json 로드/저장/부분 갱신."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.services.claude_code.schema import ClaudeCodeSettings


def test_load_missing_file_returns_defaults(store):
    assert not store.path.exists()
    s = store.load()
    assert s == ClaudeCodeSettings()


def test_load_corrupted_file_falls_back_to_defaults(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    s = store.load()
    assert s == ClaudeCodeSettings()


def test_save_and_load_roundtrip(store):
    s = ClaudeCodeSettings(model="claude-sonnet-4-6", max_turns=5, hourly_call_limit=10)
    store.save(s)

    loaded = store.load()
    assert loaded.model == "claude-sonnet-4-6"
    assert loaded.max_turns == 5
    assert loaded.hourly_call_limit == 10


def test_update_merges_partial_patch(store):
    store.save(ClaudeCodeSettings(model="claude-sonnet-4-6"))
    updated = store.update({"max_turns": 3})

    assert updated.model == "claude-sonnet-4-6"   # 기존 값 유지
    assert updated.max_turns == 3                  # 새 값 반영
    assert store.load().max_turns == 3


def test_update_rejects_unknown_field_like_api_key(store):
    """api_key 같은 정의 외 필드는 스키마(extra=forbid)에서 거부되어야 한다."""
    with pytest.raises(ValidationError):
        store.update({"api_key": "sk-ant-something"})

    # 거부된 패치는 저장되지 않아야 한다
    assert not store.path.exists()


def test_update_allow_api_key_billing_warns_on_first_enable(store, capsys):
    store.update({"allow_api_key_billing": True})
    captured = capsys.readouterr()
    assert "allow_api_key_billing" in captured.out


def test_settings_file_is_valid_json(store):
    store.save(ClaudeCodeSettings(allowed_tools=["Read"]))
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["allowed_tools"] == ["Read"]
    assert "api_key" not in data
