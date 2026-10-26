"""Prompts from a reference picture, for the Content Generator's batch tools.

It hangs off the studio's prefix. With OPENAI_API_KEY set it asks GPT-4o;
without one it asks Claude (directly, or through Comfy Router on the one Comfy
key), so a single key runs the whole app.
"""
import base64
import json
import time

import requests
from fastapi import APIRouter, Depends, File, Form, UploadFile

from . import models as M
from . import settings
from .routes import _switched_on

router = APIRouter(prefix=settings.PREFIX, dependencies=[Depends(_switched_on)])

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _variation_guidance(level: int) -> str:
    if level <= 2:
        return ("All variants should be nearly identical to the source. "
                "Keep the same subject, ethnicity, gender, age, clothing, pose, background, and lighting. "
                "Allow only microscopic differences: a very subtle shift in head angle, eye direction, or micro-expression. "
                "Think of these as near-duplicate shots from the same second of a photo shoot.")
    if level <= 4:
        return ("Variants should be clearly related but with subtle differences. "
                "Keep the same subject type, ethnicity, approximate age, and general setting. "
                "You may vary: slight pose change, facial expression shift, minor accessory swap (glasses on/off), "
                "small clothing color variation, slightly different background depth-of-field. "
                "Think same photo shoot, different moment.")
    if level <= 6:
        return ("Variants should share the core concept but show moderate creative differences. "
                "Keep the same subject type and general scene category. "
                "You may vary: clothing style and color, background location within same category, "
                "different time of day or light quality, alternate pose or activity, different age within same decade. "
                "Think same campaign brief, different creative execution.")
    if level <= 8:
        return ("Variants should be meaningfully different interpretations of the concept. "
                "Feel free to vary: ethnicity, gender presentation, age range (within adult), clothing era or style, "
                "season/weather, indoor vs outdoor, camera angle, emotional tone. "
                "Keep the core subject category the same (e.g. all portraits, all product shots). "
                "Think same stock library category, very different executions.")
    return ("Variants should be boldly different while staying commercially viable and photorealistic. "
            "Freely vary: ethnicity, age (young adult to senior), gender, body type, clothing era and style, "
            "geographic region implied by setting, season, camera perspective, emotional tone, and color palette. "
            "Only constraint: all results must look like high-end stock photography of the same general subject category. "
            "Think same broad theme, maximum creative diversity.")


REALISM = (
    "PHOTOGRAPHY REALISM REQUIREMENTS -- mandatory, apply to every single prompt:\n"
    "Each prompt must read like a professional camera operator wrote it.\n"
    "REQUIRED in every prompt:\n"
    "  - Specific camera body and lens: e.g. 'Canon EOS R5, 85mm f/1.4 portrait lens' or "
    "'Sony A7R V, 50mm f/1.2' or 'Nikon Z9, 135mm f/1.8'\n"
    "  - Precise lighting setup described as light qualities only -- direction, color temperature, "
    "hardness, shadow character: e.g. 'soft diffused daylight from camera-left, gentle fill from right', "
    "'warm low-angle backlight, long soft shadows', 'overcast even light, no harsh shadows', "
    "'cool north-facing window light, single-source directional'. "
    "NEVER name physical equipment (softbox, reflector, umbrella, light stand, studio light) -- "
    "they must not appear in the scene.\n"
    "  - Lens rendering: shallow depth of field, natural bokeh, tack-sharp focus on eyes/subject, "
    "or appropriate DOF for scene\n"
    "  - Natural imperfection: real skin texture with visible pores, natural hair strands, "
    "slight fabric wrinkles -- NOT airbrushed, plastic, or CG-smooth\n"
    "  - Specific environment detail: name actual materials, surfaces, textures, "
    "time of day, weather -- NOT generic backgrounds\n"
    "  - Color grade: e.g. 'warm Kodak Portra 400 film tones', "
    "'cool desaturated editorial grade', 'clean neutral daylight color'\n"
    "FORBIDDEN (these phrases cause AI-looking output -- never use them):\n"
    "  - hyperrealistic, hyper realistic, ultra realistic\n"
    "  - masterpiece, best quality, ultra detailed (as standalone tags)\n"
    "  - cinematic lighting (without specifics)\n"
    "  - perfect skin, flawless, airbrushed\n"
    "  - softbox, reflector, umbrella, light stand, studio light, photography light, "
    "ring light, beauty dish, diffuser panel (never name equipment -- describe the light quality instead)\n"
    "REQUIRED CLOSING TAGS -- end every prompt with exactly:\n"
    "  shot on professional camera, photorealistic, award-winning editorial photography, "
    "8k resolution, ultra sharp\n"
)

SYSTEM = (
    "You are a professional commercial photographer and photo director who writes precise shot briefs "
    "for AI image generation. Your prompts read exactly like a cinematographer's camera direction -- "
    "specific gear, real lighting setups, natural textures, named locations. "
    "The goal is output indistinguishable from a professional DSLR/mirrorless photograph. "
    "You always return valid JSON and nothing else -- no markdown, no preamble, no commentary."
)

TAIL = "shot on professional camera, photorealistic, award-winning editorial photography, 8k resolution, ultra sharp"


def _ask_openai(text: str, mime: str, b64: str, n: int, temperature: float) -> dict:
    body = {"model": "gpt-4o", "max_tokens": 500 * n, "temperature": temperature,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": [
                             {"type": "text", "text": text},
                             {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]}
    raw, last = "", None
    for attempt, wait in enumerate((0, 3, 8)):
        if wait:
            time.sleep(wait)
        try:
            r = requests.post(OPENAI_URL, json=body, timeout=120,
                              headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"})
            r.raise_for_status()
            raw = (r.json()["choices"][0]["message"]["content"] or "").strip()
            if raw:
                break
        except Exception as e:                  # noqa: BLE001 - retried, then reported
            last = e
    if not raw:
        raise ValueError(f"GPT returned no content after 3 attempts. Last error: {last}")
    return json.loads(raw.strip("```json").strip("```").strip())


def _ask_claude(text: str, mime: str, b64: str, n: int) -> dict:
    schema = {"type": "object", "additionalProperties": False, "required": ["prompts", "negative_prompts"],
              "properties": {"prompts": {"type": "array", "items": {"type": "string"}},
                             "negative_prompts": {"type": "array", "items": {"type": "string"}}}}
    content = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
               {"type": "text", "text": text}]
    return M._claude_json(SYSTEM, content, schema, effort="low", max_tokens=max(4000, 700 * n))


@router.post("/prompt-variants")
async def prompt_variants(file: UploadFile = File(...), image_style: str = Form("photography"),
                          num_variants: int = Form(1), variation_level: int = Form(5),
                          user_notes: str = Form("")):
    try:
        gpt = bool(settings.OPENAI_API_KEY) and not settings.VISITOR_KEYS
        if not (gpt or M.claude_on()):
            raise ValueError("needs OPENAI_API_KEY, ANTHROPIC_API_KEY or COMFY_API_KEY")
        content = await file.read()
        b64 = base64.b64encode(content).decode("utf-8")
        mime = file.content_type or "image/jpeg"
        n = max(1, min(num_variants, 20))
        level = max(1, min(variation_level, 10))
        is_photo = image_style.lower() not in ("illustration", "drawing", "painting", "sketch")

        # Art-director notes from the batch runner: the human's corrections for
        # this batch. Highest priority — the point is to steer away from what
        # the last run got wrong.
        notes = ""
        if (user_notes or "").strip():
            notes = ("ART DIRECTOR'S NOTES -- HIGHEST PRIORITY. These come from the human "
                     "reviewing this batch and OVERRIDE any conflicting guidance above or below. "
                     "Every single prompt MUST comply, and each negative prompt should reinforce "
                     "them where applicable (add the failure modes they forbid):\n"
                     f"{user_notes.strip()}\n\n")
        text = (
            f"Analyze this {image_style} image and generate exactly {n} distinct text-to-image prompt(s) "
            f"that could recreate variations of it (with minor changes to avoid copyright issues).\n\n"
            f"Each prompt must fully describe: subject and their specific details, environment with named "
            f"materials and textures, exact lighting setup, camera body and lens, composition and framing, "
            f"color grade, mood, and any fine foreground/background details.\n\n"
            f"{notes}"
            f"{REALISM if is_photo else ''}"
            f"VARIATION GUIDANCE -- make prompts differ from each other at this level:\n"
            f"{_variation_guidance(level)}\n\n"
            f"{notes}"
            f"Return ONLY valid JSON with two keys: 'prompts' (array of {n} positive prompt strings) and "
            f"'negative_prompts' (array of {n} negative prompts, one per positive). Negative prompts list "
            f"failures like: blurry, distorted, bad hands, watermark, airbrushed skin, CGI look. Adapt per "
            f"variant. Example: {{'prompts': ['a'], 'negative_prompts': ['blurry']}} No text outside JSON."
        )
        temperature = min(round(0.4 + (level - 1) * 0.07, 2), 1.0)
        from starlette.concurrency import run_in_threadpool
        data = await run_in_threadpool(
            (lambda: _ask_openai(text, mime, b64, n, temperature)) if gpt
            else (lambda: _ask_claude(text, mime, b64, n)))

        prompts = [p for p in data.get("prompts", []) if p and isinstance(p, str)]
        negatives = [p for p in data.get("negative_prompts", []) if p and isinstance(p, str)]
        if not prompts:
            raise ValueError("the model returned no usable prompts")
        while len(negatives) < len(prompts):
            negatives.append("blurry, low quality, distorted, watermark, bad anatomy, airbrushed, CGI")
        if is_photo:
            prompts = [p if "shot on professional camera" in p.lower() else f"{p}, {TAIL}"
                       for p in (q.strip().rstrip(",").strip() for q in prompts) if p]
        return {"prompts": prompts, "negative_prompts": negatives,
                "by": "gpt-4o" if gpt else "claude"}
    except Exception as e:                      # noqa: BLE001 - the page shows the message
        print("Variant prompt generation failed:", e)
        return {"error": str(e)}
