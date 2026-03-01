import json
import os
from datetime import datetime, timezone

DB_FILE = os.path.join(os.path.dirname(__file__), "members.json")


def _load() -> list[dict]:
    if not os.path.exists(DB_FILE):
        return []
    with open(DB_FILE, "r") as f:
        return json.load(f)


def _save(members: list[dict]):
    with open(DB_FILE, "w") as f:
        json.dump(members, f, indent=2, ensure_ascii=False)


def add_member(user_id: int, handle: str, name: str, profession: str):
    members = _load()

    # Update if already exists, otherwise append
    for m in members:
        if m["user_id"] == user_id:
            m["handle"] = handle
            m["name"] = name
            m["profession"] = profession
            m["verified_at"] = datetime.now(timezone.utc).isoformat()
            _save(members)
            return

    members.append({
        "user_id": user_id,
        "handle": handle,
        "name": name,
        "profession": profession,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    })
    _save(members)


def get_member(user_id: int) -> dict | None:
    for m in _load():
        if m["user_id"] == user_id:
            return m
    return None


def get_all_members() -> list[dict]:
    return _load()
