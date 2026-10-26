# =========================================================
# jobs.py – Gobo's reels and takes, on disk.
#
# STATE GOES ON DISK, NOT IN MEMORY . Production runs
# eight gunicorn workers: a take is started in one and its status is asked for
# from whichever worker the load balancer picks. And a video takes minutes,
# longer than Azure lets a request live (230 s), longer than a deploy leaves a
# worker alive. So:
#   * a take is one JSON file, written atomically (tmp + os.replace), one file
#     per take so two workers never rewrite each other's takes;
#   * an async take (video) stores the provider's poll URL, and ANY worker
#     that is asked for the reel advances it — the thread that submitted it
#     may be long gone;
#   * a sync take (most images) that outlives its worker is caught by the
#     heartbeat and marked failed rather than spinning for ever.
# Outputs are downloaded the moment they exist: provider links expire.
# =========================================================
import hashlib
import json, os, re, time, uuid, shutil
from typing import Dict, List, Optional


def _base() -> str:
    # STUDIO_DIR in production (the Docker image uses /data); ./data otherwise
    return os.path.abspath(os.getenv("STUDIO_DIR") or "data")


BASE = _base()
REEL_TTL_DAYS = int(os.getenv("STUDIO_REEL_TTL_DAYS", "60"))
# A ceiling on the whole data folder, in GB (0: none). The public demo keeps its
# reels on the plan's storage, which our own app shares, so strangers' films
# must never fill it; our own app has no ceiling, and its work is never thrown
# away for space.
KEEP_GB = float(os.getenv("STUDIO_KEEP_GB") or 0)
# a sync call is cut off by its own 300 s timeout; give it room before calling
# the worker dead. An async take is never stale while its poll URL answers.
SYNC_STALE = 420
ASYNC_STALE = 45 * 60
# work done here (a grade) says where it is every few seconds while it runs
LOCAL_STALE = 90
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ID = re.compile(r"^[a-z0-9-]{6,40}$")


def safe_id(v: str) -> str:
    v = str(v or "")
    if not _ID.match(v):
        raise ValueError("bad id")
    return v


def reel_dir(reel: str) -> str:
    return os.path.join(BASE, safe_id(reel))


def _write(path: str, data: Dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)                        # a reader never sees half a file


def _read(path: str) -> Optional[Dict]:
    """The take or reel at `path`, or None if there is none.

    On Azure Files (the /home share) os.replace is not atomic for a reader in
    another process: mid-rename the file can be missing or empty for a moment.
    Eight workers rewrite a running take every few seconds, so a single read
    WILL eventually land in that gap — and a caller that took None as "gone"
    dropped live clips. Retry briefly before answering None."""
    for i in range(4):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            if i == 3:
                return None
            time.sleep(0.03 * (i + 1))
    return None


# ── reels ───────────────────────────────────────────────────────────────────
def new_reel(name: str = "", app: str = "studio", owner: str = "") -> Dict:
    purge_old()
    rid = uuid.uuid4().hex[:12]
    reel = {"id": rid, "name": (name or "").strip()[:80] or "Untitled reel",
            "created": time.time(), "app": app, "owner": owner}
    for sub in ("takes", "out", "refs"):
        os.makedirs(os.path.join(reel_dir(rid), sub), exist_ok=True)
    _write(os.path.join(reel_dir(rid), "reel.json"), reel)
    return reel


def get_reel(reel: str) -> Optional[Dict]:
    return _read(os.path.join(reel_dir(reel), "reel.json"))


def rename_reel(reel: str, name: str) -> Dict:
    r = get_reel(reel) or {"id": reel, "created": time.time()}
    r["name"] = (name or "").strip()[:80] or r.get("name") or "Untitled reel"
    _write(os.path.join(reel_dir(reel), "reel.json"), r)
    return r


def list_reels(limit: int = 60, app: str = "") -> List[Dict]:
    """Newest first. `app` keeps the Content Generator's sessions and the
    studio's reels apart (a reel made before the tag counts as the studio's)."""
    out = []
    if not os.path.isdir(BASE):
        return out
    for rid in os.listdir(BASE):
        try:
            r = get_reel(rid)
        except ValueError:
            continue
        if not r or (app and r.get("app", "studio") != app):
            continue
        tdir = os.path.join(reel_dir(rid), "takes")
        files = [f for f in os.listdir(tdir) if f.endswith(".json")] if os.path.isdir(tdir) else []
        r["takes"] = len(files)
        r["touched"] = os.path.getmtime(tdir) if os.path.isdir(tdir) else r.get("created", 0)
        cover = None
        for f in sorted(files, reverse=True):
            t = _read(os.path.join(tdir, f)) or {}
            if t.get("status") == "done" and t.get("kind") == "image":
                cover = t.get("file")
                break
        r["cover"] = cover
        out.append(r)
    out.sort(key=lambda r: r.get("touched", 0), reverse=True)
    return out[:limit]


def purge_old() -> int:
    """Reels untouched for REEL_TTL_DAYS go — but only ones with nothing made
    in them (a New reel never used, a run that failed). Finished work stays in
    its maker's library until they delete it. Guarded: a purge must never take
    the page down with it."""
    removed = 0
    try:
        if not os.path.isdir(BASE):
            return 0
        cutoff = time.time() - REEL_TTL_DAYS * 86400
        for rid in os.listdir(BASE):
            if rid.startswith("_"):              # _active markers, the catalogue cache
                continue
            d = os.path.join(BASE, rid)
            if os.path.isdir(d) and os.path.getmtime(os.path.join(d, "takes") if os.path.isdir(
                    os.path.join(d, "takes")) else d) < cutoff:
                out = os.path.join(d, "out")
                if os.path.isdir(out) and os.listdir(out):
                    continue                     # it holds takes someone made
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    except Exception as e:
        print(f"[studio] purge failed (non-fatal): {e}")
    return removed


def _size(d: str) -> int:
    n = 0
    for path, _, files in os.walk(d):
        for f in files:
            try:
                n += os.path.getsize(os.path.join(path, f))
            except OSError:                      # gone while we looked
                pass
    return n


_trimmed = 0.0


def trim_to_size(force: bool = False) -> int:
    """Over KEEP_GB, whole reels go, the one touched longest ago first, until
    the folder fits again. A reel touched in the last hour stays (a take may
    still be landing in it), and a reel with a film someone shared goes only
    after every other. At most once a minute unless forced. Guarded, like the
    purge."""
    global _trimmed
    if KEEP_GB <= 0 or (not force and time.time() - _trimmed < 60):
        return 0
    _trimmed = time.time()
    removed = 0
    try:
        reels, total = [], 0
        for rid in os.listdir(BASE) if os.path.isdir(BASE) else []:
            d = os.path.join(BASE, rid)
            if rid.startswith("_") or not os.path.isdir(d):
                continue
            size = _size(d)
            total += size
            touched = max(os.path.getmtime(p) for p in (d, os.path.join(d, "takes"), os.path.join(d, "out")) if os.path.exists(p))
            reels.append((os.path.exists(os.path.join(d, SHARED)), touched, size, d))
        recent = time.time() - 3600
        for _shared, touched, size, d in sorted(reels):
            if total <= KEEP_GB * 1e9:
                break
            if touched > recent:
                continue
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            removed += 1
        if removed:
            print(f"[studio] over {KEEP_GB:g} GB: cleared the {removed} reel(s) touched longest ago")
    except Exception as e:
        print(f"[studio] trim failed (non-fatal): {e}")
    return removed


# ── shared films ────────────────────────────────────────────────────────────
# A share is a public link to one finished take: <BASE>/_shares/<token>.json
# says which, the take carries the token (so sharing again gives the same
# link), and the reel holds a SHARED marker while any of its takes is shared.
SHARES = os.path.join(BASE, "_shares")
SHARED = ".shared"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,32}$")


def get_share(token: str) -> Optional[Dict]:
    return _read(os.path.join(SHARES, token + ".json")) if _TOKEN.match(token or "") else None


def put_share(token: str, data: Dict) -> None:
    _write(os.path.join(SHARES, token + ".json"), data)
    open(os.path.join(reel_dir(data["reel"]), SHARED), "a").close()


def drop_share(token: str, reel: str) -> None:
    if _TOKEN.match(token or ""):
        try:
            os.remove(os.path.join(SHARES, token + ".json"))
        except OSError:
            pass
    if not any(t.get("share") for t in list_takes(reel)):
        try:
            os.remove(os.path.join(reel_dir(reel), SHARED))
        except OSError:
            pass


# ── the library: every finished take, across reels ──────────────────────────
# Reading every take on the share for each look would be thousands of small
# reads over SMB, so each worker keeps a reel's finished takes until its takes
# directory or reel.json changes (every write is a rename, which touches the
# directory), and re-reads after LIB_TTL regardless.
LIB_TTL = 120
_lib: Dict[str, tuple] = {}


def owner_of(t: Dict, reel: Optional[Dict]) -> str:
    """Who made a take: its own `by`, else whoever made the reel. Takes from
    before names were kept have neither."""
    return t.get("by") or (reel or {}).get("owner") or ""


def library(app: str = "") -> List[Dict]:
    """Every finished take in `app`'s reels, newest first, each with its
    reel's name and its maker."""
    out = []
    if not os.path.isdir(BASE):
        return out
    now, seen = time.time(), set()
    for rid in os.listdir(BASE):
        if rid.startswith("_"):
            continue
        try:
            d = reel_dir(rid)
            key = (os.stat(os.path.join(d, "takes")).st_mtime_ns,
                   os.stat(os.path.join(d, "reel.json")).st_mtime_ns)
        except (ValueError, OSError):
            continue
        seen.add(rid)
        hit = _lib.get(rid)
        if not hit or hit[0] != key or now - hit[1] > LIB_TTL:
            r = get_reel(rid)
            if not r:
                continue
            hit = (key, now, r, [t for t in list_takes(rid) if t.get("status") == "done" and t.get("file")])
            _lib[rid] = hit
        r, done = hit[2], hit[3]
        if app and r.get("app", "studio") != app:
            continue
        out += [{**t, "reel_name": r.get("name") or "", "owner": owner_of(t, r)} for t in done]
    for rid in [x for x in _lib if x not in seen]:
        _lib.pop(rid, None)
    out.sort(key=lambda t: t["id"], reverse=True)   # ids sort in creation order
    return out


# ── takes ───────────────────────────────────────────────────────────────────
def _take_path(reel: str, take: str) -> str:
    return os.path.join(reel_dir(reel), "takes", f"{safe_id(take)}.json")


def next_shot(reel: str) -> int:
    # a board's shots own their numbers before they have takes
    used = [t.get("shot", 0) for t in list_takes(reel)]
    used += [s.get("n", 0) for s in ((get_board(reel) or {}).get("shots") or [])]
    return max(used or [0]) + 1


# ── the board: a spot's kit and shot list ───────────────────────────────────
def _board_path(reel: str) -> str:
    return os.path.join(reel_dir(reel), "board.json")


def get_board(reel: str, known: Optional[bool] = None) -> Optional[Dict]:
    """The spot's kit and shot list, {} if the reel has none — or None when it
    has one that can't be read just now. reel.json says whether there is one,
    so a missing file is only waited for (the share's rename gap) when there
    should be one, and a caller never mistakes a blink for an empty board."""
    path = _board_path(reel)
    if known is None:
        known = bool((get_reel(reel) or {}).get("board"))
    if not known and not os.path.exists(path):
        return {}
    b = _read(path)
    return b if b is not None else (None if known else {})


def put_board(reel: str, board: Dict) -> Dict:
    _write(_board_path(reel), board)
    r = get_reel(reel)
    if r is not None and not r.get("board"):
        r["board"] = True
        _write(os.path.join(reel_dir(reel), "reel.json"), r)
    return board


def new_take(reel: str, shot: int, index: int, **fields) -> Dict:
    trim_to_size()
    # ids sort in creation order, so a directory listing is already the reel
    tid = f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:6]}"
    t = {"id": tid, "reel": reel, "shot": shot, "letter": LETTERS[index % len(LETTERS)],
         "status": "queued", "created": time.time(), "beat": time.time()}
    t.update(fields)
    _write(_take_path(reel, tid), t)
    return t


def get_take(reel: str, take: str) -> Optional[Dict]:
    return _read(_take_path(reel, take))


def update_take(reel: str, take: str, **kw) -> Dict:
    path = _take_path(reel, take)
    t = _read(path)
    if t is None:
        if os.path.exists(path):
            # there but unreadable: never replace a whole take with just these fields
            raise RuntimeError(f"take {take} could not be read; left as it was")
        t = {"id": take, "reel": reel}
    t.update(kw)
    t["beat"] = time.time()
    _write(_take_path(reel, take), t)
    return t


def list_takes(reel: str) -> List[Dict]:
    tdir = os.path.join(reel_dir(reel), "takes")
    if not os.path.isdir(tdir):
        return []
    out = []
    for f in sorted(os.listdir(tdir)):
        if f.endswith(".json"):
            t = _read(os.path.join(tdir, f))
            if t:
                out.append(stale_check(t))
    return out


def stale_check(t: Dict) -> Dict:
    """A take whose worker died says so instead of rendering for ever."""
    if t.get("status") == "queued" and t.get("reel") and t.get("id") and is_queued(t["reel"], t["id"]):
        return t                                 # waiting its turn in line, not lost
    if t.get("status") in ("queued", "running"):
        local = t.get("model") == "local"
        limit = LOCAL_STALE if local else ASYNC_STALE if t.get("poll_url") else SYNC_STALE
        if time.time() - float(t.get("beat") or t.get("created") or 0) > limit:
            t["status"] = "failed"
            if local:
                t["reason"] = "restart"
                t["error"] = t.get("error") or "The studio restarted while it was working on this."
            else:
                t["reason"] = "timeout"
                t["error"] = t.get("error") or ("We stopped waiting for the result. A run that "
                                                "finishes later may still be billed.")
    return t


def claim(reel: str, take: str, ttl: float = 20.0) -> bool:
    """One worker at a time advances an async take: an O_EXCL lock file, and a
    lock older than `ttl` is taken over (its holder died mid-poll)."""
    lock = _take_path(reel, take) + ".lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(time.time()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(lock) > ttl:
                os.utime(lock, None)
                return True
        except Exception:
            pass
        return False


def release(reel: str, take: str) -> None:
    try:
        os.remove(_take_path(reel, take) + ".lock")
    except Exception:
        pass


# ── a run's exact request, while it's out ───────────────────────────────────
# Router collects a dropped run when the same Idempotency-Key comes back with
# the same body, and a rebuilt body isn't the same (references upload again
# under new links), so the one that was sent waits here until it lands.
def _call_path(reel: str, take: str) -> str:
    return _take_path(reel, take)[:-len(".json")] + ".call"


def put_call(reel: str, take: str, call: Dict) -> None:
    _write(_call_path(reel, take), call)


def get_call(reel: str, take: str) -> Optional[Dict]:
    return _read(_call_path(reel, take))


def drop_call(reel: str, take: str) -> None:
    try:
        os.remove(_call_path(reel, take))
    except FileNotFoundError:
        pass


def delete_take(reel: str, take: str) -> None:
    drop_call(reel, take)
    t = get_take(reel, take) or {}
    for k in ("file", "thumb", "poster"):
        if t.get(k):
            try:
                os.remove(os.path.join(reel_dir(reel), "out", os.path.basename(t[k])))
            except Exception:
                pass
    try:
        os.remove(_take_path(reel, take))
    except Exception:
        pass


# ── the active list: takes waiting on a provider ────────────────────────────
# One empty file per running async take, so the pump reads a short directory
# instead of every reel on the share.
ACTIVE = os.path.join(BASE, "_active")


def mark_active(reel: str, take: str) -> None:
    os.makedirs(ACTIVE, exist_ok=True)
    open(os.path.join(ACTIVE, f"{safe_id(reel)}__{safe_id(take)}"), "w").close()


def take_exists(reel: str, take: str) -> bool:
    return os.path.exists(_take_path(reel, take))


def is_active(reel: str, take: str) -> bool:
    return os.path.exists(os.path.join(ACTIVE, f"{safe_id(reel)}__{safe_id(take)}"))


def unmark_active(reel: str, take: str) -> None:
    try:
        os.remove(os.path.join(ACTIVE, f"{reel}__{take}"))
    except Exception:
        pass


def active() -> List[tuple]:
    try:
        names = os.listdir(ACTIVE)
    except OSError:
        return []
    return [tuple(n.split("__", 1)) for n in names if "__" in n]


# ── the line: takes waiting for a free slot at their provider ───────────────
# One empty file per waiting take, named take-first so a listing is the order
# they were asked for in (a take id starts with its creation time).
QUEUE = os.path.join(BASE, "_queue")
LANES = os.path.join(BASE, "_lanes.json")
LANE_MEMORY = 7 * 24 * 3600              # a limit a provider named is trusted this long


def enqueue(reel: str, take: str) -> None:
    os.makedirs(QUEUE, exist_ok=True)
    open(os.path.join(QUEUE, f"{safe_id(take)}__{safe_id(reel)}"), "w").close()


def dequeue(reel: str, take: str) -> None:
    try:
        os.remove(os.path.join(QUEUE, f"{take}__{reel}"))
    except Exception:
        pass


def is_queued(reel: str, take: str) -> bool:
    return os.path.exists(os.path.join(QUEUE, f"{safe_id(take)}__{safe_id(reel)}"))


def queued() -> List[tuple]:
    """(reel, take) waiting, oldest first."""
    try:
        names = sorted(os.listdir(QUEUE))
    except OSError:
        return []
    return [tuple(reversed(n.split("__", 1))) for n in names if "__" in n]


def hold(name: str, ttl: float = 15.0) -> bool:
    """A lock on the share for work only one worker may do at a time (handing
    out provider slots). A lock older than `ttl` is taken over."""
    lock = os.path.join(BASE, f"_{name}.lock")
    os.makedirs(BASE, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(lock) > ttl:
                os.utime(lock, None)
                return True
        except Exception:
            pass
        return False


def drop(name: str) -> None:
    try:
        os.remove(os.path.join(BASE, f"_{name}.lock"))
    except Exception:
        pass


def lane_caps() -> Dict[str, int]:
    """Limits providers have named ("Concurrent generation limit reached (4)"), by lane."""
    caps = _read(LANES) if os.path.exists(LANES) else None
    now = time.time()
    return {k: int(v["cap"]) for k, v in (caps or {}).items()
            if isinstance(v, dict) and v.get("cap") and now - float(v.get("t") or 0) < LANE_MEMORY}


def learn_cap(lane: str, cap: int) -> None:
    caps = (_read(LANES) if os.path.exists(LANES) else None) or {}
    caps[lane] = {"cap": max(1, int(cap)), "t": time.time()}
    _write(LANES, caps)


def out_path(reel: str, name: str) -> str:
    return os.path.join(reel_dir(reel), "out", os.path.basename(name))


def ref_path(reel: str, name: str) -> str:
    return os.path.join(reel_dir(reel), "refs", os.path.basename(name))


# ── LUTs: .cube files a reel's grades can name ──────────────────────────────
# Kept per reel, by the hash of their text ("lut-<12 hex>"): one id is one
# table, wherever it's loaded (the page's gobo.wasm and the server's agree).
_LUT = re.compile(r"^lut-[0-9a-f]{12}$")


def _lut_dir(reel: str) -> str:
    return os.path.join(reel_dir(reel), "luts")


def lut_id(text: str) -> str:
    return "lut-" + hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def put_lut(reel: str, text: str, meta: Dict) -> Dict:
    lid = lut_id(text)
    d = _lut_dir(reel)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, lid + ".cube"), "w", encoding="utf-8") as f:
        f.write(text)
    meta = {**meta, "id": lid, "added": time.time()}
    _write(os.path.join(d, lid + ".json"), meta)
    return meta


def lut_text(reel: str, lid: str) -> Optional[str]:
    if not _LUT.match(lid or ""):
        return None
    try:
        with open(os.path.join(_lut_dir(reel), lid + ".cube"), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def list_luts(reel: str) -> List[Dict]:
    d = _lut_dir(reel)
    if not os.path.isdir(d):
        return []
    out = [m for m in (_read(os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.endswith(".json")) if m]
    return sorted(out, key=lambda m: m.get("added", 0))
