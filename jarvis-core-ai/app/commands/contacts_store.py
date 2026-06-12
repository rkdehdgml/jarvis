"""
contacts_store.py — 연락처 사전 (data/contacts.json) 공유 저장소
════════════════════════════════════════════════════════════════════════════════
communication.py(이메일/WhatsApp)와 utility.py(연락처 추가/검색 명령)에서
공통으로 사용하는 단순 JSON 기반 연락처 저장소.

포맷: {"이름": {"email": "...", "phone": "+8210..."}}
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
from pathlib import Path

_CONTACTS_FILE = Path(__file__).parent.parent.parent / "data" / "contacts.json"


def load_contacts() -> dict[str, dict[str, str]]:
    if not _CONTACTS_FILE.exists():
        return {}
    try:
        return json.loads(_CONTACTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_contacts(contacts: dict[str, dict[str, str]]) -> None:
    _CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONTACTS_FILE.write_text(json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8")


def find_contact(name: str) -> dict[str, str] | None:
    return load_contacts().get(name)
