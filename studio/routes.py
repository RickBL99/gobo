# =========================================================
# routes.py – Gobo: direct a shot, run it on any Comfy Router model, animate
# it, continue it, stitch the reel.  Mounted at settings.PREFIX.
#
# A camera, framing, angle, light, film, look, grade, composition and camera
# movement compiled into a prompt; takes run in parallel; negative prompt and
# seed; several models on one shot (a shootout); a Director that rewrites the
# brief for the chosen model; a reel that persists; and the video tools: grab a
# frame, continue a shot from its last frame, stitch takes into one clip.
#
#   direction.py   vocabulary + prompt compiler (pure)
#   models.py      every Comfy Router model: capabilities, payload, result
#   jobs.py        reels and takes on disk (multi-worker safe)
# =========================================================
import base64, contextvars, io, json, math, os, re, secrets, shutil, subprocess, threading, time, uuid, zipfile
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from PIL import Image

from . import comfy
from . import comfy_export as CX
from . import direction as D
from . import jobs as J
from . import models as M
from . import rust as R
from . import settings
from . import visitors as V
from .media import probe, run



def _switched_on() -> None:
    """Kill switch: STUDIO_ENABLED=0 in the app settings takes the studio's page
    and API off without a deploy (the settings change restarts the app, which
    also stops the pump)."""
    if os.getenv("STUDIO_ENABLED", "1").strip() == "0":
        raise HTTPException(503, "Gobo is switched off.")


PREFIX = settings.PREFIX
HERE = os.path.dirname(os.path.abspath(__file__))
router = APIRouter(prefix=PREFIX, dependencies=[Depends(_switched_on)])
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

MAX_TAKES = 20            # stills: the Content Generator runs batches; the studio's own stepper stops at 4
MAX_SHOOTOUT = 4
MAX_REF_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
VIDEO_TYPES = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}
POLL_EVERY = 4.0          # seconds between provider polls for one take


# ── the pump: advances every running clip, page open or not ────────────────
# A clip lands minutes after it was asked for, and a provider's result link
# expires soon after that. Waiting for the page to ask would lose any take
# whose tab was closed. So each worker runs one small loop over the active
# list; the per-take lock means only one of them polls a given take.
_pump = {"started": False}
_pump_lock = threading.Lock()


def _ensure_pump() -> None:
    with _pump_lock:
        if _pump["started"]:
            return
        _pump["started"] = True
    threading.Thread(target=_pump_loop, daemon=True, name="studio-pump").start()


def _pump_loop() -> None:
    while True:
        try:
            _pump_once()
        except Exception as e:
            print(f"[studio] pump: {e}")
        time.sleep(2.5)


def _pump_once() -> None:
    for reel, tid in J.active():
        try:
            t = J.get_take(reel, tid)
            gone = t is None and not J.take_exists(reel, tid)
        except ValueError:                        # not a take id at all
            t, gone = None, True
        if t is None:
            # Unreadable is not gone: on the shared disk a take can't be read
            # for a moment while another worker rewrites it. Only a take whose
            # file is really missing leaves the list.
            if gone:
                J.unmark_active(reel, tid)
            continue
        if t.get("status") != "running":
            J.unmark_active(reel, tid)
            continue
        if not t.get("poll_url"):
            # being sent: it holds its slot while its worker beats (_submit). A
            # direct run whose worker died is collected under its own key.
            quiet = time.time() - float(t.get("beat") or t.get("started") or 0)
            tries = int(t.get("resends") or 0)
            if quiet > DEAD and tries < RESENDS and J.get_call(reel, tid) and J.claim(reel, tid):
                try:
                    J.update_take(reel, tid, resends=tries + 1)
                finally:
                    J.release(reel, tid)
                _spawn(_recover, reel, tid)
            elif quiet > J.SYNC_STALE:
                J.unmark_active(reel, tid)
            continue
        if time.time() - float(t.get("polled") or 0) >= POLL_EVERY:
            _advance(reel, tid)
    _dispatch()


def _spawn(fn, *args) -> None:
    # the thread carries the caller's context: on the demo, whose key it runs on
    threading.Thread(target=contextvars.copy_context().run, args=(fn, *args), daemon=True).start()


def _maker(reel: str, t: Dict) -> str:
    return J.owner_of(t, J.get_reel(reel))


# ── the line: each provider gets only so many runs at once ──────────────────
# Animating a whole board asks for every clip in one breath, and a provider
# runs only so many at once: Luma took four and refused the other two
# ("Concurrent generation limit reached (4)", prod 2026-10-24). So every paid
# run waits in line on disk (any worker's pump hands out slots, one worker at a
# time) and starts when its provider has room. Clips run 4 at a time per
# provider; stills aren't held back until a provider names a limit. A limit a
# provider names is learned for its lane, and a take told "busy" or "slow
# down" goes back in line instead of failing.
VIDEO_LANE = int(os.getenv("STUDIO_VIDEO_LANE", "4"))
IMAGE_LANE = int(os.getenv("STUDIO_IMAGE_LANE", "0"))    # 0: no limit of our own
WAIT_REASONS = ("busy", "rateLimit")
MAX_WAITS = 40                          # sent back this often, a take fails and says why
RETRY_AFTER = {"busy": 8.0, "rateLimit": 20.0, "stalled": 0.0}


def _lane(t: Dict) -> str:
    """Who a take queues behind: its provider, clips and stills apart."""
    m = M.MODELS.get(t.get("model")) or {}
    clip = (m.get("kind") or t.get("kind")) in ("video", "vupscale")
    who = m.get("provider") or t.get("model") or "?"
    if m.get("engine") == "comfy":              # Router's limits, per provider behind it
        who = f"Comfy · {who}"
    return f"{who} {'clips' if clip else 'stills'}"


def _cap(lane: str) -> int:
    """How many may run at once in a lane (0: no limit)."""
    own = VIDEO_LANE if lane.endswith(" clips") else IMAGE_LANE
    named = J.lane_caps().get(lane)
    return min(c for c in (own, named) if c) if (own or named) else 0


def _enqueue(reel: str, tid: str) -> None:
    J.enqueue(reel, tid)
    _ensure_pump()
    _dispatch()


def _dispatch() -> None:
    """Start what fits, oldest first. One worker at a time hands out slots,
    so two can't both see the last free one and fill it twice."""
    line = J.queued()
    if not line or not J.hold("dispatch"):
        return
    try:
        busy: Dict[str, int] = {}
        for reel, tid in J.active():
            t = J.get_take(reel, tid)
            if t and t.get("status") == "running":
                busy[_lane(t)] = busy.get(_lane(t), 0) + 1
        ahead: Dict[str, int] = {}
        now = time.time()
        for reel, tid in line:
            try:
                t = J.get_take(reel, tid)
            except ValueError:
                t = None
            if not t or t.get("status") != "queued":
                if t is not None or not J.take_exists(reel, tid):
                    J.dequeue(reel, tid)          # stopped, deleted or already running
                continue
            lane, cap = _lane(t), _cap(_lane(t))
            if float(t.get("not_before") or 0) > now or (cap and busy.get(lane, 0) >= cap):
                n = ahead.get(lane, 0)
                ahead[lane] = n + 1
                if (t.get("ahead"), t.get("lane_cap")) != (n, cap):
                    J.update_take(reel, tid, ahead=n, lane=lane.rsplit(" ", 1)[0], lane_cap=cap)
                continue
            J.update_take(reel, tid, status="running", started=now, ahead=None)
            J.mark_active(reel, tid)
            J.dequeue(reel, tid)
            busy[lane] = busy.get(lane, 0) + 1
            _spawn(_start, reel, tid)
    finally:
        J.drop("dispatch")


def _requeue(reel: str, tid: str, e: "M.RunError") -> None:
    """The provider was full or asked us to slow down: back in line, and its
    lane learns the limit it named (or one fewer than were running)."""
    t = J.get_take(reel, tid) or {}
    J.unmark_active(reel, tid)
    waits = int(t.get("waits") or 0) + 1
    if waits > MAX_WAITS:
        J.update_take(reel, tid, status="failed", reason=e.reason, finished=time.time(),
                      error=f"{e} (Sent back in line {MAX_WAITS} times; the provider stayed full.)")
        return
    if e.reason == "busy":
        lane = _lane(t)
        named = M.busy_limit(str(e))
        if not named:
            running = sum(1 for r, x in J.active() if (J.get_take(r, x) or {}).get("status") == "running"
                          and _lane(J.get_take(r, x) or {}) == lane)
            named = max(1, running)
        J.learn_cap(lane, named)
    J.update_take(reel, tid, status="queued", poll_url=None, request_id=None, progress=None,
                  started=None, waits=waits, wait_reason=e.reason,
                  not_before=time.time() + RETRY_AFTER.get(e.reason, 10.0))
    J.enqueue(reel, tid)


def _err(msg: str, status: int = 400, **kw) -> JSONResponse:
    return JSONResponse({"error": msg, **kw}, status_code=status)


def _reel_or_404(reel: str):
    try:
        r = J.get_reel(reel)
    except ValueError:
        return None
    return r


def _who(request: Request) -> str:
    """Who is signed in: the name the sign-in leaves in the session (on the
    public demo, the visitor's id). It's what puts a take in its maker's
    library."""
    try:
        return str(request.session.get("user_name") or "")[:200]
    except Exception:
        return ""


# ── page ────────────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
async def studio_page(request: Request, stills: int = 0, director: bool = False):
    """The studio, or with stills=1 the Content Generator: the same engine and
    page limited to still models, with the batch tools (variations, prompts
    from a reference image or a whole folder, upsized downloads). director:
    open on the Director (static/director.js), the one-line front door."""
    models = M.public_models()
    if stills:
        models = [m for m in models if m["kind"] in ("image", "upscale")]
    return templates.TemplateResponse(request, "studio.html", {
        "catalogue": json.dumps(D.catalogue()),
        "models": json.dumps(models),
        "stills": bool(stills),
        "api": PREFIX,
        "director": bool(director) and not stills,
        "front": settings.FRONT,
        "sign_in": settings.sign_in_on(),
    })


@router.get("/guide", response_class=HTMLResponse)
async def guide(request: Request):
    """The users' guide: every feature, task by task, searchable."""
    from . import settings as version
    return templates.TemplateResponse(request, "studio_guide.html", {"release": version.RELEASE, "build": version.BUILD, "api": PREFIX,
                                                                     "dir": M.directory()})


@router.get("/app.js")
async def app_js():
    # served by a route, not the static mount, so it ships with the template
    # it belongs to; revalidated so a deploy is seen on the next load
    return FileResponse(os.path.join(HERE, "templates", "studio_app.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@router.get("/catalogue", response_class=JSONResponse)
async def catalogue():
    return {"direction": D.catalogue(), "models": M.public_models()}


# ── prompt ──────────────────────────────────────────────────────────────────
def _shot_inputs(b: Dict) -> Dict:
    return dict(scene=b.get("scene") or "", direction=b.get("direction") or {},
                enhance=bool(b.get("enhance", True)), video=b.get("mode") == "video",
                refs=len(b.get("refs") or []), colors=b.get("colors") or None,
                main_color=b.get("main_color"), ref_names=b.get("ref_names") or None)


@router.post("/compile", response_class=JSONResponse)
async def compile_prompt(request: Request):
    b = await request.json()
    kw = _shot_inputs(b)
    segs = D.segments(kw.pop("scene"), kw.pop("direction"), **kw)
    return {"prompt": " ".join(s["text"] for s in segs), "segments": segs}


@router.post("/direct", response_class=JSONResponse)
async def direct(request: Request):
    """The Director: the brief rewritten for the chosen model."""
    b = await request.json()
    kw = _shot_inputs(b)
    brief = D.compile_prompt(kw.pop("scene"), kw.pop("direction"), **kw)
    if not (b.get("scene") or "").strip():
        return _err("Write a scene first.")
    try:
        out = M.director_rewrite(brief, b.get("model") or "", video=b.get("mode") == "video",
                                 notes=b.get("notes") or "")
    except Exception as e:
        return _err(f"The director could not rewrite this shot: {e}", 502, prompt=brief)
    return out


# ── reels ───────────────────────────────────────────────────────────────────
@router.get("/reels", response_class=JSONResponse)
async def reels(app: str = ""):
    return {"reels": [r for r in J.list_reels(app=app) if V.owns(r)]}


@router.post("/reels", response_class=JSONResponse)
async def create_reel(request: Request):
    try:
        b = await request.json()
    except Exception:
        b = {}
    app = b.get("app") if b.get("app") in ("studio", "cg") else "studio"
    return J.new_reel(b.get("name", ""), app=app, owner=_who(request))


@router.patch("/reels/{reel}", response_class=JSONResponse)
async def rename_reel(reel: str, request: Request):
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    b = await request.json()
    return J.rename_reel(reel, b.get("name", ""))


@router.get("/reels/{reel}", response_class=JSONResponse)
async def get_reel(reel: str):
    r = _reel_or_404(reel)
    if not r:
        return _err("No such reel.", 404)
    _ensure_pump()
    takes = J.list_takes(reel)
    # self-repair: a clip still rendering must be on the pump's list; if it fell
    # off (a worker died between steps, an old race), opening the reel puts it back
    for t in takes:
        if t.get("status") == "running" and t.get("poll_url") and not J.is_active(reel, t["id"]):
            J.mark_active(reel, t["id"])
    # board: null means "there is one, ask again" — never an empty board to save over it
    return {"reel": r, "takes": [_public_take(t) for t in takes],
            "board": J.get_board(reel, known=bool(r.get("board")))}


# ── the board: a spot planned shot by shot ──────────────────────────────────
# A TV spot is a fixed pipeline, not a graph: brief → shot list → keyframes →
# clips → cut. The board holds what stays the same across shots (the kit:
# named references, a seed, the look) and the cards; the takes themselves are
# ordinary takes, tagged with their card and role, run through /run.
BOARD_SHOTS, BOARD_ELEMENTS = 40, 30


def _clean_board(b: Dict) -> Dict:
    def s(v, n=400):
        return str(v or "").strip()[:n]

    def num(v, lo, hi, d):
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return d

    els = []
    for e in (b.get("elements") or [])[:BOARD_ELEMENTS]:
        if isinstance(e, dict) and e.get("id") and e.get("file"):
            els.append({"id": s(e["id"], 40), "name": s(e.get("name"), 60) or "Untitled",
                        "kind": e.get("kind") if e.get("kind") in ("cast", "product", "place") else "cast",
                        "file": os.path.basename(s(e["file"], 120))})
    ids = {e["id"] for e in els}
    shots = []
    for x in (b.get("shots") or [])[:BOARD_SHOTS * 3]:
        if len(shots) >= BOARD_SHOTS:
            break
        if not isinstance(x, dict) or not x.get("id"):
            continue
        trim = x.get("trim") if isinstance(x.get("trim"), list) and len(x["trim"]) == 2 else None
        shots.append({
            "id": s(x["id"], 40), "n": int(num(x.get("n"), 0, 9999, 0)), "scene": s(x.get("scene"), 2000),
            "shot": s(x.get("shot"), 20) or "auto", "move": s(x.get("move"), 20) or "auto",
            "duration": num(x.get("duration"), 1, 60, 5), "elements": [e for e in (x.get("elements") or []) if e in ids],
            "start": "continue" if x.get("start") == "continue" else "cut", "land": bool(x.get("land")),
            "vo": s(x.get("vo"), 600), "super": s(x.get("super"), 200),
            "key": s(x.get("key"), 40), "clip": s(x.get("clip"), 40),
            "trim": [num(trim[0], 0, 600, 0), num(trim[1], 0, 600, 0)] if trim else None,
            "trim_for": s(x.get("trim_for"), 40),
            # a card is an AI shot, or footage someone shot (changed or extended with `tool`)
            "type": "footage" if x.get("type") == "footage" else "shot",
            "tool": s(x.get("tool"), 80), "change": s(x.get("change"), 1000),
            "tooldur": num(x.get("tooldur"), 1, 30, 5)})
    look = b.get("look") if isinstance(b.get("look"), dict) else {}
    seed = b.get("seed")
    return {"brief": s(b.get("brief"), 4000), "title": s(b.get("title"), 120), "notes": s(b.get("notes"), 1000),
            "target": num(b.get("target"), 5, 180, 30),
            "seed": int(seed) if str(seed or "").isdigit() else None, "variants": int(num(b.get("variants"), 1, 4, 1)),
            "look": {k: look.get(k) for k in ("direction", "aspect", "resolution", "pmode", "audio", "colors", "main_color")
                     if k in look},
            "elements": els, "shots": shots, "updated": time.time()}


@router.get("/reels/{reel}/board", response_class=JSONResponse)
async def get_board(reel: str):
    r = _reel_or_404(reel)
    if not r:
        return _err("No such reel.", 404)
    b = J.get_board(reel, known=bool(r.get("board")))
    if b is None:
        return _err("The board couldn't be read just now — try again.", 503)
    return {"board": b}


@router.put("/reels/{reel}/board", response_class=JSONResponse)
async def put_board(reel: str, request: Request):
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    return {"board": J.put_board(reel, _clean_board(await request.json()))}


@router.post("/reels/{reel}/board/plan", response_class=JSONResponse)
async def plan_board(reel: str, request: Request):
    """The planner, as the spot's director: the brief as a shot list that runs to
    the target length on the chosen clip model's durations."""
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    b = await request.json()
    if not (b.get("brief") or "").strip():
        return _err("Write the brief first.")
    try:
        out = await run_in_threadpool(M.plan_spot, b["brief"], float(b.get("target") or 30), b.get("model") or "",
                                      [e for e in (b.get("elements") or []) if isinstance(e, dict)][:BOARD_ELEMENTS],
                                      b.get("direction") or {},
                                      [f for f in (b.get("footage") or []) if isinstance(f, dict) and f.get("id")][:BOARD_SHOTS])
    except Exception as e:
        return _err(f"The director couldn't plan this spot: {e}", 502)
    return out


@router.post("/reels/{reel}/refs/copy", response_class=JSONResponse)
async def copy_ref(reel: str, request: Request):
    """A still from anywhere in the library, brought into this reel as a
    reference — a cast member or product shot once, used in every spot."""
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    b = await request.json()
    pairs = _lib_pairs([b.get("item") or ""])
    if not pairs:
        return _err("No such take.", 404)
    src_reel, tid = pairs[0]
    t = J.get_take(src_reel, tid) if V.owns(J.get_reel(src_reel)) else None
    if not t or t.get("status") != "done" or not t.get("file"):
        return _err("Only a finished take can be a reference.")
    if t.get("kind") != "image":
        return _err("Use a still — grab a frame from the clip first.")
    src = J.out_path(src_reel, t["file"])
    if not os.path.exists(src):
        return _err("That take's file is gone.", 404)
    name = f"ref-{uuid.uuid4().hex[:10]}{os.path.splitext(t['file'])[1].lower()}"
    shutil.copyfile(src, J.ref_path(reel, name))
    return {"name": name, "kind": "image", "w": t.get("w"), "h": t.get("h"),
            "url": f"{PREFIX}/reels/{reel}/file/{name}", "label": t.get("label")}


def _public_take(t: Dict) -> Dict:
    t = dict(t)
    for k in ("poll_url", "payload"):
        t.pop(k, None)
    if t.get("file"):
        t["url"] = f"{PREFIX}/reels/{t['reel']}/file/{t['file']}"
        if t.get("kind") == "image":
            t["thumb_url"] = f"{PREFIX}/reels/{t['reel']}/thumb/{t['file']}?w=360"
    if t.get("poster"):
        t["thumb_url"] = f"{PREFIX}/reels/{t['reel']}/thumb/{t['poster']}?w=360"
    if t.get("status") == "failed":
        t["hint"] = M.failure_hint(t)
    return t


# ── files ───────────────────────────────────────────────────────────────────
def _media_type(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
            ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
            ".zip": "application/zip"}.get(ext, "application/octet-stream")


def _find_file(reel: str, name: str) -> Optional[str]:
    for p in (J.out_path(reel, name), J.ref_path(reel, name)):
        if os.path.exists(p):
            return p
    return None


@router.get("/reels/{reel}/file/{name}")
async def reel_file(reel: str, name: str, download: int = 0):
    try:
        p = _find_file(reel, name)
    except ValueError:
        p = None
    if not p:
        return _err("Not found.", 404)
    return FileResponse(p, media_type=_media_type(p),
                        filename=os.path.basename(p) if download else None)


@router.get("/reels/{reel}/thumb/{name}")
async def reel_thumb(reel: str, name: str, w: int = 360):
    try:
        p = _find_file(reel, name)
    except ValueError:
        p = None
    if not p or not _media_type(p).startswith("image/"):
        return _err("Not found.", 404)
    w = max(64, min(1600, int(w)))
    with Image.open(p) as im:
        im = im.convert("RGB")
        h = max(1, int(im.height * w / im.width))
        im = im.resize((w, h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg",
                             headers={"Cache-Control": "private, max-age=86400"})


@router.post("/reels/{reel}/refs", response_class=JSONResponse)
async def upload_ref(reel: str, file: UploadFile = File(...)):
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    ctype = (file.content_type or "").lower()
    if ctype in IMAGE_TYPES:
        limit, ext, kind = MAX_REF_BYTES, IMAGE_TYPES[ctype], "image"
    elif ctype in VIDEO_TYPES:
        limit, ext, kind = MAX_VIDEO_BYTES, VIDEO_TYPES[ctype], "video"
    else:
        return _err("Use a PNG, JPEG or WebP image, or an MP4, MOV or WebM video.")
    data = await file.read()
    if len(data) > limit:
        return _err(f"That file is over {limit // (1024 * 1024)} MB.")
    name = f"ref-{uuid.uuid4().hex[:10]}{ext}"
    if kind == "image":
        try:
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
                im.verify()
        except Exception:
            return _err("That image could not be read.")
    else:
        w = h = None
    with open(J.ref_path(reel, name), "wb") as f:
        f.write(data)
    return {"name": name, "kind": kind, "w": w, "h": h,
            "url": f"{PREFIX}/reels/{reel}/file/{name}"}


# ── footage: a clip someone shot, brought in as a take ──────────────────────
# A real clip becomes a take like any other — in the strip, the Library, a
# board card — so every clip tool works on it: change it (restyle, swap the
# product or wardrobe from a reference), extend it, lead into it, continue it,
# stitch it. The original is kept; what the page plays and the models get is a
# 1080p H.264 copy (camera files are HEVC or ProRes, 4K, often 50 or 60 fps).
FOOTAGE_EXT = {".mp4", ".mov", ".m4v", ".webm"}


def _aspect_of(w, h) -> str:
    if not w or not h:
        return "16:9"
    want = w / h

    def ratio(a):
        x, y = a.split(":")
        return float(x) / float(y)
    return min((a["id"] for a in D.ASPECTS), key=lambda a: abs(math.log(ratio(a) / want)))


def _make_proxy(reel: str, src: str) -> Dict:
    info = probe(src)                            # raises when it isn't a video
    fps = float(info.get("fps") or 30)
    # long side at most 1920, never upscaled; ffmpeg has already turned a phone clip upright
    vf = ["scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease",
          "scale=trunc(iw/2)*2:trunc(ih/2)*2", "setsar=1", "format=yuv420p"]
    if fps > 30.5:                               # 50/60 fps → 25/30: what the models expect
        vf.append(f"fps={fps / 2 if fps / 2 <= 30.5 else 30:.3f}")
    name = f"footage-{uuid.uuid4().hex[:10]}.mp4"
    dst = J.out_path(reel, name)
    r = run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src, "-map", "0:v:0", "-map", "0:a:0?",
              "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
              "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst], timeout=600)
    if r.returncode != 0 or not os.path.exists(dst):
        raise RuntimeError((r.stderr or "ffmpeg failed")[-200:])
    out = probe(dst)
    first = f"first-{uuid.uuid4().hex[:10]}.jpg"
    run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", dst, "-frames:v", "1", "-q:v", "2",
          J.out_path(reel, first)], timeout=60)
    return {"name": name, "w": out["width"], "h": out["height"], "seconds": round(out["duration"], 3),
            "first": first if os.path.exists(J.out_path(reel, first)) else None}


def _seconds_of(reel: str, name: str) -> float:
    """How long a clip in the reel runs (0 when it can't be read)."""
    try:
        return float(probe(_find_file(reel, name)).get("duration") or 0)
    except Exception:
        return 0.0


def _cut_section(reel: str, name: str, trim) -> str:
    """The stretch [in, out] of a clip as its own file — what a change or an
    extension is run on, so a model gets the piece the card uses."""
    src = _find_file(reel, name)
    if not src:
        raise RuntimeError("the clip is missing")
    full = float(probe(src).get("duration") or 0)
    a = max(0.0, min(float(trim[0]), full - 0.2))
    e = max(a + 0.2, min(float(trim[1]), full))
    if a < 0.05 and e > full - 0.05:
        return name                              # the whole clip: nothing to cut
    out = f"cut-{uuid.uuid4().hex[:10]}.mp4"
    r = run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{e - a:.3f}", "-i", src,
              "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
              "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", J.ref_path(reel, out)], timeout=300)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffmpeg failed")[-200:])
    return out


@router.post("/reels/{reel}/footage", response_class=JSONResponse)
async def import_footage(reel: str, request: Request, file: UploadFile = File(...),
                         shot: int = Form(0), board: str = Form("")):
    """A clip someone shot → a "Footage" take (and its first frame, which a
    lead-in lands on). On a board card it is that card's clip."""
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    ext = VIDEO_TYPES.get((file.content_type or "").lower()) or os.path.splitext(file.filename or "")[1].lower()
    if ext not in FOOTAGE_EXT:
        return _err("Footage comes in as MP4, MOV or WebM.")
    data = await file.read(MAX_VIDEO_BYTES + 1)
    if len(data) > MAX_VIDEO_BYTES:
        return _err("Footage is limited to 100 MB for now. Export a 1080p H.264 clip: 30 seconds is about 40–60 MB.")
    orig = f"orig-{uuid.uuid4().hex[:10]}{ext}"
    with open(J.ref_path(reel, orig), "wb") as f:
        f.write(data)
    try:
        px = await run_in_threadpool(_make_proxy, reel, J.ref_path(reel, orig))
    except Exception as e:
        try:
            os.remove(J.ref_path(reel, orig))
        except OSError:
            pass
        return _err(f"That video couldn't be read: {e}")
    who = _who(request)
    card = {"board": board[:40]} if board else {}
    shot = shot if shot > 0 else J.next_shot(reel)
    base = sum(1 for t in J.list_takes(reel) if t.get("shot") == shot)
    stem = os.path.splitext(os.path.basename(file.filename or "clip"))[0][:48]
    t = J.new_take(reel, shot, base, kind="video", model="local", label=f"Footage · {stem}", prompt="",
                   aspect=_aspect_of(px["w"], px["h"]), request={}, settings={}, action="footage", by=who,
                   source={"file": orig, "name": file.filename or "", "bytes": len(data)},
                   **({**card, "role": "clip"} if card else {}))
    poster = await run_in_threadpool(_poster, reel, J.out_path(reel, px["name"]), px["name"])
    t = J.update_take(reel, t["id"], status="done", file=px["name"], finished=time.time(),
                      **{**poster, "w": px["w"], "h": px["h"], "seconds": px["seconds"]})
    key = None
    if px["first"]:
        k = J.new_take(reel, shot, base + 1, kind="image", model="local", label="First frame", prompt="",
                       aspect=t["aspect"], request={}, settings={}, action="frame", parent=t["id"], by=who,
                       **({**card, "role": "key"} if card else {}))
        key = J.update_take(reel, k["id"], status="done", file=px["first"], finished=time.time(), w=px["w"], h=px["h"])
    return {"take": _public_take(t), "key": _public_take(key) if key else None}


# ── running a shot ──────────────────────────────────────────────────────────
@router.post("/reels/{reel}/run", response_class=JSONResponse)
async def run_shot(reel: str, request: Request):
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    b = await request.json()
    mode = "video" if b.get("mode") == "video" else "image"
    models = [m for m in (b.get("models") or [b.get("model")]) if m][:MAX_SHOOTOUT]
    if not models:
        return _err("Pick a model.")
    for m in models:
        spec = M.MODELS.get(m)
        if not spec or spec["kind"] != mode:
            return _err(f"{m} is not a {mode} model.")
    scene = (b.get("scene") or "").strip()
    prompt = (b.get("prompt_override") or "").strip()
    # A batch of written prompts (variations, a folder of references): one take
    # each, every one directed the same way, each with its own negative.
    items = []
    for x in (b.get("prompts") or [])[:MAX_TAKES]:
        x = x if isinstance(x, dict) else {"scene": x}
        if str(x.get("scene") or "").strip():
            items.append({"scene": str(x["scene"]).strip(),
                          "negative_prompt": str(x.get("negative_prompt") or "").strip()})
    if items and (mode != "image" or len(models) > 1):
        return _err("A batch of prompts runs on one still model.")
    if not scene and not prompt and not items:
        return _err("Write a scene first.")
    if not prompt and not items:
        kw = _shot_inputs(b)
        prompt = D.compile_prompt(kw.pop("scene"), kw.pop("direction"), **kw)
    takes_n = 1 if mode == "video" else max(1, min(MAX_TAKES, int(b.get("takes") or 1)))
    if len(models) > 1:
        takes_n = 1                        # a shootout is one take per model
    if items:
        takes_n = len(items)
    req = {
        "prompt": prompt, "negative_prompt": (b.get("negative_prompt") or "").strip(),
        "aspect": b.get("aspect") or "16:9", "resolution": b.get("resolution") or "",
        "duration": b.get("duration"), "audio": bool(b.get("audio")),
        "seed": b.get("seed"), "refs": [os.path.basename(x) for x in (b.get("refs") or [])][:10],
        "first_frame": os.path.basename(b.get("first_frame") or "") or None,
        "last_frame": os.path.basename(b.get("last_frame") or "") or None,
        "source_video": os.path.basename(b.get("source_video") or "") or None,
    }
    for ref in [*req["refs"], req["first_frame"], req["last_frame"], req["source_video"]]:
        if ref and not _find_file(reel, ref):
            return _err("A reference file is missing — add it again.")
    trim = b.get("source_trim")
    if req["source_video"] and isinstance(trim, list) and len(trim) == 2:
        try:
            req["source_video"] = await run_in_threadpool(_cut_section, reel, req["source_video"], trim)
        except Exception as e:
            return _err(f"Couldn't cut that stretch of the clip: {e}")
    # what each tool takes of footage is checked here, whatever page sent it: a
    # stretch it can't use fails at the provider with nothing useful to say
    short = await run_in_threadpool(_cant_afford, sum(_price(m) for m in models) * takes_n)
    if short:
        return _err(short, 402, reason="credits")
    lengths = {}
    if req["source_video"]:
        secs = await run_in_threadpool(_seconds_of, reel, req["source_video"])
        for m in models:
            lengths[m], why = M.fit_source(m, secs, req["duration"]) if secs else (req["duration"], "")
            if why:
                return _err(why, reason="validation")
    # The settings that made the shot ride on every take, so "Reuse settings"
    # and "Generate again" rebuild it exactly.
    settings = {k: b.get(k) for k in ("mode", "scene", "direction", "enhance", "colors",
                                      "main_color", "aspect", "resolution", "takes", "duration",
                                      "audio", "negative_prompt", "seed", "refs", "first_frame",
                                      "last_frame", "source_video", "prompt_override", "ref_names", "source_trim")}
    _ensure_pump()
    # a board card shoots onto its own shot number; its takes letter on from the last
    try:
        shot = int(b.get("shot") or 0)
    except (TypeError, ValueError):
        shot = 0
    base = 0
    if shot > 0:
        base = sum(1 for t in J.list_takes(reel) if t.get("shot") == shot)
    else:
        shot = J.next_shot(reel)
    board = {"board": str(b["board"])[:40], "role": "clip" if b.get("role") == "clip" else "key"} if b.get("board") else {}
    who = _who(request)
    made = []
    for mi, m in enumerate(models):
        for i in range(takes_n):
            r = dict(req)
            if m in lengths:
                r["duration"] = lengths[m]
            p, st = prompt, settings
            if items:
                kw = _shot_inputs({**b, "scene": items[i]["scene"]})
                p = D.compile_prompt(kw.pop("scene"), kw.pop("direction"), **kw)
                r["prompt"] = p
                r["negative_prompt"] = items[i]["negative_prompt"] or r["negative_prompt"]
                st = {**settings, "scene": items[i]["scene"], "negative_prompt": r["negative_prompt"], "takes": 1}
            # several takes of one shot vary by seed; a locked seed is honoured
            # on take A and stepped for the rest so they still differ
            if r.get("seed") not in (None, "") and i:
                try:
                    r["seed"] = int(r["seed"]) + i
                except Exception:
                    pass
            t = J.new_take(reel, shot, base + mi * takes_n + i, kind=mode, model=m,
                           label=M.MODELS[m]["label"], prompt=p, aspect=r["aspect"],
                           request=r, settings=st, action=b.get("action") or "generate",
                           parent=b.get("parent"), by=who, **board)
            made.append(t)
            _enqueue(reel, t["id"])
    return {"shot": shot, "takes": [_public_take(t) for t in made]}


def _start(reel: str, tid: str) -> None:
    """Send a take the line has given a slot (it is already running and on the
    active list, so the slot counts while the request is in flight)."""
    t = J.get_take(reel, tid)
    if not t or t.get("status") != "running":
        J.unmark_active(reel, tid)
        return
    with V.acting_for(_maker(reel, t)) as keyed:
        if not keyed:                            # the demo restarted and forgot this visitor's key
            J.update_take(reel, tid, status="failed", reason="unavailable", finished=time.time(),
                          error="Sign in again with your Comfy key, then run it again. Nothing was charged.")
            J.unmark_active(reel, tid)
            return
        _send(reel, tid, t)


def _send(reel: str, tid: str, t: Dict) -> None:
    try:
        call = M.build(t["model"], t["request"], lambda n: _find_file(reel, n))
    except Exception as e:
        return _refused(reel, tid, e)
    # each send is a run of its own under a fresh key (Router's queue may still
    # hold the last one), on the route it goes by: direct once Router's queue
    # stalled on this take, or while it's stalled for everyone
    call.update(sync=bool(t.get("comfy_sync")) or comfy.go_sync(), key=uuid.uuid4().hex)
    J.update_take(reel, tid, endpoint=call["endpoint"], payload_preview=M.elide(call["payload"]))
    _deliver(reel, tid, call)


def _refused(reel: str, tid: str, e: Exception) -> None:
    if isinstance(e, M.RunError) and e.reason in WAIT_REASONS:
        return _requeue(reel, tid, e)
    if isinstance(e, M.RunError):
        J.update_take(reel, tid, status="failed", reason=e.reason, error=str(e), finished=time.time())
    else:
        J.update_take(reel, tid, status="failed", reason="client", error=str(e)[:400], finished=time.time())
    J.unmark_active(reel, tid)


def _deliver(reel: str, tid: str, call: Dict) -> None:
    """Send a built call and land what comes back."""
    try:
        res = _submit(reel, tid, call)
    except Exception as e:
        J.drop_call(reel, tid)
        return _refused(reel, tid, e)
    if res.get("poll_url"):
        J.update_take(reel, tid, poll_url=res["poll_url"], request_id=res.get("request_id"),
                      polled=time.time())
    else:
        _store_result(reel, tid, res)
    J.drop_call(reel, tid)


# ── a direct run whose worker died ──────────────────────────────────────────
# A direct run holds one connection for minutes, and a restart in that time
# lost the answer while Comfy finished and billed the clip (two MiniMax clips,
# 2026-10-26: a release at 05:04 in the middle of them). Router keeps the
# generation, and the same Idempotency-Key with the same body collects it
# without charging again; that is also how a direct run past Router's
# ten-minute deadline (504) is collected. So the call that was sent waits
# beside the take, the worker beats while it waits, and the pump resends a
# take whose worker went quiet.
BEAT_EVERY = 30
DEAD = 4 * BEAT_EVERY
RESENDS = 2
SAME_KEY_TRIES = 3


def _submit(reel: str, tid: str, call: Dict) -> Dict:
    J.put_call(reel, tid, call)
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(BEAT_EVERY):
            t = J.get_take(reel, tid)
            if t and t.get("status") == "running":
                try:
                    J.update_take(reel, tid)         # (update_take sets the beat)
                except RuntimeError:
                    pass

    ticker = threading.Thread(target=beat, daemon=True)
    ticker.start()
    try:
        for n in range(SAME_KEY_TRIES):
            try:
                return M.submit(call)
            except M.RunError as e:
                # a direct run that timed out or is still being admitted is still
                # running at Comfy: ask again under the same key, never a new one
                if not call.get("sync") or e.reason not in WAIT_REASONS or n == SAME_KEY_TRIES - 1:
                    raise
                time.sleep(15 * (n + 1))
    finally:
        stop.set()
        ticker.join()


def _recover(reel: str, tid: str) -> None:
    t, call = J.get_take(reel, tid), J.get_call(reel, tid)
    if not t or not call or t.get("status") != "running" or t.get("poll_url"):
        return
    with V.acting_for(_maker(reel, t)) as keyed:
        if not keyed:                            # the demo restarted and forgot this visitor's key
            J.drop_call(reel, tid)
            J.update_take(reel, tid, status="failed", reason="unavailable", finished=time.time(),
                          error="The studio restarted while this was rendering, and it needs your Comfy key to "
                                "collect it. Check your usage at platform.comfy.org before running it again.")
            J.unmark_active(reel, tid)
            return
        _deliver(reel, tid, call)


def _advance(reel: str, tid: str) -> None:
    if not J.claim(reel, tid):
        return
    try:
        t = J.get_take(reel, tid)
        if not t or t.get("status") != "running" or not t.get("poll_url"):
            return
        with V.acting_for(_maker(reel, t)) as keyed:
            if keyed:                            # (a forgotten key: it waits until they sign in again)
                _poll(reel, tid, t)
    finally:
        J.release(reel, tid)


def _poll(reel: str, tid: str, t: Dict) -> None:
    J.update_take(reel, tid, polled=time.time())
    try:
        res = M.poll(t["poll_url"], t["model"])
    except M.RunError as e:
        if e.reason == "stalled":                  # out of Router's stuck queue: send it directly
            J.update_take(reel, tid, comfy_sync=True, waits=max(0, int(t.get("waits") or 0) - 1))
            return _requeue(reel, tid, e)
        if e.reason in WAIT_REASONS:
            return _requeue(reel, tid, e)
        J.update_take(reel, tid, status="failed", reason=e.reason, error=str(e),
                      finished=time.time())
        J.unmark_active(reel, tid)
        return
    if res.get("pending"):
        J.update_take(reel, tid, progress=res.get("progress"))
        return
    _store_result(reel, tid, res)


def _store_result(reel: str, tid: str, res: Dict) -> None:
    """Save the first output locally (provider links expire) and finish."""
    t = J.get_take(reel, tid) or {}
    if t.get("status") == "cancelled":
        J.unmark_active(reel, tid)            # the user stopped waiting; keep it stopped
        return
    try:
        data, ext = M.fetch_output(res)
    except M.RunError as e:
        J.update_take(reel, tid, status="failed", reason=e.reason, error=str(e),
                      finished=time.time())
        J.unmark_active(reel, tid)
        return
    name = f"{t.get('shot', 0):03d}{t.get('letter', 'A')}-{tid[-6:]}{ext}"
    path = J.out_path(reel, name)
    with open(path, "wb") as f:
        f.write(data)
    extra = {}
    if ext in (".jpg", ".png", ".webp"):
        try:
            with Image.open(path) as im:
                extra["w"], extra["h"] = im.size
        except Exception:
            pass
    else:
        name, path = _browser_playable(reel, name, path)
        poster = _poster(reel, path, name)
        if poster:
            extra.update(poster)
        if t.get("action") == "extend":
            extra["new_from"] = _extension_starts(reel, t, path)
    if res.get("cost") is not None:
        extra["cost"] = res["cost"]
    if res.get("credits"):
        extra["credits"] = res["credits"]          # Comfy's X-Comfy-Credits-Used, where it's sent
    if res.get("seed") is not None:
        extra["seed_used"] = res["seed"]
    J.update_take(reel, tid, status="done", file=name, finished=time.time(),
                  elapsed=round(time.time() - float(t.get("started") or t.get("created") or 0), 1),
                  **extra)
    J.unmark_active(reel, tid)


def _browser_playable(reel: str, name: str, path: str):
    """HEVC (some Seedance and Kling outputs) won't play in Chrome or Firefox
    on Windows; re-encode it to H.264 once, here, rather than in every viewer."""
    try:
        r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                  "stream=codec_name", "-of", "csv=p=0", path], timeout=60)
        codec = (r.stdout or "").strip().lower()
        if codec in ("h264", "vp8", "vp9", "av1", ""):
            return name, path
        out_name = os.path.splitext(name)[0] + "-h264.mp4"
        out = J.out_path(reel, out_name)
        r = run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", path, "-c:v", "libx264",
                  "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
                  "-movflags", "+faststart", out], timeout=600)
        if r.returncode == 0 and os.path.exists(out):
            os.remove(path)
            return out_name, out
    except Exception as e:
        print(f"[studio] transcode skipped: {e}")
    return name, path


def _resized_still(path: str, size: int) -> bytes:
    """Long side = size, lightly sharpened, JPEG q95 tagged 300 dpi — what the
    Content Generator has always delivered for filing."""
    from PIL import ImageFilter
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        k = size / max(w, h)
        im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.Resampling.LANCZOS)
        im = im.filter(ImageFilter.SHARPEN)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=95, dpi=(300, 300))
        return buf.getvalue()


def _gray(path: str, at: float) -> Optional[bytes]:
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{at:.3f}", "-i", path, "-frames:v", "1",
                        "-vf", "scale=64:36,format=gray", "-f", "rawvideo", "-"], capture_output=True, timeout=60)
    return r.stdout if r.returncode == 0 and len(r.stdout) == 64 * 36 else None


def _extension_starts(reel: str, t: Dict, path: str) -> float:
    """Where the new part of an extension begins. Some extend models return
    only the continuation, some the original with it attached; which one is
    read off the result itself — it opens on the source's own frames or it
    doesn't — so the board plays just the new seconds either way."""
    try:
        src = _find_file(reel, (t.get("request") or {}).get("source_video") or "")
        if not src:
            return 0.0
        sd, rd = float(probe(src)["duration"]), float(probe(path)["duration"])
        if rd < sd + 0.5:
            return 0.0                           # too short to hold both
        a, b = _gray(src, min(0.3, sd / 2)), _gray(path, min(0.3, sd / 2))
        if not a or not b:
            return 0.0
        diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        return round(sd, 3) if diff < 14 else 0.0
    except Exception as e:
        print(f"[studio] extension check failed (non-fatal): {e}")
        return 0.0


def _poster(reel: str, video_path: str, name: str) -> Dict:
    """First frame + size + duration of a clip, for the strip and the stage."""
    out = {}
    try:
        info = probe(video_path)
        out["w"], out["h"] = info.get("width"), info.get("height")
        out["seconds"] = info.get("duration")
        pname = os.path.splitext(name)[0] + "-poster.jpg"
        r = run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", video_path, "-frames:v", "1",
                  "-q:v", "3", J.out_path(reel, pname)], timeout=60)
        if r.returncode == 0:
            out["poster"] = pname
    except Exception as e:
        print(f"[studio] poster failed (non-fatal): {e}")
    return out


@router.post("/reels/{reel}/takes/{tid}/retry", response_class=JSONResponse)
async def retry(reel: str, tid: str, request: Request):
    """Run the same take again — same request, optionally on another model."""
    try:
        t = J.get_take(reel, tid)
    except ValueError:
        t = None
    if not t:
        return _err("No such take.", 404)
    try:
        b = await request.json()
    except Exception:
        b = {}
    model = b.get("model") or t["model"]
    if model not in M.MODELS or M.MODELS[model]["kind"] != t["kind"]:
        return _err("That model can't run this take.")
    rq = dict(t["request"])
    if rq.get("source_video"):                   # the same footage check as a first run
        secs = await run_in_threadpool(_seconds_of, reel, rq["source_video"])
        if secs:
            rq["duration"], why = M.fit_source(model, secs, rq.get("duration") if model == t["model"] else None)
            if why:
                return _err(why, reason="validation")
    short = await run_in_threadpool(_cant_afford, _price(model))
    if short:
        return _err(short, 402, reason="credits")
    card = {k: t[k] for k in ("board", "role") if t.get(k)}
    nt = J.new_take(reel, t["shot"], ord(t.get("letter", "A")) - 65 + 1, kind=t["kind"],
                    model=model, label=M.MODELS[model]["label"], prompt=t["prompt"],
                    aspect=t.get("aspect"), request=rq, settings=t.get("settings"),
                    action="retry", parent=tid, by=_who(request), **card)
    _enqueue(reel, nt["id"])
    return {"take": _public_take(nt)}


@router.post("/reels/{reel}/takes/{tid}/cancel", response_class=JSONResponse)
async def cancel(reel: str, tid: str):
    """Stop waiting. The provider may still finish (and bill) it; we say so."""
    try:
        t = J.get_take(reel, tid)
    except ValueError:
        t = None
    if not t:
        return _err("No such take.", 404)
    if t.get("model") == "local" and t.get("status") in ("queued", "running"):
        # a grade in the background: its worker sees this and stops
        t = J.update_take(reel, tid, status="cancelled", finished=time.time(), progress=None,
                          error="You stopped it. The original take is untouched; nothing was spent.")
    elif t.get("status") == "queued" and not t.get("poll_url"):
        J.dequeue(reel, tid)
        t = J.update_take(reel, tid, status="cancelled", finished=time.time(),
                          error="You took it out of the line before it was sent. Nothing was spent.")
    elif t.get("status") in ("queued", "running"):
        msg = "You stopped this take. A provider that already started it may still bill it."
        if str(t.get("poll_url") or "").startswith(comfy.BASE):
            # Router can take it out of its queue: nothing is billed for the wait
            said = await run_in_threadpool(comfy.cancel, t["poll_url"])
            if said == "cancelled":
                msg = "You stopped this take and Comfy took it out of its queue."
        t = J.update_take(reel, tid, status="cancelled", finished=time.time(), error=msg)
    return {"take": _public_take(t)}


# ── Open in ComfyUI ─────────────────────────────────────────────────────────
@router.get("/reels/{reel}/comfyui")
async def comfyui_workflow(reel: str, takes: str = "", tid: str = ""):
    """A take, some takes, or every finished Comfy take in the reel, as a ComfyUI
    workflow (workflow_api.json) and the keyframes it loads, zipped."""
    r = _reel_or_404(reel)
    if not r:
        return _err("No such reel.", 404)
    want = [x for x in (takes.split(",") if takes else [tid] if tid else []) if x]
    pool = [t for t in J.list_takes(reel) if t.get("status") == "done"]
    picked = [t for t in pool if t["id"] in want] if want else [t for t in pool if CX.supported(t.get("model"))]
    if not picked:
        return _err("No finished take here was made on a model ComfyUI has a node for.")
    try:
        data, _ = await run_in_threadpool(CX.bundle, picked, lambda n: _find_file(reel, n), r.get("name") or "")
    except ValueError as e:
        return _err(str(e))
    stem = re.sub(r"[^A-Za-z0-9]+", "-", (r.get("name") or "gobo")).strip("-")[:40] or "gobo"
    name = f"{stem}-{picked[0].get('shot', 0):03d}{picked[0].get('letter', '')}" if len(picked) == 1 else stem
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}-comfyui.zip"'})


@router.post("/reels/{reel}/takes/{tid}/recheck", response_class=JSONResponse)
async def recheck(reel: str, tid: str):
    """Ask the provider again about a take we stopped waiting for. Stopping a
    take never stopped the provider: if it finished (and billed), this
    collects the result; if it is still going, the take resumes."""
    try:
        t = J.get_take(reel, tid)
    except ValueError:
        t = None
    if not t:
        return _err("No such take.", 404)
    if t.get("status") not in ("cancelled", "failed") or not t.get("poll_url"):
        return _err("Only a stopped take that reached the provider can be checked again.")
    try:
        res = await run_in_threadpool(M.poll, t["poll_url"], t["model"])
    except M.RunError as e:
        return _err(str(e), 409, reason=e.reason)
    J.update_take(reel, tid, status="running", error=None, reason=None, finished=None,
                  polled=time.time(), progress=res.get("progress"))
    if res.get("pending"):
        J.mark_active(reel, tid)
        _ensure_pump()
    else:
        await run_in_threadpool(_store_result, reel, tid, res)
    return {"take": _public_take(J.get_take(reel, tid))}


@router.delete("/reels/{reel}/takes/{tid}", response_class=JSONResponse)
async def delete_take(reel: str, tid: str):
    try:
        J.delete_take(reel, tid)
    except ValueError:
        return _err("No such take.", 404)
    return {"ok": True}


@router.post("/reels/{reel}/takes/{tid}/star", response_class=JSONResponse)
async def star(reel: str, tid: str, request: Request):
    b = await request.json()
    try:
        t = J.update_take(reel, tid, starred=bool(b.get("starred")))
    except ValueError:
        return _err("No such take.", 404)
    return {"take": _public_take(t)}


# ── post: upscale, frames, stitch ───────────────────────────────────────────
@router.post("/reels/{reel}/takes/{tid}/upscale", response_class=JSONResponse)
async def upscale(reel: str, tid: str, request: Request):
    try:
        t = J.get_take(reel, tid)
    except ValueError:
        t = None
    if not t or t.get("status") != "done" or not t.get("file"):
        return _err("Only a finished take can be upscaled.")
    try:
        b = await request.json()
    except Exception:
        b = {}
    model = b.get("model") or (M.DEFAULT_VIDEO_UPSCALER if t["kind"] == "video"
                               else M.CUTOUT if b.get("tool") == "cutout" else M.DEFAULT_UPSCALER)
    if model not in M.MODELS or M.MODELS[model]["kind"] != ("vupscale" if t["kind"] == "video" else "upscale"):
        return _err("That finishing tool can't run on this take.")
    short = await run_in_threadpool(_cant_afford, _price(model))
    if short:
        return _err(short, 402, reason="credits")
    req = {"prompt": t.get("prompt", ""), "source": t["file"], "scale": b.get("scale") or 2}
    nt = J.new_take(reel, t["shot"], ord(t.get("letter", "A")) - 65, kind=t["kind"], model=model,
                    label=M.MODELS[model]["label"], prompt=t.get("prompt", ""),
                    aspect=t.get("aspect"), request=req, settings=t.get("settings"),
                    action="cutout" if b.get("tool") == "cutout" else "upscale", parent=tid,
                    by=_who(request))
    _enqueue(reel, nt["id"])
    return {"take": _public_take(nt)}


def _local_take(reel: str, src: Dict, kind: str, action: str, file: str, by: str = "",
                shot: int = 0, **extra) -> Dict:
    shot = shot or (src.get("shot", 0) if action != "stitch" else J.next_shot(reel))
    t = J.new_take(reel, shot, 0, kind=kind, model="local", label=extra.pop("label", "Frame"),
                   prompt=src.get("prompt", ""), aspect=src.get("aspect"),
                   request={}, settings=src.get("settings"), action=action,
                   parent=src.get("id"), by=by)
    if file is None:                             # still being made: it renders like any take
        return J.update_take(reel, t["id"], status="running", started=time.time(), **extra)
    return J.update_take(reel, t["id"], status="done", file=file, finished=time.time(), **extra)


@router.post("/reels/{reel}/takes/{tid}/frame", response_class=JSONResponse)
async def grab_frame(reel: str, tid: str, request: Request):
    """A still from a clip: any moment, or the very last frame — which is how a
    shot is continued (the next clip starts exactly where this one ended)."""
    try:
        t = J.get_take(reel, tid)
    except ValueError:
        t = None
    if not t or t.get("kind") != "video" or not t.get("file"):
        return _err("Only a finished clip has frames.")
    b = await request.json()
    src = J.out_path(reel, t["file"])
    name = f"frame-{uuid.uuid4().hex[:10]}.jpg"
    dst = J.out_path(reel, name)
    if b.get("at") == "last":
        # -sseof seeks from the end; the last decodable frame wins (update=1)
        cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-sseof", "-0.5", "-i", src,
               "-update", "1", "-q:v", "2", dst]
    else:
        at = max(0.0, float(b.get("at") or 0))
        cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", src,
               "-frames:v", "1", "-q:v", "2", dst]
    r = run(cmd, timeout=90)
    if r.returncode != 0 or not os.path.exists(dst):
        return _err("Could not read a frame from that clip.", 500)
    with Image.open(dst) as im:
        w, h = im.size
    # a board card that continues the shot before it takes this frame as its keyframe
    card = {"board": str(b["board"])[:40], "role": "key"} if b.get("board") else {}
    try:
        shot = max(0, int(b.get("shot") or 0))
    except (TypeError, ValueError):
        shot = 0
    nt = _local_take(reel, t, "image", "frame", name, by=_who(request), shot=shot, w=w, h=h,
                     label="Last frame" if b.get("at") == "last" else "Frame grab", **card)
    return {"take": _public_take(nt)}


# ── grading ─────────────────────────────────────────────────────────────────
# A 2K clip, or a whole film, takes minutes to grade on the B2 plan: longer than
# Azure keeps a request open (about 230 s), so the page lost the answer while
# the server went on and made the take. A clip's grade is a take of its own now,
# rendering like any other and saying which frame it's on; a still is quick and
# comes back at once. One clip at a time: the cores are shared.
_grading = threading.Lock()


def _grade_clip(reel: str, tid: str, src: str, name: str, spec: Dict) -> None:
    dst = J.out_path(reel, name)
    said = [0.0]

    def live(note: str) -> bool:
        """Say where it is (once a second at most); False once it was stopped."""
        if time.time() - said[0] < 1:
            return True
        said[0] = time.time()
        t = J.get_take(reel, tid)
        if t is None:                            # being rewritten this instant: next time
            return True
        if t.get("status") != "running":
            return False
        try:
            J.update_take(reel, tid, progress=note)
        except RuntimeError:
            pass
        return True

    info = None
    try:
        while not _grading.acquire(timeout=5):
            if not live("waiting for another grade to finish"):
                return
        try:
            info = R.grade_film(src, dst, spec, progress=lambda done, total: live(f"frame {done} of {total}"))
        finally:
            _grading.release()
    except R.Stopped:
        pass
    except Exception as e:
        J.update_take(reel, tid, status="failed", reason="client", finished=time.time(), progress=None,
                      error=f"Grading failed: {str(e)[-300:]}")
    extra = _poster(reel, dst, name) if info else {}
    t = J.get_take(reel, tid)
    if not info or not t or t.get("status") != "running":      # failed, or stopped from the page
        for f in (name, extra.get("poster")):
            if f and os.path.exists(J.out_path(reel, f)):
                os.remove(J.out_path(reel, f))
        return
    J.update_take(reel, tid, status="done", file=name, finished=time.time(), progress=None,
                  frames=info["frames"], grade_seconds=info["seconds"], **extra)


@router.post("/reels/{reel}/takes/{tid}/grade", response_class=JSONResponse)
async def grade_take(reel: str, tid: str, request: Request):
    """A take through a look ({film, grade, amount, grain, …}), in Rust: the
    Lab's own grader (crates/scrim), run here by wasmtime. A clip is graded
    frame by frame with the grain moving and its sound kept, in the background
    (the answer is the new take, running); a still at full size, at once. The
    graded take sits beside the original, which is left alone."""
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    try:
        t = J.get_take(reel, tid)
    except ValueError:
        t = None
    if not t or t.get("status") != "done" or t.get("kind") not in ("video", "image") or not t.get("file") \
            or not os.path.exists(J.out_path(reel, t["file"])):
        return _err("Only a finished take can be graded.")
    b = await request.json()
    spec = b.get("look")
    if not isinstance(spec, dict) or not spec:
        return _err("Pick a look.")
    try:
        await run_in_threadpool(R.look, spec)
    except ValueError as e:
        return _err(f"That look doesn't work: {e}")
    label = str(b.get("label") or "").strip()[:60] or "Graded"
    video = t["kind"] == "video"
    name = f"graded-{uuid.uuid4().hex[:10]}.{'mp4' if video else 'jpg'}"
    if video:
        nt = _local_take(reel, t, "video", "grade", None, by=_who(request), label=label, look=spec,
                         progress="starting", w=t.get("w"), h=t.get("h"))
        _spawn(_grade_clip, reel, nt["id"], J.out_path(reel, t["file"]), name, spec)
        return {"take": _public_take(nt)}
    dst = J.out_path(reel, name)
    try:
        info = await run_in_threadpool(R.grade_still, J.out_path(reel, t["file"]), dst, spec)
    except Exception as e:
        if os.path.exists(dst):
            os.remove(dst)
        return _err(f"Grading failed: {str(e)[-300:]}", 500)
    nt = _local_take(reel, t, "image", "grade", name, by=_who(request), label=label, look=spec,
                     w=info["w"], h=info["h"])
    return {"take": _public_take(nt)}


# ── share a film ────────────────────────────────────────────────────────────
# A public page for one finished take (shell.py serves it at /f/<token>, no
# sign-in). Nothing is public until its maker presses Share, and Unshare
# takes the link down. The reel's owner check (app.py) covers both routes.
@router.post("/reels/{reel}/takes/{tid}/share", response_class=JSONResponse)
async def share_take(reel: str, tid: str, request: Request):
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    try:
        t = J.get_take(reel, tid)
    except ValueError:
        t = None
    if not t or t.get("status") != "done" or t.get("kind") not in ("video", "image") or not t.get("file"):
        return _err("Only a finished take can be shared.")
    try:
        b = await request.json()
    except Exception:
        b = {}
    token = t.get("share") or secrets.token_urlsafe(9)
    old = J.get_share(token) or {}                     # shared again: the same link, and its title unless a new one comes
    J.put_share(token, {"reel": reel, "take": tid, "by": old.get("by") or _who(request),
                        "created": old.get("created") or time.time(),
                        "title": " ".join(str(b.get("title") or old.get("title") or t.get("label") or "").split())[:90]})
    t = J.update_take(reel, tid, share=token)
    return {"url": f"/f/{token}", "take": _public_take(t)}


@router.delete("/reels/{reel}/takes/{tid}/share", response_class=JSONResponse)
async def unshare_take(reel: str, tid: str):
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    try:
        t = J.get_take(reel, tid)
    except ValueError:
        t = None
    if not t:
        return _err("No such take.", 404)
    token = t.get("share")
    t = J.update_take(reel, tid, share="")
    J.drop_share(token, reel)
    return {"take": _public_take(t)}


@router.post("/reels/{reel}/stitch", response_class=JSONResponse)
async def stitch(reel: str, request: Request):
    """Join takes, in the order given, into one clip. Clips play as they are;
    a still is held for `still_seconds` (default 3). Everything is scaled and
    padded to one frame — the first clip's, or the first still's when there is
    no clip — at one rate, so mixed models still make one file."""
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    b = await request.json()
    ids = [x for x in (b.get("takes") or []) if x][:40]
    try:
        hold = min(10.0, max(1.0, float(b.get("still_seconds") or 3)))
    except (TypeError, ValueError):
        hold = 3.0
    parts = []
    for x in ids:
        try:
            t = J.get_take(reel, x)
        except ValueError:
            t = None
        if t and t.get("status") == "done" and t.get("kind") in ("video", "image") and t.get("file") \
                and os.path.exists(J.out_path(reel, t["file"])):
            parts.append(t)
    if len(parts) < 2:
        return _err("Pick at least two finished takes to stitch.")
    lead = next((t for t in parts if t["kind"] == "video"), parts[0])
    if lead["kind"] == "video":
        info = probe(J.out_path(reel, lead["file"]))
        W, H = int(info.get("width") or 1280), int(info.get("height") or 720)
    else:
        with Image.open(J.out_path(reel, lead["file"])) as im:
            W, H = im.size
        k = min(1.0, 1920 / max(W, H))           # a 4K still is no reason for a 4K film
        W, H = int(W * k), int(H * k)
    W, H = W // 2 * 2, H // 2 * 2
    fps = 24
    # trims: {take id: [in, out]} — a clip plays only that stretch; a still is
    # held for out − in (how a board of keyframes becomes a timed animatic)
    trims = b.get("trims") if isinstance(b.get("trims"), dict) else {}

    def cut_of(tid):
        x = trims.get(tid)
        try:
            a, e = float(x[0]), float(x[1])
        except (TypeError, ValueError, IndexError):
            return None
        return (a, e) if e > a >= 0 else None

    ins, chains, labels = [], [], []
    for i, c in enumerate(parts):
        p = J.out_path(reel, c["file"])
        cut = cut_of(c["id"])
        if c["kind"] == "image":
            h = min(30.0, max(0.5, cut[1] - cut[0])) if cut else hold
            ins += ["-loop", "1", "-framerate", str(fps), "-t", f"{h:.3f}", "-i", p]
            has_audio, dur = False, h
        else:
            info = probe(p)
            full = float(info.get("duration") or 5)
            if cut:
                a = max(0.0, min(cut[0], full - 0.1))
                dur = max(0.1, min(cut[1], full) - a)
                ins += ["-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", p]
            else:
                ins += ["-i", p]
                dur = full
            has_audio = bool(info.get("has_audio"))
        chains.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                      f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{i}]")
        if has_audio:
            chains.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            # silence the length of the part, so concat keeps picture and sound aligned
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(chains) + f";{''.join(labels)}concat=n={len(parts)}:v=1:a=1[v][a]"
    name = f"reel-{uuid.uuid4().hex[:10]}.mp4"
    dst = J.out_path(reel, name)
    # off the event loop: a long stitch must not stall every other request on this worker
    r = await run_in_threadpool(run, ["ffmpeg", "-nostdin", "-y", "-v", "error", *ins, "-filter_complex", graph,
                                       "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
                                       "-crf", "18", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst],
                                timeout=900)
    if r.returncode != 0 or not os.path.exists(dst):
        return _err(f"Stitching failed: {(r.stderr or '')[-300:]}", 500)
    extra = _poster(reel, dst, name)
    nv = sum(1 for c in parts if c["kind"] == "video")
    ni = len(parts) - nv
    label = " + ".join(x for x in (nv and f"{nv} clip{'s' if nv > 1 else ''}",
                                   ni and f"{ni} still{'s' if ni > 1 else ''}") if x)
    nt = _local_take(reel, {**lead, "prompt": " / ".join(c.get("prompt", "")[:80] for c in parts)},
                     "video", "stitch", name, by=_who(request),
                     label=str(b.get("label") or "").strip()[:60] or f"Stitched · {label}",
                     parts=[c["id"] for c in parts], **extra)
    return {"take": _public_take(nt)}


@router.post("/reels/{reel}/takes/delete", response_class=JSONResponse)
async def delete_takes(reel: str, request: Request):
    """Delete several takes at once — the bin's selection. A take still
    rendering is left alone (stop it first), so nothing billed goes missing."""
    if not _reel_or_404(reel):
        return _err("No such reel.", 404)
    b = await request.json()
    done, kept = [], []
    for x in (b.get("takes") or [])[:200]:
        try:
            t = J.get_take(reel, x)
        except ValueError:
            continue
        if not t:
            continue
        if t.get("status") in ("queued", "running"):
            kept.append(x)
            continue
        J.delete_take(reel, x)
        done.append(x)
    return {"deleted": done, "kept": kept}


@router.get("/reels/{reel}/download")
async def download_reel(reel: str, starred: int = 0, takes: str = "", size: int = 0):
    """Every finished take as a zip — or only the starred ones, or only the
    ones named in `takes` (the bin's selection, comma-separated). `size`
    (2000 / 4000 / 8000) delivers stills at that long side, lightly sharpened
    and tagged 300 dpi, ready to file; clips go as they are."""
    r = _reel_or_404(reel)
    if not r:
        return _err("No such reel.", 404)
    only = {x for x in takes.split(",") if x}
    takes = [t for t in J.list_takes(reel) if t.get("status") == "done" and t.get("file")
             and (not starred or t.get("starred")) and (not only or t["id"] in only)]
    if not takes:
        return _err("Nothing finished to download yet.")
    buf = await run_in_threadpool(_zip_takes, [(t, "") for t in takes], size)
    return _zip_response(buf, r.get("name", "reel"), size)


def _slug(s: str, n: int = 40) -> str:
    return "".join(c if c.isalnum() else "-" for c in s)[:n].strip("-")


def _zip_takes(items: List[tuple], size: int) -> io.BytesIO:
    """(take, folder) pairs as one zip, with shots.json saying what each file is.
    `size` (2000 / 4000 / 8000) delivers stills at that long side, lightly
    sharpened and tagged 300 dpi, ready to file; clips go as they are."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = []
        for t, folder in items:
            p = J.out_path(t["reel"], t["file"])
            if not os.path.exists(p):
                continue
            name = t["file"]
            if size in (2000, 4000, 8000) and t.get("kind") == "image":
                name = os.path.splitext(name)[0] + ".jpg"
                z.writestr(folder + name, _resized_still(p, size))
            else:
                z.write(p, folder + name)
            manifest.append({"file": folder + name, **{k: t.get(k) for k in (
                "shot", "letter", "kind", "label", "model", "prompt", "aspect", "action")}})
        z.writestr("shots.json", json.dumps(manifest, indent=2))
    buf.seek(0)
    return buf


def _zip_response(buf: io.BytesIO, name: str, size: int) -> StreamingResponse:
    safe = _slug(name) or "reel"
    if size in (2000, 4000, 8000):
        safe += f"-{size // 1000}k"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{safe}.zip"'})


# ── the library: everything someone made, across reels ──────────────────────
# A take stays until its maker deletes it, wherever it was shot. Takes from
# before names were kept (23 Oct 2026) belong to nobody: they show under
# Everyone, and anyone may tidy them away.
LIB_PAGE = 60


@router.get("/library", response_class=JSONResponse)
async def library(request: Request, app: str = "studio", who: str = "mine", kind: str = "",
                  starred: int = 0, q: str = "", offset: int = 0, limit: int = LIB_PAGE):
    me = _who(request)
    every = await run_in_threadpool(J.library, app if app in ("studio", "cg") else "studio")
    if V.on():                                   # the demo: a visitor's library is only their own
        every = [t for t in every if t["owner"] == me]
    mine = [t for t in every if me and t["owner"] == me]
    pool = every if who == "all" else mine
    counts = {"mine": len(mine), "all": len(every),
              "video": sum(t["kind"] == "video" for t in pool),
              "image": sum(t["kind"] == "image" for t in pool)}
    if kind in ("video", "image"):
        pool = [t for t in pool if t["kind"] == kind]
    counts["starred"] = sum(bool(t.get("starred")) for t in pool)
    if starred:
        pool = [t for t in pool if t.get("starred")]
    words = q.lower().split()
    if words:
        def hay(t):
            return " ".join(str(x or "") for x in (t.get("prompt"), t.get("label"), t.get("model"),
                                                    t.get("reel_name"), t["owner"],
                                                    (t.get("settings") or {}).get("scene"))).lower()
        pool = [t for t in pool if all(w in hay(t) for w in words)]
    offset, limit = max(0, offset), max(1, min(200, limit))
    page = [{**_public_take(t), "reel_name": t["reel_name"], "owner": t["owner"],
             "yours": not t["owner"] or t["owner"] == me} for t in pool[offset:offset + limit]]
    return {"items": page, "total": len(pool), "counts": counts, "me": me}


def _lib_pairs(items) -> List[tuple]:
    """'reel:take' strings (or {reel, take}) → (reel, take), ids checked."""
    out = []
    for x in items:
        if isinstance(x, str):
            reel, _, tid = x.partition(":")
        elif isinstance(x, dict):
            reel, tid = str(x.get("reel") or ""), str(x.get("take") or "")
        else:
            continue
        try:
            out.append((J.safe_id(reel), J.safe_id(tid)))
        except ValueError:
            continue
    return out


@router.post("/library/delete", response_class=JSONResponse)
async def library_delete(request: Request):
    """Delete takes from anywhere in the library: yours, or ones nobody's name
    is on. Someone else's take, or one still rendering, is left alone."""
    me = _who(request)
    b = await request.json()
    done, kept = [], []
    for reel, tid in _lib_pairs((b.get("items") or [])[:200]):
        r, t = J.get_reel(reel), J.get_take(reel, tid)
        if not r or not t:
            continue
        owner = J.owner_of(t, r)
        if t.get("status") in ("queued", "running") or (owner and owner != me) or not V.owns(r):
            kept.append(f"{reel}:{tid}")
            continue
        J.delete_take(reel, tid)
        done.append(f"{reel}:{tid}")
    return {"deleted": done, "kept": kept}


@router.post("/library/claim", response_class=JSONResponse)
async def library_claim(request: Request):
    """Put your name on takes that carry none — the ones made before names were
    kept. A take with someone's name on it keeps it."""
    me = _who(request)
    if not me:
        return _err("Sign in first, then try again.", 401)
    if V.on():
        return _err("Nothing to claim here.", 403)
    b = await request.json()
    done = []
    for reel, tid in _lib_pairs((b.get("items") or [])[:200]):
        r, t = J.get_reel(reel), J.get_take(reel, tid)
        if not r or not t or J.owner_of(t, r):
            continue
        try:
            J.update_take(reel, tid, by=me)
        except RuntimeError:                     # unreadable for a moment on the share; leave it
            continue
        done.append(f"{reel}:{tid}")
    return {"claimed": done, "me": me}


@router.get("/library/download")
async def library_download(items: str = "", size: int = 0):
    """Takes from any reels as one zip, a folder per reel."""
    picked = []
    for reel, tid in _lib_pairs([x for x in items.split(",") if x][:200]):
        r, t = J.get_reel(reel), J.get_take(reel, tid)
        if r and t and V.owns(r) and t.get("status") == "done" and t.get("file"):
            picked.append((t, f"{_slug(r.get('name') or 'reel')}-{reel[:4]}/"))
    if not picked:
        return _err("Nothing finished to download.")
    buf = await run_in_threadpool(_zip_takes, picked, size)
    return _zip_response(buf, "library", size)


# ── the balance ─────────────────────────────────────────────────────────────
# The header once read the balance only when the page opened: two clips later
# it still showed what the account had before them.
# So the balance is read live (cached a few seconds across the page's asks),
# shown next to what is already committed (runs rendering or in line, at
# each model's price), turns amber below STUDIO_LOW_CREDIT, and a run the
# account can't cover is refused before anything is sent.
LOW_CREDIT = float(os.getenv("STUDIO_LOW_CREDIT", "5"))
_bal: Dict[str, Dict] = {}            # whose balance (a visitor's, on the demo) → {t, v}
_bal_lock = threading.Lock()


def _balance(max_age: float = 10.0) -> Optional[float]:
    """Dollars left on the Comfy account, or None when it can't be asked."""
    who = V.current.get()
    with _bal_lock:
        hit = _bal.get(who)
        if hit and time.time() - hit["t"] < max_age:
            return hit["v"]
    v = comfy.balance()
    if v is not None:
        with _bal_lock:
            _bal[who] = {"t": time.time(), "v": v}
    return v


def _price(model: str) -> float:
    c = (M.MODELS.get(model) or {}).get("cost")
    return float(c) if isinstance(c, (int, float)) else 0.0


def _committed() -> Dict[str, float]:
    """Roughly what runs already rendering, and runs still in line, will cost."""
    out = {"rendering": 0.0, "waiting": 0.0}
    for key, marks in (("rendering", J.active()), ("waiting", J.queued())):
        for reel, tid in marks:
            try:
                t = J.get_take(reel, tid)
            except ValueError:
                continue
            if t and t.get("status") in ("queued", "running"):
                out[key] += _price(t.get("model"))
    return {k: round(v, 2) for k, v in out.items()}


def _cant_afford(need: float) -> Optional[str]:
    """Why the account can't pay for `need` more, or None. Runs waiting in line
    haven't been charged yet, so they come off first; a run already rendering
    may be held already, so it doesn't."""
    if need <= 0:
        return None
    bal = _balance()
    if bal is None:
        return None                          # Comfy can't say: let the run decide
    waiting = _committed()["waiting"]
    if bal - waiting >= need:
        return None
    line = f" and about ${waiting:.2f} is already waiting in line" if waiting else ""
    return (f"Comfy has ${bal:.2f}{line}, and this needs about ${need:.2f}. "
            "Add credits in ComfyUI (Settings, Plan & Credits), then run it again.")


@router.get("/credits", response_class=JSONResponse)
async def credits(fresh: int = 0):
    bal = await run_in_threadpool(_balance, 0.0 if fresh else 10.0)
    if bal is None:
        return _err("Comfy didn't say what the balance is.", 502)
    c = await run_in_threadpool(_committed)
    left = round(bal - c["rendering"] - c["waiting"], 2)
    return {"credits": bal, "bank": "Comfy", **c, "left": left, "low": left < LOW_CREDIT, "low_at": LOW_CREDIT,
            "as_of": time.time()}
