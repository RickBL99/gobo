"""ffmpeg and ffprobe, called safely.

Both need to be on PATH (the Docker image installs them).
"""
import json
import os
import subprocess
from typing import Any, Dict, List

from PIL import Image

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".heic")
FPS = 30
STILL_SECONDS = 10.0        # a still can be cut to any length up to this


def run(cmd: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    """Run a tool with its stdin closed, and turn a timeout into a failure.

    STDIN MUST BE CLOSED. ffmpeg reads stdin by default (that is how you press
    q to stop it), and capture_output only redirects stdout and stderr, so the
    child inherits the server's stdin. Under a process manager that is not a
    terminal that ever delivers EOF, and ffmpeg can sit on a read that never
    returns: a render stops dead partway through with no error.

    A timeout comes back as a non-zero result rather than an exception: every
    caller already handles "this one failed", none of them handle a raise.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print(f"[studio] timed out after {timeout}s: {' '.join(cmd[:3])} … {os.path.basename(cmd[-1])}")
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXT


def probe(path: str) -> Dict[str, Any]:
    """Duration, dimensions, fps and whether there is sound."""
    if is_image(path):
        with Image.open(path) as im:
            w, h = im.size
        return {"duration": STILL_SECONDS, "width": w, "height": h, "fps": FPS,
                "has_audio": False, "vertical": h >= w, "kind": "image"}
    r = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path], timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr[:200]}")
    data = json.loads(r.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise RuntimeError("no video stream")
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    dur = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    return {"duration": dur, "width": w, "height": h, "fps": round(fps, 3),
            "has_audio": bool(a), "vertical": h >= w, "kind": "video"}
