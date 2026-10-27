"""A take and its look, opened in ComfyUI with Gobo's own nodes (comfyui/).

"Open in ComfyUI" in the Lab hands ComfyUI a workflow that loads the take
from Gobo, rebuilds its look as nodes (the stock and grade with the sliders, a
LUT, a shot match) and grades it with the same Rust, then saves it. ComfyUI
runs on the colourist's own machine, so it fetches the take by a signed link
that works without signing in and stops working after a week.
"""
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Dict, Optional

from . import jobs as J, settings

LINK_DAYS = 7
FILMS = {"clean", "portra", "t500", "d250", "bw400", "slide", "expired", "bleach", "instant", "vhs"}
GRADES = {"natural", "teal", "noir", "kodachrome", "neon", "desert", "nordic", "pastel"}
# the Look node's sliders and the look's own keys for them (comfyui/nodes.py)
SLIDERS = {"exposure": "exposure", "contrast": "contrast", "pivot": "pivot", "saturation": "sat", "warmth": "temp",
           "tint": "tint", "fade": "fade", "white_point": "white", "grain": "grain", "grain_size": "grain_size",
           "halation": "halation", "vignette": "vignette"}
COLOUR = ("shadows", "highlights", "mono", "bleach", "wheels", "curves", "cdl")


# ── links ComfyUI can fetch ─────────────────────────────────────────────────
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _sig(payload: str) -> str:
    return hmac.new(settings.session_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def sign(reel: str, name: str, days: int = LINK_DAYS) -> str:
    """A token for one file of a reel (a take's file, or luts/<id>.cube)."""
    payload = _b64(json.dumps({"r": reel, "n": name, "e": int(time.time() + days * 86400)}, separators=(",", ":")).encode())
    return f"{payload}.{_sig(payload)}"


def opened(token: str) -> Optional[Dict]:
    """{r, n} for a good, unexpired token; None otherwise."""
    payload, _, sig = (token or "").partition(".")
    if not payload or not hmac.compare_digest(sig, _sig(payload)):
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    if d.get("e", 0) < time.time():
        return None
    return d


def path_of(d: Dict) -> Optional[str]:
    """The file a token stands for, inside its reel only."""
    try:
        reel = J.safe_id(d.get("r"))
    except ValueError:
        return None
    name = str(d.get("n") or "")
    if name.startswith("luts/"):
        p = os.path.join(J.reel_dir(reel), "luts", os.path.basename(name))
    else:
        p = J.out_path(reel, os.path.basename(name))
    return p if os.path.isfile(p) else None


# ── the workflow ────────────────────────────────────────────────────────────
def _round(v):
    """Numbers as a person reads them in the node's box (4 places is finer than any slider)."""
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, dict):
        return {k: _round(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_round(x) for x in v]
    return v


def _name(t: Dict) -> str:
    return f"Shot {t.get('shot') or '?'}{t.get('letter') or ''}"


def workflow(site: str, reel: str, take: Dict, look: Dict, film: str = "", grade: str = "",
             reference: Optional[Dict] = None) -> Dict:
    """ComfyUI's API format: load the take, the look as nodes, grade, save."""
    site = site.rstrip("/")
    link = lambda name: f"{site}/t/{sign(reel, name)}"
    film = film if film in FILMS else "clean"
    grade = grade if grade in GRADES else "natural"
    g: Dict[str, Dict] = {}

    def node(cls: str, inputs: Dict, title: str = "") -> str:
        nid = str(len(g) + 1)
        g[nid] = {"class_type": cls, "inputs": inputs, **({"_meta": {"title": title}} if title else {})}
        return nid

    shot = node("GoboLoadTake", {"link": link(take["file"]), "name": f"{_name(take)} · {take.get('label') or ''}".strip(" ·")},
                f"{_name(take)} from Gobo")
    sliders = {k: round(float(look[v]), 4) for k, v in SLIDERS.items() if isinstance(look.get(v), (int, float))}
    colour = _round({k: look[k] for k in COLOUR if look.get(k) not in (None, 0, 0.0)})
    have = node("GoboLook", {"film": film, "grade": grade, "amount": round(float(look.get("amount", 1.0)), 4), **sliders,
                             "colour": json.dumps(colour)}, "Look")
    lut = look.get("lut3d") if isinstance(look.get("lut3d"), dict) else None
    if lut and lut.get("id") and J.lut_text(reel, lut["id"]) is not None:
        meta = next((m for m in J.list_luts(reel) if m.get("id") in (lut["id"], lut.get("of"))), {})
        have = node("GoboLUT", {"look": [have, 0], "lut": link(f"luts/{lut['id']}.cube"), "amount": round(float(lut.get("mix", 1.0)), 4),
                                "where": "first" if lut.get("first") else "after the look",
                                "name": meta.get("title") or meta.get("name") or ""},
                    f"LUT · {meta.get('title') or meta.get('name') or 'from Gobo'}{' (its rest)' if lut.get('of') else ''}")
    if reference and reference.get("file"):
        ref = node("GoboLoadTake", {"link": link(reference["file"]), "name": _name(reference)}, f"{_name(reference)} (the reference)")
        have = node("GoboShotMatch", {"look": [have, 0], "take": [shot, 0], "reference": [ref, 0]}, f"Match to {_name(reference)}")
    out = node("GoboGrade", {"take": [shot, 0], "look": [have, 0]}, "Gobo Grade")
    stem = f"gobo/{_name(take).replace(' ', '')}-graded"
    if take.get("kind") == "video":
        node("SaveVideo", {"video": [out, 0], "filename_prefix": stem, "format": "mp4"}, "Save")
    else:
        node("SaveImage", {"images": [out, 1], "filename_prefix": stem}, "Save")
    return g
