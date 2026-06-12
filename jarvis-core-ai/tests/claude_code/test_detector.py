"""ClaudeDetector: CLI 탐지 및 온보딩 상태 판별."""

from __future__ import annotations

import json
import sys

import pytest

from app.services.claude_code import detector as detector_module
from app.services.claude_code.detector import ClaudeDetector
from app.services.claude_code.schema import ClaudeCodeSettings


def _settings_loader(**overrides):
    settings = ClaudeCodeSettings(**overrides)
    return lambda: settings


@pytest.mark.asyncio
async def test_detect_not_installed_when_no_path_found(monkeypatch):
    """claude_path 미지정 + PATH에 없음 + (비-Windows) → 설치 필요 상태."""
    monkeypatch.setattr(detector_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(detector_module.sys, "platform", "linux")

    det = ClaudeDetector(_settings_loader())
    result = await det.detect(force=True)

    assert result.installed is False
    assert result.error


@pytest.mark.asyncio
async def test_detect_uses_configured_claude_path(tmp_path, monkeypatch):
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    fake_claude.chmod(0o755)

    async def fake_run_probe(cmd):
        return 0, "1.2.3 (Claude Code)", ""

    monkeypatch.setattr(detector_module, "_run_probe", fake_run_probe)
    # 자격 증명 파일이 없는 환경에서도 동작하도록 홈을 임시 디렉터리로
    monkeypatch.setattr(detector_module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    det = ClaudeDetector(_settings_loader(claude_path=str(fake_claude)))
    result = await det.detect(force=True)

    assert result.installed is True
    assert result.version == "1.2.3"
    assert result.path == str(fake_claude)


@pytest.mark.asyncio
async def test_detect_falls_back_to_path_when_configured_path_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(detector_module.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(detector_module.sys, "platform", "linux")

    async def fake_run_probe(cmd):
        return 0, "2.0.0", ""

    monkeypatch.setattr(detector_module, "_run_probe", fake_run_probe)
    monkeypatch.setattr(detector_module.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    det = ClaudeDetector(_settings_loader(claude_path="/nonexistent/claude"))
    result = await det.detect(force=True)

    assert result.installed is True
    assert result.path == "/usr/bin/claude"


@pytest.mark.asyncio
async def test_detect_version_probe_failure_reports_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(detector_module.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(detector_module.sys, "platform", "linux")

    async def fake_run_probe(cmd):
        return 1, "", "command not found"

    monkeypatch.setattr(detector_module, "_run_probe", fake_run_probe)

    det = ClaudeDetector(_settings_loader())
    result = await det.detect(force=True)

    assert result.installed is False
    assert "claude --version" in result.error


@pytest.mark.asyncio
async def test_detect_picks_up_subscription_oauth_from_credentials_file(monkeypatch, tmp_path):
    monkeypatch.setattr(detector_module.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(detector_module.sys, "platform", "linux")

    async def fake_run_probe(cmd):
        return 0, "1.0.0", ""

    monkeypatch.setattr(detector_module, "_run_probe", fake_run_probe)

    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "x"}}), encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    det = ClaudeDetector(_settings_loader())
    result = await det.detect(force=True)

    assert result.installed is True
    assert result.logged_in is True
    assert result.auth_method == "subscription_oauth"


def test_note_api_key_source_updates_cache():
    det = ClaudeDetector(_settings_loader())
    det._cache = detector_module.DetectResult(installed=True, path="/usr/bin/claude")

    det.note_api_key_source("none")

    assert det._cache.logged_in is True
    assert det._cache.auth_method == "subscription_oauth"


def test_note_login_failure_updates_cache():
    det = ClaudeDetector(_settings_loader())
    det._cache = detector_module.DetectResult(installed=True, path="/usr/bin/claude", logged_in=True)

    det.note_login_failure()

    assert det._cache.logged_in is False


def test_base_cmd_for_wsl_strips_anthropic_api_key():
    cmd = ClaudeDetector.base_cmd_for("/home/user/.local/bin/claude", via_wsl=True)
    assert cmd[:4] == ["wsl.exe", "-e", "env", "-u"]
    assert "ANTHROPIC_API_KEY" in cmd


def test_base_cmd_for_non_wsl_is_passthrough():
    cmd = ClaudeDetector.base_cmd_for("/usr/bin/claude", via_wsl=False)
    assert cmd == ["/usr/bin/claude"]
