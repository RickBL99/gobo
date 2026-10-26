# =========================================================
# catalog.py – every Segmind model Gobo can drive (the optional second catalogue).
#
# Segmind publishes its whole catalogue without a key: GET /models lists it
# (50 a page) and GET /model/<slug> returns each model's JSON-Schema of
# inputs. That schema is what lets one studio drive ~400 models without a
# hand-written payload builder for each: models.py maps the canonical
# shot (prompt, aspect, resolution, duration, references…) onto whatever each
# model calls those things.
#
# Two sources, merged:
#   studio/catalog.json       a snapshot shipped with the code (built by
#                             tools/build_catalog.py) — always there;
#   <STUDIO_DIR>/_segmind_catalog.json
#                             a live refresh, written by refresh_live() — new
#                             models appear without a deploy.
# Some of Segmind's schemas are empty; for those the snapshot keeps inputs
# read from the model's docs page, and a refresh never overwrites them with
# nothing.
# =========================================================
import json, os, threading, time
from typing import Dict, List, Optional

import requests

from . import jobs as J

SEG_PUBLIC = "https://api.segmind.com"
BUNDLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")
LIVE = os.path.join(J.BASE, "_segmind_catalog.json")

_cache: Dict = {"t": 0.0, "src": None, "models": []}
_lock = threading.Lock()
_refresh = {"running": False, "done": 0, "total": 0, "error": None, "finished": None}


# ── schema → compact inputs ─────────────────────────────────────────────────
def _media_of(prop: Dict) -> Optional[str]:
    meta = (prop.get("x-file-metadata") or {}).get("allowed-types") or []
    kinds = {t.split("/")[0] for t in meta if isinstance(t, str)}
    for k in ("image", "video", "audio"):
        if k in kinds:
            return k
    if prop.get("format") == "uri" or meta:
        return "file"
    return None


def _options(prop: Dict) -> Optional[List]:
    for key in ("oneOf", "anyOf"):
        alts = prop.get(key)
        if isinstance(alts, list) and alts and all(isinstance(a, dict) and "const" in a for a in alts):
            return [a["const"] for a in alts]
    if isinstance(prop.get("enum"), list):
        return list(prop["enum"])
    return None


def normalize_schema(ps: Dict) -> Dict[str, Dict]:
    """JSON-Schema properties → {name: {t, o, d, mn, mx, md, mul, mi, adv, hid}}."""
    out = {}
    props = (ps or {}).get("properties") or {}
    required = set((ps or {}).get("required") or [])
    for name, p in props.items():
        if not isinstance(p, dict):
            continue
        t = p.get("type")
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), t[0] if t else None)
        rec = {"t": t}
        o = _options(p)
        if o is not None:
            rec["o"] = o
        if p.get("default") is not None:
            rec["d"] = p["default"]
        for a, b in (("minimum", "mn"), ("maximum", "mx")):
            if p.get(a) is not None:
                rec[b] = p[a]
        md = _media_of(p)
        if t == "array":
            items = p.get("items") or {}
            md = md or _media_of(items)
            rec["mul"] = True
            if p.get("maxItems"):
                rec["mi"] = p["maxItems"]
            if items.get("type") == "object":
                rec["obj"] = True
        if md:
            rec["md"] = md
        ui = p.get("x-ui-metadata") or {}
        if ui.get("advanced"):
            rec["adv"] = True
        if ui.get("hidden"):
            rec["hid"] = True
        if name in required:
            rec["req"] = True
        out[name] = rec
    return out


def compact_from_scratch(x: Dict) -> Dict:
    """The research catalogue's record (verbose inputs) → our compact one."""
    inputs = {}
    req = set(x.get("required") or [])
    media = {m["param"]: m for m in (x.get("media_inputs") or [])}
    for name, p in (x.get("inputs") or {}).items():
        if not isinstance(p, dict) or "[" in name:
            continue
        rec = {"t": p.get("type")}
        if p.get("options") is not None:
            rec["o"] = p["options"]
        if p.get("default") is not None:
            rec["d"] = p["default"]
        if p.get("min") is not None:
            rec["mn"] = p["min"]
        if p.get("max") is not None:
            rec["mx"] = p["max"]
        m = media.get(name)
        md = (m or {}).get("kind") or p.get("media")
        if md:
            rec["md"] = md
        if p.get("type") == "array" or (m or {}).get("multiple"):
            rec["mul"] = True
            mi = p.get("maxItems") or (m or {}).get("max_items")
            if mi:
                rec["mi"] = mi
            if p.get("item_fields"):
                rec["obj"] = True
        if p.get("advanced"):
            rec["adv"] = True
        if p.get("hidden"):
            rec["hid"] = True
        if p.get("required") or name in req:
            rec["req"] = True
        inputs[name] = rec
    return {
        "slug": x["slug"], "name": x.get("name") or x["slug"], "provider": x.get("provider") or "",
        "category": x.get("category"), "also_in": x.get("also_in") or [],
        "caps": x.get("capabilities") or [], "inputs": inputs,
        "cost": x.get("avg_cost_usd"), "latency": x.get("avg_latency_s"),
        "price": (x.get("price") or "")[:160], "new": bool(x.get("is_new")),
        "pop": x.get("popularity_score") or 0, "listed": x.get("listed_in_public_catalog", True),
        "soon": bool(x.get("coming_soon")), "created": (x.get("created_at") or "")[:10],
        "desc": (x.get("notes") or "")[:140],
    }


# Segmind's own category names, in our words. Ours decide which picker a
# model lands in; theirs are coarser (imageToImage holds edits AND upscalers).
_SEG_CAT = {"textToImage": "text-to-image", "imageToImage": "image-edit",
            "textToVideo": "text-to-video", "imageToVideo": "image-to-video",
            "videoToVideo": "video-edit", "upscale": "upscale"}


def compact_from_live(m: Dict, prev: Optional[Dict]) -> Optional[Dict]:
    inputs = normalize_schema(m.get("parameters_schema") or {})
    if not inputs and prev:
        inputs = prev.get("inputs") or {}
    if not inputs:
        return None
    cat = (prev or {}).get("category") or _SEG_CAT.get(m.get("category"), m.get("category") or "")
    rec = dict(prev or {})
    rec.update({
        "slug": m["slug"], "name": m.get("name") or m["slug"], "provider": m.get("provider") or rec.get("provider", ""),
        "category": cat, "inputs": inputs, "cost": m.get("average_cost", rec.get("cost")),
        "latency": m.get("latency_seconds", rec.get("latency")), "new": bool(m.get("is_new")),
        "pop": m.get("popularity_score") or 0, "listed": True, "soon": bool(m.get("coming_soon")),
        "created": (m.get("created_at") or rec.get("created") or "")[:10],
        "desc": (m.get("description") or rec.get("desc") or "")[:140],
    })
    rec.setdefault("also_in", [])
    rec.setdefault("caps", [])
    return rec


# ── load ────────────────────────────────────────────────────────────────────
def _read(path: str) -> Optional[Dict]:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def load(force: bool = False) -> List[Dict]:
    """The merged catalogue. Re-read when the live file changes, so a refresh
    done by one worker reaches every worker without a restart."""
    with _lock:
        try:
            live_m = os.path.getmtime(LIVE)
        except OSError:
            live_m = 0
        if not force and _cache["models"] and _cache["t"] >= live_m:
            return _cache["models"]
        bundled = (_read(BUNDLED) or {}).get("models") or []
        by = {m["slug"]: m for m in bundled}
        live = _read(LIVE) if live_m else None
        if live:
            for m in live.get("models") or []:
                by[m["slug"]] = m
        _cache.update(t=max(live_m, time.time()), src="live" if live else "bundled",
                      models=list(by.values()),
                      refreshed=(live or {}).get("refreshed"))
        return _cache["models"]


def source() -> Dict:
    load()
    return {"source": _cache.get("src"), "refreshed": _cache.get("refreshed"),
            "count": len(_cache["models"]), "refresh": dict(_refresh)}


# ── live refresh ────────────────────────────────────────────────────────────
def refresh_live() -> Dict:
    """Start a background pull of Segmind's public catalogue. Idempotent: a
    refresh already running is reported, not doubled."""
    with _lock:
        if _refresh["running"]:
            return dict(_refresh)
        _refresh.update(running=True, done=0, total=0, error=None, finished=None)
    threading.Thread(target=_pull, daemon=True).start()
    return dict(_refresh)


def _pull() -> None:
    try:
        listing, offset = [], 0
        while True:
            r = requests.get(f"{SEG_PUBLIC}/models", params={"limit": 50, "offset": offset}, timeout=30)
            r.raise_for_status()
            page = r.json()
            listing += page.get("models") or []
            _refresh["total"] = page.get("total") or len(listing)
            if not page.get("has_more") or not page.get("models"):
                break
            offset += len(page["models"])
        prev = {m["slug"]: m for m in load(force=True)}
        out = []
        for i, m in enumerate(listing):
            _refresh["done"] = i + 1
            if m.get("coming_soon"):
                continue
            try:
                d = requests.get(f"{SEG_PUBLIC}/model/{m['slug']}", timeout=20)
                full = d.json() if d.status_code == 200 else m
            except Exception:
                full = m
            rec = compact_from_live({**m, **full}, prev.get(m["slug"]))
            if rec:
                out.append(rec)
            time.sleep(0.12)                      # be a polite client of a free API
        os.makedirs(os.path.dirname(LIVE), exist_ok=True)
        tmp = LIVE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"refreshed": time.time(), "models": out}, f)
        os.replace(tmp, LIVE)
        load(force=True)
    except Exception as e:
        _refresh["error"] = str(e)[:300]
    finally:
        _refresh["running"] = False
        _refresh["finished"] = time.time()
