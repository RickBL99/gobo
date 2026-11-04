"""Colour from shot to shot.

Every clip is a fresh render, and a clip model decides its colour on its own:
an extension comes back darker and bluer, two angles of a scene were never the
same, and a drawn film shows it most, because flat colour has nowhere to hide.
The prompt, the sheets and the style picture get a shot into the neighbourhood;
this is what holds the value.

Two things, both through the Rust the Lab runs in the page (crates/scrim's
matching, crates/flag's cut check), run here by wasmtime on frames read small:

  match()  — a clip against a reference (its own keyframe, or the last frame of
             the shot before): the CDL that pulls the clip's tones onto the
             reference's, and how far apart they were before and after (ΔE in
             Oklab ×100). The grader applies the CDL frame by frame.
  check()  — each cut of a film: does shot B pick up where A left off (a score
             and a verdict: match, drift, jump), and how far apart in colour.

The match is statistics, not pixels — the spread of each channel and how
colourful the frame is — so a 320-pixel frame is as good as the full one and a
hundred times quicker to read.
"""
import json
import os
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps

from . import rust as R

Frame = Tuple[bytes, int, int]          # RGBA bytes, width, height

SIZE = 320          # frames for a match are read this wide
CUT_SIZE = 480      # frames for a cut check (the Lab's own size)
ALREADY = 1.0       # ΔE under which a clip already matches: no take made
GAIN = 0.8          # a match is made only when it closes the gap to this much of what it was
COLOUR_DRIFT = 2.5  # ΔE across a cut past which the cut drifts in colour, whatever else matches
MATCH_AT = ("first", "mid", "last")     # where a clip is sampled for its colour


def plan_match(before: float, after: float) -> Dict:
    """Whether a match is worth a take: not when the clip already matches, and
    not when the CDL found wouldn't close the gap (the two frames differ in
    content more than in colour, and forcing them would be wrong)."""
    if before < ALREADY:
        return {"match": False, "why": "already matches"}
    if after > before * GAIN:
        return {"match": False, "why": "the match wouldn't close the gap: they differ in content, not colour"}
    return {"match": True, "why": f"{before:.1f} → {after:.1f}"}


def _rgba(im: Image.Image, size: int) -> Frame:
    im = ImageOps.exif_transpose(im).convert("RGBA")
    if max(im.size) > size:
        im.thumbnail((size, size))
    return im.tobytes(), im.size[0], im.size[1]


def still_frame(path: str, size: int = SIZE) -> Frame:
    with Image.open(path) as im:
        return _rgba(im, size)


def clip_seconds(path: str) -> float:
    try:
        return float((R.probe(path) or {}).get("duration") or 0)
    except Exception:
        return 0.0


def clip_frames(path: str, ats: Sequence = MATCH_AT, size: int = SIZE) -> List[Frame]:
    """Frames of a clip: 'first', 'mid', 'last', or seconds in. Each is one
    ffmpeg seek, scaled on the way out so the file read back is small."""
    out: List[Frame] = []
    secs = None
    with tempfile.TemporaryDirectory(prefix="frames-") as tmp:
        for i, at in enumerate(ats):
            dst = os.path.join(tmp, f"{i}.png")
            scale = f"scale={size}:-2"
            if at == "last":
                cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-sseof", "-0.5", "-i", path,
                       "-vf", scale, "-update", "1", dst]
            else:
                if at == "mid":
                    secs = clip_seconds(path) if secs is None else secs
                    at = max(0.0, secs / 2)
                elif at == "first":
                    at = 0.0
                cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{float(at):.3f}", "-i", path,
                       "-vf", scale, "-frames:v", "1", dst]
            r = R.run(cmd, timeout=90)
            if r.returncode != 0 or not os.path.exists(dst):
                raise ValueError("Could not read a frame from that clip.")
            out.append(still_frame(dst, size))
    return out


def frames_of(path: str, kind: str, size: int = SIZE, ats: Sequence = MATCH_AT) -> List[Frame]:
    return clip_frames(path, ats, size) if kind == "video" else [still_frame(path, size)]


def _lend(g, frames: Sequence[Frame]) -> Tuple[int, int]:
    return g.put(b"".join(f[0] for f in frames))


def match(take: Sequence[Frame], reference: Sequence[Frame]) -> Dict:
    """`take` matched to `reference` (frames of each, any number): {cdl,
    before, after}, or {error}."""
    g = R.Wasm("gobo.wasm")
    a, b = _lend(g, take), _lend(g, reference)
    try:
        return json.loads(g.answer(g.call("scrim_match", a[0], a[1], b[0], b[1])))
    finally:
        g.call("free", *a)
        g.call("free", *b)


def compare(a: Frame, b: Frame, continuous: bool = False) -> Dict:
    """Does shot B pick up where shot A left off? A's last frame, B's first:
    {score, verdict, notes, fixes}, or {error}."""
    g = R.Wasm("gobo.wasm")
    pa, pb = g.put(a[0]), g.put(b[0])
    try:
        return json.loads(g.answer(g.call("flag_compare", pa[0], pa[1], a[1], a[2], pb[0], pb[1], b[1], b[2],
                                          1 if continuous else 0)))
    finally:
        g.call("free", *pa)
        g.call("free", *pb)


def check(items: Sequence[Tuple[str, str]], continuous: Optional[Sequence[bool]] = None) -> Dict:
    """A film's cuts, in order: `items` are (path, kind) per take; `continuous[i]`
    says take i is meant to carry straight on from the one before. Each cut:
    {from, to, verdict, score, colour (ΔE before any match), notes, fixes}."""
    ends: List[Tuple[Frame, Frame]] = []
    for path, kind in items:
        if kind == "video":
            first, last = clip_frames(path, ("first", "last"), CUT_SIZE)
        else:
            first = last = still_frame(path, CUT_SIZE)
        ends.append((first, last))
    cuts = []
    for i in range(len(ends) - 1):
        cont = bool(continuous[i + 1]) if continuous and i + 1 < len(continuous) else False
        c = compare(ends[i][1], ends[i + 1][0], cont)
        colour = match([ends[i + 1][0]], [ends[i][1]]).get("before")
        c.update({"from": i, "to": i + 1, "colour": colour})
        # the cut check reads composition and exposure; colour alone can drift under it
        if c.get("verdict") == "match" and colour is not None and colour >= COLOUR_DRIFT:
            c["verdict"] = "drift"
            c.setdefault("notes", []).append(f"colour {colour:.1f} apart")
        cuts.append(c)
    return {"cuts": cuts, "off": sum(1 for c in cuts if c.get("verdict") != "match")}
