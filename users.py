"""
Простое хранилище доступа: кто может открывать приложение.
Список хранится в allowed_users.json рядом с приложением — переживает
перезапуски сервера (на бесплатных хостингах диск обычно сохраняется между
деплоями, но не между сменой плана/региона — для серьёзного объёма
переключите на настоящую БД, для одной компании файла достаточно).
"""
import json
import os
from pathlib import Path

DATA_FILE = Path(__file__).parent / "allowed_users.json"


def _load() -> dict:
    if not DATA_FILE.exists():
        return {"allowed": {}, "pending": {}}
    return json.loads(DATA_FILE.read_text())


def _save(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def is_admin(user_id: int, admin_id: int) -> bool:
    return int(user_id) == int(admin_id)


def is_allowed(user_id: int) -> bool:
    data = _load()
    return str(user_id) in data["allowed"]


def add_pending(user_id: int, name: str) -> None:
    data = _load()
    data["pending"][str(user_id)] = name
    _save(data)


def approve(user_id: int) -> str | None:
    data = _load()
    name = data["pending"].pop(str(user_id), None)
    if name is not None:
        data["allowed"][str(user_id)] = name
        _save(data)
    return name


def deny(user_id: int) -> None:
    data = _load()
    data["pending"].pop(str(user_id), None)
    _save(data)


def list_allowed() -> dict:
    return _load()["allowed"]
