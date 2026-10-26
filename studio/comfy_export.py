"""Open in ComfyUI: a take (or a whole film) as a ComfyUI workflow.

Every shot Gobo makes on Comfy's models has a ComfyUI partner node that makes
the same thing (comfy_api_nodes: LtxApi25ImageToVideo, Flux3ImageNode,
ByteDance2FirstLastFrameNode…). This writes the API-format workflow those
nodes run from: {"<id>": {"class_type", "inputs", "_meta": {"title"}}}, links
as ["<id>", <output>], model-dependent settings as dotted keys ("model",
"model.duration"), and a LoadImage per keyframe. Dropped on a ComfyUI canvas
(or opened with Workflow → Open) it lays itself out and runs, signed in to
Comfy; the zip carries the keyframes for ComfyUI's input/ folder.

Signatures from the ComfyUI source (commit 19ae172, 2026-10-24); the option
lists are what each node accepts, and values are snapped onto them.
"""
import io
import json
import math
import os
import re
import zipfile
from typing import Callable, Dict, List, Optional, Tuple

ASPECTS_16_9 = ["16:9", "9:16"]
DEFAULT_SEED = 42


def _ratio(a) -> Optional[float]:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[:x]\s*(\d+(?:\.\d+)?)\s*$", str(a or ""))
    return float(m.group(1)) / float(m.group(2)) if m and float(m.group(2)) else None


def _near_aspect(want: str, options: List[str]) -> str:
    w = _ratio(want) or 16 / 9
    scored = [(abs(math.log(_ratio(o) / w)), o) for o in options if _ratio(o)]
    return min(scored)[1] if scored else options[0]


def _near_num(want, options: List, cast=int):
    try:
        w = float(want)
    except (TypeError, ValueError):
        return options[0]
    nums = [(abs(float(o) - w), o) for o in options if str(o).replace(".", "").isdigit()]
    return min(nums)[1] if nums else options[0]


def _tier(res: str, video: bool = True) -> int:
    m = re.match(r"(\d+)", str(res or ""))
    n = int(m.group(1)) if m else (720 if video else 2)
    return n


def _pick_res(res: str, options: List[str]) -> str:
    """'720p' onto a node's resolutions: 480p/720p/1080p, 768P/2K, 720P…"""
    want = _tier(res) or 720
    def px(o):
        o = str(o).lower()
        if o.endswith("k"):
            return {"2k": 1440, "4k": 2160, "1k": 1024}.get(o, 1080)
        m = re.match(r"(\d+)", o)
        return int(m.group(1)) if m else 720
    return min(options, key=lambda o: abs(px(o) - want))


def _wh_for(aspect: str, res: str, options: List[str]) -> str:
    """LTX: '1920x1080' / '1080x1920' etc., nearest shape then size."""
    w = _ratio(aspect) or 16 / 9
    want = _tier(res) or 720
    def key(o):
        a, b = (int(x) for x in o.split("x"))
        return (abs(math.log((a / b) / w)), abs(min(a, b) - want))
    return min(options, key=key)


def _seed(req: Dict) -> int:
    try:
        return int(str(req.get("seed") or "").strip()) % 2147483647
    except ValueError:
        return DEFAULT_SEED


LTX_FAST_RES = ["1280x720", "720x1280", "1920x1080", "1080x1920", "2560x1440", "1440x2560", "3840x2160", "2160x3840"]
LTX_PRO_RES = ["1280x720", "720x1280", "1920x1080", "1080x1920"]
SEEDANCE_NAMES = {"dreamina-seedance-2-5": "Seedance 2.5", "dreamina-seedance-2-0-fast": "Seedance 2.0 Fast",
                  "dreamina-seedance-2-0-mini": "Seedance 2.0 Mini", "dreamina-seedance-2-0": "Seedance 2.0"}
RATIOS = ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]


class Graph:
    def __init__(self):
        self.nodes: Dict[str, Dict] = {}
        self.files: Dict[str, str] = {}           # name in input/ → path on disk
        self.made: Dict[str, str] = {}            # a take's file → the node that makes it here
        self.n = 0

    def add(self, cls: str, inputs: Dict, title: str = "") -> str:
        self.n += 1
        nid = str(self.n)
        self.nodes[nid] = {"class_type": cls, "inputs": inputs, **({"_meta": {"title": title}} if title else {})}
        return nid

    def image(self, path: str, name: str, title: str) -> List:
        self.files[name] = path
        return [self.add("LoadImage", {"image": name}, title), 0]


def _shot(g: Graph, t: Dict, find: Callable[[str], Optional[str]], tag: str) -> str:
    """Add one take's nodes; returns the id of its generating node."""
    mid = t.get("model") or ""
    req = t.get("request") or {}
    prompt = t.get("prompt") or req.get("prompt") or ""
    aspect, res, dur = req.get("aspect") or t.get("aspect") or "16:9", req.get("resolution") or "", req.get("duration")
    audio = bool(req.get("audio"))
    seed = _seed(req)

    def frame(field: str, label: str):
        name = req.get(field)
        if name and name in g.made:               # the keyframe is made in this workflow: wire it in
            return [g.made[name], 0]
        path = find(name) if name else None
        if not path:
            return None
        ext = os.path.splitext(path)[1].lower() or ".png"
        return g.image(path, f"gobo_{tag}_{field}{ext}", f"{tag} · {label}")

    first, last = frame("first_frame", "first frame"), frame("last_frame", "last frame")
    refs = []
    for i, r in enumerate((req.get("refs") or [])[:6]):
        p = find(r)
        if p:
            refs.append(g.image(p, f"gobo_{tag}_ref{i + 1}{os.path.splitext(p)[1].lower() or '.png'}", f"{tag} · reference {i + 1}"))

    if mid in ("ltx/ltx-2-5-fast", "ltx/ltx-2-5-pro"):
        pro = mid.endswith("pro")
        durs = ["2", "3", "4", "5", "6", "8", "10"] + ([] if pro else ["12", "14", "16", "18", "20"])
        ins = {"model": "LTX-2.5 (Pro)" if pro else "LTX-2.5 (Fast)", "model.duration": _near_num(dur or 6, durs),
               "model.resolution": _wh_for(aspect, res, LTX_PRO_RES if pro else LTX_FAST_RES), "model.fps": "25",
               "model.generate_audio": audio, "prompt": prompt, "seed": seed}
        if first:
            ins["image"] = first
            if last:
                ins["last_frame"] = last
            return g.add("LtxApi25ImageToVideo", ins, f"{tag} · LTX 2.5")
        return g.add("LtxApi25TextToVideo", ins, f"{tag} · LTX 2.5")

    if mid == "bfl/flux-3-image":
        ins = {"prompt": prompt, "aspect_ratio": _near_aspect(aspect, ["21:9", "2:1", "16:9", "3:2", "7:5", "4:3", "5:4", "1:1",
                                                                       "4:5", "3:4", "5:7", "2:3", "9:16", "1:2", "9:21"]),
               "resolution": {"512": "0.75K", "1k": "1K", "1.5k": "1.5K", "2k": "2K", "4k": "4K"}.get(str(res).lower(), "2K"),
               "grounding": True, "safety_tolerance": 4, "seed": seed}
        for i, r in enumerate(refs):
            ins[f"images.image_{i + 1}"] = r
        return g.add("Flux3ImageNode", ins, f"{tag} · FLUX 3")

    if mid == "bfl/flux-3-video":
        ins = {"prompt": prompt, "aspect_ratio": _near_aspect(aspect, ["21:9", "2:1", "16:9", "4:3", "1:1", "3:4", "9:16"]),
               "duration": str(_near_num(dur or 6, list(range(5, 21)))), "resolution": _pick_res(res, ["720p", "1080p", "1440p", "2160p"]),
               "generate_audio": audio, "safety_tolerance": 2, "seed": seed}
        if first:
            ins.update({"keyframes.image_0": first, "placement": "spread across the clip"})
            return g.add("Flux3ImageToVideoNode", ins, f"{tag} · FLUX 3 Video")
        return g.add("Flux3TextToVideoNode", ins, f"{tag} · FLUX 3 Video")

    if mid.startswith("byteplus/dreamina-seedance"):
        name = next((v for k, v in SEEDANCE_NAMES.items() if k in mid), "Seedance 2.0")
        ins = {"model": name, "model.prompt": prompt,
               "model.resolution": _pick_res(res, ["480p", "720p"] if "fast" in mid or "mini" in mid else ["480p", "720p", "1080p"]),
               "model.ratio": "adaptive" if first else _near_aspect(aspect, RATIOS),
               "model.duration": int(_near_num(dur or 5, list(range(4, 16)))), "model.generate_audio": audio,
               "seed": seed, "watermark": False}
        if first:
            ins["first_frame"] = first
            if last:
                ins["last_frame"] = last
            return g.add("ByteDance2FirstLastFrameNode", ins, f"{tag} · {name}")
        ins["model.output_format"] = "mp4"
        return g.add("ByteDance2TextToVideoNode", ins, f"{tag} · {name}")

    if mid.startswith("wan/wan3.0-video"):
        model = "wan3.0-video-prime" if mid.endswith("prime") else "wan3.0-video"
        ins = {"model": model, "model.prompt": prompt, "model.resolution": _pick_res(res, ["1080P", "720P", "480P"]),
               "model.ratio": "adaptive" if first else _near_aspect(aspect, ["16:9", "9:16", "1:1", "4:3", "3:4"]),
               "model.duration": str(_near_num(dur or 5, [str(i) for i in range(2, 16)])), "model.audio": audio,
               "model.prompt_extend": False, "seed": seed, "watermark": False}
        if first:
            ins["first_frame"] = first
            if last:
                ins["last_frame"] = last
            return g.add("Wan3ImageToVideoApi", ins, f"{tag} · Wan 3.0")
        for i, r in enumerate(refs):
            ins[f"model.reference_images.image_{i + 1}"] = r
        return g.add("Wan3ReferenceToVideoApi", ins, f"{tag} · Wan 3.0")

    if mid.startswith("veo/veo-3.1"):
        model = "veo-3.1-fast-generate" if "fast" in mid else "veo-3.1-generate"
        d = int(_near_num(dur or 8, [4, 6, 8]))
        if first and last:
            return g.add("Veo3FirstLastFrameNode", {"prompt": prompt, "negative_prompt": req.get("negative_prompt") or "",
                         "resolution": _pick_res(res, ["720p", "1080p", "4k"]), "aspect_ratio": _near_aspect(aspect, ASPECTS_16_9),
                         "duration": d, "seed": seed, "first_frame": first, "last_frame": last, "model": model,
                         "generate_audio": audio}, f"{tag} · Veo 3.1")
        ins = {"prompt": prompt, "aspect_ratio": _near_aspect(aspect, ASPECTS_16_9), "resolution": _pick_res(res, ["720p", "1080p", "4k"]),
               "negative_prompt": req.get("negative_prompt") or "", "duration_seconds": d, "enhance_prompt": False,
               "person_generation": "ALLOW", "seed": seed, "model": model, "generate_audio": audio}
        if first:
            ins["image"] = first
        return g.add("Veo3VideoGenerationNode", ins, f"{tag} · Veo 3.1")

    if mid == "minimax/minimax-h3":
        ins = {"model": "MiniMax H3", "model.prompt": prompt, "model.resolution": _pick_res(res, ["768P", "2K"]),
               "model.duration": int(_near_num(dur or 5, list(range(4, 16)))), "seed": seed, "watermark": False}
        if first:
            ins["first_frame"] = first
            if last:
                ins["last_frame"] = last
            return g.add("MinimaxHailuo03FirstLastFrameNode", ins, f"{tag} · MiniMax H3")
        ins["model.ratio"] = _near_aspect(aspect, RATIOS)
        return g.add("MinimaxHailuo03TextToVideoNode", ins, f"{tag} · MiniMax H3")

    if mid in ("kling/kling-v3-omni", "kling/kling-video-o1"):
        ins = {"model_name": mid.split("/")[1], "prompt": prompt, "duration": int(_near_num(dur or 5, list(range(3, 16)))),
               "resolution": _pick_res(res, ["720p", "1080p", "4k"]), "storyboards": "disabled", "generate_audio": audio, "seed": seed}
        if first:
            ins["first_frame"] = first
            if last:
                ins["end_frame"] = last
            return g.add("KlingOmniProFirstLastFrameNode", ins, f"{tag} · Kling 3 Omni")
        ins["aspect_ratio"] = _near_aspect(aspect, ["16:9", "9:16", "1:1"])
        return g.add("KlingOmniProTextToVideoNode", ins, f"{tag} · Kling 3 Omni")

    if mid == "vertexai/gemini-nano-banana-2.1":
        ins = {"prompt": prompt, "model": "Gemini Nano Banana 2.1",
               "model.aspect_ratio": _near_aspect(aspect, ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]),
               "model.resolution": {"1k": "1K", "2k": "2K", "4k": "4K"}.get(str(res).lower(), "2K"),
               "model.thinking_level": "MEDIUM", "seed": seed, "response_modalities": "IMAGE"}
        for i, r in enumerate(refs):
            ins[f"model.images.image_{i + 1}"] = r
        return g.add("GeminiNanoBanana2V2", ins, f"{tag} · Nano Banana 2.1")

    raise ValueError(f"No ComfyUI node mapping for {mid} yet.")


SUPPORTED = re.compile(r"^(ltx/ltx-2-5-(fast|pro)|bfl/flux-3-(image|video)|byteplus/dreamina-seedance|wan/wan3\.0-video"
                       r"|veo/veo-3\.1|minimax/minimax-h3$|kling/kling-(v3-omni|video-o1)$|vertexai/gemini-nano-banana-2\.1$)")


def supported(model: str) -> bool:
    return bool(SUPPORTED.match(model or ""))


def workflow(takes: List[Dict], find: Callable[[str], Optional[str]]) -> Tuple[Dict, Dict[str, str], List[str]]:
    """Takes → (API-format workflow, files for input/, takes left out and why)."""
    g, skipped = Graph(), []
    # stills first, so a clip can start from the keyframe node that makes its frame
    for t in sorted(takes, key=lambda t: (t.get("kind") == "video", t.get("shot") or 0, t.get("letter") or "")):
        tag = f"{int(t.get('shot') or 0):03d}{t.get('letter') or ''}"
        try:
            node = _shot(g, t, find, tag)
        except ValueError as e:
            skipped.append(f"{tag}: {e}")
            continue
        video = t.get("kind") == "video"
        if t.get("file"):
            g.made[t["file"]] = node
        g.add("SaveVideo" if video else "SaveImage",
              {"video" if video else "images": [node, 0], "filename_prefix": f"{'video/' if video else ''}gobo/{tag}",
               **({"format": "auto", "codec": "auto"} if video else {})}, f"{tag} · save")
    return g.nodes, g.files, skipped


README = """Gobo → ComfyUI
==============

1. Copy everything in input/ into your ComfyUI input folder (ComfyUI/input/).
   (Or open each Load Image node and upload the picture it names.)
2. Drag workflow_api.json onto the ComfyUI canvas, or use Workflow → Open.
   It lays itself out: one Load Image → partner node → Save chain per shot.
3. Sign in to your Comfy account in ComfyUI (the partner nodes run on Comfy's
   API credits, the same ones Gobo used) and press Run.

Made with Gobo{title}. Prompts are the ones Gobo sent; settings are snapped to
what each node offers.
"""


def bundle(takes: List[Dict], find: Callable[[str], Optional[str]], title: str = "") -> Tuple[bytes, List[str]]:
    nodes, files, skipped = workflow(takes, find)
    if not nodes:
        raise ValueError("Nothing here has a ComfyUI node yet. " + "; ".join(skipped))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("workflow_api.json", json.dumps(nodes, indent=2, ensure_ascii=False))
        for name, path in files.items():
            z.write(path, f"input/{name}")
        z.writestr("README.txt", README.format(title=f": {title}" if title else "")
                   + ("\nLeft out (no ComfyUI node yet):\n  " + "\n  ".join(skipped) + "\n" if skipped else ""))
    return buf.getvalue(), skipped
