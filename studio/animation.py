"""The Animation board: a film drawn and animated from references, not from a seed.

An animated film is the Board's pipeline (kit → shot list → keyframes → clips
→ the cut) with one difference that changes everything: nothing in it is
photographed. The style has to be stated and held, the characters have to be
designed once and drawn the same in every shot, the backgrounds are art that
sets the world, and the shots usually exist already as storyboard panels. So
this module adds, on top of the Board:

  * a style bible: a named style (cel, painterly, 3D CG…) or a style picture,
    written into every prompt;
  * character model sheets: each cast member's own reference drawings (as
    many as they have) consolidated into one in-style design, then a
    turnaround sheet with expressions, in two steps — the sheet is what every
    keyframe and clip is drawn from;
  * background plates: each place painted once as background art;
  * a cast line-up: every sheet on one labelled picture, because the models
    take four to six references and a scene with three characters, a
    background and a panel would otherwise not fit;
  * storyboard panels read into cards (what the camera sees, framing,
    movement, who is in it, the dialogue written on the panel), the panel
    itself riding on the keyframe as a composition reference;
  * clips drawn either from the keyframe (the Board's way) or straight from
    the references, for the models that hold a cast from references alone.

Why not the seed: the seed fixes the noise a model starts from, and the
models that matter here (Seedance, Kling, Grok, Nano Banana) either ignore it
or get a different picture from it as soon as the prompt changes. A face
stays the same from shot to shot only when every shot is drawn from the same
sheet. That is the whole design.

The reference picture a prompt points at is cited the way each model family
reads it ("@Image 1", "@Image1", "Image1", "image 1"), so the prompt can say
which picture is who.
"""
import base64
import hashlib
import io
import os
import re
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from . import direction as D

# ── the style bible ──────────────────────────────────────────────────────────
STYLES: List[Dict] = [
    {"id": "cel", "label": "2D cel anime",
     "phrase": "2D cel animation: clean confident ink lines, flat cel shading with one shadow tone, a limited palette, painted backgrounds, no photographic texture"},
    {"id": "painterly", "label": "Painterly 2D",
     "phrase": "hand-painted 2D animation: soft watercolour and gouache backgrounds with visible brushwork, characters drawn with simple clean lines and gentle cel shading"},
    {"id": "tv2d", "label": "Western TV 2D",
     "phrase": "bold-outline 2D television animation: thick clean outlines, flat saturated colour, simple geometric shapes, a clean vector look"},
    {"id": "cg", "label": "3D CG feature",
     "phrase": "stylised 3D CG animation: rounded appealing forms, soft subsurface skin, big expressive eyes, cinematic lighting, nothing photoreal"},
    {"id": "retro", "label": "Retro 80s–90s anime",
     "phrase": "1980s–90s cel anime: hand-inked lines, muted palette, slight film grain and halation, painted backgrounds, the look of animation shot on film"},
    {"id": "cutout", "label": "Paper cutout",
     "phrase": "paper cutout animation: layered flat paper shapes with visible paper texture, hard edges, small drop shadows between the layers"},
    {"id": "stopmo", "label": "Stop-motion",
     "phrase": "stop-motion puppet animation: handmade miniature sets, felt, clay and fabric textures, slightly stepped motion, shallow tabletop depth of field"},
    {"id": "ink", "label": "Ink and halftone",
     "phrase": "graphic ink-and-halftone look: heavy black ink lines, halftone shading, a few spot colours, the texture of a printed comic page"},
    {"id": "custom", "label": "From the style picture",
     "phrase": "the film's own drawn style, exactly as in the style reference"},
]
STYLE_BY_ID = {s["id"]: s for s in STYLES}

# what a finished frame must never carry: the words that pull a drawing model
# towards a photograph or a storyboard
NEGATIVE = "photograph, photorealistic, live action, text, captions, speech bubbles, panel borders, watermark, signature"
NEGATIVE_CG = "live action photograph, text, captions, speech bubbles, panel borders, watermark, signature"

# ── which models, and how each reads a reference ─────────────────────────────
# The models that keep a drawn character the same across shots, in the order
# the blade picks them when the person hasn't chosen. Catalogue ids; a model
# that isn't in the catalogue today is skipped.
KEYFRAME_MODELS = [
    ("vertexai/gemini-3-pro-image", "the steadiest with several references: a sheet, a plate, a panel and a style picture together"),
    ("nano-banana-pro", "the steadiest with several references: a sheet, a plate, a panel and a style picture together"),
    ("vertexai/gemini-nano-banana-2.1", "the same family, cheaper; six references"),
    ("nano-banana-2", "the same family, cheaper"),
    ("byteplus/seedream-5-0-pro-260628", "strong linework and colour, up to ten references"),
    ("seedream-5-pro", "strong linework and colour, up to ten references"),
    ("byteplus/seedream-5-0-260128", "strong linework and colour"),
    ("seedream-4.5", "strong linework and colour, cheap"),
    ("qwen/qwen-image-3.0-pro", "three references, good at following a sketch"),
    ("qwen-image-3", "three references, good at following a sketch"),
    ("openai/gpt-image-2.5-sunburst", "reads a storyboard panel well; slower"),
    ("gpt-image-2.5-sunburst", "reads a storyboard panel well; slower"),
]
CLIP_MODELS = [
    ("byteplus/dreamina-seedance-2-5-260628", "reference-to-video up to 15 s: the cast from the sheets, no keyframe needed; cannot mix references with a first frame"),
    ("seedance-2.5", "reference-to-video up to 30 s with up to 30 pictures: the cast from the sheets, no keyframe needed; cannot mix references with a first frame"),
    ("kling/kling-v3-omni", "takes the sheets AND the keyframe at once: the steadiest way to animate a keyframe"),
    ("kling/kling-video-o1", "as Kling 3 Omni, the earlier model"),
    ("wan2.7-r2v", "takes the sheets AND the keyframe at once; five pictures, up to 10 s"),
    ("kling-o1-reference-image-to-video", "reference-to-video from four pictures, 5 or 10 s"),
    ("xai/grok-imagine-video-1.5", "reference-to-video up to 15 s, good with retro and stylised looks; 720p with references"),
    ("grok-imagine-video-1.5-reference-to-video", "reference-to-video up to 15 s from seven pictures, good with retro and stylised looks; 720p"),
    ("minimax/minimax-h3", "reference-to-video, up to 15 s"),
    ("minimax-h3-reference-to-video", "reference-to-video from five pictures, up to 15 s"),
    ("minimax-h3-max-reference-to-video", "reference-to-video from nine pictures, up to 15 s"),
    ("seedance-2.0-fast", "reference-to-video from nine pictures, quick and cheap for drafts"),
    ("pixverse-v6-reference-to-video", "reference-to-video from ten pictures, cheap for drafts"),
    ("wan/wan2.7-r2v", "reference-to-video, five references, up to 10 s"),
    ("veo/veo-3.1-generate-001", "three references, or a keyframe; 8 s, with sound"),
    ("veo-3.1", "references, or a keyframe; 8 s, with sound"),
]
# Models that accept reference pictures together with a starting frame. The
# others take one or the other: give them the keyframe (it already carries the
# cast) and nothing else, or references and no frame.
REFS_WITH_FRAME = {"kling/kling-v3-omni", "kling/kling-video-o1", "wan2.7-r2v"}


def cite(model_id: str, n: int) -> str:
    """How this model's prompt points at its n-th reference picture."""
    m = (model_id or "").lower()
    if m.startswith(("byteplus/", "seedance")):
        return f"@Image {n}"
    if m.startswith(("kling", "xai/", "grok")):
        return f"@Image{n}"
    if m.startswith("wan"):
        return f"Image{n}"
    if "pixverse" in m:
        return f"@image{n}"
    return f"image {n}"


# ── reference planning ───────────────────────────────────────────────────────
# A model takes a handful of pictures. What goes in, in order of what the shot
# can least do without: the cast (each sheet, or the line-up when the sheets
# don't fit), the storyboard panel, the background plate, the style picture.
ROLES = ("key", "clip_from_key", "clip_from_refs", "hero", "sheet", "plate")


def _el(e: Dict, key: str) -> str:
    return os.path.basename(str(e.get(key) or ""))


def plan_refs(role: str, cap: int, board: Dict, card: Optional[Dict] = None, element: Optional[Dict] = None,
              refs_with_frame: bool = False) -> List[Dict]:
    """→ [{file, name, kind}] the model gets, within its cap, with kind one of
    sheet | lineup | panel | plate | style | original."""
    anim = board.get("anim") or {}
    els = {e["id"]: e for e in (board.get("elements") or []) if e.get("id")}
    style = next((e for e in els.values() if e.get("kind") == "style" and _el(e, "file")), None)
    out: List[Dict] = []

    def add(file, name, kind):
        if file and len(out) < cap and all(o["file"] != file for o in out):
            out.append({"file": file, "name": name, "kind": kind})

    if role in ("hero", "sheet", "plate"):
        e = element or {}
        room = cap - (1 if style else 0)
        if role == "sheet" and _el(e, "hero"):
            add(_el(e, "hero"), e.get("name") or "", "original")    # the consolidated design leads
        for f in (e.get("files") or [])[:max(0, room - len(out))]:
            add(os.path.basename(str(f)), e.get("name") or "", "original")
        if style:
            add(_el(style, "file"), style.get("name") or "Style", "style")
        return out[:cap]

    if role == "clip_from_key" and not refs_with_frame:
        return []                                  # the keyframe carries the cast
    card = card or {}
    picked = [els[i] for i in (card.get("elements") or []) if i in els]
    cast = [e for e in picked if e.get("kind") in ("cast", "product") and (_el(e, "sheet") or _el(e, "file"))]
    places = [e for e in picked if e.get("kind") == "place" and _el(e, "file")]
    panel = _el(card, "panel") if role in ("key", "clip_from_refs") else ""
    lineup = _el(anim, "lineup")
    others = (1 if panel else 0) + (1 if places else 0)
    # every sheet on its own when they fit beside the panel and the plate;
    # otherwise the line-up, one picture that names them all
    if cast and (len(cast) + others > cap) and lineup and len(cast) > 1:
        add(lineup, ", ".join(e.get("name") or "" for e in cast), "lineup")
    else:
        for e in cast:
            add(_el(e, "sheet") or _el(e, "file"), e.get("name") or "", "sheet")
    if panel:
        add(panel, "", "panel")
    for e in places[:1]:
        add(_el(e, "file"), e.get("name") or "", "plate")
    if style:
        add(_el(style, "file"), style.get("name") or "Style", "style")
    return out[:cap]


# ── the prompts ──────────────────────────────────────────────────────────────
def style_line(board: Dict) -> str:
    anim = board.get("anim") or {}
    s = STYLE_BY_ID.get(anim.get("style") or "cel") or STYLE_BY_ID["cel"]
    notes = str(anim.get("style_notes") or "").strip().rstrip(".")
    return s["phrase"] + (f"; {notes}" if notes else "")


def negative_for(board: Dict) -> str:
    return NEGATIVE_CG if (board.get("anim") or {}).get("style") in ("cg", "stopmo") else NEGATIVE


def _ref_lines(model_id: str, refs: List[Dict]) -> List[str]:
    """One sentence per reference, saying what it is and what to take from it."""
    lines = []
    for i, r in enumerate(refs, 1):
        c, n = cite(model_id, i), r.get("name") or ""
        k = r["kind"]
        if k == "sheet":
            lines.append(f"{c} is the character model sheet for {n}: draw {n} exactly as on the sheet — the same face, hair, costume, colours and proportions.")
        elif k == "lineup":
            lines.append(f"{c} is the cast line-up, each character labelled by name ({n}): draw each one exactly as shown there — the same faces, hair, costumes, colours and proportions.")
        elif k == "panel":
            lines.append(f"{c} is the storyboard panel for this shot: match its composition, staging, poses and camera angle, finished as a frame of the film, not a sketch.")
        elif k == "plate":
            lines.append(f"{c} is the background art for {n}: set the shot there, keeping its layout, palette, perspective and light.")
        elif k == "style":
            lines.append(f"{c} shows the film's drawn style: match its linework, shading, colour and finish.")
        elif k == "original":
            lines.append(f"{c} is a drawing of {n}: keep that design faithfully.")
    return lines


def _frame_phrase(card: Dict) -> str:
    return D.option("shot", {"shot": card.get("shot")}).get("phrase") or ""


def _move_phrase(card: Dict) -> str:
    return D.option("move", {"move": card.get("move")}).get("phrase") or ""


def prompt_for(role: str, model_id: str, board: Dict, refs: List[Dict], card: Optional[Dict] = None,
               element: Optional[Dict] = None) -> str:
    style = style_line(board)
    if role in ("hero", "sheet", "plate"):
        e = element or {}
        name, look = str(e.get("name") or "the character").strip(), str(e.get("look") or "").strip().rstrip(".")
        originals = [r for r in refs if r["kind"] == "original"]
        if role == "plate":
            body = (f"Background art plate for an animated film, in {style}. {name}" + (f": {look}" if look else "") +
                    ". A wide establishing view of the place with no characters and no text: a painted environment with "
                    "consistent perspective and one clear light, the kind of plate the characters are drawn over.")
        elif role == "hero":
            body = (f"Character design reference for an animated film, in {style}. {name}" + (f": {look}" if look else "") +
                    ". A single front three-quarter view from the knees up, neutral stance, looking a little past the "
                    "camera, on a plain white background with no text." +
                    (" Consolidate the drawings given into one definitive design, keeping their face, hair, costume and colours." if originals else ""))
        else:
            body = (f"Character model sheet for an animated film, in {style}. {name}" + (f": {look}" if look else "") +
                    ". One plain white sheet with no text, labels or borders: a full-body turnaround — front, three-quarter, "
                    "side and back views standing on one ground line at the same height — and below it four head-and-shoulders "
                    "expressions (neutral, happy, surprised, angry) and a small strip of the costume's colour swatches. "
                    "The same face, hair, costume, colours and proportions in every view.")
        return " ".join([body] + _ref_lines(model_id, refs))

    card = card or {}
    scene = str(card.get("scene") or "").strip().rstrip(".")
    frame, move = _frame_phrase(card), _move_phrase(card)
    parts = [f"A frame of an animated film, in {style}." if role == "key" else f"A shot from an animated film, in {style}."]
    if scene:
        parts.append(scene + ".")
    if frame:
        parts.append(frame[0].upper() + frame[1:] + ".")
    if role == "key":
        parts.append("One finished frame: clean, fully coloured, the characters on model, no text.")
    else:
        if move:
            parts.append(f"One continuous shot, camera movement: {move}.")
        else:
            parts.append("One continuous shot.")
        if role == "clip_from_key":
            parts.append("The clip starts on the frame given and carries its drawing style, characters and background through to the end; nothing turns photographic.")
        parts.append("The characters stay on model throughout: the same faces, costumes and proportions as their references, animated with clear readable poses.")
    parts += _ref_lines(model_id, refs)
    return " ".join(parts)


def plan(role: str, pm: Dict, board: Dict, card: Optional[Dict] = None, element: Optional[Dict] = None) -> Dict:
    """Everything one run needs: the prompt, the pictures in order, their
    names and kinds, and the negative prompt."""
    if role not in ROLES:
        raise ValueError("unknown role")
    cap = int(pm.get("refs") or 0)
    mid = pm.get("id") or ""
    refs = plan_refs(role, cap, board, card, element, refs_with_frame=mid in REFS_WITH_FRAME)
    note = ""
    if role == "clip_from_key" and not refs and cap:
        note = f"{pm.get('label', 'This model')} takes references or a starting frame, not both: the keyframe carries the cast."
    elif role in ("key", "clip_from_refs") and not cap:
        note = f"{pm.get('label', 'This model')} takes no reference pictures, so the cast can't reach it. Pick one that does."
    return {"prompt": prompt_for(role, mid, board, refs, card, element),
            "refs": [r["file"] for r in refs], "ref_names": [r["name"] for r in refs],
            "ref_kinds": [r["kind"] for r in refs], "negative": negative_for(board), "note": note}


# ── the cast line-up ─────────────────────────────────────────────────────────
def _font(size: int):
    for p in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def lineup(items: List[Tuple[str, str]], out_dir: str, height: int = 1024, gap: int = 32) -> str:
    """Every sheet side by side on white, each labelled with its name: one
    picture that carries the whole cast. items = [(name, path)]. Up to six
    on a row; more wrap. → the file name written in out_dir."""
    if not items:
        raise ValueError("nothing to line up")
    font = _font(max(24, height // 24))
    tiles = []
    for name, path in items[:12]:
        with Image.open(path) as im:
            im = im.convert("RGB")
            w = max(1, round(im.width * height / im.height))
            tiles.append((name, im.resize((w, height), Image.LANCZOS)))
    per_row = 6 if len(tiles) > 6 else len(tiles)
    rows = [tiles[i:i + per_row] for i in range(0, len(tiles), per_row)]
    label_h = font.size + 24 if hasattr(font, "size") else 48
    W = max(sum(t.width for _, t in r) + gap * (len(r) + 1) for r in rows)
    H = len(rows) * (height + label_h + gap) + gap
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    y = gap
    for r in rows:
        x = gap
        for name, t in r:
            canvas.paste(t, (x, y))
            if name:
                tw = draw.textlength(name, font=font) if hasattr(draw, "textlength") else len(name) * font.size * 0.6
                draw.text((x + (t.width - tw) / 2, y + height + 8), name, fill=(20, 20, 20), font=font)
            x += t.width + gap
        y += height + label_h + gap
    key = hashlib.sha1("|".join(f"{n}:{p}:{os.path.getmtime(p)}" for n, p in items).encode()).hexdigest()[:10]
    name = f"lineup-{key}.png"
    canvas.save(os.path.join(out_dir, name), "PNG", optimize=True)
    return name


# ── reading storyboard panels ────────────────────────────────────────────────
_READER_SYSTEM = """You are a storyboard supervisor on an animated film. You read hand-drawn storyboard panels and turn each one into a card the studio will draw as a keyframe and then animate, one card per panel, in the order given.

For each panel give:
- `scene`: what the camera sees, in present tense, 1-3 concrete sentences a model can draw from alone: who is in frame (use the kit's exact names when you recognise them), where they are, what they do, how the shot ends. Describe what is drawn, not the drawing (never "a sketch of"). No text or speech in `scene`.
- `shot`: the framing id, from the list given, matching the panel's framing.
- `move`: the camera movement id, from the list given; "static" when nothing says the camera moves. Read arrows and notes for pans, pushes and tracks.
- `duration`: how long the shot should run, in whole seconds, from the allowed values: long enough for the action and any dialogue (about 2.5 words a second).
- `elements`: the kit names that appear in the panel, exactly as written in the kit (people, props, the place).
- `start`: "continue" only when the panel picks up at the exact moment the previous panel ends, same place, the camera carrying on; otherwise "cut".
- `vo`: any dialogue or caption written on or under the panel, as spoken words, or "".
- `super`: on-screen text the panel calls for, or "".
- `notes`: anything else the panel says (sound effects, timing, action notes), or "".
Also:
- `cast`: anyone or anything that recurs across the panels and is NOT in the kit — give a short `name`, its `kind` (cast, product or place) and a `look`: one precise description to design them from (a person: age, build, face, hair, exactly what they wear; a place: layout, materials, light). At most 6. Use those names in `scene` and `elements` too.
- `title`: a working title, from the story the panels tell."""


def _image_block(path: str, max_side: int = 1568) -> Dict:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def read_storyboard(paths: List[str], names: List[str], durations: List[int], ask) -> Dict:
    """Panels → cards, through the vision model (`ask(system, content, schema)`
    is models._claude_json with images). Holds the answer to the lists given."""
    frames = [o["id"] for o in D.GROUPS["shot"]["options"]]
    moves = [o["id"] for o in D.GROUPS["move"]["options"]]
    durations = sorted({int(d) for d in durations if d}) or [4, 5, 6, 8, 10]
    kit = "\n".join(f"- {n}" for n in names) or "(empty)"
    content: List[Dict] = [{"type": "text", "text":
                            f"Framing ids: {', '.join(frames)}\nMovement ids: {', '.join(moves)}\n"
                            f"Allowed durations (seconds): {', '.join(map(str, durations))}\nKit:\n{kit}\n\n"
                            f"{len(paths)} panels follow, in story order. Give exactly one card per panel, in the same order."}]
    for i, p in enumerate(paths, 1):
        content.append({"type": "text", "text": f"Panel {i}:"})
        content.append(_image_block(p))
    card = {"type": "object", "additionalProperties": False,
            "required": ["scene", "shot", "move", "duration", "elements", "start", "vo", "super", "notes"],
            "properties": {"scene": {"type": "string"}, "shot": {"type": "string", "enum": frames},
                           "move": {"type": "string", "enum": moves}, "duration": {"type": "integer"},
                           "elements": {"type": "array", "items": {"type": "string"}},
                           "start": {"type": "string", "enum": ["cut", "continue"]},
                           "vo": {"type": "string"}, "super": {"type": "string"}, "notes": {"type": "string"}}}
    cast = {"type": "object", "additionalProperties": False, "required": ["name", "kind", "look"],
            "properties": {"name": {"type": "string"}, "kind": {"type": "string", "enum": ["cast", "product", "place"]},
                           "look": {"type": "string"}}}
    out = ask(_READER_SYSTEM, content, {
        "type": "object", "additionalProperties": False, "required": ["title", "cast", "cards"],
        "properties": {"title": {"type": "string"}, "cast": {"type": "array", "items": cast},
                       "cards": {"type": "array", "items": card}}})
    by = {n.lower(): n for n in names}
    cards = []
    for i, x in enumerate((out.get("cards") or [])[:len(paths)]):
        try:
            want = int(x.get("duration") or 5)
        except (TypeError, ValueError):
            want = 5
        cards.append({"scene": str(x.get("scene") or "").strip()[:2000],
                      "shot": x.get("shot") if x.get("shot") in frames else "auto",
                      "move": x.get("move") if x.get("move") in moves else "auto",
                      "duration": min(durations, key=lambda d: (abs(d - want), d)),
                      "elements": [str(e).strip() for e in (x.get("elements") or []) if str(e).strip()][:12],
                      "start": "cut" if i == 0 or x.get("start") != "continue" else "continue",
                      "vo": str(x.get("vo") or "").strip()[:600], "super": str(x.get("super") or "").strip()[:200],
                      "notes": str(x.get("notes") or "").strip()[:600], "panel": os.path.basename(paths[i])})
    for p in paths[len(cards):]:                       # a panel the reader skipped still gets its card
        cards.append({"scene": "", "shot": "auto", "move": "auto", "duration": durations[len(durations) // 2],
                      "elements": [], "start": "cut", "vo": "", "super": "", "notes": "", "panel": os.path.basename(p)})
    seen, new_cast = {n.lower() for n in names}, []
    for c in (out.get("cast") or [])[:6]:
        name, look = str(c.get("name") or "").strip()[:60], str(c.get("look") or "").strip()[:600]
        if name and look and name.lower() not in seen:
            seen.add(name.lower())
            new_cast.append({"name": name, "kind": c.get("kind") if c.get("kind") in ("cast", "product", "place") else "cast", "look": look})
    known = {**by, **{c["name"].lower(): c["name"] for c in new_cast}}
    for c in cards:
        c["elements"] = [known[e.lower()] for e in c["elements"] if e.lower() in known]
    return {"title": str(out.get("title") or "").strip()[:120], "cast": new_cast, "cards": cards}


# ── what the page is told ────────────────────────────────────────────────────
def catalogue(public: List[Dict]) -> Dict:
    have = {m["id"]: m for m in public}
    keys = [{"id": i, "label": have[i]["label"], "refs": have[i].get("refs") or 0, "why": why}
            for i, why in KEYFRAME_MODELS if i in have and have[i].get("refs")]
    clips = [{"id": i, "label": have[i]["label"], "refs": have[i].get("refs") or 0, "why": why,
              "refs_with_frame": i in REFS_WITH_FRAME, "first_frame": bool(have[i].get("first_frame") or have[i].get("i2v")),
              "cite": cite(i, 1)}
             for i, why in CLIP_MODELS if i in have]
    return {"styles": [{"id": s["id"], "label": s["label"]} for s in STYLES], "keyframe_models": keys,
            "clip_models": clips, "refs_with_frame": sorted(REFS_WITH_FRAME)}
