from __future__ import annotations


def get_users_bucket(data: dict) -> dict:
    users = data.get("users")
    if isinstance(users, dict):
        return users
    data["users"] = {}
    return data["users"]
