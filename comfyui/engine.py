"""Gobo's Rust inside ComfyUI: the same gobo.wasm the Gobo app runs in the
page and on its server (crates/gobo-wasm, built by tools/build_wasm.sh), run
here by wasmtime, so a grade made in ComfyUI is the grade Gobo showed.

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
VIDEO = (".mp4", ".mov", ".webm", ".mkv", ".m4v")

_engine = wasmtime.Engine()
_module: Optional[wasmtime.Module] = None
_load = threading.Lock()
_local = threading.local()


class Gobo:
    """One instance of gobo.wasm. It has its own memory, so one thread at a time."""

    def __init__(self):
        global _module
        with _load:
            if _module is None:
                _module = wasmtime.Module.from_file(_engine, WASM)
        self.store = wasmtime.Store(_engine)
        self.x = wasmtime.Instance(self.store, _module, []).exports(self.store)
        self.mem = self.x["memory"]
        self.luts = set()

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

    def compare(self, a: np.ndarray, b: np.ndarray, continuous: bool) -> Dict:
        (ah, aw), (bh, bw) = a.shape[:2], b.shape[:2]
        return self._lent(a, lambda pa, na: self._lent(b, lambda pb, nb: self.ask(
            self.call("flag_compare", pa, na, aw, ah, pb, nb, bw, bh, 1 if continuous else 0))))


def gobo() -> Gobo:
    """This thread's instance (ComfyUI runs a prompt on one thread)."""
    if not hasattr(_local, "g"):
        _local.g = Gobo()
    return _local.g


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
