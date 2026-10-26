# =========================================================
# studio_models.py – one shot, any model on Comfy Router or Segmind.
#
# The studio speaks ONE request (prompt, negative, seed, aspect, resolution,
# duration, audio, references, first/last frame, source video). Every model
# names those differently — aspect_ratio / ratio / size "1536x1024" /
# width+height / resolution "2K" / output_resolution "4K" … — so each model's
# published input schema is read once into ROLES (which field is the prompt,
# which takes the starting frame, which sets the size) and the request is
# mapped onto them, snapping to the nearest option the model offers.
# This is Comfy's canonical-parameter layer, applied to Segmind's schemas.
#
# Two engines, one request. Segmind's models run on its v2 queue (POST
# /v2/<slug> → request_id, poll, fetch); Comfy Router's (comfy.py: ids with a
# slash, "bfl/flux-3-image") on Router's queue, where each model takes its
# provider's own body, so the flat payload built here is shaped into it last.
# Either way nothing holds a worker for the minutes a clip takes.
# =========================================================
import base64, hashlib, io, json, math, mimetypes, os, re, time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import requests

from . import catalog as C
from . import comfy, settings

SEG_BASE = "https://api.segmind.com/v1"
SEG_V2 = "https://api.segmind.com/v2"
SEG_UPLOAD = "https://workflows-api.segmind.com/upload-asset"


class RunError(Exception):
    """A take failed for a reason the page can name (reason) and explain."""
    def __init__(self, msg: str, reason: str = "provider"):
        super().__init__(msg)
        self.reason = reason


# ── field roles ─────────────────────────────────────────────────────────────
PROMPT = ["prompt", "positivePrompt", "positive_prompt", "promptText", "text_prompt", "prompt_text", "text",
          "input_text", "instruction", "edit_prompt", "caption"]
NEGATIVE = ["negative_prompt", "negativePrompt", "neg_prompt", "negative"]
ASPECT = ["aspect_ratio", "aspectRatio", "ratio", "aspect"]
SIZEISH = ["size", "image_size", "width_and_height", "output_size", "dimensions", "resolution",
           "output_resolution", "target_resolution", "img_size", "quality", "video_quality"]
DURATION = ["duration", "duration_seconds", "video_length", "seconds", "length", "num_seconds",
            "video_duration"]
AUDIO = ["generate_audio", "generate_audio_switch", "audio", "with_audio", "sound", "enable_audio",
         "audio_generation", "generate_sound"]
REF_MULTI = ["image_urls", "images", "image_input", "reference_images", "ref_images", "input_images",
             "reference_image_urls", "style_reference_urls", "image_list"]
REF_SINGLE = ["image", "image_url", "input_image", "image_1", "image_2", "image_3", "image2", "image3",
              "input_image_1", "input_image_2", "image_file", "base_image", "source_image", "source_img",
              "subject_image", "product_image", "image_prompt", "ip_image", "redux_image", "style_image",
              "reference_image", "first_reference_image", "second_reference_image", "init_image", "img",
              "input_img"]
FIRST = ["first_frame_url", "first_frame", "first_frame_image", "start_image_url", "start_image",
         "start_frame", "image", "image_url", "input_image", "promptImage", "source_image", "init_image",
         "input_reference", "image_file", "img"]
LAST = ["last_frame", "last_frame_url", "last_frame_image", "last_frame_uri", "end_image_url",
        "end_image", "end_frame", "image_tail", "tail_image_url", "last_image"]
SOURCE_VIDEO = ["video", "video_url", "input_video", "video_uri", "start_video", "source_video",
                "video_file"]
SCALE = ["scale_factor", "upscale_factor", "scale", "upscale", "upscale_by"]

KNOWN_ASPECTS = ["21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16", "9:21"]
_WH = re.compile(r"(\d{3,5})\s*[x×*]\s*(\d{3,5})")
_RATIO = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[:x]\s*(\d+(?:\.\d+)?)\s*$")
IMG_TIER = {"512": 512, "768sq": 768, "1k": 1024, "1.5k": 1536, "2k": 2048, "3k": 3072, "4k": 4096}
VID_TIER = {"360p": 360, "480p": 480, "540p": 540, "576p": 576, "720p": 720, "768p": 768, "1080p": 1080,
            "hd": 720, "fhd": 1080, "2k": 1440, "4k": 2160}


def _ratio(v) -> Optional[float]:
    m = _RATIO.match(str(v))
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return a / b if a and b else None


def _wh(v) -> Optional[Tuple[int, int]]:
    m = _WH.search(str(v))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _aspect_label(r: float) -> Optional[str]:
    """A ratio as the nearest familiar aspect, if within 3 % — so a 1344x768
    size option is offered as 16:9, not as 7:4."""
    best = min(KNOWN_ASPECTS, key=lambda a: abs(math.log(_ratio(a) / r)))
    return best if abs(math.log(_ratio(best) / r)) < 0.03 else None


def _norm_aspect(v) -> Optional[str]:
    r = _ratio(v)
    if not r:
        return None
    a = str(v).replace("x", ":").replace(" ", "")
    return a if a in KNOWN_ASPECTS else (_aspect_label(r) or a)


def _num(v) -> Optional[float]:
    try:
        return float(str(v).lower().rstrip("s"))
    except Exception:
        return None


def _tier_px(v, video: bool) -> Optional[int]:
    k = str(v).strip().lower()
    table = VID_TIER if video else IMG_TIER
    if k in table:
        return table[k]
    if not video and k in VID_TIER:
        return None
    return None


def _first(inputs: Dict, names: List[str], pred=None) -> Optional[str]:
    for n in names:
        if n in inputs and (pred is None or pred(inputs[n])):
            return n
    return None


def _is_media(kind):
    return lambda p: p.get("md") in (kind, "file")


def _is_str(p):
    return p.get("t") in ("string", None) and not p.get("md")


# ── reading one model ───────────────────────────────────────────────────────
def analyse(rec: Dict) -> Optional[Dict]:
    """Roles and capabilities of one catalogue record, or None when the studio
    can't drive it (needs a mask, a pose, an audio track…)."""
    cat = rec.get("category")
    if rec.get("slug") in POST_TOOLS:
        cat = "upscale"                      # a promptless still-in, still-out finishing tool
    kind = {"text-to-image": "image", "image-edit": "image", "upscale": "upscale",
            "text-to-video": "video", "image-to-video": "video", "video-edit": "video",
            "video-extend": "video", "video-upscale-enhance": "vupscale"}.get(cat)
    if not kind or not rec.get("listed", True) or rec.get("soon"):
        return None
    inp = rec.get("inputs") or {}
    video = kind in ("video", "vupscale")
    R: Dict = {"kind": kind, "slug": rec["slug"]}
    R["prompt"] = _first(inp, PROMPT, _is_str)
    R["negative"] = _first(inp, NEGATIVE, _is_str)
    R["seed"] = "seed" if "seed" in inp and inp["seed"].get("t") in ("integer", "number", None) else None

    used = {R["prompt"], R["negative"], R["seed"]}
    # media
    if kind in ("image", "upscale"):
        R["refs_multi"] = _first(inp, REF_MULTI, lambda p: p.get("mul") and not p.get("obj")
                                 and p.get("md") in ("image", "file", None))
        singles = [n for n in REF_SINGLE if n in inp and not inp[n].get("mul") and _is_media("image")(inp[n])]
        R["refs_single"] = [] if R["refs_multi"] else singles
        R["refs_max"] = (inp[R["refs_multi"]].get("mi") or 10) if R["refs_multi"] else len(R["refs_single"])
        need = [n for n in ([R["refs_multi"]] + R["refs_single"]) if n and inp[n].get("req")]
        R["refs_min"] = max([1 if R["refs_multi"] in need else 0] +
                            [R["refs_single"].index(n) + 1 for n in need if n in R["refs_single"]])
        used |= {R["refs_multi"], *R["refs_single"]}
    else:
        R["first"] = _first(inp, FIRST, lambda p: not p.get("mul") and _is_media("image")(p))
        R["last"] = _first(inp, LAST, lambda p: not p.get("mul") and _is_media("image")(p))
        R["source"] = _first(inp, SOURCE_VIDEO, lambda p: not p.get("mul") and p.get("md") in ("video", "file"))
        R["refs_multi"] = _first(inp, ["reference_images", "image_urls", "images", "ref_images"],
                                 lambda p: p.get("mul") and not p.get("obj") and p.get("md") in ("image", "file", None))
        if not R["first"] and "keyframes" in inp and inp["keyframes"].get("mul") and not inp["keyframes"].get("obj"):
            R["first"] = "keyframes"            # FLUX 3 i2v: a list whose first item starts the clip
        R["refs_single"] = []
        R["refs_max"] = (inp[R["refs_multi"]].get("mi") or 4) if R["refs_multi"] else 0
        R["refs_min"] = 1 if R["refs_multi"] and inp[R["refs_multi"]].get("req") else 0
        if R["first"] == R["source"]:
            R["first"] = None
        used |= {R["first"], R["last"], R["source"], R["refs_multi"]}
        # an i2v model whose frame field is also its only "image" — a video
        # model with `image` required starts from a still
        R["requires_frame"] = bool(R["first"] and inp[R["first"]].get("req"))
        R["requires_source"] = bool(R["source"] and inp[R["source"]].get("req"))
        if kind == "video" and R["requires_source"] and not R["prompt"]:
            pass

    # anything else required that we can't supply → the studio can't run it
    for n, p in inp.items():
        if n in used or not p.get("req"):
            continue
        if p.get("md") or p.get("obj") or (p.get("mul") and p.get("md")):
            return None
        if p.get("d") is None and not p.get("o") and n not in DURATION + ASPECT + SIZEISH + SCALE:
            if p.get("t") in ("string",) and n not in PROMPT:
                return None

    if kind in ("image", "video") and not R["prompt"]:
        # an edit/extend with no prompt (bg removal, interpolation…) isn't a shot
        if not (kind == "video" and R.get("source")):
            return None

    # aspect
    a = _first(inp, ASPECT, lambda p: bool(p.get("o")))
    R["aspect"] = a
    aspects = []
    if a:
        aspects = [x for x in (_norm_aspect(o) for o in inp[a]["o"]) if x]
    # size-ish
    R["wh_field"] = R["tier_field"] = None
    tiers, whs = [], []
    for n in SIZEISH:
        p = inp.get(n)
        if not p or not p.get("o") or n in used:
            continue
        opts = p["o"]
        parsed = [(o, _wh(o)) for o in opts if _wh(o)]
        if parsed and not R["wh_field"] and len(parsed) >= max(1, len(opts) // 2):
            R["wh_field"] = n
            whs = parsed
            continue
        tv = [o for o in opts if _tier_px(o, video)]
        if tv and not R["tier_field"]:
            R["tier_field"] = n
            tiers = tv
    if R["wh_field"] and not aspects:
        aspects = sorted({lab for _, (w, h) in whs for lab in [_aspect_label(w / h)] if lab},
                         key=lambda x: -_ratio(x))
    R["w"] = "width" if inp.get("width", {}).get("t") in ("integer", "number") else None
    R["h"] = "height" if inp.get("height", {}).get("t") in ("integer", "number") else None
    if R["w"] and R["h"] and not aspects and kind == "image":
        aspects = list(KNOWN_ASPECTS[:-1])
    R["aspects"] = list(dict.fromkeys(aspects))
    R["tiers"] = tiers
    R["whs"] = whs
    if tiers:
        R["resolutions"] = tiers
    elif whs and kind == "image":
        buckets = sorted({_bucket(max(w, h)) for _, (w, h) in whs}, key=lambda b: IMG_TIER[b.lower()])
        R["resolutions"] = buckets
    elif R["w"] and kind == "image":
        mx = inp["width"].get("mx") or 2048
        R["resolutions"] = ["1K"] + (["2K"] if mx >= 2048 else [])
    else:
        R["resolutions"] = []
    R["default_resolution"] = inp[R["tier_field"]].get("d") if R["tier_field"] else None

    # duration
    d = _first(inp, DURATION, lambda p: p.get("t") in ("integer", "number", "string", None))
    R["duration"] = d
    durs = []
    if d:
        p = inp[d]
        if p.get("o"):
            durs = sorted({int(v) for v in (_num(o) for o in p["o"]) if v and v > 0})
        elif p.get("mn") is not None and p.get("mx") is not None:
            lo, hi = max(1, int(p["mn"])), int(p["mx"])
            if hi - lo <= 60:
                durs = list(range(lo, hi + 1))
    R["durations"] = durs
    R["audio"] = _first(inp, AUDIO, lambda p: p.get("t") == "boolean")
    R["scale"] = _first(inp, SCALE)
    R["codec"] = "output_codec" if "h264" in [str(o).lower() for o in inp.get("output_codec", {}).get("o", [])] else None
    R["watermark"] = [n for n, p in inp.items() if "watermark" in n.lower() and p.get("t") == "boolean"]
    R["base64"] = "base64" if inp.get("base64", {}).get("t") == "boolean" else None
    R["required_defaults"] = {n: p["d"] for n, p in inp.items()
                              if p.get("req") and p.get("d") is not None and n not in used}
    return R


def _bucket(px: int) -> str:
    return "1K" if px <= 1200 else "1.5K" if px <= 1700 else "2K" if px <= 2600 else "4K"


# ── the curated front of the list ───────────────────────────────────────────
# Featured models come first, in this order and under these groups; a
# "family" is one entry that switches slug when a reference or a starting
# frame is attached (Comfy's generate/edit and generate/animate pairs).
FEATURED = {
    "image": [
        ("Flagship", [
            ("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst", None),
            ("nano-banana-2.1", "Nano Banana 2.1", None),
            ("seedream-5-pro", "Seedream 5.0 Pro", None),
            ("flux-3", "FLUX 3", {"t": "flux-3-text-to-image", "edit": "flux-3-image-edit"}),
            ("nano-banana-pro", "Nano Banana Pro", None),
            ("gpt-image-2.5-flare", "GPT Image 2.5 Flare", None),
            ("flux-2-max", "FLUX 2 Max", None),
            ("grok-imagine-image-2", "Grok Imagine Image 2", None),
            ("wan2.7-image-pro", "Wan 2.7 Image Pro", None),
            ("kling-3-image", "Kling 3.0", {"t": "kling-3-text2image", "edit": "kling-3-image2image"}),
        ]),
        ("Typography & design", [
            ("ideogram-4.5", "Ideogram 4.5", {"t": "ideogram-4.5", "edit": "ideogram-4.5-edit"}),
            ("recraft-v4-pro", "Recraft V4 Pro", None),
            ("recraft-v4", "Recraft V4", None),
            ("qwen-image-2.1-pro", "Qwen Image 2.1 Pro", None),
            ("p-image-ideogram", "P Image (Ideogram)", None),
        ]),
        ("Fast & cheap", [
            ("flux-3-klein", "FLUX 3 Klein", {"t": "flux-3-klein-text-to-image", "edit": "flux-3-klein-image-edit"}),
            ("qwen-image-2.1-turbo", "Qwen Image 2.1 Turbo", None),
            ("seedream-v5-lite", "Seedream 5.0 Lite", {"t": "seedream-v5-lite-text-to-image", "edit": "seedream-v5-lite-image-to-image"}),
            ("z-image-turbo", "Z Image Turbo", None),
            ("p-image", "P Image", None),
            ("hidream-l1-fast", "HiDream I1 Fast", None),
        ]),
        ("Edit & reference", [
            ("flux-kontext-max", "FLUX Kontext Max", None),
            ("gpt-image-1.5", "GPT Image 1.5", {"t": "gpt-image-1.5", "edit": "gpt-image-1.5-edit"}),
            ("seedream-4.5", "Seedream 4.5", None),
            ("qwen-image-edit-plus", "Qwen Image Edit Plus", None),
            ("p-image-edit", "P Image Edit", None),
            ("bria-expand-image", "Bria Expand (outpaint)", None),
        ]),
    ],
    "video": [
        ("Flagship", [
            ("veo-3.1", "Veo 3.1", None),
            ("kling-3-pro", "Kling 3.0 Pro", {"t": "kling-3-pro-text2video", "i2v": "kling-3-pro-image2video"}),
            ("seedance-2.5", "Seedance 2.5", None),
            ("sora-2-pro", "Sora 2 Pro", None),
            ("wan3.0-video", "Wan 3.0", None),
            ("flux-3-video", "FLUX 3 Video", {"t": "flux-3-text-to-video", "i2v": "flux-3-image-to-video"}),
            ("gemini-omni-1.1", "Gemini Omni 1.1", None),
            ("kling-o3", "Kling O3", {"t": "kling-o3-text2video", "i2v": "kling-o3-image2video"}),
            ("grok-imagine-video-1.5", "Grok Imagine Video 1.5", {"t": "grok-imagine-video-1.5-text-to-video", "i2v": "grok-imagine-video-1.5-image-to-video"}),
            ("luma-ray-3-2", "Luma Ray 3.2", None),
        ]),
        ("Fast & cheap", [
            ("veo-3.1-fast", "Veo 3.1 Fast", None),
            ("seedance-2.0-fast", "Seedance 2.0 Fast", None),
            ("seedance-2.0-mini", "Seedance 2.0 Mini", None),
            ("minimax-h3", "MiniMax H3", {"t": "minimax-h3-text-to-video", "i2v": "minimax-h3-image-to-video"}),
            ("minimax-h3-max-turbo", "MiniMax H3 Max Turbo", None),
            ("ltx-2.5-fast", "LTX 2.5 Fast", None),
            ("pixverse-v6", "PixVerse V6", None),
            ("p-video-2-pro", "P Video 2 Pro", None),
            ("wan3.0-video-prime", "Wan 3.0 Prime", None),
        ]),
        ("Cinema control", [
            ("ltx-2.5-pro", "LTX 2.5 Pro", None),
            ("sora-2", "Sora 2", None),
            ("kling-3-standard", "Kling 3.0 Standard", {"t": "kling-3-standard-text2video", "i2v": "kling-3-standard-image2video"}),
            ("hailuo-2.3", "Hailuo 2.3", None),
            ("runway-gen4-turbo", "Runway Gen-4 Turbo", None),
            ("happyhorse-1.1", "HappyHorse 1.1", None),
            ("pixverse-5-transition", "PixVerse 5 Transition", None),
            ("wan2.7", "Wan 2.7", {"t": "wan2.7-t2v", "i2v": "wan2.7-i2v"}),
        ]),
        ("Edit & extend a clip", [
            ("pixverse-v6-extend", "PixVerse V6 Extend", None),
            ("flux-3-extend-video", "FLUX 3 Extend", None),
            ("gemini-omni-1.1-video-extend", "Gemini Omni Extend", None),
            ("runway-gen4-aleph", "Runway Aleph", None),
            ("kling-o3-video2video-edit", "Kling O3 Video Edit", None),
            ("wan2.7-videoedit", "Wan 2.7 Video Edit", None),
            ("flux-3-video-edit", "FLUX 3 Video Edit", None),
            ("modify-video", "Luma Modify", None),
        ]),
    ],
    "upscale": [("Upscale", [
        ("clarityai-crystal-upscaler", "Crystal Upscaler", None),
        ("topaz-image-upscale", "Topaz Image Upscale", None),
        ("clarity-upscaler", "Clarity Upscaler", None),
        ("bria-increase-resolution", "Bria Increase Resolution", None),
        ("bria-remove-background", "Bria Remove Background", None),
    ])],
    "vupscale": [("Upscale", [
        ("topaz-video-upscale", "Topaz Video Upscale", None),
        ("kandinsky-6-vsr", "Kandinsky 6 VSR", None),
        ("bria-increase-video-resolution", "Bria Video Resolution", None),
    ])],
}
# promptless finishing tools, run on a finished still like an upscaler
POST_TOOLS = {"bria-remove-background": "cutout", "bg-removal-v2": "cutout"}
CUTOUT = "bria-remove-background"
DEFAULT_UPSCALER = "clarityai-crystal-upscaler"
DEFAULT_VIDEO_UPSCALER = "topaz-video-upscale"
TEXT_MODELS = ("ideogram", "recraft", "gpt-image", "qwen-image", "p-image-ideogram", "nano-banana")

PROVIDERS = {"BYTEDANCE": "ByteDance", "GOOGLE": "Google", "OPENAI": "OpenAI", "BLACK_FOREST_LABS": "Black Forest Labs",
             "BFL": "Black Forest Labs", "KUAIVISION": "Kling", "KLING": "Kling", "ALIBABA": "Alibaba", "XAI": "xAI",
             "MINIMAX": "MiniMax", "SEGMIND": "Segmind", "RECRAFT": "Recraft", "IDEOGRAM": "Ideogram",
             "PRUNA": "Pruna", "LUMA": "Luma", "RUNWAY": "Runway", "LIGHTRICKS": "Lightricks", "PIXVERSE": "PixVerse",
             "BRIA": "Bria", "TOPAZ": "Topaz", "STABILITY": "Stability", "TENCENT": "Tencent", "QWEN": "Qwen",
             "STABILITY_AI": "Stability AI", "LTX_STUDIO": "Lightricks", "RUNWAY_ML": "Runway", "CLARITYAI": "Clarity AI",
             "TOPAZ_LABS": "Topaz Labs", "XAI_(GROK)": "xAI", "ZHIPU_AI": "Zhipu AI", "HIDREAM": "HiDream",
             "HEYGEN": "HeyGen", "VEED": "VEED", "HIGGSFIELD": "Higgsfield", "META": "Meta", "SBER": "Sber"}


def _provider(p: str) -> str:
    p = (p or "").strip()
    return PROVIDERS.get(p.upper().replace(" ", "_"), p.title() if p.isupper() else p) or "Other"


_registry: Dict = {"t": None, "models": {}, "order": [], "recs": {}}


# ── your LoRAs ──────────────────────────────────────────────────────────────
# Style fine-tunes trained in the LoRA Manager (/api/lora) are Segmind
# endpoints of their own, listed in its registry. They are FLUX underneath and
# take FLUX's inputs; the trigger word has to lead the prompt or the style
# doesn't show, and without an explicit step count they under-diffuse (blurry).
LORA_REGISTRY = os.path.join(os.path.abspath(os.getenv("STUDIO_DIR") or "data"), "loras", "registry.json")
LORA_ASPECTS = ["1:1", "2:3", "3:4", "4:5", "9:16", "3:2", "4:3", "16:9", "21:9"]
LORA_DEFAULTS = {"num_inference_steps": 28, "steps": 28, "guidance_scale": 3.5, "scheduler": "euler"}


def _lora_mtime() -> float:
    try:
        return os.path.getmtime(LORA_REGISTRY)
    except OSError:
        return 0.0


def lora_records() -> List[Dict]:
    """The registry's ready LoRAs as catalogue records, so they are picked,
    priced and built like any other model."""
    try:
        with open(LORA_REGISTRY) as f:
            reg = json.load(f)
    except Exception:
        return []
    out = []
    for e in reg if isinstance(reg, list) else []:
        if e.get("status") != "AVAILABLE" or not e.get("endpoint_slug"):
            continue
        out.append({"slug": e["endpoint_slug"], "name": e.get("name") or "LoRA", "provider": "lora",
                    "category": "text-to-image", "listed": True, "cost": None, "latency": None,
                    "desc": f"Your FLUX style LoRA · trigger word {e.get('trigger_word') or '—'}",
                    "inputs": {"prompt": {"t": "string", "req": True},
                               "aspect_ratio": {"t": "string", "o": LORA_ASPECTS, "d": "1:1"},
                               "seed": {"t": "integer"}},
                    "lora": {"id": e.get("id"), "trigger": e.get("trigger_word") or ""}})
    return out


def engines() -> Tuple[bool, bool]:
    """(Comfy Router, Segmind): which catalogues the studio offers. Comfy
    whenever its key is set; Segmind when its key is, or when no key is set at
    all (the catalogue still shows). STUDIO_ENGINES overrides both."""
    if settings.VISITOR_KEYS:                    # the demo spends only visitors' Comfy keys
        return True, False
    want = settings.ENGINES
    if want:
        return "comfy" in want, "segmind" in want
    return bool(settings.COMFY_API_KEY), bool(settings.SEGMIND_API_KEY or not settings.COMFY_API_KEY)


def _registry_build() -> Dict:
    use_comfy, use_seg = engines()
    cat = C.load() if use_seg else []
    crecs = comfy.records() if use_comfy else []
    if (_registry["t"] is cat and _registry.get("cr") is crecs and _registry.get("lm") == _lora_mtime()
            and _registry.get("eng") == (use_comfy, use_seg)):
        return _registry
    loras = lora_records() if use_seg else []
    recs = {m["slug"]: m for m in cat + loras + crecs}
    roles = {}
    for slug, rec in recs.items():
        try:
            r = analyse(rec)
        except Exception as e:
            print(f"[studio] could not read {slug}: {e}")
            r = None
        if r:
            roles[slug] = r
    models, order, claimed = {}, [], set()

    def entry(mid, label, group, variants, featured):
        base_slug = variants.get("t") if variants else mid
        R = roles.get(base_slug)
        if not R:
            return None
        rec = recs[base_slug]
        m = {"id": mid, "label": label or rec.get("name") or mid, "kind": R["kind"],
             "provider": _provider(rec.get("provider")), "group": group, "featured": featured,
             "slugs": {"t": base_slug}, "roles": {"t": R}}
        for k in ("edit", "i2v"):
            if variants and variants.get(k) in roles:
                m["slugs"][k] = variants[k]
                m["roles"][k] = roles[variants[k]]
        m["cost"] = rec.get("cost")
        m["latency"] = rec.get("latency")
        m["new"] = rec.get("new")
        m["desc"] = rec.get("desc") or ""
        m["created"] = rec.get("created") or ""
        m["pop"] = rec.get("pop") or 0
        m["engine"] = rec.get("engine") or "segmind"
        m["price_per"] = rec.get("price_per")
        return m

    for kind, groups in comfy.HEROES.items():
        for group, items in groups:
            for mid, label in items:
                m = entry(mid, label, group, None, True)
                if m:
                    models[mid] = m
                    order.append(mid)
                    claimed.add(mid)
    for kind, groups in FEATURED.items():
        for group, items in groups:
            for mid, label, variants in items:
                m = entry(mid, label, group, variants, True)
                if m:
                    models[mid] = m
                    order.append(mid)
                    claimed |= set(m["slugs"].values())
    for rec in loras:
        m = entry(rec["slug"], rec["name"], "Your LoRAs", None, True)
        if m:
            m["provider"] = "Your LoRA"
            m["lora"] = rec["lora"]
            mid = f"lora-{(rec['lora'].get('id') or rec['slug'])[:8]}"
            m["id"] = mid
            models[mid] = m
            order.append(mid)
            claimed.add(rec["slug"])
    rest = sorted((s for s in roles if s not in claimed),
                  key=lambda s: (-(recs[s].get("pop") or 0), s))
    for s in rest:
        R = roles[s]
        group = (f"Comfy · {recs[s].get('provider')}" if recs[s].get("engine") == comfy.ENGINE
                 else f"All · {_provider(recs[s].get('provider'))}")
        m = entry(s, recs[s].get("name"), group, None, False)
        if m:
            models[s] = m
            order.append(s)
    _registry.update(t=cat, cr=crecs, lm=_lora_mtime(), eng=(use_comfy, use_seg),
                     models=models, order=order, recs=recs)
    return _registry


class _Models(dict):
    """MODELS[id] — always the current catalogue (a refresh swaps it in)."""
    def _m(self):
        return _registry_build()["models"]

    def __getitem__(self, k):
        return self._m()[k]

    def get(self, k, d=None):
        return self._m().get(k, d)

    def __contains__(self, k):
        return k in self._m()

    def __iter__(self):
        return iter(self._m())

    def __len__(self):
        return len(self._m())

    def items(self):
        return self._m().items()


MODELS = _Models()


def _public_caps(R: Dict) -> Dict:
    c = {"aspects": R.get("aspects") or [], "resolutions": R.get("resolutions") or [],
         "durations": R.get("durations") or [], "audio": bool(R.get("audio"))}
    if R.get("default_resolution"):
        c["default_resolution"] = R["default_resolution"]
    return c


def public_models() -> List[Dict]:
    reg = _registry_build()
    out = []
    for mid in reg["order"]:
        m = reg["models"][mid]
        R = m["roles"]["t"]
        pm = {"id": mid, "label": m["label"], "kind": m["kind"], "provider": m["provider"],
              "group": m["group"], "featured": m["featured"], "notes": m["desc"][:110],
              "engine": m.get("engine") or "segmind",
              "price": round(m["cost"], 4) if isinstance(m.get("cost"), (int, float)) else None,
              "price_per": m.get("price_per"),
              "latency": round(m["latency"]) if isinstance(m.get("latency"), (int, float)) else None,
              "badge": "New" if m.get("new") else None, **_public_caps(R),
              "negative": bool(R.get("negative")), "seed": bool(R.get("seed")),
              # what the footage tools need: which job a model does, and what it must be given
              "category": (reg["recs"].get(m["slugs"]["t"]) or {}).get("category") or "",
              "takes_prompt": bool(R.get("prompt")),
              # the directory's one-line verdict, shown under the name in the picker
              "about": (model_notes()["models"].get(mid) or {}).get("best_for") or ""}
        tags = []
        if any(m["slugs"]["t"].startswith(p) for p in TEXT_MODELS):
            tags.append("text")
        if "svg" in m["slugs"]["t"]:
            tags.append("vector")
        if isinstance(m.get("latency"), (int, float)) and m["latency"] < 15:
            tags.append("fast")
        pm["tags"] = tags
        if m["kind"] == "image":
            E = m["roles"].get("edit")
            pm["refs"] = (E or R).get("refs_max") or 0
            pm["requires_ref"] = (R.get("refs_min") or 0) > 0
            if E:
                pm["ref_aspects"] = E.get("aspects") or pm["aspects"]
            pm["max_px"] = max([IMG_TIER.get(str(t).lower(), 0) for t in pm["resolutions"]] or [0]) or None
        elif m["kind"] == "video":
            I = m["roles"].get("i2v")
            pm["refs"] = R.get("refs_max") or 0
            pm["first_frame"] = bool(R.get("first")) or bool(I)
            pm["last_frame"] = bool(R.get("last"))
            pm["requires_frame"] = bool(R.get("requires_frame"))
            pm["source_video"] = bool(R.get("source"))
            pm["requires_source"] = bool(R.get("requires_source"))
            pm["requires_ref"] = (R.get("refs_min") or 0) > 0
            if I:
                pm["i2v"] = {**_public_caps(I), "last_frame": bool(I.get("last"))}
            if mid in SOURCE_SECONDS:
                pm["source_min"], pm["source_max"] = SOURCE_SECONDS[mid]
        out.append(pm)
    return out


# ── footage: how long a stretch each tool takes ─────────────────────────────
# Seconds of source clip a footage tool accepts, where a hard limit is known.
# A stretch outside it fails at the provider with nothing useful to say ("did
# not produce an output"), so it is stopped before it is sent. Kling's edit
# tools: Segmind's page gives no limit, Leonardo's and RunComfy's Kling docs
# cap the source at 10 s, and on prod 11.9 s and 10.7 s both failed
# (2026-10-23). The rest are from each model's Segmind page.
SOURCE_SECONDS = {
    "kling-o3-video2video-edit": (3, 10), "kling-o3-video2video-reference": (3, 10),
    "kling-o1-video-to-video-edit": (3, 10), "kling-o1-video-to-video-reference": (3, 10),
    "wan2.7-videoedit": (2, 10), "gemini-omni-1.1-video-edit": (0, 10), "gemini-omni-1.1-video-extend": (0, 10),
    "flux-3-video-edit": (0, 15), "p-video-edit": (0, 15), "pixverse-v6-extend": (0, 30),
}


def fit_source(mid: str, seconds: float, duration=None) -> Tuple[Optional[int], str]:
    """(length to ask for, "") when `mid` can work on `seconds` of footage, or
    (None, why not). A change tool with a length setting is told the
    stretch's length: left at its default it disagrees with the clip, and the
    model makes nothing. An extend tool's length is how much it adds, so it
    stays as asked."""
    m = MODELS.get(mid) or {}
    label = m.get("label") or mid
    lo, hi = SOURCE_SECONDS.get(mid, (0, 0))
    if hi and seconds > hi + 0.05:
        return None, (f"{label} works on up to {hi} s of footage, and this stretch is {seconds:.1f} s. "
                      f"Set In and Out {hi} s or less apart and run it again.")
    if lo and seconds < lo - 0.05:
        return None, f"{label} needs at least {lo} s of footage, and this stretch is {seconds:.1f} s. Widen In and Out."
    try:
        asked = round(float(duration)) if duration not in (None, "") else None
    except (TypeError, ValueError):
        asked = None
    R = (m.get("roles") or {}).get("t") or {}
    durs = R.get("durations") or []
    rec = _registry_build()["recs"].get((m.get("slugs") or {}).get("t")) or {}
    if not durs or rec.get("category") == "video-extend" or asked is not None:
        return asked, ""
    if seconds > max(durs) + 0.05 or seconds < min(durs) - 0.05:
        return None, (f"{label} makes {min(durs)}–{max(durs)} s, and this stretch is {seconds:.1f} s. "
                      "Set In and Out to fit.")
    return min(durs, key=lambda d: abs(d - seconds)), ""


# ── failures, in plain English ──────────────────────────────────────────────
# A failed take keeps the provider's own words (error) and why it failed
# (reason); the page shows this next to them: what it means for you, and what
# to try next.
def failure_hint(t: Dict) -> str:
    label = t.get("label") or "The model"
    err, reason = str(t.get("error") or ""), t.get("reason") or ""
    footage = bool((t.get("request") or {}).get("source_video"))
    google = re.search(r"google|veo|gemini", f"{t.get('model', '')} {(MODELS.get(t.get('model')) or {}).get('provider', '')}", re.I)
    if re.search(r"did not produce an output|returned no file|made nothing", err, re.I):
        if footage:
            lo, hi = SOURCE_SECONDS.get(t.get("model"), (0, 0))
            cap = f" ({label} takes up to {hi} s)" if hi else ""
            return (f"{label} ran but made nothing. That usually means it couldn't use the footage: most often "
                    f"the stretch is longer than it takes{cap}. Set In and Out closer together and run it again, "
                    "or pick another tool.")
        return (f"{label} ran but made nothing. That usually means it couldn't use what it was sent. "
                "Try a simpler prompt or fewer references, or pick another model.")
    if reason == "policy":
        return (f"{label}'s content filter refused it. Reword the prompt, or leave out a reference that shows "
                "a real person or a brand." + (" Google's models refuse real people: for footage of people, "
                                               "pick a tool that isn't Google's." if google else ""))
    if reason == "credits":
        return "The Segmind account is out of credits. Top it up at segmind.com, then run it again."
    if reason == "rateLimit":
        return "Too many runs at once. Wait a minute and run it again."
    if reason == "busy":
        return (f"{label}'s maker kept saying it was already running as many as it allows at once, "
                "so the take waited in line and gave up after a long while. Run it again when fewer are rendering.")
    if reason == "unavailable":
        return f"{label} is down or overloaded right now. Try again in a few minutes, or pick another model."
    if reason == "network":
        return "We couldn't reach Segmind. Run it again in a moment."
    if reason == "timeout":
        return ("We stopped waiting after the run went quiet. Click Check again in the Shot view: if it "
                "finished, the result is collected.")
    if reason == "validation":
        return (f"{label} refused what we sent: usually a length, size or shape it doesn't take. "
                "Its own words are below. Try other settings, or another model.")
    return f"{label} failed at the provider. Run it again; if it fails twice, pick another model."


# ── the model directory: what each model is good at, in plain English ───────
# Hand-written notes (model_notes.json, researched from the makers'
# docs, Segmind's write-ups, reviews and our own runs) laid over the live
# catalogue. The facts line is built from the specs, so prices and lengths
# never go stale in the prose; a model with no note yet still gets an entry.
NOTES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_notes.json")
_notes: Dict = {"mt": None, "d": {"models": {}, "makers": {}}}
DIR_SECTIONS = [
    ("dir-images", "Model directory: pictures", "Models that make a still from your words, most of them also from a reference picture."),
    ("dir-edit", "Model directory: editing pictures", "Models that change a picture you give them: swap, restyle, extend, retouch."),
    ("dir-video", "Model directory: clips", "Models that make a video clip from words, a start frame, or both."),
    ("dir-footage", "Model directory: changing a clip", "Tools that work on a clip you already have: restyle it, swap something, or make it longer."),
    ("dir-finish", "Model directory: upscalers and finishing", "Promptless tools for a finished take: bigger, sharper, or cut out."),
]


def model_notes() -> Dict:
    try:
        mt = os.path.getmtime(NOTES_FILE)
    except OSError:
        return _notes["d"]
    if _notes["mt"] != mt:
        try:
            with open(NOTES_FILE) as f:
                d = json.load(f)
            _notes.update(mt=mt, d={"models": d.get("models") or {}, "makers": d.get("makers") or {}})
        except Exception as e:
            print(f"[studio] model notes unreadable: {e}")
    return _notes["d"]


def anchor(mid: str) -> str:
    """The guide's link target for a model (ids carry dots: veo-3.1)."""
    return "m-" + re.sub(r"[^a-z0-9]+", "-", mid.lower()).strip("-")


def _usd(v: float) -> str:
    return f"${v:.2f}" if v >= 0.1 else f"${v:.3f}".rstrip("0")


def _secs(v: float) -> str:
    return f"{round(v)} s" if v < 90 else f"{round(v / 60)} min"


def _shapes(aspects: List[str]) -> Optional[str]:
    a = set(aspects or [])
    if not a:
        return None
    if a == {"1:1"}:
        return "square only"
    if len(a) >= 7:
        return "almost any shape"
    have = [n for n, keys in (("wide", ("16:9", "21:9", "3:2", "4:3")), ("square", ("1:1",)), ("tall", ("9:16", "2:3", "3:4", "9:21")))
            if a & set(keys)]
    return " and ".join([", ".join(have[:-1]), have[-1]] if len(have) > 1 else have) if have else None


def _lengths(d: List) -> Optional[str]:
    d = sorted({int(x) for x in d or [] if isinstance(x, (int, float)) and 0 < x <= 60})
    if not d:
        return None
    if len(d) == 1:
        return f"{d[0]} s clips"
    if len(d) == 2:
        return f"{d[0]} or {d[1]} s clips"
    return f"{d[0]}–{d[-1]} s clips"


def model_facts(pm: Dict) -> List[str]:
    """The specs, said plainly: what the card's facts line shows."""
    k, f = pm["kind"], []
    if pm.get("price") is not None:
        f.append(f"≈{_usd(pm['price'])} a " + {"image": "picture", "video": "clip"}.get(k, "run"))
    if pm.get("latency"):
        f.append(("fast, " if pm["latency"] < 15 else "") + f"about {_secs(pm['latency'])}")
    if k == "image":
        px = pm.get("max_px")
        if px:
            f.append(f"up to {px // 1024}K" if px % 1024 == 0 else f"up to {px} px")
        s = _shapes(pm.get("aspects"))
        if s:
            f.append(s)
        if pm.get("requires_ref"):
            f.append("needs a picture to work on")
        elif pm.get("refs"):
            f.append(f"up to {pm['refs']} reference picture{'s' if pm['refs'] > 1 else ''}")
        else:
            f.append("words only, no references")
    elif k == "video":
        i2v = pm.get("i2v") or {}
        n = _lengths(pm.get("durations") or i2v.get("durations"))
        if n:
            f.append(n)
        top = str((pm.get("resolutions") or i2v.get("resolutions") or [""])[-1])
        if re.fullmatch(r"\d{3,4}p|[24]K", top, re.I):
            f.append(f"up to {top}")
        s = _shapes(pm.get("aspects"))
        if s and not pm.get("requires_source"):
            f.append(s)
        if pm.get("audio"):                         # no switch doesn't mean silent: some always add sound
            f.append("makes sound")
        if pm.get("requires_source"):
            f.append("works on a clip you give it")
        elif pm.get("requires_frame"):
            f.append("needs a start frame")
        elif pm.get("first_frame"):
            f.append("can start from a still")
        if pm.get("last_frame") or i2v.get("last_frame"):
            f.append("can end on a still")
        if pm.get("refs"):
            f.append(f"up to {pm['refs']} reference picture{'s' if pm['refs'] > 1 else ''}")
        if re.search("google", pm.get("provider") or "", re.I):
            f.append("no real people")
    if pm.get("seed"):
        f.append("repeatable with a seed")
    return f


def _section(pm: Dict) -> str:
    c, k = pm.get("category") or "", pm["kind"]
    if k in ("upscale", "vupscale") or c in ("upscale", "video-upscale-enhance"):
        return "dir-finish"
    if k == "video":
        return "dir-footage" if pm.get("requires_source") or c in ("video-edit", "video-extend") else "dir-video"
    return "dir-edit" if pm.get("requires_ref") or c == "image-edit" else "dir-images"


_STATUS_ORDER = {"current": 0, "older": 1, "legacy": 2}


def directory() -> Dict:
    """Every model in the picker, as guide entries: our picks first in the
    picker's order, then the rest by maker, current before superseded."""
    notes = model_notes()
    pub = public_models()
    label = {m["id"]: m["label"] for m in pub}
    sections = {sid: {"id": sid, "title": t, "lede": lede, "groups": []} for sid, t, lede in DIR_SECTIONS}
    picks, rest = {}, {}
    for pm in pub:
        n = notes["models"].get(pm["id"]) or {}
        maker = n.get("maker") or pm["provider"]
        e = {"id": pm["id"], "anchor": anchor(pm["id"]), "label": pm["label"], "maker": maker,
             "host": pm["provider"] if n.get("maker") and n["maker"] != pm["provider"] else None,
             "kind": pm["kind"], "featured": pm["featured"], "pick": pm["group"] if pm["featured"] else None,
             "facts": model_facts(pm), "summary": n.get("summary") or pm.get("notes") or "",
             "good": n.get("good") or [], "limits": n.get("limits") or [], "best_for": n.get("best_for") or "",
             "tip": n.get("tip") or "", "status": n.get("status") or "current",
             "newer": n.get("newer") if n.get("newer") in label else None, "written": bool(n)}
        if e["newer"]:
            e["newer_label"], e["newer_anchor"] = label[e["newer"]], anchor(e["newer"])
        e["keys"] = " ".join(filter(None, [pm["id"], maker, e["host"], pm["provider"], pm["category"].replace("-", " "),
                                            " ".join(pm.get("tags") or []), e["status"], "pick" if e["pick"] else ""]))
        sid = _section(pm)
        own = pm["group"] == "Your LoRAs"
        bucket = picks if pm["featured"] else rest
        g = pm["group"] if own else f"Our picks · {pm['group']}" if bucket is picks else maker
        bucket.setdefault(sid, {}).setdefault(g, []).append(e)
    for sid, sec in sections.items():
        for g, es in (picks.get(sid) or {}).items():
            sec["groups"].append({"title": g, "anchor": f"{sid}-" + anchor(g)[2:], "pick": True, "models": es})
        for g in sorted(rest.get(sid) or {}, key=str.lower):
            es = sorted(rest[sid][g], key=lambda e: _STATUS_ORDER.get(e["status"], 0))
            sec["groups"].append({"title": g, "anchor": f"{sid}-" + anchor(g)[2:], "pick": False, "models": es})
        sec["count"] = sum(len(g["models"]) for g in sec["groups"])
    makers = [{"name": k, "anchor": "maker-" + anchor(k)[2:], "text": v} for k, v in sorted(notes["makers"].items(), key=lambda kv: kv[0].lower())]
    has = {mk["name"]: mk["anchor"] for mk in makers}
    for sec in sections.values():
        for g in sec["groups"]:
            for e in g["models"]:
                e["maker_anchor"] = has.get(e["maker"])
    return {"sections": [s for s in sections.values() if s["groups"]], "makers": makers,
            "count": len(pub), "written": sum(1 for m in pub if m["id"] in notes["models"])}


# ── media: a public URL the provider can fetch ──────────────────────────────
# Segmind's providers fetch inputs by URL, and this app sits behind a login,
# so a reference can't be linked from here. It goes to a private blob
# container with a 48-hour read link (our account, our expiry); if that is
# not configured it goes to Segmind's own storage instead (public, and the
# URL carries the account email — the fallback, not the default).
_url_cache: Dict[str, Tuple[str, float]] = {}
BLOB_CONTAINER = os.getenv("STUDIO_BLOB_CONTAINER", "studio-inputs")
SAS_HOURS = 48


def _blob_url(path: str) -> Optional[str]:
    conn = settings.AZURE_STORAGE_CONNECTION_STRING
    if not conn:
        return None
    from azure.storage.blob import BlobServiceClient, BlobSasPermissions, generate_blob_sas
    svc = BlobServiceClient.from_connection_string(conn)
    cont = svc.get_container_client(BLOB_CONTAINER)
    try:
        cont.create_container()                  # private by default
    except Exception:
        pass
    with open(path, "rb") as f:
        data = f.read()
    name = f"{hashlib.sha1(data).hexdigest()[:20]}{os.path.splitext(path)[1].lower()}"
    blob = cont.get_blob_client(name)
    if not blob.exists():
        from azure.storage.blob import ContentSettings
        blob.upload_blob(data, overwrite=True, content_settings=ContentSettings(
            content_type=mimetypes.guess_type(path)[0] or "application/octet-stream"))
    acct = svc.account_name
    key = svc.credential.account_key
    sas = generate_blob_sas(account_name=acct, container_name=BLOB_CONTAINER, blob_name=name,
                            account_key=key, permission=BlobSasPermissions(read=True),
                            expiry=datetime.now(timezone.utc) + timedelta(hours=SAS_HOURS))
    return f"https://{acct}.blob.core.windows.net/{BLOB_CONTAINER}/{name}?{sas}"


def _segmind_url(path: str) -> str:
    with open(path, "rb") as f:
        data = f.read()
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    r = requests.post(SEG_UPLOAD, headers={"x-api-key": settings.SEGMIND_API_KEY, "Content-Type": "application/json"},
                      json={"data_urls": [f"data:{mime};base64,{base64.b64encode(data).decode()}"]}, timeout=180)
    if r.status_code != 200:
        raise RunError(f"Could not upload the reference to Segmind ({r.status_code}).", "upload")
    urls = (r.json() or {}).get("file_urls") or []
    if not urls:
        raise RunError("Segmind's storage returned no link for the reference.", "upload")
    return urls[0]


def media_url(path: str) -> str:
    key = f"{path}:{os.path.getmtime(path)}"
    hit = _url_cache.get(key)
    if hit and hit[1] > time.time() + 3600:
        return hit[0]
    url = None
    try:
        url = _blob_url(path)
    except Exception as e:
        print(f"[studio] blob upload failed, using Segmind storage: {e}")
    if not url:
        url = _segmind_url(path)
    _url_cache[key] = (url, time.time() + SAS_HOURS * 3600)
    return url


# ── canonical request → native payload ──────────────────────────────────────
def _snap(options: List, want: float, key) -> object:
    cands = [(o, key(o)) for o in options]
    cands = [(o, v) for o, v in cands if v]
    if not cands:
        return None
    return min(cands, key=lambda ov: abs(math.log(ov[1] / want)))[0]


def _typed(p: Dict, v):
    t = p.get("t")
    try:
        if t == "integer":
            return int(float(v))
        if t == "number":
            return float(v)
        if t == "boolean":
            return bool(v)
        if t == "string":
            return str(v)
    except Exception:
        pass
    return v


def build(model_id: str, req: Dict, find: Callable[[str], Optional[str]]) -> Dict:
    """→ {"endpoint": slug, "payload": {...}} for one take."""
    m = MODELS.get(model_id)
    if not m:
        raise RunError("That model is no longer in the catalogue.", "unavailable")
    refs = [r for r in (req.get("refs") or []) if r]
    variant = "t"
    if m["kind"] == "image" and refs and "edit" in m["roles"]:
        variant = "edit"
    if m["kind"] == "video" and req.get("first_frame") and "i2v" in m["roles"]:
        variant = "i2v"
    slug, R = m["slugs"][variant], m["roles"][variant]
    rec = _registry_build()["recs"].get(slug) or {}
    inp = rec.get("inputs") or {}
    P: Dict = {}
    video = m["kind"] in ("video", "vupscale")
    on_comfy = rec.get("engine") == comfy.ENGINE
    # Segmind fetches references by link; for Comfy they stay local files until
    # comfy.shape knows whether the model wants a link, a data URL or base64
    media = comfy.file_ref if on_comfy else media_url

    def path_of(name):
        p = find(name) if name else None
        if not p:
            raise RunError("A reference file is missing — add it again.", "upload")
        return p

    # text
    if m["kind"] in ("upscale", "vupscale"):
        src = req.get("source")
        field = (R.get("refs_single") or [None])[0] or R.get("refs_multi") if m["kind"] == "upscale" else R.get("source")
        if not field:
            raise RunError(f"{m['label']} has no input this studio can fill.", "validation")
        url = media(path_of(src))
        P[field] = [url] if inp.get(field, {}).get("mul") else url
        if R.get("prompt") and req.get("prompt"):
            P[R["prompt"]] = req["prompt"][:2000]
        sc = R.get("scale")
        if sc:
            p = inp.get(sc, {})
            want = float(req.get("scale") or 2)
            if p.get("o"):
                P[sc] = _snap(p["o"], want, lambda o: _num(str(o).lower().rstrip("x")))
            else:
                P[sc] = _typed(p, max(p.get("mn", want), min(p.get("mx", want), want)))
        if R.get("tier_field") and m["kind"] == "vupscale":
            opts = R["tiers"]
            P[R["tier_field"]] = next((o for o in opts if str(o).lower() in ("1080p", "1080")), opts[-1])
    else:
        if R.get("prompt"):
            P[R["prompt"]] = req.get("prompt") or ""
        elif not R.get("source"):
            raise RunError(f"{m['label']} takes no prompt.", "validation")
        if R.get("negative") and req.get("negative_prompt"):
            P[R["negative"]] = req["negative_prompt"]
        if R.get("seed") and str(req.get("seed") or "").strip():
            try:
                P[R["seed"]] = int(req["seed"])
            except Exception:
                pass

        # framing: aspect + size
        want_a = _ratio(req.get("aspect") or "16:9") or 16 / 9
        if R.get("aspect"):
            opts = inp[R["aspect"]]["o"]
            pick = _snap(opts, want_a, _ratio)
            if pick is not None:
                P[R["aspect"]] = pick
        res = str(req.get("resolution") or "")
        if R.get("tier_field"):
            opts = R["tiers"]
            pick = next((o for o in opts if str(o).lower() == res.lower()), None)
            if pick is None:
                want_px = _tier_px(res, video) or (720 if video else 2048)
                pick = _snap(opts, want_px, lambda o: _tier_px(o, video))
            if pick is not None:
                P[R["tier_field"]] = pick
        if R.get("wh_field"):
            target = IMG_TIER.get(res.lower(), 2048 if not video else 1280) if not video else (
                {360: 640, 480: 854, 540: 960, 720: 1280, 768: 1366, 1080: 1920}.get(_tier_px(res, True) or 720, 1280))
            err = lambda wh: abs(math.log((wh[0] / wh[1]) / want_a))
            good = [(o, wh) for o, wh in R["whs"] if err(wh) < 0.03]
            if not good:
                # no option at this aspect: the shape matters more than the size —
                # a 3:2 ask gets the nearest landscape, never a square
                best = min(err(wh) for _, wh in R["whs"])
                good = [(o, wh) for o, wh in R["whs"] if err(wh) <= best + 0.01]
            pool = good
            pick = min(pool, key=lambda owh: (abs(math.log(max(owh[1]) / target)),
                                               abs(math.log((owh[1][0] / owh[1][1]) / want_a))))
            P[R["wh_field"]] = pick[0]
        if R.get("w") and R.get("h") and not R.get("wh_field") and not R.get("aspect"):
            base = IMG_TIER.get(res.lower(), 1024)
            pw, ph = inp["width"], inp["height"]
            if want_a >= 1:
                w, h = base, base / want_a
            else:
                w, h = base * want_a, base
            clamp = lambda v, p: int(max(p.get("mn", 256), min(p.get("mx", 2048), round(v / 16) * 16)))
            P["width"], P["height"] = clamp(w, pw), clamp(h, ph)

        # time + sound
        if R.get("duration") and req.get("duration"):
            p = inp[R["duration"]]
            want = float(req["duration"])
            if p.get("o"):
                pick = _snap([o for o in p["o"] if (_num(o) or 0) > 0], want, _num)
                if pick is not None:
                    P[R["duration"]] = pick
            else:
                P[R["duration"]] = _typed(p, max(p.get("mn", want), min(p.get("mx", want), want)))
        if R.get("audio"):
            P[R["audio"]] = bool(req.get("audio"))

        # references and frames
        if m["kind"] == "image":
            if refs:
                if R.get("refs_multi"):
                    P[R["refs_multi"]] = [media(path_of(r)) for r in refs[:R["refs_max"] or 10]]
                elif R.get("refs_single"):
                    for field, r in zip(R["refs_single"], refs):
                        P[field] = media(path_of(r))
                else:
                    raise RunError(f"{m['label']} doesn't take reference images.", "validation")
            elif R.get("refs_min"):
                raise RunError(f"{m['label']} edits an image — add a reference first.", "validation")
        else:
            if req.get("first_frame"):
                if not R.get("first"):
                    raise RunError(f"{m['label']} can't start from an image.", "validation")
                url = media(path_of(req["first_frame"]))
                P[R["first"]] = [url] if inp.get(R["first"], {}).get("mul") else url
            elif R.get("requires_frame"):
                raise RunError(f"{m['label']} starts from an image — add a starting frame.", "validation")
            if req.get("last_frame") and R.get("last"):
                P[R["last"]] = media(path_of(req["last_frame"]))
            if req.get("source_video"):
                if not R.get("source"):
                    raise RunError(f"{m['label']} doesn't take a video.", "validation")
                P[R["source"]] = media(path_of(req["source_video"]))
            elif R.get("requires_source"):
                raise RunError(f"{m['label']} edits a clip — add the video to edit.", "validation")
            if refs and R.get("refs_multi"):
                P[R["refs_multi"]] = [media(path_of(r)) for r in refs[:R["refs_max"] or 4]]

    # house rules: clean output, browser-playable video, URLs not base64
    for w in R.get("watermark") or []:
        P[w] = False
    if R.get("codec"):
        P[R["codec"]] = "h264"
    if R.get("base64"):
        P[R["base64"]] = False
    for k, v in (R.get("required_defaults") or {}).items():
        P.setdefault(k, v)
    if m.get("lora"):
        trig = m["lora"].get("trigger") or ""
        if trig and R.get("prompt") and not str(P.get(R["prompt"], "")).lower().startswith(trig.lower()):
            P[R["prompt"]] = f"{trig}, {P.get(R['prompt'], '')}"
        for k, v in LORA_DEFAULTS.items():
            P.setdefault(k, v)
    if on_comfy:
        return {"endpoint": slug, "payload": comfy.shape(rec, P), "label": m["label"], "engine": comfy.ENGINE,
                "kind": m["kind"]}
    return {"endpoint": slug, "payload": P, "label": m["label"]}


def elide(obj, limit: int = 160):
    if isinstance(obj, str):
        if obj.startswith("https://") and "?" in obj:
            return obj.split("?")[0] + "?…"          # never show a SAS token
        return obj if len(obj) <= limit else f"{obj[:limit]}…"
    if isinstance(obj, dict):
        return {k: elide(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [elide(v, limit) for v in obj]
    return obj


# ── Segmind v2 queue ────────────────────────────────────────────────────────
def _headers() -> Dict:
    return {"x-api-key": settings.SEGMIND_API_KEY, "Content-Type": "application/json"}


_POLICY = re.compile(r"policy|safety|moderat|nsfw|sensitive|inappropriate|blocked", re.I)
# a provider at its limit of runs at once ("Concurrent generation limit reached
# (4)", Luma via Segmind): not a failure but a wait, so the take goes back in line
_BUSY = re.compile(r"concurren|too many (?:active|running|pending|simultaneous|parallel)|parallel (?:task|request|generation)s? limit", re.I)


def busy_limit(msg: str) -> Optional[int]:
    """The number of runs at once a busy provider named, if it named one."""
    m = re.search(r"(?:concurren\w*|parallel)\D{0,40}?\(?(\d{1,3})\)?", msg or "", re.I)
    return int(m.group(1)) if m and 0 < int(m.group(1)) < 100 else None


def _error_of(r: requests.Response) -> RunError:
    try:
        j = r.json()
        msg = j.get("error") or j.get("message") or j.get("detail") or json.dumps(j)[:300]
        if isinstance(msg, (dict, list)):
            msg = json.dumps(msg)[:300]
    except Exception:
        msg = (r.text or "")[:300]
    msg = str(msg)
    s = r.status_code
    if s == 401:
        return RunError("Segmind rejected the API key.", "unavailable")
    if s == 402 or re.search(r"credit|balance|insufficient", msg, re.I):
        return RunError("Not enough Segmind credits for this shot. Top up at segmind.com.", "credits")
    if _BUSY.search(msg):
        return RunError(msg[:300], "busy")
    if s == 429:
        return RunError("Too many runs at once (Segmind allows 50 a minute). Wait a moment and try again.", "rateLimit")
    if re.search(r"temporarily unavailable|try again in a few|overloaded|capacity", msg, re.I):
        return RunError(f"{msg[:240]} Try another model, or again in a few minutes.", "unavailable")
    if _POLICY.search(msg):
        return RunError(f"The model blocked the prompt or a reference under its content policy. {msg[:200]}", "policy")
    if s in (400, 406, 422):
        return RunError(f"The model rejected these inputs: {msg[:300]}", "validation")
    if s == 404:
        return RunError("Segmind doesn't know this model or request any more.", "unavailable")
    return RunError(f"The run failed at Segmind ({s}): {msg[:240]}", "provider")


def submit(call: Dict) -> Dict:
    if call.get("engine") == comfy.ENGINE:
        return comfy.submit(call["endpoint"], call["payload"], call.get("key"),
                            kind=call.get("kind", ""), sync=bool(call.get("sync")))
    url = f"{SEG_V2}/{call['endpoint']}"
    try:
        r = requests.post(url, headers=_headers(), json=call["payload"], timeout=60)
    except requests.RequestException as e:
        raise RunError(f"Couldn't reach Segmind: {e}", "network")
    if r.status_code >= 300:
        raise _error_of(r)
    j = r.json()
    rid = j.get("request_id") or j.get("id")
    if not rid:
        raise RunError(f"Segmind accepted the run but returned no request id: {json.dumps(j)[:200]}", "response")
    return {"request_id": rid, "poll_url": f"{SEG_V2}/requests/{rid}/status"}


def poll(poll_url: str, model: str = "") -> Dict:
    if poll_url.startswith(comfy.BASE):
        return comfy.poll(poll_url, (MODELS.get(model) or {}).get("kind") or "")
    try:
        r = requests.get(poll_url, headers=_headers(), timeout=30)
    except requests.RequestException as e:
        return {"pending": True, "progress": f"network: {e}"[:80]}
    if r.status_code == 404:
        raise RunError("Segmind no longer has this request (records expire soon after they finish).", "response")
    if r.status_code == 422:
        raise _error_of(r)
    if r.status_code >= 300:
        if r.status_code >= 500 or r.status_code == 429:
            return {"pending": True}
        raise _error_of(r)
    j = r.json()
    st = str(j.get("status") or "").upper()
    if st in ("QUEUED", "PROCESSING", "IN_PROGRESS", "PENDING", "RUNNING", ""):
        return {"pending": True, "progress": st.lower() or None}
    if st == "FAILED":
        msg = str(j.get("error") or j.get("message") or "The model failed.")
        reason = ("busy" if _BUSY.search(msg)
                  else "unavailable" if re.search(r"temporarily unavailable|try again in a few", msg, re.I)
                  else "policy" if _POLICY.search(msg) else "provider")
        raise RunError(msg[:400], reason)
    # COMPLETED → fetch the result body
    res_url = poll_url[:-len("/status")] if poll_url.endswith("/status") else poll_url
    r2 = requests.get(res_url, headers=_headers(), timeout=60)
    if r2.status_code >= 300:
        raise _error_of(r2)
    body = r2.json()
    url = _output_url(body)
    if not url:
        raise RunError(f"Segmind finished but returned no file: {json.dumps(body)[:200]}", "response")
    metrics = body.get("metrics") or j.get("metrics") or {}
    return {"url": url, "cost": metrics.get("cost"), "seed": body.get("seed")}


def _output_url(body) -> Optional[str]:
    if isinstance(body, str):
        return body if body.startswith("http") else None
    if isinstance(body, list):
        for x in body:
            u = _output_url(x)
            if u:
                return u
        return None
    if isinstance(body, dict):
        for k in ("video", "images", "image", "output", "audio", "url", "outputs", "result", "file"):
            if k in body:
                u = _output_url(body[k])
                if u:
                    return u
    return None


_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}


def fetch_output(res: Dict) -> Tuple[bytes, str]:
    if res.get("data"):                       # Comfy results can arrive inline (base64, raw bytes)
        return res["data"], res.get("ext") or ".bin"
    url = res.get("url")
    try:
        r = requests.get(url, timeout=300)
    except requests.RequestException as e:
        raise RunError(f"The result was made but couldn't be downloaded: {e}", "response")
    if r.status_code != 200:
        raise RunError(f"The result link answered {r.status_code}.", "response")
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].lower()
    ext = _EXT.get(ctype) or os.path.splitext(url.split("?")[0])[1].lower() or ".bin"
    if ext == ".jpeg":
        ext = ".jpg"
    return r.content, ext


# ── the director ────────────────────────────────────────────────────────────
DIALECTS = {
    "veo": "Veo responds to film language and generates sound: describe the action beat by beat, the camera move, and the ambience; put any spoken line in quotes.",
    "kling": "Kling follows motion verbs: subject action first, then the camera move, then the look. Keep it under about 90 words.",
    "seedance": "Seedance handles multi-shot direction ('Shot 1: … Shot 2: …') and precise camera moves. It refuses realistic faces from reference photos.",
    "sora": "Sora is physics-aware: describe the scene as it unfolds over time, with concrete materials and motion.",
    "wan": "Wan reads plain descriptive prose; lead with subject and action, then setting, light and camera.",
    "flux": "FLUX wants prose with the subject first, specific nouns over adjectives, and no tag lists.",
    "gpt-image": "GPT Image follows natural language and exact layout; quoted words render as text.",
    "nano-banana": "Nano Banana (Gemini) reads narrative description; say what the picture is for and how it is composed.",
    "seedream": "Seedream likes a concise, ordered description: subject, action, setting, light, lens, grade.",
    "ideogram": "Ideogram renders text: put every word that must appear in quotes and say where it goes.",
    "recraft": "Recraft is a design model: describe layout, typography and palette explicitly.",
    "ltx": "LTX is fast and literal: one clear action and one camera move.",
    "minimax": "MiniMax/Hailuo responds to [camera move] tags and strong motion verbs.",
    "grok": "Grok Imagine reads natural language; be concrete about motion and timing.",
    "luma": "Luma Ray favours smooth cinematic moves and clear subject motion.",
}

_DIRECTOR_SYSTEM = """You are a director of photography writing the final prompt for an AI image or video model.

You receive a shot brief compiled from a director's choices: framing, angle, camera body, lens, focal length, aperture, light, film stock, look, grade, composition, and for video the camera movement. Rewrite it as the single best prompt for the named model.

Rules:
- Keep EVERY direction choice in the brief. You may reword it into natural cinematographer's language, but never drop or contradict one.
- Keep the scene's subject, action and setting exactly; add only concrete, plausible detail that serves them (materials, weather, background life, micro-actions).
- Describe light as its qualities (direction, colour, softness, contrast); never name lighting equipment, which models paint into the frame.
- No real people, brands, logos or trademarks unless the scene names them. No artist names.
- No filler tags (masterpiece, 8k, ultra-detailed, trending).
- For video: one continuous shot unless the brief asks for more; say what moves, how the camera moves, and how the shot ends.
- Fit the model's dialect, given below. Stay under 160 words.
- negative_prompt: a short comma list of concrete failure modes for this shot (only if useful).
- notes: one short sentence to the user on what you changed and why."""


def _anthropic():
    import anthropic
    key = settings.ANTHROPIC_API_KEY
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def director_rewrite(brief: str, model_id: str, video: bool = False, notes: str = "") -> Dict:
    m = MODELS.get(model_id) or {}
    slug = (m.get("slugs") or {}).get("t", model_id)
    dialect = next((v for k, v in DIALECTS.items() if slug.startswith(k) or k in slug), "")
    user = (f"Model: {m.get('label', model_id)} ({'video' if video else 'still image'})\n"
            f"Dialect: {dialect or 'Plain descriptive prose.'}\n"
            + (f"Director's extra notes (highest priority): {notes.strip()}\n" if notes.strip() else "")
            + f"\nBrief:\n{brief}")
    out = _claude_json(_DIRECTOR_SYSTEM, user, {
        "type": "object", "additionalProperties": False,
        "required": ["prompt", "negative_prompt", "notes"],
        "properties": {"prompt": {"type": "string"}, "negative_prompt": {"type": "string"},
                       "notes": {"type": "string"}}},
        effort=os.getenv("STUDIO_DIRECTOR_EFFORT", "low"), max_tokens=4000)
    out["prompt"] = (out.get("prompt") or brief).strip()
    out["brief"] = brief
    return out


def claude_on() -> bool:
    """Claude is reachable: with its own key, or through Comfy Router."""
    if settings.VISITOR_KEYS:
        return True                              # on the visitor's own Comfy key
    return bool(settings.ANTHROPIC_API_KEY or settings.COMFY_API_KEY)


def _claude_json(system: str, user: str, schema: Dict, effort: str = "low", max_tokens: int = 4000) -> Dict:
    """One structured call to Claude: the reply is JSON matching `schema`.
    With ANTHROPIC_API_KEY it goes to Anthropic; otherwise through Comfy
    Router on the studio's one Comfy key."""
    kw = dict(
        model=os.getenv("STUDIO_DIRECTOR_MODEL", "claude-opus-5-5"),
        max_tokens=max_tokens,
        system=system,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user}],
    )
    if settings.VISITOR_KEYS or (not settings.ANTHROPIC_API_KEY and settings.COMFY_API_KEY):
        resp = comfy.claude({k: v for k, v in kw.items() if k != "model"}, kw["model"])
        stop = resp.get("stop_reason")
        if stop == "refusal":
            raise RuntimeError("the director declined this brief")
        if stop == "max_tokens":
            raise RuntimeError("the director ran out of room — shorten the brief")
        text = next((b.get("text", "") for b in resp.get("content") or [] if b.get("type") == "text"), "")
        return json.loads(text)
    import anthropic
    client = _anthropic()
    try:
        # a policy decline re-runs on the fallback model inside the same call
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                           fallbacks="default", **kw)
    except anthropic.BadRequestError as e:
        if "fallback" not in str(e).lower():
            raise
        resp = client.messages.create(**kw)    # the account doesn't have fallbacks; run plain
    if resp.stop_reason == "refusal":
        raise RuntimeError("the director declined this brief")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("the director ran out of room — shorten the brief")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return json.loads(text)


_PLANNER_SYSTEM = """You are a commercial director and storyboard artist. You turn a brief into the shot list for a TV spot that will be generated shot by shot with an AI video model: each shot first as a keyframe still, then animated from it.

Rules:
- The durations must add up to the target length, within one second. Every duration must be one of the allowed values given.
- One clear action per shot. `scene` says what the camera sees, in present tense, 1-3 concrete sentences: who, where, what happens, how the shot ends. Make each shot stand alone: a model reading only that card must be able to draw it.
- Name kit elements exactly as written in the kit whenever they appear, and list them in `elements`. Never introduce a named character or product that isn't in the kit; unnamed extras and props are fine.
- No on-screen text, captions, logos-as-text or dialogue in `scene`: spoken words go in `vo`, on-screen words in `super`.
- `shot` is the framing id and `move` the camera movement id, from the lists given. Vary them the way an editor would: establish, then come in close, a hero product angle, a wide release.
- `start` is "cut" for a new setup. Use "continue" only when the shot picks up at the exact moment the previous one ended, same place, the camera carrying on.
- `vo`: the voice-over line under this shot, at most about 2.5 words per second of the shot, or "". `super`: short on-screen text for this shot, or "".
- Hook in the first two to three seconds. Show the product or the promise clearly. End on a hero or brand moment.
- The look (camera, light, film, grade) is fixed for the whole spot and is given to you; don't restate it in the scenes.
- Real footage may already be shot. Each piece is given with an id, its length and what's in it. It is fixed: include every piece exactly once, where it best serves the story, as an item whose `footage` is its id, `duration` its length and `scene` its description. Plan the AI shots around the footage so the whole spot reads as one shoot: match its people, place and light, and use "continue" for an AI shot that picks up right where a piece of footage ends. AI shots set `footage` to "".
- `title`: a working title for the spot. `notes`: one or two sentences to the user on the idea and the structure."""


def plan_spot(brief: str, target: float, model_id: str, elements: List[Dict], direction: Dict,
              footage: Optional[List[Dict]] = None) -> Dict:
    """A brief → {title, notes, shots: [{scene, shot, move, duration, elements, start, vo, super}]},
    the durations snapped to what the clip model can make and summing to the target."""
    from . import direction as D
    m = MODELS.get(model_id) or {}
    roles = m.get("roles") or {}
    R = roles.get("i2v") or roles.get("t") or {}          # a board's clips run from their keyframes
    # a few schemas call a frame count "duration" (LTX: 97, 129…); a shot is seconds
    durations = sorted({int(d) for d in (_public_caps(R).get("durations") or []) if d and 0 < d <= 60}) or [4, 5, 6, 8, 10]
    frames = [o["id"] for o in D.GROUPS["shot"]["options"]]
    moves = [o["id"] for o in D.GROUPS["move"]["options"]]
    names = [str(e.get("name") or "").strip() for e in elements if str(e.get("name") or "").strip()]
    look = " ".join(x for x in (", ".join(D.phrases(D.CAMERA, direction)), ", ".join(D.phrases(D.LOOKS, direction))) if x)
    kit = "\n".join(f"- {e['name']} ({e.get('kind') or 'cast'})" for e in elements if e.get("name")) or "(none)"
    footage = [f for f in (footage or []) if f.get("id")]
    filmed = "\n".join(f"- {f['id']} ({float(f.get('seconds') or 5):.1f} s): {str(f.get('notes') or 'no description').strip()[:400]}"
                      for f in footage) or "(none)"
    user = (f"Target length: {target:g} seconds\n"
            f"Clip model: {m.get('label', model_id or 'any')} — allowed shot durations: {', '.join(map(str, durations))} seconds\n"
            f"Framing ids: {', '.join(frames)}\nMovement ids: {', '.join(moves)}\n"
            f"Look, fixed for the spot: {look or 'not set'}\nKit:\n{kit}\nFootage already shot:\n{filmed}\n\nBrief:\n{brief.strip()}")
    shot = {"type": "object", "additionalProperties": False,
            "required": ["scene", "shot", "move", "duration", "elements", "start", "vo", "super", "footage"],
            "properties": {"scene": {"type": "string"}, "shot": {"type": "string", "enum": frames},
                           "move": {"type": "string", "enum": moves}, "duration": {"type": "number"},
                           "elements": {"type": "array", "items": {"type": "string"}},
                           "start": {"type": "string", "enum": ["cut", "continue"]},
                           "vo": {"type": "string"}, "super": {"type": "string"}, "footage": {"type": "string"}}}
    out = _claude_json(_PLANNER_SYSTEM, user, {
        "type": "object", "additionalProperties": False, "required": ["title", "notes", "shots"],
        "properties": {"title": {"type": "string"}, "notes": {"type": "string"},
                       "shots": {"type": "array", "items": shot}}},
        effort=os.getenv("STUDIO_PLANNER_EFFORT", "medium"), max_tokens=12000)
    return _fit_plan(out, durations, names, target, footage)


def _fit_plan(out: Dict, durations: List[int], names: List[str], target: float,
              footage: Optional[List[Dict]] = None) -> Dict:
    """Hold the planner to what can be shot: durations the model makes, kit
    names only, the first shot a cut, every piece of footage exactly once at
    its own length — and say how far off the target it is."""
    by = {n.lower(): n for n in names}
    real = {f["id"]: f for f in (footage or [])}
    shots, placed = [], set()
    for x in (out.get("shots") or [])[:40]:
        fid = str(x.get("footage") or "")
        if fid in real:
            if fid not in placed:
                placed.add(fid)
                shots.append({"footage": fid, "duration": float(real[fid].get("seconds") or 5),
                              "scene": str(real[fid].get("notes") or ""), "start": "cut"})
            continue
        shots.append({**x, "footage": ""})
    for fid, f in real.items():                  # a piece the planner left out still goes in, at the end
        if fid not in placed:
            shots.append({"footage": fid, "duration": float(f.get("seconds") or 5), "scene": str(f.get("notes") or ""), "start": "cut"})
    out_shots = []
    for i, x in enumerate(shots):
        if x["footage"]:
            out_shots.append(x)
            continue
        try:
            want = float(x.get("duration") or 5)
        except (TypeError, ValueError):
            want = 5.0
        out_shots.append({**x, "duration": min(durations, key=lambda d: (abs(d - want), d)),
                          "elements": [by[e.lower()] for e in (x.get("elements") or []) if str(e).lower() in by],
                          "start": "cut" if i == 0 else (x.get("start") if x.get("start") in ("cut", "continue") else "cut")})
    total = round(sum(s["duration"] for s in out_shots), 2)
    return {"title": out.get("title") or "", "notes": out.get("notes") or "", "shots": out_shots,
            "total": total, "target": target}
