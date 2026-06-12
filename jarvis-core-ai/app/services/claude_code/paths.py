"""
paths.py — 사용자 데이터 경로 해석
════════════════════════════════════════════════════════════════════════════════
설정 파일·사용량 로그는 exe 배포 후 사용자 PC에서 변경되는 값이므로
프로젝트 디렉토리(.env)와 분리해 사용자 쓰기 가능 위치에 저장한다.

우선순위:
  ① JARVIS_DATA_DIR 환경변수 (테스트/특수 배포용 오버라이드)
  ② Windows  : %APPDATA%\\jarvis-core\\
  ③ 그 외     : $XDG_CONFIG_HOME/jarvis-core/ (기본 ~/.config/jarvis-core/)
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def jarvis_data_dir() -> Path:
    """사용자 데이터 디렉토리를 반환 (없으면 생성)."""
    override = os.environ.get("JARVIS_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "jarvis-core"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        base = Path(xdg) / "jarvis-core"
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_file_path() -> Path:
    """사용자 설정 파일(claude_settings.json) 경로."""
    return jarvis_data_dir() / "claude_settings.json"


def usage_log_path() -> Path:
    """호출별 사용량 로그(claude_usage.jsonl) 경로."""
    return jarvis_data_dir() / "claude_usage.jsonl"


def workspace_dir() -> Path:
    """claude subprocess의 작업 디렉토리.

    전용 빈 디렉토리를 사용해 임의 프로젝트의 CLAUDE.md / 프로젝트 설정이
    자동 로드되는 것을 방지한다.
    """
    ws = jarvis_data_dir() / "claude_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws
