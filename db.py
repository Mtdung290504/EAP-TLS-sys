import json
import os
import fcntl
from config import CONFIG

DB_PATH = CONFIG["DB_PATH"]

_EMPTY_DB = {
    "users": {},
    "devices": {},
    "requests": {}
}


def load_db() -> dict:
    """Đọc db.json với shared lock. Không cache."""
    if not os.path.exists(DB_PATH):
        return {k: dict(v) if isinstance(v, dict) else v for k, v in _EMPTY_DB.items()}
    with open(DB_PATH, "r", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            data = json.load(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    # Đảm bảo các key cần thiết tồn tại
    for key in _EMPTY_DB:
        if key not in data:
            data[key] = {}
    return data


def save_db(data: dict) -> None:
    """Ghi db.json với exclusive lock."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with open(DB_PATH, "r+" if os.path.exists(DB_PATH) else "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            json.dump(data, f, ensure_ascii=False, indent="\t")
            f.truncate()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def get_user(uid: str) -> dict | None:
    """Trả về dict thông tin user hoặc None nếu không tồn tại."""
    db = load_db()
    return db["users"].get(uid)


def get_devices(uid: str) -> dict:
    """Trả về dict devices của user, hoặc {} nếu chưa có."""
    db = load_db()
    return db["devices"].get(uid, {})


def get_requests() -> dict:
    """Trả về toàn bộ dict requests."""
    db = load_db()
    return db.get("requests", {})
