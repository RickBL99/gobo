"""Gobo's Rust inside ComfyUI: the same gobo.wasm the Gobo app runs in the
page and on its server (crates/gobo-wasm, built by tools/build_wasm.sh), run
here by wasmtime, so a grade made in ComfyUI is the grade Gobo showed; and the
shot compiler, direction.wasm (crates/direction), with its words from
direction.json, so the Director writes the prompt Gobo would.

The ABI is the crates' own: ask `alloc(len)` for a block and write into it;
JSON arguments are handed over (the module frees them), pixel buffers are lent
(freed with `free(ptr, len)`), and an answer comes back as one u64, pointer in
the high half and length in the low, read and then freed.

Takes come from Gobo as signed links and are kept in ComfyUI's input folder
(under gobo/), so a workflow runs again without fetching them again.
"""
import hashlib
import json
import os
import threading
import urllib.request
from typing import Callable, Dict, List, Optional

import numpy as np
import wasmtime

HERE = os.path.dirname(os.path.abspath(__file__))
WASM = os.path.join(HERE, "gobo.wasm")
DIRECTION = os.path.join(HERE, "direction.wasm")
WORDS = os.path.join(HERE, "direction.json")
VIDEO = (".mp4", ".mov", ".webm", ".mkv", ".m4v")

_engine = wasmtime.Engine()
_modules: Dict[str, wasmtime.Module] = {}
_load = threading.Lock()
_local = threading.local()


class _Wasm:
    """One instance of a module. It has its own memory, so one thread at a time."""

    def __init__(self, path: str):
        with _load:
            if path not in _modules:
                _modules[path] = wasmtime.Module.from_file(_engine, path)
        self.store = wasmtime.Store(_engine)
        self.x = wasmtime.Instance(self.store, _modules[path], []).exports(self.store)
        self.mem = self.x["memory"]

    def call(self, fn: str, *args):
        return self.x[fn](self.store, *args)

    def put(self, data: bytes):
        p = self.call("alloc", len(data))
        self.mem.write(self.store, data, p)
        return p, len(data)

    def json(self, v):
        return self.put(json.dumps(v).encode())

    def ask(self, packed: int) -> Dict:
        packed &= (1 << 64) - 1
        p, n = packed >> 32, packed & 0xFFFFFFFF
        out = json.loads(bytes(self.mem.read(self.store, p, p + n)))
        self.call("free", p, n)
        if isinstance(out, dict) and "error" in out:
            raise ValueError(out["error"])
        return out



class Gobo(_Wasm):
    """gobo.wasm: scrim (the look lab), flag (the script supervisor), dingle."""

    def __init__(self):
        super().__init__(WASM)
        self.luts = set()

    def _lent(self, rgba: np.ndarray, fn: Callable[[int, int], Dict]):
        p, n = self.put(np.ascontiguousarray(rgba, dtype=np.uint8).tobytes())
        try:
            return fn(p, n)
        finally:
            self.call("free", p, n)

    # ── what the nodes use ──────────────────────────────────────────────────
    def look(self, spec: Dict) -> Dict:
        """A look resolved to its numbers (presets by name, then the numbers)."""
        return self.ask(self.call("scrim_look", *self.json(spec)))

    def load_lut(self, lid: str, text: str) -> Dict:
        out = self.ask(self.call("scrim_lut_load", *self.json({"id": lid, "text": text})))
        self.luts.add(lid)
        return out

    def unpack_lut(self, lid: str) -> Dict:
        return self.ask(self.call("scrim_lut_unpack", *self.json({"id": lid})))

    def grade(self, rgba: np.ndarray, look: Dict, seed: int = 0) -> np.ndarray:
        """An H×W×4 uint8 picture through a look (the grain seeded per frame)."""
        h, w = rgba.shape[:2]
        p, n = self.put(np.ascontiguousarray(rgba, dtype=np.uint8).tobytes())
        try:
            self.ask(self.call("scrim_grade", p, n, w, h, *self.json(look), seed & 0xFFFFFFFF))
            return np.frombuffer(bytes(self.mem.read(self.store, p, p + n)), dtype=np.uint8).reshape(h, w, 4).copy()
        finally:
            self.call("free", p, n)

    def read(self, rgba: np.ndarray) -> Dict:
        h, w = rgba.shape[:2]
        return self._lent(rgba, lambda p, n: self.ask(self.call("scrim_read", p, n, w, h)))

    def match(self, take: List[np.ndarray], ref: List[np.ndarray]) -> Dict:
        a = np.concatenate([f.reshape(-1, 4) for f in take])
        b = np.concatenate([f.reshape(-1, 4) for f in ref])
        return self._lent(a, lambda pa, na: self._lent(b, lambda pb, nb: self.ask(self.call("scrim_match", pa, na, pb, nb))))

    def direct(self, body: Dict) -> Dict:
        """Strokes drawn on a frame ({w, h, strokes: [{pen, label, points: [[x, y, ms]…]}]})
        read as a camera move and the subject's action (crates/dingle)."""
        return self.ask(self.call("dingle_direct", *self.json(body)))

    def compare(self, a: np.ndarray, b: np.ndarray, continuous: bool) -> Dict:
        (ah, aw), (bh, bw) = a.shape[:2], b.shape[:2]
        return self._lent(a, lambda pa, na: self._lent(b, lambda pb, nb: self.ask(
            self.call("flag_compare", pa, na, aw, ah, pb, nb, bw, bh, 1 if continuous else 0))))


def gobo() -> Gobo:
    """This thread's instance (ComfyUI runs a prompt on one thread)."""
    if not hasattr(_local, "g"):
        _local.g = Gobo()
    return _local.g


# ── the shot compiler ───────────────────────────────────────────────────────
_words: Optional[Dict] = None


def words() -> Dict:
    """Every direction choice ({groups, defaults, order}), as Gobo writes them
    (studio/direction.py, exported by tools/build_comfy_vocabulary.py)."""
    global _words
    if _words is None:
        with open(WORDS, encoding="utf-8") as f:
            _words = json.load(f)
    return _words


class Director(_Wasm):
    """direction.wasm with the vocabulary loaded: a shot in, the prompt out,
    in labelled pieces (direction, scene, enhance, reference)."""

    def __init__(self):
        super().__init__(DIRECTION)
        if self.call("load_catalogue", *self.json({"groups": words()["groups"]})) != 0:
            raise RuntimeError("the shot compiler refused its vocabulary")

    def compile(self, body: Dict) -> Dict:
        return self.ask(self.call("compile", *self.json(body)))


def director() -> Director:
    if not hasattr(_local, "d"):
        _local.d = Director()
    return _local.d


def choice(group: str, value: str) -> str:
    """A group's option id from what a node shows (its label) or the id itself;
    anything else is Auto."""
    for o in words()["groups"][group]["options"]:
        if value in (o["id"], o["label"]):
            return o["id"]
    return "auto"


def read_drawing(body: Dict) -> Dict:
    """A drawing as the shot takes it: the camera move (one of the Director's,
    else its words go into the scene) and the subject's action, in words.
    → {move, action, said} (said: what the drawing reads as, for the node)."""
    if not (body or {}).get("strokes"):
        return {"move": "", "action": "", "said": "Nothing drawn yet."}
    r = gobo().direct(body)
    c = r.get("camera") or {}
    moves = {o["id"] for o in words()["groups"]["move"]["options"]}
    move, said, extra = "", [], []
    if c.get("move"):
        label = next((o["label"] for o in words()["groups"]["move"]["options"] if o["id"] == c["move"]), "")
        if c["move"] in moves:
            move = c["move"]
        else:
            p = str(c.get("phrase") or "").strip().rstrip(".")
            extra.append(p[:1].upper() + p[1:] + "." if p else "")
        said.append(f"Camera: {label or c['move']}: {c.get('phrase', '')} ({c.get('why', '')})")
    else:
        said.append("No camera stroke: the camera stays as set.")
    if r.get("action"):
        said.append(f"Action: {r['action']}")
    return {"move": move, "action": " ".join(x for x in [*extra, r.get("action") or ""] if x), "said": "\n".join(said)}


def compile_shot(scene: str, picks: Dict[str, str], video: bool = True, cinematic: bool = True,
                 drawn: Optional[Dict] = None) -> Dict:
    """{prompt, segments, direction}: the scene and the choices, as Gobo writes
    them; a drawing's move wins over the picked one and its action follows the scene."""
    if drawn:
        if drawn.get("move"):
            picks = {**picks, "move": drawn["move"]}
        if drawn.get("action"):
            scene = (scene.strip().rstrip(".") + ". " if scene.strip() else "") + drawn["action"]
    d = {g: choice(g, v) for g, v in picks.items() if g in words()["groups"]}
    d = {g: v for g, v in d.items() if v != "auto"}
    out = director().compile({"scene": scene or "", "direction": d, "enhance": bool(cinematic),
                              "mode": "video" if video else "image"})
    return {**out, "direction": d}


# ── looks that name a LUT ───────────────────────────────────────────────────
def with_luts(g: Gobo, look: Dict) -> Dict:
    """Load the .cube a look names into the instance. Gobo hands it over as a
    link (lut3d.url) or the file's text (lut3d.text); either way the look
    that reaches Rust names it by id."""
    lut = look.get("lut3d")
    if not isinstance(lut, dict):
        return look
    text = lut.get("text")
    if not text and lut.get("url"):
        with open(fetch(lut["url"], ".cube"), encoding="utf-8") as f:
            text = f.read()
    if not text:
        return look
    lid = lut.get("id") or "lut-" + hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
    if lid not in g.luts:
        g.load_lut(lid, text)
    return {**look, "lut3d": {k: v for k, v in {**lut, "id": lid}.items() if k not in ("text", "url")}}


# ── takes from Gobo ─────────────────────────────────────────────────────────
def _folder() -> str:
    try:
        import folder_paths                      # ComfyUI's own
        root = folder_paths.get_input_directory()
    except ImportError:
        root = os.path.join(HERE, "cache")
    d = os.path.join(root, "gobo")
    os.makedirs(d, exist_ok=True)
    return d


def fetch(url: str, ext: str = "") -> str:
    """A file Gobo links to, kept by the link's hash; a path is used as it is."""
    if os.path.isfile(url):
        return url
    if not url.startswith(("http://", "https://")):
        raise ValueError("Paste the take's link from Gobo (Open in ComfyUI fills it in).")
    name = hashlib.sha1(url.encode()).hexdigest()[:16]
    for have in os.listdir(_folder()):
        if have.startswith(name + "."):
            return os.path.join(_folder(), have)
    req = urllib.request.Request(url, headers={"User-Agent": "Gobo-ComfyUI"})
    with urllib.request.urlopen(req, timeout=120) as r:
        kind = r.headers.get("Content-Type", "")
        ext = ext or {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm", "image/jpeg": ".jpg",
                      "image/png": ".png", "image/webp": ".webp", "text/plain": ".cube"}.get(kind.split(";")[0].strip(), "")
        if not ext:
            ext = os.path.splitext(url.split("?")[0])[1].lower() or ".bin"
        path = os.path.join(_folder(), name + ext)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    os.replace(tmp, path)
    return path


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO


# ── pictures in and out ─────────────────────────────────────────────────────
def _space(stream) -> str:
    # as Gobo reads a clip (studio/rust.py _matrix): Rec.601 only when the file
    # says so; untagged reads as Rec.709, as a browser shows it
    cs = getattr(stream.codec_context, "colorspace", None)
    name = str(getattr(cs, "name", cs) or "").lower()
    return "itu601" if any(k in name for k in ("601", "470", "smpte170")) else "itu709"


def _rgba(frame, space: str) -> np.ndarray:
    return frame.reformat(format="rgba", src_colorspace=space, dst_colorspace=space).to_ndarray()


def still(path: str) -> np.ndarray:
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        return np.asarray(ImageOps.exif_transpose(im).convert("RGBA")).copy()


def frames_at(path: str, where: List[float], width: int = 480) -> List[np.ndarray]:
    """Frames at these fractions of a take (a still is its one picture), scaled down."""
    if not is_video(path):
        im = still(path)
        return [im[:: max(1, im.shape[1] // width), :: max(1, im.shape[1] // width)]]
    import av
    out = []
    with av.open(path) as c:
        s = c.streams.video[0]
        space = _space(s)
        dur = float(s.duration * s.time_base) if s.duration else float(c.duration or 0) / 1e6
        for at in where:
            c.seek(int(max(0.0, min(dur - 0.05, dur * at)) / s.time_base), stream=s, any_frame=False, backward=True)
            target = max(0.0, min(dur - 0.05, dur * at))
            got = None
            for fr in c.decode(s):
                got = fr
                if fr.time is None or fr.time >= target - 0.02:
                    break
            if got is not None:
                img = _rgba(got, space)
                k = max(1, img.shape[1] // width)
                out.append(img[::k, ::k].copy())
    return out


def _cover(fr, space: str, w: int, h: int) -> np.ndarray:
    """A frame as RGBA at w×h, scaled to fill and cropped to the middle (a
    shot from a model that made another shape keeps its proportions)."""
    sw, sh = fr.width, fr.height
    k = max(w / sw, h / sh)
    cw, ch = max(w, round(sw * k / 2) * 2), max(h, round(sh * k / 2) * 2)
    img = fr.reformat(width=cw, height=ch, format="rgba", src_colorspace=space, dst_colorspace=space,
                      interpolation="BICUBIC").to_ndarray()
    x, y = (cw - w) // 2, (ch - h) // 2
    return np.ascontiguousarray(img[y:y + h, x:x + w])


def crop_to(img: np.ndarray, aspect: float) -> np.ndarray:
    """The middle of a picture at another shape (w/h), as a film crops a shot to fit."""
    h, w = img.shape[:2]
    if w / h > aspect + 1e-3:
        cw = max(2, round(h * aspect))
        x = (w - cw) // 2
        return np.ascontiguousarray(img[:, x:x + cw])
    if w / h < aspect - 1e-3:
        ch = max(2, round(w / aspect))
        y = (h - ch) // 2
        return np.ascontiguousarray(img[y:y + ch])
    return img


def grade_sequence(clips: List[str], looks: List[Dict], dst: str, size, fps: float,
                   progress: Optional[Callable[[int, int], None]] = None) -> Dict:
    """Shots one after another, each through its own look, into one silent
    film at `size` (w, h) and `fps`: a shot made at another size is scaled to
    fill and cropped to the middle, one at another frame rate keeps its
    length. The grain runs on from shot to shot, as one piece of film."""
    import av
    g = gobo()
    looks = [with_luts(g, lk) for lk in looks]
    w, h = int(size[0]) // 2 * 2, int(size[1]) // 2 * 2
    n = 0
    total = 0
    for src in clips:
        with av.open(src) as c:
            s = c.streams.video[0]
            total += int(float(s.duration * s.time_base if s.duration else (c.duration or 0) / 1e6) * fps)
    with av.open(dst, "w", format="mp4") as out:
        ov = out.add_stream("libx264", rate=round(fps, 3) if fps % 1 else int(fps))
        ov.width, ov.height, ov.pix_fmt = w, h, "yuv420p"
        ov.options = {"crf": "18", "preset": "medium"}
        for k, v in (("colorspace", 1), ("color_primaries", 1), ("color_trc", 1)):
            try:
                setattr(ov.codec_context, k, v)
            except (AttributeError, TypeError, ValueError):
                pass
        for src, look in zip(clips, looks):
            with av.open(src) as c:
                vs = c.streams.video[0]
                space = _space(vs)
                rate = float(vs.average_rate or fps)
                made = 0                                   # frames of this shot written so far
                for i, fr in enumerate(c.decode(vs)):
                    # as many frames of the film as this frame of the shot covers
                    upto = round((i + 1) / rate * fps)
                    if upto <= made:
                        continue
                    img = g.grade(_cover(fr, space, w, h), look, n)
                    vf = av.VideoFrame.from_ndarray(img, format="rgba").reformat(
                        format="yuv420p", src_colorspace="itu709", dst_colorspace="itu709")
                    while made < upto:
                        for p in ov.encode(vf):
                            out.mux(p)
                        made += 1
                        n += 1
                        if progress:
                            progress(n, max(total, n))
        for p in ov.encode():
            out.mux(p)
    return {"frames": n, "width": w, "height": h, "fps": fps}


def video_info(path: str) -> Dict:
    """A clip's size, frame rate and length."""
    import av
    with av.open(path) as c:
        s = c.streams.video[0]
        dur = float(s.duration * s.time_base) if s.duration else float(c.duration or 0) / 1e6
        return {"w": s.codec_context.width, "h": s.codec_context.height, "fps": float(s.average_rate or 24), "seconds": dur}


def grade_video(src: str, dst: str, look: Dict, progress: Optional[Callable[[int, int], None]] = None) -> Dict:
    """Every frame of a clip through a look, the grain moving frame to frame
    (seed = frame number, as Gobo plays it), the sound copied as it is."""
    import av
    g = gobo()
    look = with_luts(g, look)
    with av.open(src) as inp:
        vs = inp.streams.video[0]
        space = _space(vs)
        aud = inp.streams.audio[0] if inp.streams.audio else None
        total = vs.frames or 0
        w, h = vs.codec_context.width // 2 * 2, vs.codec_context.height // 2 * 2
        with av.open(dst, "w", format="mp4") as out:
            ov = out.add_stream("libx264", rate=vs.average_rate or 24)
            ov.width, ov.height, ov.pix_fmt = w, h, "yuv420p"
            ov.options = {"crf": "18", "preset": "medium"}
            for k, v in (("colorspace", 1), ("color_primaries", 1), ("color_trc", 1)):
                if space == "itu709":
                    try:
                        setattr(ov.codec_context, k, v)
                    except (AttributeError, TypeError, ValueError):
                        pass
            oa = None
            if aud is not None:
                try:
                    oa = out.add_stream_from_template(aud)
                except (ValueError, av.FFmpegError):
                    oa = None                       # a sound mp4 can't hold: left out
            n = 0
            streams = [vs] + ([aud] if oa is not None else [])
            for packet in inp.demux(*streams):
                if packet.dts is None:
                    continue
                if oa is not None and packet.stream is aud:
                    packet.stream = oa
                    out.mux(packet)
                    continue
                for fr in packet.decode():
                    img = g.grade(_rgba(fr, space)[:h, :w], look, n)
                    vf = av.VideoFrame.from_ndarray(img, format="rgba").reformat(
                        format="yuv420p", src_colorspace=space, dst_colorspace=space)
                    for p in ov.encode(vf):
                        out.mux(p)
                    n += 1
                    if progress:
                        progress(n, total)
            for p in ov.encode():
                out.mux(p)
    return {"frames": n, "width": w, "height": h}
