# =========================================================
# studio_direction.py – the shot-direction vocabulary for Gobo.
#
# The words live here; the Rust shot compiler (crates/direction) puts them
# together. segments() and compile_prompt() run it through studio/rust.py:
# the same direction.wasm the page previews with, so the prompt a shot shows
# is the prompt it sends.
#
# The studio began as a copy of the picker layout of Comfy's
# Cinematic Studio app (camera, framing, light, film, look, grade); the words
# are our own. Angle, movement and composition are groups of their own,
# because the camera move is the part a model most often ignores when it is
# buried mid-sentence.
#
# The camera choices add WORDS to the prompt. No image model simulates a lens;
# a 135mm and a 14mm are two different sentences, nothing more. That is still
# worth having: the words are the ones a cinematographer would use, in the
# order the models respond to best (framing first, the look last).
# =========================================================
from typing import Dict, List, Optional
import os

from . import rust

AUTO = {"id": "auto", "label": "Auto", "phrase": ""}


def _opts(rows) -> List[Dict[str, str]]:
    return [AUTO] + [{"id": i, "label": l, "phrase": p} for i, l, p in rows]


GROUPS: Dict[str, Dict] = {
    # ── camera ──────────────────────────────────────────────────────
    "body": {"title": "Body", "section": "camera", "options": _opts([
        ("digital", "Digital cinema", "a digital cinema camera with wide latitude"),
        ("large", "Large format", "a large-format cinema camera"),
        ("super35", "Super 35", "a Super 35 cinema camera"),
        ("film35", "35mm film", "35mm motion picture film"),
        ("film16", "16mm film", "grainy 16mm film"),
        ("handheld", "Handheld", "a shoulder-mounted documentary camera"),
        ("imax", "IMAX 65mm", "IMAX 65mm film camera"),
        ("phone", "Phone", "smartphone camera"),
        ("medium", "Medium format stills", "medium format stills camera"),
    ])},
    "lens": {"title": "Lens", "section": "camera", "options": _opts([
        ("prime", "Spherical", "clean spherical primes"),
        ("anamorphic", "Anamorphic", "anamorphic lenses with oval bokeh"),
        ("vintage", "Vintage", "soft vintage glass"),
        ("macro", "Macro", "a macro lens at close focus"),
        ("tilt", "Tilt-shift", "a tilt-shift lens with a thin slice in focus"),
        ("fisheye", "Fisheye", "fisheye lens"),
        ("zoom", "Long zoom", "long telephoto zoom lens"),
    ])},
    "focal": {"title": "Focal length", "section": "camera", "options": _opts(
        [(f, f"{f}mm", f"{f}mm") for f in ("14", "24", "35", "50", "85", "135")]
        + [("200", "200mm", "200mm")])},
    "aperture": {"title": "Aperture", "section": "camera", "options": _opts(
        [(a, f"f/{a}", f"f/{a}") for a in ("1.4", "2", "2.8", "4", "8")]
        + [("16", "f/16", "f/16, deep focus")])},

    # ── direction ───────────────────────────────────────────────────────────
    "shot": {"title": "Framing", "section": "direction", "options": _opts([
        ("xwide", "Extreme wide", "extreme wide shot, the subject small in a vast frame"),
        ("wide", "Wide", "wide shot taking in the whole scene"),
        ("medium", "Medium", "medium shot"),
        ("close", "Close-up", "close-up"),
        ("xclose", "Extreme close-up", "extreme close-up"),
        ("ots", "Over the shoulder", "over-the-shoulder shot"),
        ("low", "Low angle", "low angle looking up at the subject"),
        ("cowboy", "Cowboy", "cowboy shot, framed from mid-thigh up"),
        ("two", "Two-shot", "two-shot"),
        ("insert", "Insert", "insert shot of a detail"),
    ])},
    # its own group, so a framing and an angle combine
    "angle": {"title": "Angle", "section": "direction", "options": _opts([
        ("eye", "Eye level", "eye-level camera"),
        ("high", "High angle", "high angle looking down"),
        ("overhead", "Overhead", "overhead top-down view"),
        ("dutch", "Dutch tilt", "dutch angle"),
        ("worm", "Worm's eye", "worm's-eye view from the ground"),
        ("pov", "Point of view", "point-of-view shot"),
        ("aerial", "Aerial", "aerial view from high above"),
    ])},
    # video only: a still has no movement, and the models that ignore a
    # move written mid-scene follow one stated on its own.
    "move": {"title": "Movement", "section": "direction", "video": True, "options": _opts([
        ("static", "Locked off", "locked-off static camera"),
        ("push", "Push in", "slow push-in toward the subject"),
        ("pull", "Pull out", "slow pull-out revealing the scene"),
        ("track", "Tracking", "tracking shot moving alongside the subject"),
        ("dolly", "Dolly sideways", "lateral dolly move"),
        ("crane", "Crane up", "crane shot rising up"),
        ("orbit", "Orbit", "slow orbit around the subject"),
        ("handheld", "Handheld follow", "handheld camera following the action"),
        ("pan", "Pan", "slow pan across the scene"),
        ("tilt", "Tilt up", "tilt up from the ground to the subject"),
        ("whip", "Whip pan", "fast whip pan"),
        ("drone", "Drone flyover", "aerial drone flyover"),
        ("zoom", "Crash zoom", "sudden crash zoom"),
        ("vertigo", "Dolly zoom", "dolly zoom, vertigo effect"),
        # the signature moves (Gobo): what draw-to-direct reads strokes as, and
        # the named moves people come to AI video for
        ("arc", "Arc", "arc move curving around the subject"),
        ("orbit360", "360° orbit", "full 360-degree orbit around the subject"),
        ("bullet", "Bullet time", "bullet time, the moment frozen as the camera sweeps around the subject"),
        ("tiltdown", "Tilt down", "tilt down from the sky to the subject"),
        ("cranedown", "Crane down", "crane shot descending toward the subject"),
        ("fpv", "FPV dive", "FPV drone dive, fast and swooping low through the scene"),
        ("eyesin", "Eyes in", "rapid push-in that dives straight into the subject's eyes"),
        ("earth", "Earth zoom-out", "pull back from the subject and rise through the clouds until the curve of the earth shows"),
        ("snorri", "Snorricam", "snorricam rig, the actor's face locked in frame while the world swings around them"),
        ("headtrack", "Head tracking", "camera locked on the subject's head, close and steady as they move"),
        ("dutchroll", "Dutch roll", "camera rolls slowly into a dutch tilt"),
        ("rack", "Rack focus", "rack focus from the foreground to the subject"),
        ("robo", "Robo arm", "precise high-speed robotic arm move whipping around the subject"),
        ("hyperlapse", "Hyperlapse", "hyperlapse moving through the scene, time rushing past"),
    ])},
    "light": {"title": "Light", "section": "direction", "options": _opts([
        ("golden", "Golden hour", "low golden-hour sun"),
        ("overcast", "Overcast", "flat soft light under an overcast sky"),
        ("blue", "Blue hour", "deep blue twilight just after sunset"),
        ("night", "Practical night", "night lit only by practical lamps and signs"),
        ("neon", "Neon", "neon signs washing the scene in colour"),
        ("lowkey", "Low key", "low-key light, most of the frame in shadow"),
        ("silhouette", "Silhouette", "hard backlight turning the subject to a silhouette"),
        ("noon", "Hard noon", "hard midday sun, crisp shadows"),
        ("window", "Window light", "soft window light from the side"),
        ("candle", "Candlelight", "warm candlelight"),
        ("moon", "Moonlight", "cool moonlight"),
        ("fog", "Fog", "diffused light through fog"),
    ])},
    "film": {"title": "Film", "section": "direction", "options": _opts([
        ("clean", "Clean digital", "a clean digital image"),
        ("t500", "Tungsten 500T", "tungsten-balanced 500T film stock"),
        ("d250", "Daylight 250D", "daylight 250D film stock"),
        ("bw400", "Black & white 400", "black-and-white 400 film"),
        ("slide", "Slide film", "saturated reversal slide film"),
        ("expired", "Expired film", "expired film with shifted colour and lifted blacks"),
        ("bleach", "Bleach bypass", "a desaturated, contrasty bleach-bypass print"),
        ("portra", "Portra 400", "Kodak Portra 400 film"),
        ("instant", "Instant film", "instant film print"),
        ("vhs", "VHS", "VHS tape footage"),
    ])},
    "look": {"title": "Look", "section": "direction", "options": _opts([
        ("neonoir", "Neo-noir", "neo-noir mood"),
        ("western", "Western", "classic western mood"),
        ("scifi", "Science fiction", "science-fiction mood"),
        ("drama", "Period drama", "period-drama mood"),
        ("thriller", "Thriller", "tense thriller mood"),
        ("doc", "Documentary", "observational documentary feel"),
        ("road", "Road movie", "road-movie feel"),
        ("editorial", "Editorial", "magazine editorial look, styled wardrobe, crisp fashion lighting"),
        ("commercial", "Commercial", "polished commercial advertising look, bright, clean and glossy"),
        ("horror", "Horror", "horror film look, creeping dread, deep shadows, sickly colour"),
        ("musical", "Music video", "music video look, bold saturated coloured light and haze"),
    ])},
    "grade": {"title": "Grade", "section": "direction", "options": _opts([
        ("teal", "Teal and orange", "teal shadows against warm orange skin tones"),
        ("noir", "Noir", "a black-and-white grade"),
        ("kodachrome", "Kodachrome", "Kodachrome-style colour with deep reds and blues"),
        ("neon", "Magenta & cyan", "a magenta and cyan palette"),
        ("desert", "Desert", "a warm, dusty desert palette"),
        ("nordic", "Nordic", "a cold blue-grey palette"),
        ("pastel", "Pastel", "soft pastel palette"),
        ("natural", "Natural", "natural true-to-life colour"),
    ])},
    # stock buyers ask for copy space more than for any film stock
    "compose": {"title": "Composition", "section": "direction", "options": _opts([
        ("thirds", "Rule of thirds", "subject on the rule of thirds"),
        ("center", "Centred", "centred symmetrical composition"),
        ("lines", "Leading lines", "leading lines drawing the eye to the subject"),
        ("space", "Copy space", "generous empty negative space for copy"),
        ("frame", "Frame in frame", "subject framed by a doorway or window"),
        ("depth", "Layered depth", "foreground, midground and background layers"),
    ])},
}

# Each grade's tile: shadow, skin, foliage, sky and highlight, put through that
# grade by the Rust grader (crates/scrim), so the tile is what the grade does.
SWATCH_REFERENCE = ["#24201d", "#b98266", "#5a7346", "#86a9cc", "#eeeae2"]
_swatches: Dict[str, List[str]] = {}


def swatches() -> Dict[str, List[str]]:
    if not _swatches:
        _swatches["auto"] = SWATCH_REFERENCE
        for o in GROUPS["grade"]["options"][1:]:
            _swatches[o["id"]] = rust.swatch({"grade": o["id"]}, SWATCH_REFERENCE)
    return _swatches


CAMERA = ["body", "lens", "focal", "aperture"]
LOOKS = ["light", "film", "look", "grade"]

# The defaults: the frame a cinematographer would hand you if you said
# nothing — large format, anamorphic, a night street in neo-noir.
DEFAULTS = {"body": "large", "lens": "anamorphic", "focal": "50", "aperture": "2.8",
            "shot": "medium", "angle": "auto", "move": "auto", "light": "night",
            "film": "t500", "look": "neonoir", "grade": "teal", "compose": "auto"}

ASPECTS = [
    {"id": "21:9", "name": "Scope"}, {"id": "16:9", "name": "Widescreen"},
    {"id": "4:3", "name": "Academy"}, {"id": "3:2", "name": "Landscape photo"},
    {"id": "2:3", "name": "Portrait photo"}, {"id": "1:1", "name": "Square"},
    {"id": "4:5", "name": "Portrait social"}, {"id": "9:16", "name": "Vertical"},
]


# Starter shots: one click to a directed shot, each showing off a different
# corner of the pickers.
STARTERS = [
    {"id": "lighthouse", "label": "Lighthouse keeper in a storm", "aspect": "21:9",
     "scene": "A lighthouse keeper climbs the iron spiral stair with a lantern as the storm hammers the windows.",
     "direction": {"lens": "anamorphic", "focal": "24", "shot": "wide", "light": "night",
                   "film": "t500", "look": "drama", "grade": "nordic"}},
    {"id": "wok", "label": "Street cook, steam and neon", "aspect": "16:9",
     "scene": "A street cook tosses noodles over a roaring wok, steam rising into the market's neon signs.",
     "direction": {"body": "film35", "lens": "vintage", "focal": "50", "shot": "medium", "light": "neon",
                   "film": "t500", "look": "neonoir", "grade": "neon"}},
    {"id": "orchard", "label": "Kids in an orchard, late sun", "aspect": "3:2",
     "scene": "Two kids race between rows of apple trees, late sun flaring through the leaves.",
     "direction": {"body": "film16", "lens": "prime", "focal": "35", "shot": "wide", "light": "golden",
                   "film": "portra", "look": "doc", "grade": "kodachrome"}},
    {"id": "kitchen", "label": "Family breakfast, copy space left", "aspect": "3:2",
     "scene": "A father and his young daughter laugh over pancakes at a sunlit kitchen table.",
     "direction": {"body": "digital", "lens": "prime", "focal": "35", "aperture": "2", "shot": "medium",
                   "light": "window", "film": "clean", "look": "commercial", "grade": "natural",
                   "compose": "space"}},
    {"id": "surfer", "label": "Surfer at dawn, drone push", "aspect": "16:9", "video": True,
     "scene": "A lone surfer paddles out through glassy turquoise water as the sun breaks the horizon.",
     "direction": {"body": "digital", "lens": "prime", "focal": "24", "shot": "xwide", "angle": "aerial",
                   "move": "drone", "light": "golden", "film": "clean", "look": "commercial",
                   "grade": "natural"}},
]



def option(group: str, direction: Optional[Dict]) -> Dict[str, str]:
    """The chosen option of a group; an unknown or missing id is Auto, so a
    stale saved shot never breaks, it just says less."""
    want = (direction or {}).get(group)
    for o in GROUPS[group]["options"]:
        if o["id"] == want:
            return o
    return AUTO


def phrases(groups: List[str], direction: Optional[Dict]) -> List[str]:
    return [p for p in (option(g, direction)["phrase"] for g in groups) if p]


def segments(scene: str, direction: Optional[Dict] = None, enhance: bool = True,
             video: bool = False, refs: int = 0, colors=None, main_color=None,
             ref_names: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """The prompt in labelled pieces, so the page can show which control wrote
    which words: framing, scene, enhance, camera, movement, look, composition,
    references, palette. Written by the Rust shot compiler."""
    return rust.compile_shot({"scene": scene or "", "direction": direction or {}, "enhance": enhance,
                              "mode": "video" if video else "image", "refs": list(range(refs or 0)),
                              "colors": colors, "main_color": main_color, "ref_names": ref_names},
                             GROUPS)["segments"]


def compile_prompt(scene: str, direction: Optional[Dict] = None, **kw) -> str:
    return " ".join(s["text"] for s in segments(scene, direction, **kw))


SAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "samples")


def samples() -> Dict[str, Dict[str, str]]:
    """{group: {option id: url}} for every rendered sample tile
    (tools/build_samples.py); a picker without one draws its own."""
    out: Dict[str, Dict[str, str]] = {}
    try:
        names = os.listdir(SAMPLES_DIR)
    except OSError:
        return out
    for n in names:
        g, _, rest = n.partition("-")
        if g in GROUPS and rest.endswith(".webp"):
            v = int(os.path.getmtime(os.path.join(SAMPLES_DIR, n)))
            out.setdefault(g, {})[rest[:-5]] = f"/static/samples/{n}?v={v}"
    return out


def catalogue() -> Dict:
    """Everything the page needs to draw the pickers, from the one source."""
    return {"groups": GROUPS, "defaults": DEFAULTS, "aspects": ASPECTS,
            "swatches": swatches(), "starters": STARTERS, "samples": samples(),
            "order": {"camera": CAMERA, "direction": ["shot", "angle", "move", "light", "film",
                                                      "look", "grade", "compose"]}}
