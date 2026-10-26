"""Bring your own key (STUDIO_KEYS=visitor): the public demo.

A visitor signs in with their own Comfy API key. Comfy checks it; the studio
keeps it in this process's memory under the visitor's id and writes it
nowhere: not to disk, not to a log, not to the session cookie, which carries
only the id. Every Comfy call made for that visitor uses it, whether it comes
from a page request or from the pump finishing their takes. A restart forgets
every key, and the sign-in page sends again one the browser was asked to
remember.

The id is a hash of the key, so the same key always finds the same reels, and
a visitor sees only reels with their id on them.
"""
import contextvars
import hashlib
import threading
from contextlib import contextmanager
from typing import Dict, Optional

from . import settings

_keys: Dict[str, str] = {}
_lock = threading.Lock()
# who this request (or this pump step) is for
current: contextvars.ContextVar = contextvars.ContextVar("gobo_visitor", default="")


def on() -> bool:
    return settings.VISITOR_KEYS


def visitor_id(key: str) -> str:
    return "v-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def remember(key: str) -> str:
    vid = visitor_id(key)
    with _lock:
        _keys[vid] = key
    return vid


def key_for(vid: str) -> str:
    return _keys.get(vid or "", "")


def forget(vid: str) -> None:
    with _lock:
        _keys.pop(vid or "", None)


def owns(reel: Optional[Dict]) -> bool:
    """Whether the visitor on this request may see a reel (always, off the demo)."""
    return not on() or bool(reel and reel.get("owner") and reel.get("owner") == current.get())


@contextmanager
def acting_for(vid: str):
    """Comfy calls inside run on this visitor's key (a no-op off the demo)."""
    from . import comfy
    if not on():
        yield True
        return
    key = key_for(vid)
    t1, t2 = current.set(vid or ""), comfy.KEY.set(key)
    try:
        yield bool(key)
    finally:
        comfy.KEY.reset(t2)
        current.reset(t1)
