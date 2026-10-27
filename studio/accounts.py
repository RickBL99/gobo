"""People who may sign in, each with their own name and password, added,
reset and removed by an admin on the Users page (/users).

They're kept in the data folder as .accounts.json, the passwords hashed with
scrypt and never stored as typed. Who is an admin is the server's to say
(STUDIO_ADMINS, comma-separated names), so nobody can make themselves one.

The shared password (STUDIO_PASSWORD) still lets someone in under the name
they type, but never under a name that has an account: that takes the
account's own password. A session remembers which version of the account it
signed in with, so removing someone or resetting their password signs them
out everywhere.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Dict, List, Optional, Tuple

from . import settings

MIN_PASSWORD = 8
_lock = threading.Lock()
_cache: Dict = {"key": None, "data": {}}


def _path() -> str:
    return os.path.join(os.path.abspath(os.getenv("STUDIO_DIR") or "data"), ".accounts.json")


def norm(name: str) -> str:
    """A name as it's kept: single spaces, at most 60 characters."""
    return " ".join((name or "").split())[:60]


def is_admin(name: str) -> bool:
    return bool(name) and norm(name).lower() in settings.ADMINS


def _load() -> Dict[str, Dict]:
    """Every account by its lower-case name (read again when the file changes)."""
    path = _path()
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (st.st_mtime_ns, st.st_size)
    if _cache["key"] != key:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f).get("users") or {}
        except (OSError, ValueError):
            return _cache["data"]                 # mid-write on a share: the last good copy
        _cache.update(key=key, data=data)
    return _cache["data"]


def _save(users: Dict[str, Dict]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{secrets.token_hex(4)}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"users": users}, f, indent=1)
    os.replace(tmp, path)
    _cache.update(key=None)


def _hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def _matches(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), h)


def any_accounts() -> bool:
    return bool(_load())


def has(name: str) -> bool:
    return norm(name).lower() in _load()


def version(name: str) -> Optional[int]:
    """Which version of the account a session must carry (None: no account)."""
    a = _load().get(norm(name).lower())
    return a.get("rev", 1) if a else None


def check(name: str, password: str) -> Optional[Tuple[str, int]]:
    """(the account's name, its version) when the password is right."""
    a = _load().get(norm(name).lower())
    if not a or not _matches(password, a.get("hash", "")):
        return None
    return a["name"], a.get("rev", 1)


def _valid(name: str, password: str) -> str:
    if len(name) < 2:
        return "A username needs two characters at least."
    if any(c in name for c in ":,<>\"'"):
        return "A username can't have : , < > or quotes in it."
    if len(password or "") < MIN_PASSWORD:
        return f"A password needs {MIN_PASSWORD} characters at least."
    return ""


def add(name: str, password: str, by: str) -> Dict:
    name = norm(name)
    why = _valid(name, password)
    if why:
        raise ValueError(why)
    with _lock:
        users = dict(_load())
        if name.lower() in users:
            raise ValueError(f"{name} already has an account: reset their password instead.")
        users[name.lower()] = {"name": name, "hash": _hash(password), "rev": 1, "added": time.time(), "by": by}
        _save(users)
        return _public(users[name.lower()])


def set_password(name: str, password: str, by: str) -> Dict:
    why = _valid(norm(name), password)
    if why:
        raise ValueError(why)
    with _lock:
        users = dict(_load())
        a = users.get(norm(name).lower())
        if not a:
            raise KeyError(name)
        users[norm(name).lower()] = {**a, "hash": _hash(password), "rev": a.get("rev", 1) + 1,
                                     "changed": time.time(), "changed_by": by}
        _save(users)
        return _public(users[norm(name).lower()])


def remove(name: str) -> None:
    with _lock:
        users = dict(_load())
        if users.pop(norm(name).lower(), None) is None:
            raise KeyError(name)
        _save(users)


def signed_in(name: str) -> None:
    """Note when someone last signed in (shown on the Users page)."""
    with _lock:
        users = dict(_load())
        a = users.get(norm(name).lower())
        if a:
            users[norm(name).lower()] = {**a, "last": time.time()}
            _save(users)


def _public(a: Dict) -> Dict:
    return {k: a.get(k) for k in ("name", "added", "by", "changed", "last")} | {"admin": is_admin(a["name"])}


def listing() -> List[Dict]:
    return sorted((_public(a) for a in _load().values()), key=lambda a: a["name"].lower())
