from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from services.storage_utils import get_users_bucket


def load_storage(storage_path: str) -> dict:
    if not os.path.exists(storage_path):
        return {"users": {}}
    try:
        with open(storage_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
            if not isinstance(payload, dict):
                return {"users": {}}
            get_users_bucket(payload)
            return payload
    except json.JSONDecodeError:
        return {"users": {}}


def save_storage(storage_path: str, data: dict) -> None:
    directory = os.path.dirname(storage_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-data-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, storage_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_user(storage_path: str, user_id: int) -> dict | None:
    data = load_storage(storage_path)
    return get_users_bucket(data).get(str(user_id))


def create_user(storage_path: str, user_id: int, date_format: str) -> dict:
    data = load_storage(storage_path)
    users = get_users_bucket(data)
    now = datetime.now(timezone.utc).strftime(date_format)
    users[str(user_id)] = {
        "registered_at": now,
        "channels": [],
        "last_query": "",
        "last_range": {"from": None, "to": None},
        "last_parse": None,
    }
    save_storage(storage_path, data)
    return users[str(user_id)]


def ensure_or_create_user(storage_path: str, user_id: int, date_format: str) -> dict:
    user = get_user(storage_path, user_id)
    if user:
        return user
    return create_user(storage_path, user_id, date_format)


def upsert_user(storage_path: str, user_id: int, user_payload: dict) -> None:
    data = load_storage(storage_path)
    get_users_bucket(data)[str(user_id)] = user_payload
    save_storage(storage_path, data)
