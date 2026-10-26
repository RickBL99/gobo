"""Gobo's Rust on the server: the same two .wasm files the page loads
(studio/static/direction.wasm and gobo.wasm, built from crates/ by
tools/build_wasm.sh), run here by wasmtime. One build in both places, so the
prompt the page previews is the prompt the server sends, and a take graded
here is the Lab's preview, frame for frame.

The ABI is the crates' own: ask `alloc(len)` for a block and write into it;
JSON arguments are handed over (the module frees them), pixel buffers are lent
(freed with `free(ptr, len)`), and an answer comes back as one u64, pointer in
the high half and length in the low, which is read and then freed.
"""
import json
import os
import subprocess
import tempfile
import threading
import time
from typing import Dict, Tuple

import wasmtime
from PIL import Image, ImageOps

from .media import probe, run

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_engine = wasmtime.Engine()
_modules: Dict[str, wasmtime.Module] = {}
_load = threading.Lock()


class Wasm:
    """One instance of a module. It has its own memory, so one thread at a time."""

    def __init__(self, name: str):
        with _load:
            if name not in _modules:          # compiled once per process; instances are cheap
                _modules[name] = wasmtime.Module.from_file(_engine, os.path.join(STATIC, name))
        self.store = wasmtime.Store(_engine)
        self.x = wasmtime.Instance(self.store, _modules[name], []).exports(self.store)
        self.mem = self.x["memory"]

    def call(self, fn: str, *args):
        return self.x[fn](self.store, *args)

    def put(self, data: bytes) -> Tuple[int, int]:
        p = self.call("alloc", len(data))
        self.mem.write(self.store, data, p)
        return p, len(data)

    def json(self, v) -> Tuple[int, int]:
        return self.put(json.dumps(v).encode())

    def answer(self, packed: int) -> bytes:
        packed &= (1 << 64) - 1
        p, n = packed >> 32, packed & 0xFFFFFFFF
        out = bytes(self.mem.read(self.store, p, p + n))
        self.call("free", p, n)
        return out


# ── the shot compiler (crates/direction) ────────────────────────────────────
_compiler = None                              # (the vocabulary it was given, its instance)
_compiling = threading.Lock()


def compile_shot(body: Dict, vocabulary: Dict) -> Dict:
    """{prompt, segments} for a shot: the body POST /studio/compile takes, and
    every group's options ({group: {"options": [{id, phrase}, …]}}), which stay
    written in Python (studio/direction.py) and are handed in once."""
    global _compiler
    with _compiling:
        if _compiler is None or _compiler[0] is not vocabulary:
            w = Wasm("direction.wasm")
            if w.call("load_catalogue", *w.json({"groups": vocabulary})) != 0:
                raise RuntimeError("the shot compiler refused the catalogue")
            _compiler = (vocabulary, w)
        w = _compiler[1]
        out = json.loads(w.answer(w.call("compile", *w.json(body))))
    if "error" in out:
        raise ValueError(out["error"])
    return out


# ── the grader (crates/scrim) ───────────────────────────────────────────────
def look(spec: Dict) -> Dict:
    """A look resolved to its numbers, or ValueError naming what's wrong."""
    g = Wasm("gobo.wasm")
    out = json.loads(g.answer(g.call("scrim_look", *g.json(spec))))
    if "error" in out:
        raise ValueError(out["error"])
    return out


def swatch(spec: Dict, colours) -> list:
    """Hex colours through a look (no grain, glow or vignette): what a grade
    does, as a strip of tiles."""
    g = Wasm("gobo.wasm")
    rgba = b"".join(bytes([int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16), 255]) for c in colours)
    px = g.put(rgba)
    _grade(g, px, len(colours), 1, {**spec, "grain": 0, "halation": 0, "vignette": 0}, 1)
    out = g.mem.read(g.store, px[0], px[0] + px[1])
    return ["#%02x%02x%02x" % tuple(out[i:i + 3]) for i in range(0, len(out), 4)]


def _grade(g: Wasm, px: Tuple[int, int], w: int, h: int, spec: Dict, seed: int) -> None:
    out = json.loads(g.answer(g.call("scrim_grade", px[0], px[1], w, h, *g.json(spec), seed)))
    if "error" in out:
        raise ValueError(out["error"])


def grade_still(src: str, dst: str, spec: Dict) -> Dict:
    """A still through a look at full size (grain seed 7, as the Lab's still)."""
    look(spec)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGBA")
    w, h = im.size
    g = Wasm("gobo.wasm")
    px = g.put(im.tobytes())
    _grade(g, px, w, h, spec, 7)
    Image.frombytes("RGBA", (w, h), bytes(g.mem.read(g.store, px[0], px[0] + px[1]))).convert("RGB") \
        .save(dst, quality=94)
    return {"w": w, "h": h}


def _matrix(path: str) -> str:
    """The clip's YUV matrix. Untagged HD reads as BT.709, as a browser shows it,
    so the graded film matches the Lab's preview of it."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_space",
             "-of", "csv=p=0", path], timeout=30)
    return "bt601" if (r.stdout or "").strip() in ("bt470bg", "smpte170m") else "bt709"


# swscale's fast path truncates: a decode and re-encode would darken the film
# about two levels before any look touched it
SWS = "accurate_rnd+full_chroma_int+bicubic"


def grade_film(src: str, dst: str, spec: Dict, timeout: int = 900) -> Dict:
    """Every frame of a clip through a look, the grain moving frame to frame (seed
    = frame number, as the Lab plays it); the sound is copied untouched.

    ffmpeg decodes to RGBA on a pipe, each frame is graded in the module's
    memory, and a second ffmpeg encodes what comes back. Both write their errors
    to a file, never a pipe, so neither can stall on a full buffer."""
    look(spec)
    info = probe(src)
    w, h, fps = int(info["width"]), int(info["height"]), info.get("fps") or 24
    if not w or not h:
        raise RuntimeError("the clip has no picture size")
    m = _matrix(src)
    n = w * h * 4
    g = Wasm("gobo.wasm")
    px = (g.call("alloc", n), n)
    t0 = time.time()
    frames = 0
    with tempfile.TemporaryFile() as err:
        dec = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-i", src, "-map", "0:v:0",
                                "-vf", f"scale=in_color_matrix={m}:flags={SWS}", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err)
        enc = subprocess.Popen(["ffmpeg", "-nostdin", "-y", "-v", "error",
                                "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0",
                                "-i", src, "-map", "0:v", "-map", "1:a?",
                                "-vf", f"scale=out_color_matrix={m}:out_range=tv:flags={SWS},format=yuv420p",
                                "-colorspace", m.replace("bt601", "smpte170m"), "-c:v", "libx264", "-preset", "veryfast",
                                "-crf", "17", "-c:a", "copy", "-shortest", "-movflags", "+faststart", dst],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        try:
            while True:
                buf = dec.stdout.read(n)
                if len(buf) < n:
                    break
                g.mem.write(g.store, buf, px[0])
                frames += 1
                _grade(g, px, w, h, spec, frames)
                enc.stdin.write(g.mem.read(g.store, px[0], px[0] + n))
                if time.time() - t0 > timeout:
                    raise RuntimeError(f"grading took longer than {timeout}s")
            enc.stdin.close()
            ok = enc.wait(timeout=300) == 0 and dec.wait(timeout=30) == 0
        except BaseException:
            for p in (dec, enc):
                p.kill()
            raise
        if not ok or not frames:
            err.seek(0)
            raise RuntimeError((err.read().decode(errors="replace") or "ffmpeg failed")[-300:])
    return {"frames": frames, "seconds": round(time.time() - t0, 2), "w": w, "h": h}
