"""The Comfy Router engine: every take runs on Comfy's model router.

Comfy Router (https://api.comfy.org, docs.comfy.org/development/comfy-router)
puts ~260 hosted models behind one key: FLUX, Seedance, LTX, MiniMax, Veo,
Kling, Luma, Runway, Wan, GPT Image, Nano Banana and more. Each model takes
its provider's own native request body and returns its own native result, so
this module does three jobs:

  1. CATALOGUE. Each model's published OpenAPI schema becomes one compact
     input description (models.analyse reads it), so framing, aspect,
     duration, references and frames are mapped by one piece of code for
     every model. Most models are flat. A few
     families nest their inputs and get a small adapter each: BytePlus/MiniMax
     put prompt and media in a `content` list, Wan/Qwen split `input` and
     `parameters` (Wan 2.7+ with a typed `media` list), Veo uses
     `instances`/`parameters` with base64 media, Gemini image models take
     `contents` parts, Luma nests a clip's settings in `video`, and Kling Omni
     tags its frames in `image_list`.
  2. SHAPING. models.build fills the flat fields; shape() turns them into the
     native body, resolving media to whatever the model accepts (a link, a
     data URL, raw base64). Files go up to Comfy's own storage, so one key
     runs everything.
  3. RUNNING. Router's queue: submit (idempotent), poll the status, then read
     the native result and find the picture or clip in it.

The catalogue ships as studio/comfy_catalog.json (tools/build_comfy_catalog.py
makes it from the live API), so the app starts without a network call.
"""
import base64
import contextvars
import hashlib
import json
import mimetypes
import os
import re
import socket
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import settings

BASE = "https://api.comfy.org"
HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "comfy_catalog.json")
ENGINE = "comfy"

PROVIDERS = {
    "bfl": "Black Forest Labs", "byteplus": "ByteDance", "ltx": "Lightricks", "minimax": "MiniMax",
    "veo": "Google", "vertexai": "Google", "gemini-interactions": "Google", "kling": "Kling",
    "luma": "Luma", "luma_2": "Luma", "runway": "Runway", "wan": "Alibaba", "qwen": "Alibaba",
    "openai": "OpenAI", "xai": "xAI", "krea": "Krea", "ideogram": "Ideogram", "recraft": "Recraft",
    "pika": "Pika", "moonvalley": "Moonvalley", "bria": "Bria", "higgsfield": "Higgsfield",
    "tencent": "Tencent", "meshy": "Meshy", "elevenlabs": "ElevenLabs", "wavespeed": "WaveSpeed",
    "fal": "fal", "runware": "Runware", "freepik": "Freepik", "veed": "VEED", "pruna": "Pruna",
    "beeble": "Beeble", "synclabs": "Sync Labs", "heygen": "HeyGen", "anthropic": "Anthropic",
}
# Resellers of other makers' models (fal's Seedance, Runware's Nano Banana…):
# the first-party version is already listed, so theirs are left out unless
# they are the only route to a model (crystal upscalers, patina…).
RESELLER_DUPES = re.compile(r"^(fal|runware|wavespeed|pika|higgsfield)/.*(seedance|nano-banana|gpt-image|kling|wan-3)")

# ── the hero roster: the models the studio leads with, labelled properly ────
# (id, label). Everything else in the catalogue is listed under "All".
HEROES = {
    "video": [
        ("Comfy · Cinema", [
            ("byteplus/dreamina-seedance-2-5-260628", "Seedance 2.5"),
            ("ltx/ltx-2-5-pro", "LTX 2.5 Pro"),
            ("minimax/minimax-h3", "MiniMax H3"),
            ("veo/veo-3.1-generate-001", "Veo 3.1"),
            ("kling/kling-v3-omni", "Kling 3 Omni"),
            ("luma_2/ray-3.2", "Luma Ray 3.2"),
            ("bfl/flux-3-video", "FLUX 3 Video"),
            ("wan/wan3.0-video", "Wan 3.0"),
            ("xai/grok-imagine-video-1.5", "Grok Imagine Video 1.5"),
            ("runway/gen4_turbo", "Runway Gen-4 Turbo"),
        ]),
        ("Comfy · Fast drafts", [
            ("ltx/ltx-2-5-fast", "LTX 2.5 Fast"),
            ("byteplus/dreamina-seedance-2-0-fast-260128", "Seedance 2.0 Fast"),
            ("veo/veo-3.1-fast-generate-001", "Veo 3.1 Fast"),
            ("kling/kling-3.0-turbo", "Kling 3 Turbo"),
            ("pika/pika-2.5-text-to-video", "Pika 2.5"),
        ]),
        ("Comfy · Change a clip", [
            ("higgsfield/higgsfield-genjutsu-motion-transfer-v1.0", "Recast · keep the motion, swap the cast"),
            ("runway/aleph2", "Runway Aleph 2"),
            ("wan/wan2.7-videoedit", "Wan 2.7 Video Edit"),
            ("bfl/video-edit-v1", "FLUX Video Edit"),
        ]),
    ],
    "image": [
        ("Comfy · Stills", [
            ("bfl/flux-3-image", "FLUX 3"),
            ("openai/gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst"),
            ("vertexai/gemini-nano-banana-2.1", "Nano Banana 2.1"),
            ("vertexai/gemini-3-pro-image", "Nano Banana Pro"),
            ("byteplus/seedream-5-0-260128", "Seedream 5"),
            ("krea/krea-3", "Krea 3"),
            ("luma_2/uni-1-max", "Luma Uni-1 Max"),
            ("runway/gen4_image", "Runway Gen-4 Image"),
            ("kling/kling-image-o1", "Kling Image O1"),
            ("xai/grok-imagine-image-pro", "Grok Imagine Image Pro"),
            ("bfl/flux-kontext-max", "FLUX Kontext Max"),
            ("ideogram/p-image-ideogram", "Ideogram (P Image)"),
        ]),
    ],
    "upscale": [
        ("Comfy · Finishing", [
            ("fal/crystal-upscaler", "Crystal Upscaler"),
            ("wavespeed/seedvr2", "SeedVR2 Upscale"),
            ("bria/image-edit-remove-background", "Bria Remove Background"),
        ]),
    ],
    "vupscale": [
        ("Comfy · Finishing", [
            ("bfl/video-upscale-v1", "FLUX Video Upscale"),
            ("wavespeed/flashvsr", "FlashVSR"),
            ("fal/crystal-video-upscaler", "Crystal Video Upscaler"),
        ]),
    ],
}
# What a run costs, in dollars, from docs.comfy.org/development/comfy-router/pricing
# (211 credits = $1), at the studio's default size: (dollars, "second" | "run").
# Router has no price API; the rest show no price until a run reports its credits.
PRICES = {
    "ltx/ltx-2-5-fast": (0.1287, "second"), "ltx/ltx-2-5-pro": (0.1716, "second"),
    "minimax/minimax-h3": (0.1287, "second"), "wan/wan3.0-video": (0.10, "second"),
    "veo/veo-3.1-fast-generate-001": (0.10, "second"),
    # Seedance bills tokens: about 21,600 a second at 720p
    "byteplus/dreamina-seedance-2-0-fast-260128": (0.173, "second"),
    "byteplus/dreamina-seedance-2-5-260628": (0.33, "second"),
    "bfl/flux-3-image": (0.07, "run"), "vertexai/gemini-nano-banana-2.1": (0.06, "run"),
}
HERO_LABELS = {mid: label for groups in HEROES.values() for _, items in groups for mid, label in items}

# Per-model facts the schema states only in prose.
DURATION_LIMITS = {
    "byteplus/dreamina-seedance-2-5-260628": (4, 15), "byteplus/dreamina-seedance-2-0-260128": (4, 15),
    "byteplus/dreamina-seedance-2-0-fast-260128": (4, 15), "byteplus/dreamina-seedance-2-0-mini": (4, 15),
    "byteplus/seedance-1-5-pro-251215": (4, 12), "byteplus/seedance-1-0-pro-250528": (3, 12),
    "byteplus/seedance-1-0-pro-fast-251015": (3, 12),
    "wan/wan3.0-video": (2, 15), "wan/wan3.0-video-prime": (2, 15), "wan/wan2.7-i2v": (2, 15),
    "wan/wan2.7-t2v": (2, 15), "wan/wan2.7-r2v": (2, 10), "wan/wan2.6-i2v": (5, 15), "wan/wan2.6-t2v": (5, 15),
}
VEO_DURATIONS = ["4", "6", "8"]

# Sizes and shapes some schemas only describe ("e.g. 1024x1024"), from each
# provider's own documentation. Keyed by a pattern on the model id.
SEEDREAM_SIZES = ["2048x2048", "2304x1728", "1728x2304", "2560x1440", "1440x2560", "2496x1664", "1664x2496",
                  "3024x1296", "4096x4096", "4608x3456", "3456x4608", "5120x2880", "2880x5120", "4992x3328",
                  "3328x4992", "6048x2592"]
LTX_PRO = ["1280x720", "720x1280", "1920x1080", "1080x1920"]
OVERRIDES = [
    (r"^openai/gpt-image", {"size": {"t": "string", "o": ["1024x1024", "1536x1024", "1024x1536"]}}),
    (r"^bfl/flux-kontext", {"aspect_ratio": {"t": "string", "o": ["21:9", "16:9", "3:2", "4:3", "1:1", "3:4", "2:3",
                                                                  "9:16", "9:21"]}}),
    (r"^byteplus/seedream-(4-5|5-0)", {"size": {"t": "string", "o": SEEDREAM_SIZES}}),
    (r"^ltx/ltx-2-5-pro$", {"resolution": {"t": "string", "o": LTX_PRO, "req": True}}),
    (r"^ltx/ltx-2-5-fast$", {"resolution": {"t": "string", "req": True, "o": LTX_PRO + [
        "2560x1440", "1440x2560", "3840x2160", "2160x3840"]}}),
    # FLUX 3 Video: a list of stills, the first of which starts the clip; `mode` follows
    (r"^bfl/flux-3-video$", {"keyframes": {"t": "array", "mul": True, "md": "image", "mi": 10},
                             "mode": {"t": "string", "o": ["t2v", "i2v"], "d": "t2v", "req": True}}),
]
# LTX 2.5 on Router reads only words; ComfyUI's LTX nodes start from a frame
# through Comfy's LTX proxy (comfy_api_nodes/nodes_ltxv.py: POST
# /proxy/ltx/v2/image-to-video, then GET …/{id} until it's done). So an LTX
# shot with a frame goes that way, on the same key.
LTX_PROXY = "/proxy/ltx/v2/image-to-video"
LTX_DONE = {"succeeded", "succeed", "success", "completed", "finished", "done", "complete"}
LTX_FAILED = {"cancelled", "canceled", "canceling", "fail", "failed", "error"}
# Native names the studio's mapper doesn't know, as names it does.
ALIASES = {"promptImage": "first_frame", "videoUri": "video", "duration_s": "duration"}
WAN_MEDIA = re.compile(r"wan2\.7|wan3\.0|happyhorse")
# A field that has to change with another: (field, when this one is sent, value).
SWITCH = {"bfl/flux-3-video": ("mode", "keyframes", "i2v")}

PROMPT_NAMES = {"prompt", "positivePrompt", "promptText", "text_prompt", "prompt_text", "text", "instruction"}
DROP = {"model", "callback_url", "webhook_url", "webhook_secret", "external_task_id", "idempotency_key",
        "includeCost", "deliveryMethod", "taskUUID", "taskType", "numberResults", "response_format",
        "enable_base64_output", "sync_mode", "moderation", "pubsubTopic", "storageUri", "draft_task"}


# ── reading a schema ────────────────────────────────────────────────────────

def _deref(s: Dict, x: Any) -> Any:
    seen = 0
    while isinstance(x, dict) and "$ref" in x and seen < 20:
        x = s["components"]["schemas"][x["$ref"].split("/")[-1]]
        seen += 1
    if isinstance(x, dict) and "allOf" in x:
        merged: Dict = {"properties": {}, "required": []}
        for part in x["allOf"]:
            p = _deref(s, part)
            merged["properties"].update(p.get("properties", {}))
            merged["required"] += p.get("required", [])
            for k in ("type", "description", "enum", "default", "items"):
                if k in p and k not in merged:
                    merged[k] = p[k]
        x = {**{k: v for k, v in x.items() if k != "allOf"}, **merged}
    return x


def _body(s: Dict) -> Dict:
    post = next(iter(s["paths"].values()))["post"]
    return _deref(s, next(iter(post["requestBody"]["content"].values()))["schema"])


MEDIA_HINT = re.compile(r"\b(url|uri|base64|https?|data:)\b", re.I)


def _media(name: str, p: Dict) -> Optional[str]:
    """The kind of file a field carries, if it carries one."""
    n = name.lower()
    desc = (p.get("description") or "")
    if p.get("format") == "uri" or MEDIA_HINT.search(desc) or n.endswith(("_url", "_uri", "url", "uri", "urls")):
        if "video" in n:
            return "video"
        if "audio" in n or "sound" in n:
            return "audio"
        if any(w in n for w in ("image", "img", "frame", "keyframe", "reference", "photo", "picture")):
            return "image"
        if n in ("images", "image", "source", "input"):
            return "image"
    return None


_ASPECT_RX = re.compile(r"(?<![\d.x])(\d{1,2})\s*([:x])\s*(\d{1,2})(?!\d|\.\d|x\d)")
_TIER_RX = re.compile(r"(?<![\w.])(\d(?:\.\d)?k|\d{3,4}p|768sq)(?!\w)", re.I)
_WH_RX = re.compile(r"(?<!\d)(\d{3,4})x(\d{3,4})(?!\d)")


def _prose_options(name: str, desc: str) -> Optional[List[str]]:
    """Choices a schema lists in its description rather than an enum:
    "Output aspect ratio: auto, 21:9, 16:9…", "1k or 2k.", "e.g. 1024x1024"."""
    n = name.lower()
    if "aspect" in n or n == "ratio":
        opts = [f"{a}{sep}{b}" for a, sep, b in _ASPECT_RX.findall(desc)]
    elif "resolution" in n or n == "size":
        whs = [f"{w}x{h}" for w, h in _WH_RX.findall(desc)]
        tiers = _TIER_RX.findall(desc)
        opts = whs if len(whs) >= 2 and n == "size" else (tiers or whs)
    else:
        return None
    opts = list(dict.fromkeys(opts))
    return opts if len(opts) >= 2 else None


def _spec(s: Dict, name: str, p: Dict, required: List[str]) -> Dict:
    """One field in the studio's compact form: {t, d, o, mn, mx, req, mul, mi, md, obj}."""
    p = _deref(s, p)
    out: Dict = {}
    alts = p.get("anyOf") or p.get("oneOf")
    if alts:
        alts = [_deref(s, a) for a in alts]
        arr = next((a for a in alts if a.get("type") == "array"), None)
        if arr:                                     # "a string, or a list of strings"
            p = {**arr, "description": p.get("description") or arr.get("description")}
        else:
            nums = [a for a in alts if a.get("type") in ("integer", "number") or "minimum" in a]
            if nums:                                # Seedance duration: -1 or 2..30
                lo = max((a.get("minimum") for a in nums if a.get("minimum", -1) >= 0), default=None)
                hi = max((a.get("maximum") for a in nums if a.get("maximum") is not None), default=None)
                p = {"type": "integer", "minimum": lo, "maximum": hi, "description": p.get("description")}
            else:
                p = alts[0]
    t = p.get("type")
    if t == "array":
        item = _deref(s, p.get("items") or {})
        out["mul"] = True
        if p.get("maxItems"):
            out["mi"] = p["maxItems"]
        if item.get("type") == "object" or "properties" in item:
            out["obj"] = True
        md = _media(name, {**item, "description": p.get("description") or item.get("description")})
        if md:
            out["md"] = md
        out["t"] = "array"
    elif t == "object" or "properties" in p:
        out["obj"] = True
        out["t"] = "object"
    else:
        md = _media(name, p)
        if md:
            out["md"] = md
            out["t"] = "File (URL)"
        else:
            out["t"] = t or ("string" if p.get("enum") and isinstance(p["enum"][0], str) else None)
    if p.get("enum"):
        out["o"] = list(p["enum"])
    elif out.get("t") == "string":
        prose = _prose_options(name, p.get("description") or "")
        if prose:
            out["o"] = prose
    if "default" in p:
        out["d"] = p["default"]
    if p.get("minimum") is not None:
        out["mn"] = p["minimum"]
    if p.get("maximum") is not None:
        out["mx"] = p["maximum"]
    if name in required:
        out["req"] = True
    return out


def _category(ins: List[str], outs: List[str], spec: Dict, mid: str) -> Optional[str]:
    has_prompt = any(n in spec for n in PROMPT_NAMES)
    media = {n: p for n, p in spec.items() if p.get("md")}
    req_image = any(p.get("req") and p.get("md") == "image" for p in media.values())
    req_video = any(p.get("req") and p.get("md") == "video" for p in media.values())
    has_video_in = any(p.get("md") == "video" for p in media.values())
    if "video" in outs:
        if has_video_in and re.search(r"upscal|vsr", mid):
            return "video-upscale-enhance"          # some take an optional prompt; still a finishing tool
        if not has_prompt:
            if has_video_in and re.search(r"enhanc", mid):
                return "video-upscale-enhance"
            return None
        if req_video or (has_video_in and "edit" in mid):
            return "video-edit"
        if "extend" in mid:
            return "video-extend"
        return "image-to-video" if req_image else "text-to-video"
    if "image" in outs:
        if not has_prompt:
            if re.search(r"upscal|seedvr|increase-resolution|remove-background$", mid):
                return "upscale"
            return None
        return "image-edit" if req_image else "text-to-image"
    return None


def _label(mid: str) -> str:
    if mid in HERO_LABELS:
        return HERO_LABELS[mid]
    name = mid.split("/", 1)[1]
    name = re.sub(r"-(\d{6})$", "", name)             # BytePlus date stamps
    words = re.split(r"[-_]", name)
    fix = {"gpt": "GPT", "ltx": "LTX", "vsr": "VSR", "i2v": "Image→Video", "t2v": "Text→Video",
           "r2v": "Reference→Video", "t2i": "Text→Image", "i2i": "Image→Image", "ai": "AI", "v3": "V3",
           "4k": "4K", "hd": "HD", "uni": "Uni"}
    return " ".join(fix.get(w.lower(), w.capitalize() if not re.match(r"^v?\d", w) else w) for w in words)


# ── the nested families ─────────────────────────────────────────────────────
# Each returns (virtual flat spec, family name). Virtual names are the ones
# models.analyse already understands (prompt, first_frame, last_frame,
# reference_images, video, duration, ratio, resolution, generate_audio…).

def _content_family(s: Dict, body: Dict) -> Tuple[Dict, str]:
    """BytePlus Seedance and MiniMax H3: prompt and media as `content` items."""
    req = body.get("required", [])
    spec: Dict = {"prompt": {"t": "string", "req": True},
                  "first_frame": {"t": "File (URL)", "md": "image"},
                  "last_frame": {"t": "File (URL)", "md": "image"},
                  "reference_images": {"t": "array", "mul": True, "md": "image", "mi": 4}}
    ins = s.get("x-comfy-router-input-modalities") or []
    if "video" in ins:
        spec["video"] = {"t": "File (URL)", "md": "video"}
    if "image" not in ins:
        for k in ("first_frame", "last_frame", "reference_images"):
            spec.pop(k)
    for n, p in body.get("properties", {}).items():
        if n in ("content", *DROP):
            continue
        spec[n] = _spec(s, n, p, req)
    return spec, "content"


def _wan_family(s: Dict, body: Dict) -> Tuple[Dict, str]:
    """Wan and Qwen: `input` (prompt, media) and `parameters` (everything else)."""
    props = body.get("properties", {})
    inp = _deref(s, props.get("input", {}))
    par = _deref(s, props.get("parameters", {}))
    spec: Dict = {}
    ip = inp.get("properties", {})
    mid = s.get("x-comfy-router-model-id") or ""
    if "messages" in ip:                          # Qwen image: a chat message with text + images
        spec["prompt"] = {"t": "string", "req": True}
        spec["reference_images"] = {"t": "array", "mul": True, "md": "image", "mi": 3}
        family = "qwen"
    elif "media" in ip and WAN_MEDIA.search(mid):
        # Wan 2.7 / 3.0 and HappyHorse: frames, references and the clip to edit
        # are typed items in `input.media`, not img_url
        spec["prompt"] = {"t": "string", "req": True}
        spec["negative_prompt"] = {"t": "string"}
        if re.search(r"video-?edit", mid):
            spec["video"] = {"t": "File (URL)", "md": "video", "req": True}
            spec["reference_images"] = {"t": "array", "mul": True, "md": "image", "mi": 4}
        elif "r2v" in mid:
            spec["reference_images"] = {"t": "array", "mul": True, "md": "image", "mi": 5, "req": True}
        elif "i2v" in mid:
            spec["first_frame"] = {"t": "File (URL)", "md": "image", "req": True}
            if "wan2.7" in mid:
                spec["last_frame"] = {"t": "File (URL)", "md": "image"}
        elif "wan3.0" in mid:
            spec["first_frame"] = {"t": "File (URL)", "md": "image"}
            spec["last_frame"] = {"t": "File (URL)", "md": "image"}
            spec["reference_images"] = {"t": "array", "mul": True, "md": "image", "mi": 10}
        family = "wan_media"
    else:
        if "prompt" in ip:
            spec["prompt"] = {"t": "string", "req": "prompt" in inp.get("required", [])}
        if "negative_prompt" in ip:
            spec["negative_prompt"] = {"t": "string"}
        ins = s.get("x-comfy-router-input-modalities") or []
        if "img_url" in ip and "image" in ins:
            spec["first_frame"] = {"t": "File (URL)", "md": "image",
                                   "req": "img_url" in inp.get("required", [])}
        if "video_url" in ip:
            spec["video"] = {"t": "File (URL)", "md": "video", "req": "video_url" in inp.get("required", [])}
        elif "reference_video_urls" in ip and "video" in ins:
            spec["video"] = {"t": "File (URL)", "md": "video"}
        family = "wan"
    preq = par.get("required", [])
    for n, p in par.get("properties", {}).items():
        if n in DROP or (n == "size" and "resolution" in par.get("properties", {})):
            continue                              # size wants "1280*720"; resolution sets the tier
        sp = _spec(s, n, p, preq)
        if n == "audio":
            n = "generate_audio"
        spec[n] = sp
    return spec, family


def _veo_family(s: Dict, body: Dict) -> Tuple[Dict, str]:
    props = body.get("properties", {})
    inst = _deref(s, (_deref(s, props.get("instances", {})).get("items")) or {})
    par = _deref(s, props.get("parameters", {}))
    ip = inst.get("properties", {})
    spec: Dict = {"prompt": {"t": "string", "req": True}}
    if "image" in ip:
        spec["first_frame"] = {"t": "File (URL)", "md": "image"}
    if "lastFrame" in ip:
        spec["last_frame"] = {"t": "File (URL)", "md": "image"}
    if "referenceImages" in ip:
        spec["reference_images"] = {"t": "array", "mul": True, "md": "image", "mi": 3}
    pp = par.get("properties", {})
    if "aspectRatio" in pp:
        spec["aspect_ratio"] = _spec(s, "aspectRatio", pp["aspectRatio"], [])
    spec["duration"] = {"t": "string", "o": VEO_DURATIONS, "d": "8"}
    if "generateAudio" in pp:
        spec["generate_audio"] = {"t": "boolean", "d": True}
    if "resolution" in pp:
        spec["resolution"] = _spec(s, "resolution", pp["resolution"], [])
    if "negativePrompt" in pp:
        spec["negative_prompt"] = {"t": "string"}
    if "seed" in pp:
        spec["seed"] = {"t": "integer"}
    return spec, "veo"


def _gemini_family(s: Dict, body: Dict) -> Tuple[Dict, str]:
    return ({"prompt": {"t": "string", "req": True},
             "reference_images": {"t": "array", "mul": True, "md": "image", "mi": 6},
             "aspect_ratio": {"t": "string", "o": ["1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "16:9", "9:16", "21:9"]},
             "resolution": {"t": "string", "o": ["1K", "2K", "4K"], "d": "2K"},
             "seed": {"t": "integer"}}, "gemini")


def _luma_family(s: Dict, body: Dict, outs: List[str]) -> Tuple[Dict, str]:
    """Luma's agents API: one body for Uni (stills) and Ray (clips), told apart
    by `type`; a clip's length, size and guide frames sit in a `video` object."""
    props = body.get("properties", {})
    spec: Dict = {"prompt": {"t": "string", "req": True}}
    if "video" in outs:
        vid = _deref(s, props.get("video", {})).get("properties", {})
        spec["aspect_ratio"] = {"t": "string", "o": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]}
        spec["duration"] = _spec(s, "duration", vid.get("duration", {}), [])
        spec["resolution"] = _spec(s, "resolution", vid.get("resolution", {}), [])
        spec["first_frame"] = {"t": "File (URL)", "md": "image"}
        spec["last_frame"] = {"t": "File (URL)", "md": "image"}
    else:
        spec["aspect_ratio"] = _spec(s, "aspect_ratio", props.get("aspect_ratio", {}), [])
        spec["reference_images"] = {"t": "array", "mul": True, "md": "image", "mi": 9}
    return spec, "luma"


def _kling_omni_family(s: Dict, body: Dict) -> Tuple[Dict, str]:
    """Kling Omni and O1: frames and references go in one `image_list`, each
    tagged first_frame / end_frame or left as a reference; std/pro is 720p/1080p."""
    props = body.get("properties", {})
    spec: Dict = {"prompt": {"t": "string", "req": True},
                  "first_frame": {"t": "File (URL)", "md": "image"},
                  "last_frame": {"t": "File (URL)", "md": "image"},
                  "reference_images": {"t": "array", "mul": True, "md": "image", "mi": 4},
                  "resolution": {"t": "string", "o": ["720p", "1080p"], "d": "1080p"},
                  "generate_audio": {"t": "boolean"}}
    for n in ("aspect_ratio", "duration"):
        if n in props:
            spec[n] = _spec(s, n, props[n], [])
    return spec, "kling_omni"


def _patch(mid: str, spec: Dict, family: str) -> Tuple[Dict, Dict]:
    """Known names, shapes and sizes the schema doesn't state outright → (spec, aliases)."""
    aliases = {}
    if family == "flat":
        for native, virtual in ALIASES.items():
            if native in spec and virtual not in spec:
                sp = spec.pop(native)
                md = "video" if virtual == "video" else "image" if virtual == "first_frame" else None
                spec[virtual] = ({"t": "File (URL)", "md": md, **({"req": True} if sp.get("req") else {})}
                                 if md else sp)
                aliases[virtual] = native
        for n, p in spec.items():                 # Kling: sound "on" / "off"
            if sorted(map(str, p.get("o") or [])) == ["off", "on"]:
                spec[n] = {"t": "boolean", "onoff": True}
    for pat, fields in OVERRIDES:
        if re.search(pat, mid):
            for n, p in fields.items():
                spec[n] = {**{k: v for k, v in spec.get(n, {}).items() if k not in p}, **p}
    return spec, aliases


def record(s: Dict) -> Optional[Dict]:
    """A catalogue record in the studio's format, or None if the studio can't drive it."""
    mid = s.get("x-comfy-router-model-id") or ""
    if not mid or RESELLER_DUPES.match(mid):
        return None
    outs = s.get("x-comfy-router-output-modalities") or []
    ins = s.get("x-comfy-router-input-modalities") or []
    if not ({"image", "video"} & set(outs)) or "3d" in outs:
        return None
    try:
        body = _body(s)
    except Exception:
        return None
    props = body.get("properties", {})
    if not props:
        return None
    if "content" in props and "byteplus" in mid or mid.startswith("minimax/"):
        spec, family = _content_family(s, body)
    elif "input" in props and "parameters" in props and (mid.startswith(("wan/", "qwen/"))):
        spec, family = _wan_family(s, body)
    elif "instances" in props:
        spec, family = _veo_family(s, body)
    elif "contents" in props and "generationConfig" in props:
        if "video" in outs or "text" in outs and "image" not in outs:
            return None
        spec, family = _gemini_family(s, body)
    elif mid.startswith("luma_2/"):
        spec, family = _luma_family(s, body, outs)
    elif mid.startswith("kling/") and "image_list" in props and "video" in outs:
        spec, family = _kling_omni_family(s, body)
    else:
        req = body.get("required", [])
        spec = {n: _spec(s, n, p, req) for n, p in props.items() if n not in DROP}
        family = "flat"
    spec, aliases = _patch(mid, spec, family)
    if mid in ("ltx/ltx-2-5-fast", "ltx/ltx-2-5-pro"):
        spec["first_frame"] = {"t": "File (URL)", "md": "image"}
        spec["last_frame"] = {"t": "File (URL)", "md": "image"}
        family = "ltx"
    # durations the schema only states in prose
    lim = DURATION_LIMITS.get(mid)
    if lim and "duration" in spec:
        spec["duration"] = {**{k: v for k, v in spec["duration"].items() if k not in ("o",)},
                            "t": "integer", "mn": lim[0], "mx": lim[1]}
    cat = _category(ins, outs, spec, mid)
    if not cat:
        return None
    provider = mid.split("/")[0]
    price, per = PRICES.get(mid, (None, None))
    return {"slug": mid, "name": _label(mid), "provider": PROVIDERS.get(provider, provider.capitalize()),
            "category": cat, "inputs": spec, "cost": price, "price_per": per, "latency": None, "listed": True, "soon": False,
            "engine": ENGINE, "family": family, "pop": 1000 if mid in HERO_LABELS else 0,
            "desc": f"{_label(mid)} on Comfy Router ({mid}).",
            "ins": ins, "outs": outs, **({"alias": aliases} if aliases else {})}


# ── the bundled catalogue ───────────────────────────────────────────────────
_cache: Dict = {"t": None, "recs": []}


def records() -> List[Dict]:
    """Comfy models in the studio's record format (empty without the snapshot)."""
    try:
        mt = os.path.getmtime(CATALOG)
    except OSError:
        return []
    if _cache["t"] != mt:
        with open(CATALOG, encoding="utf-8") as f:
            _cache["recs"] = json.load(f).get("models", [])
        _cache["t"] = mt
    return _cache["recs"]


def enabled() -> bool:
    return settings.VISITOR_KEYS or bool(settings.COMFY_API_KEY)


# The key a call runs on: the studio's own, or on the public demo
# (STUDIO_KEYS=visitor) the visitor's, set for each request and each pump step
# by visitors.acting_for. The demo never falls back to a server key.
KEY: contextvars.ContextVar = contextvars.ContextVar("comfy_key", default=None)


def api_key() -> str:
    k = KEY.get()
    if k is not None:
        return k
    return "" if settings.VISITOR_KEYS else settings.COMFY_API_KEY


def is_comfy(model_slug: str) -> bool:
    return "/" in (model_slug or "")


# ── media ───────────────────────────────────────────────────────────────────

class FileRef(dict):
    """A local file a field should carry; shape() decides how it travels."""


def file_ref(path: str) -> FileRef:
    return FileRef(__file__=path)


def _data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"


def _raw_b64(path: str) -> Tuple[str, str]:
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode(), mime


_hosted: Dict[str, Tuple[str, float]] = {}


def host(path: str) -> str:
    """Put a file on Comfy's own storage (the upload the ComfyUI API nodes
    use) and return a signed link a model can fetch. Named by content, so the
    same frame is uploaded once."""
    key = f"{path}:{os.path.getmtime(path)}"
    hit = _hosted.get(key)
    if hit and hit[1] > time.time() + 1800:
        return hit[0]
    with open(path, "rb") as f:
        data = f.read()
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    name = f"gobo-{hashlib.sha1(data).hexdigest()[:24]}{os.path.splitext(path)[1].lower()}"
    r = requests.post(f"{BASE}/customers/storage", headers=_headers(),
                      json={"file_name": name, "content_type": mime}, timeout=30)
    if r.status_code != 200:
        raise _error(r)
    j = r.json()
    up = requests.put(j["upload_url"], data=data, headers={"Content-Type": mime}, timeout=300)
    if up.status_code >= 300:
        raise RuntimeError(f"Comfy storage refused the upload ({up.status_code})")
    m = re.search(r"[?&]Expires=(\d+)", j["download_url"])
    _hosted[key] = (j["download_url"], float(m.group(1)) if m else time.time() + 6 * 3600)
    return j["download_url"]


def _link(path: str) -> str:
    """A link the model can fetch: Comfy's storage (one key runs it all), or,
    for a still only, a data URL."""
    try:
        return host(path)
    except Exception as e:                          # noqa: BLE001 - try the next way
        print(f"[comfy] Comfy storage didn't take {os.path.basename(path)}: {e}")
    if (mimetypes.guess_type(path)[0] or "").startswith("image/"):
        return _data_url(path)
    from .models import RunError
    raise RunError("Couldn't put the clip where the model can fetch it (a video has to be a public link).",
                   "upload")


def _resolve(v):
    if isinstance(v, FileRef):
        return _link(v["__file__"])
    if isinstance(v, list):
        return [_resolve(x) for x in v]
    return v


# ── shaping: flat fields → the native body ──────────────────────────────────

def shape(rec: Dict, P: Dict) -> Dict:
    fam = rec.get("family", "flat")
    P = dict(P)
    if fam == "content":
        items = [{"type": "text", "text": P.pop("prompt", "")}]
        for role, key in (("first_frame", "first_frame"), ("last_frame", "last_frame")):
            if P.get(key):
                items.append({"type": "image_url", "image_url": {"url": _resolve(P.pop(key))}, "role": role})
            P.pop(key, None)
        for r in P.pop("reference_images", None) or []:
            items.append({"type": "image_url", "image_url": {"url": _resolve(r)}, "role": "reference_image"})
        if P.get("video"):
            items.append({"type": "video_url", "video_url": {"url": _resolve(P.pop("video"))}, "role": "reference_video"})
        P.pop("video", None)
        return {"content": items, **{k: _resolve(v) for k, v in P.items()}}
    if fam == "wan":
        inp, par = {}, {}
        for k in ("prompt", "negative_prompt"):
            if k in P:
                inp[k] = P.pop(k)
        if P.get("first_frame"):
            inp["img_url"] = _resolve(P.pop("first_frame"))
        P.pop("first_frame", None)
        if P.get("video"):
            src = rec["inputs"].get("video", {})
            inp["video_url" if src.get("req") or "edit" in rec["slug"] else "reference_video_urls"] = (
                _resolve(P.pop("video")) if (src.get("req") or "edit" in rec["slug"]) else [_resolve(P.pop("video"))])
        P.pop("video", None)
        if "generate_audio" in P:
            par["audio"] = P.pop("generate_audio")
        par.setdefault("prompt_extend", False)           # the studio already wrote the prompt
        par.update({k: _resolve(v) for k, v in P.items()})
        return {"input": inp, "parameters": par}
    if fam == "wan_media":
        inp = {k: P.pop(k) for k in ("prompt", "negative_prompt") if k in P}
        media = []
        for key, kind in (("video", "video"), ("first_frame", "first_frame"), ("last_frame", "last_frame")):
            v = P.pop(key, None)
            if v:
                media.append({"type": kind, "url": _resolve(v)})
        media += [{"type": "reference_image", "url": _resolve(r)} for r in (P.pop("reference_images", None) or [])]
        if media:
            inp["media"] = media
        par = {"audio": P.pop("generate_audio")} if "generate_audio" in P else {}
        par.setdefault("prompt_extend", False)
        par.update({k: _resolve(v) for k, v in P.items()})
        return {"input": inp, "parameters": par}
    if fam == "qwen":
        content = [{"text": P.pop("prompt", "")}]
        content += [{"image": _resolve(r)} for r in (P.pop("reference_images", None) or [])]
        par = {k: v for k, v in P.items()}
        par.setdefault("prompt_extend", False)
        return {"input": {"messages": [{"role": "user", "content": content}]}, "parameters": par}
    if fam == "veo":
        inst: Dict = {"prompt": P.pop("prompt", "")}
        for key, field in (("first_frame", "image"), ("last_frame", "lastFrame")):
            v = P.pop(key, None)
            if isinstance(v, FileRef):
                data, mime = _raw_b64(v["__file__"])
                inst[field] = {"bytesBase64Encoded": data, "mimeType": mime}
        refs = P.pop("reference_images", None) or []
        if refs:
            inst["referenceImages"] = []
            for r in refs:
                data, mime = _raw_b64(r["__file__"])
                inst["referenceImages"].append({"image": {"bytesBase64Encoded": data, "mimeType": mime},
                                                "referenceType": "asset"})
        par: Dict = {"sampleCount": 1, "personGeneration": "allow_adult"}
        if "aspect_ratio" in P:
            par["aspectRatio"] = P.pop("aspect_ratio")
        if "duration" in P:
            par["durationSeconds"] = int(float(P.pop("duration")))
        if "generate_audio" in P:
            par["generateAudio"] = bool(P.pop("generate_audio"))
        if "resolution" in P:
            par["resolution"] = P.pop("resolution")
        if "negative_prompt" in P:
            par["negativePrompt"] = P.pop("negative_prompt")
        if "seed" in P:
            par["seed"] = P.pop("seed")
        return {"instances": [inst], "parameters": par}
    if fam == "gemini":
        parts: List[Dict] = [{"text": P.pop("prompt", "")}]
        for r in P.pop("reference_images", None) or []:
            data, mime = _raw_b64(r["__file__"])
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
        cfg: Dict = {"responseModalities": ["IMAGE"], "imageConfig": {}}
        if P.get("aspect_ratio"):
            cfg["imageConfig"]["aspectRatio"] = P.pop("aspect_ratio")
        if P.get("resolution"):
            cfg["imageConfig"]["imageSize"] = P.pop("resolution")
        if "seed" in P:
            cfg["seed"] = P.pop("seed")
        return {"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}
    if fam == "luma":
        clip = "video" in (rec.get("outs") or [])
        body: Dict = {"prompt": P.pop("prompt", ""), "type": "video" if clip else "image"}
        if P.get("aspect_ratio"):
            body["aspect_ratio"] = P.pop("aspect_ratio")
        if clip:
            vid: Dict = {}
            for k in ("duration", "resolution"):
                if P.get(k):
                    vid[k] = P.pop(k)
            for key, field in (("first_frame", "start_frame"), ("last_frame", "end_frame")):
                if isinstance(P.get(key), FileRef):
                    vid[field] = _ref_obj(P.pop(key))
            if vid:
                body["video"] = vid
        refs = P.pop("reference_images", None) or []
        if refs:
            body["image_ref"] = [_ref_obj(r) for r in refs]
        return body
    if fam == "kling_omni":
        images = []
        for key, kind in (("first_frame", "first_frame"), ("last_frame", "end_frame")):
            v = P.pop(key, None)
            if v:
                images.append({"image_url": _resolve(v), "type": kind})
        images += [{"image_url": _resolve(r)} for r in (P.pop("reference_images", None) or [])]
        body = {"prompt": P.pop("prompt", "")}
        if images:
            body["image_list"] = images
        if P.get("resolution"):
            body["mode"] = "std" if str(P.pop("resolution")).startswith("720") else "pro"
        if "generate_audio" in P:
            body["sound"] = "on" if P.pop("generate_audio") else "off"
        if any(i.get("type") == "first_frame" for i in images):
            P.pop("aspect_ratio", None)          # the frame sets the shape
        body.update({k: _resolve(v) for k, v in P.items()})
        return body
    if fam == "ltx":
        first, last = P.pop("first_frame", None), P.pop("last_frame", None)
        if not first:
            return {k: _resolve(v) for k, v in P.items()}          # words only: Router
        body = {"_route": LTX_PROXY, "prompt": P.get("prompt", ""), "model": rec["slug"].split("/", 1)[1],
                "duration": int(float(P.get("duration") or 6)), "resolution": P.get("resolution") or "1920x1080",
                "fps": 25, "generate_audio": bool(P.get("generate_audio", True)), "image_uri": _resolve(first)}
        if last:
            body["last_frame_uri"] = _resolve(last)
        return body
    # flat: the model's own names, with the studio's aliases turned back
    for virtual, native in (rec.get("alias") or {}).items():
        if virtual in P:
            P[native] = P.pop(virtual)
    for n, p in (rec.get("inputs") or {}).items():
        if p.get("onoff") and n in P:
            P[n] = "on" if P[n] else "off"
    sw = SWITCH.get(rec.get("slug"))
    if sw and P.get(sw[1]):
        P[sw[0]] = sw[2]
    return {k: _resolve(v) for k, v in P.items()}


def _ref_obj(v: FileRef) -> Dict:
    """Luma's image reference: {"url"}, or {"data", "media_type"} when only
    inline will do."""
    url = _link(v["__file__"])
    if not url.startswith("data:"):
        return {"url": url}
    data, mime = _raw_b64(v["__file__"])
    return {"data": data, "media_type": mime}


# ── running ─────────────────────────────────────────────────────────────────

def _headers(extra: Optional[Dict] = None) -> Dict:
    return {"X-API-Key": api_key(), "Content-Type": "application/json", **(extra or {})}


def _error(r: requests.Response):
    from .models import RunError
    etype = r.headers.get("X-Comfy-Error-Type") or ""
    try:
        j = r.json()
        msg = j.get("detail") or j.get("message") or j.get("error") or json.dumps(j)[:300]
        etype = etype or j.get("error_type") or ""
        if isinstance(msg, (dict, list)):
            msg = json.dumps(msg)[:300]
    except Exception:
        msg = (r.text or "")[:300]
    msg = str(msg)
    s = r.status_code
    if s == 401:
        return RunError("Comfy rejected your API key: sign in again with a current one." if settings.VISITOR_KEYS
                        else "Comfy rejected the API key (COMFY_API_KEY).", "unavailable")
    if s == 402 or etype == "insufficient_credits":
        return RunError("Not enough Comfy credits for this shot. Top up at platform.comfy.org.", "credits")
    if etype == "concurrency_limit_exceeded" or (s == 409 and "concurren" in msg.lower()):
        return RunError(f"Comfy Router: too many runs at once. {msg[:200]}", "busy")
    if s == 429:
        return RunError("Comfy Router asked us to slow down. Trying again shortly.", "rateLimit")
    if etype == "content_policy_violation" or re.search(r"policy|safety|moderat|nsfw", msg, re.I):
        return RunError(f"The model blocked the prompt or a reference under its content policy. {msg[:200]}", "policy")
    if s in (400, 422) or etype == "invalid_input":
        return RunError(f"The model rejected these inputs: {msg[:300]}", "validation")
    if s == 403 or etype == "not_enabled":
        return RunError(f"This model isn't enabled for the Comfy workspace: {msg[:200]}", "unavailable")
    if s == 404:
        return RunError("Comfy Router doesn't know this model or request.", "unavailable")
    if s in (502, 503, 504):
        # "temporarily unavailable, retry with backoff": back in line, not failed
        return RunError("Comfy Router is briefly unavailable; the take goes again shortly.", "rateLimit")
    if s == 410:
        return RunError("Comfy Router no longer has this result (kept 24 hours).", "response")
    return RunError(f"The run failed at Comfy Router ({s}): {msg[:240]}", "provider")


def claude(body: Dict, model: str = "claude-opus-5-5", tries: int = 3) -> Dict:
    """One Anthropic Messages call through Router, synchronously (Claude
    answers in seconds, so it skips the queue): the native message comes back."""
    from .models import RunError
    url = f"{BASE}/v2/models/anthropic/{model}"
    for i in range(tries):
        try:
            r = requests.post(url, headers=_headers(), json=body, timeout=300)
        except requests.RequestException as e:
            if i == tries - 1:
                raise RunError(f"Couldn't reach Comfy Router for Claude: {e}", "network")
            time.sleep(2 * (i + 1))
            continue
        if r.status_code in (429, 502, 503, 504) and i < tries - 1:
            time.sleep(float(r.headers.get("Retry-After") or 2 * (i + 1)))
            continue
        if r.status_code >= 300:
            raise _error(r)
        return r.json()
    raise RunError("Claude via Comfy Router stayed busy.", "busy")


def check_key(key: str) -> str:
    """"" when Comfy accepts the key, else what to tell the person who typed it."""
    key = (key or "").strip()
    if not key or len(key) > 500 or any(c.isspace() for c in key):
        return "Paste your Comfy API key."
    try:
        r = requests.get(f"{BASE}/customers/balance", headers={"X-API-Key": key}, timeout=15)
    except requests.RequestException:
        return "Comfy didn't answer. Try again in a moment."
    if r.status_code in (401, 403):
        return "Comfy didn't accept that key. Check it at platform.comfy.org."
    return "" if r.status_code == 200 else f"Comfy answered {r.status_code}. Try again in a moment."


def balance() -> Optional[float]:
    """Dollars left on the Comfy account, or None when Comfy can't be asked."""
    if not api_key():
        return None
    try:
        r = requests.get(f"{BASE}/customers/balance", headers=_headers(), timeout=10)
        if r.status_code != 200:
            return None
        j = r.json()
        return round(float(j.get("effective_balance_cents", j.get("amount_cents"))) / 100, 2)
    except Exception:
        return None


# ── a stuck queue ────────────────────────────────────────────────────────────
# Router's queue for an account can stop moving (seen 2026-10-24: three runs
# hung at their providers and everything after them sat at the same place in
# line for hours) while the synchronous route still runs at once. So a take
# whose place in line doesn't move for STALL seconds is taken out (waiting is
# never billed) and sent synchronously, and for SYNC_FOR seconds after that
# every new take goes synchronously too. STUDIO_COMFY_SYNC=1 always does.
STALL = float(os.getenv("STUDIO_COMFY_STALL", "240"))
SYNC_FOR = 1800.0
_line: Dict[str, Tuple[Any, float]] = {}
_sync_until = [0.0]


def go_sync() -> bool:
    return os.getenv("STUDIO_COMFY_SYNC", "") == "1" or time.time() < _sync_until[0]


def _stalled(poll_url: str, position) -> bool:
    seen = _line.get(poll_url)
    now = time.time()
    if not seen or seen[0] != position:
        _line[poll_url] = (position, now)
        return False
    return now - seen[1] > STALL


class _KeepAlive(requests.adapters.HTTPAdapter):
    """A direct run sends nothing back until the clip is done, and a connection
    silent for minutes can be dropped on the way without either end hearing:
    two six-minute MiniMax clips (2026-10-25) finished and were billed, and the
    answer never arrived. TCP keepalive probes every 30 s keep it open."""

    def init_poolmanager(self, *a, **kw):
        from urllib3.connection import HTTPConnection
        opts = list(HTTPConnection.default_socket_options) + [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
        idle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)   # Linux / macOS
        for name, value in ((idle, 30), (getattr(socket, "TCP_KEEPINTVL", None), 15), (getattr(socket, "TCP_KEEPCNT", None), 8)):
            if name:
                opts.append((socket.IPPROTO_TCP, name, value))
        kw["socket_options"] = opts
        super().init_poolmanager(*a, **kw)


_direct = requests.Session()
_direct.mount("https://", _KeepAlive())


def run_sync(slug: str, body: Dict, kind: str = "", key: Optional[str] = None) -> Dict:
    """Run a model and wait for the result on one connection (no queue)."""
    from .models import RunError
    url = f"{BASE}/v2/models/{slug}"
    try:
        r = _direct.post(url, headers=_headers({"Idempotency-Key": key or uuid.uuid4().hex}),
                         json=body, timeout=(15, 1200))
    except requests.Timeout:
        raise RunError("Comfy Router never sent the result back. It may still have finished and charged the run "
                       "(Comfy's direct route can lose a long clip); check your usage at platform.comfy.org.", "network")
    except requests.RequestException as e:
        raise RunError(f"Comfy Router didn't finish the run: {e}", "network")
    if r.status_code >= 300:
        raise _error(r)
    credits = r.headers.get("X-Comfy-Credits-Used")
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].lower()
    if ctype.startswith(("image/", "video/")):
        return {"data": r.content, "ext": mimetypes.guess_extension(ctype) or ".bin", "credits": credits,
                "cost": dollars(credits)}
    found = extract(r.json(), kind)
    if not found:
        raise RunError(f"Comfy Router finished but returned no file: {r.text[:200]}", "response")
    return {**found, "credits": credits, "cost": dollars(credits)}


def submit(slug: str, body: Dict, key: Optional[str] = None, kind: str = "", sync: bool = False) -> Dict:
    from .models import RunError
    if not api_key():
        raise RunError("Sign in again with your Comfy key to run this." if settings.VISITOR_KEYS
                       else "Add COMFY_API_KEY to run models on Comfy Router.", "unavailable")
    if body.get("_route"):
        return _proxy_submit(dict(body))
    if sync or go_sync():
        return run_sync(slug, body, kind, key)
    url = f"{BASE}/v2/models/{slug}/requests"
    try:
        r = requests.post(url, headers=_headers({"Idempotency-Key": key or uuid.uuid4().hex}),
                          json=body, timeout=90)
    except requests.RequestException as e:
        raise RunError(f"Couldn't reach Comfy Router: {e}", "network")
    if r.status_code >= 300:
        raise _error(r)
    j = r.json()
    status = j.get("status_url") or f"{BASE}/v2/models/{slug}/requests/{j['request_id']}/status"
    return {"request_id": j.get("request_id"), "poll_url": status}


def _proxy_submit(body: Dict) -> Dict:
    from .models import RunError
    route = body.pop("_route")
    try:
        r = requests.post(BASE + route, headers=_headers(), json=body, timeout=90)
    except requests.RequestException as e:
        raise RunError(f"Couldn't reach Comfy's LTX service: {e}", "network")
    if r.status_code >= 300:
        raise _error(r)
    jid = r.json().get("id")
    if not jid:
        raise RunError(f"Comfy's LTX service gave no job id: {r.text[:200]}", "response")
    return {"request_id": jid, "poll_url": f"{BASE}{route}/{jid}"}


def _proxy_poll(poll_url: str) -> Dict:
    from .models import RunError
    try:
        r = requests.get(poll_url, headers=_headers(), timeout=30)
    except requests.RequestException as e:
        return {"pending": True, "progress": f"network: {e}"[:80]}
    if r.status_code == 429 or r.status_code >= 500:
        return {"pending": True}
    if r.status_code >= 300:
        raise _error(r)
    j = r.json()
    st = str(j.get("status") or "").lower()
    if st in LTX_DONE:
        url = ((j.get("result") or {}).get("video_url"))
        if not url:
            raise RunError("LTX finished without a video.", "response")
        credits = r.headers.get("X-Comfy-Credits-Used")
        return {"url": url, "credits": credits, "cost": dollars(credits)}
    if st in LTX_FAILED:
        raise RunError(str(j.get("error") or j.get("message") or f"LTX {st}")[:300], "provider")
    return {"pending": True, "progress": st or None}


def poll(poll_url: str, kind: str = "") -> Dict:
    from .models import RunError
    if "/proxy/" in poll_url:
        return _proxy_poll(poll_url)
    try:
        r = requests.get(poll_url, headers=_headers(), timeout=30)
    except requests.RequestException as e:
        return {"pending": True, "progress": f"network: {e}"[:80]}
    if r.status_code in (429,) or r.status_code >= 500:
        return {"pending": True}
    if r.status_code >= 300:
        raise _error(r)
    j = r.json()
    st = str(j.get("status") or "").upper()
    if st == "IN_QUEUE" and _stalled(poll_url, j.get("queue_position")):
        cancel(poll_url)
        _line.pop(poll_url, None)
        _sync_until[0] = time.time() + SYNC_FOR
        raise RunError(f"Comfy Router's queue hasn't moved in {STALL / 60:.0f} minutes; running it directly instead.",
                       "stalled")
    if st in ("IN_QUEUE", "IN_PROGRESS", ""):
        pos = j.get("queue_position")
        return {"pending": True, "progress": (f"in queue ({pos} ahead)" if st == "IN_QUEUE" and pos
                                              else st.lower().replace("_", " ") or None)}
    if j.get("error_type"):
        et = j["error_type"]
        msg = j.get("error") or j.get("message") or et
        reason = {"content_policy_violation": "policy", "cancelled": "cancelled",
                  "queue_timeout": "busy"}.get(et, "provider")
        if et == "queue_timeout":
            msg = "Comfy Router's queue timed out before the model ran; it goes back in line"
        raise RunError(f"{msg}"[:400], reason)
    res_url = j.get("response_url") or poll_url.rsplit("/status", 1)[0]
    r2 = requests.get(res_url, headers=_headers(), timeout=120)
    if r2.status_code == 202:
        return {"pending": True}
    if r2.status_code >= 300:
        raise _error(r2)
    credits = r2.headers.get("X-Comfy-Credits-Used")
    ctype = (r2.headers.get("Content-Type") or "").split(";")[0].lower()
    if ctype.startswith(("image/", "video/")):
        return {"data": r2.content, "ext": mimetypes.guess_extension(ctype) or ".bin", "credits": credits,
                "cost": dollars(credits)}
    try:
        body = r2.json()
    except ValueError:
        raise RunError("Comfy Router finished but the result wasn't readable.", "response")
    found = extract(body, kind)
    if not found:
        raise RunError(f"Comfy Router finished but returned no file: {json.dumps(body)[:200]}", "response")
    found["credits"] = credits
    found["cost"] = dollars(credits)
    return found


CREDITS_PER_USD = 211                 # docs.comfy.org/development/comfy-router/pricing


def dollars(credits) -> Optional[float]:
    try:
        return round(float(credits) / CREDITS_PER_USD, 4) if credits not in (None, "") else None
    except (TypeError, ValueError):
        return None


def cancel(poll_url: str) -> str:
    """Ask Router to drop a request: "cancelled", "finished" (too late), or "unknown"."""
    if "/proxy/" in poll_url:
        return "unknown"                          # the LTX service has no cancel
    url = poll_url.rsplit("/status", 1)[0] + "/cancel"
    try:
        r = requests.put(url, headers=_headers(), timeout=20)
    except requests.RequestException:
        return "unknown"
    if r.status_code == 409:
        return "finished"
    if r.status_code >= 300:
        return "unknown"
    try:
        st = requests.get(poll_url, headers=_headers(), timeout=20).json()
        return "cancelled" if st.get("error_type") == "cancelled" else "unknown"
    except Exception:
        return "unknown"


MEDIA_EXT = {"image": (".png", ".jpg", ".jpeg", ".webp", ".gif"), "video": (".mp4", ".mov", ".webm", ".m4v")}
B64_KEYS = ("b64_json", "base64", "bytesBase64Encoded", "data", "image_base64", "video_base64")


def extract(body: Any, kind: str = "") -> Optional[Dict]:
    """The picture or clip in a model's native result: {"url"} or {"data", "ext"}."""
    urls: List[Tuple[int, str]] = []
    blobs: List[Tuple[int, bytes, str]] = []
    want = "video" if kind in ("video", "vupscale") else "image"

    def score_url(key: str, u: str) -> int:
        path = u.split("?")[0].lower()
        sc = 0
        if path.endswith(MEDIA_EXT[want]):
            sc += 10
        elif path.endswith(MEDIA_EXT["image" if want == "video" else "video"]):
            sc -= 5                                  # a poster frame, not the clip
        k = key.lower()
        if want in k:
            sc += 4
        if k in ("url", "uri", "sample", "output", "video_url", "image_url", "file_url"):
            sc += 2
        if "thumb" in k or "poster" in k or "cover" in k or "last_frame" in k:
            sc -= 6
        return sc

    def walk(x, key="", mime=""):
        if isinstance(x, dict):
            m = x.get("mimeType") or x.get("mime_type") or x.get("content_type") or mime
            for k, v in x.items():
                walk(v, k, m)
        elif isinstance(x, list):
            for v in x:
                walk(v, key, mime)
        elif isinstance(x, str):
            if x.startswith(("http://", "https://")):
                urls.append((score_url(key, x), x))
            elif x.startswith("data:") and ";base64," in x[:80]:
                head, data = x.split(",", 1)
                blobs.append((5, base64.b64decode(data), head[5:].split(";")[0]))
            elif key in B64_KEYS and len(x) > 2000:
                try:
                    blobs.append((4, base64.b64decode(x), mime))
                except Exception:
                    pass

    walk(body)
    if blobs:
        sc, data, mime = max(blobs, key=lambda b: b[0])
        ext = mimetypes.guess_extension(mime or "") or (".mp4" if want == "video" else ".png")
        return {"data": data, "ext": ".jpg" if ext in (".jpe", ".jpeg") else ext}
    if urls:
        sc, u = max(urls, key=lambda t: t[0])
        if sc > -3:
            return {"url": u}
    return None
