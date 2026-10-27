"""Gobo's Rust as ComfyUI nodes. The Gobo Director writes the shot for any
video or image model, the way a cinematographer would (Gobo's shot compiler);
Steal the Look reads any frame's look and the choices behind it; a take comes
in from Gobo (or any file), a look is built the way Gobo's Lab builds one (a
film stock and a grade, then the sliders, a LUT, a shot match), and Gobo Grade
puts every frame through it with the same Rust Gobo uses, sound and all.

Three things flow between them: a take (GOBO_TAKE: the file and what it is), a
look (GOBO_LOOK: the look as Gobo writes it, JSON-shaped) and direction
(GOBO_DIRECTION: the choices of a shot, {group: option id}).
"""
import json
import os
import time

import numpy as np
import torch

from . import engine as E

# the studio's stocks and grades (crates/scrim/src/look.rs FILMS and GRADES)
FILMS = ["clean", "portra", "t500", "d250", "bw400", "slide", "expired", "bleach", "instant", "vhs"]
GRADES = ["natural", "teal", "noir", "kodachrome", "neon", "desert", "nordic", "pastel"]
# the sliders Gobo's Lab and Colour page show: [key, min, max, step, default]
SLIDERS = [("exposure", -4.0, 4.0, 0.01, 0.0), ("contrast", 0.3, 3.0, 0.01, 1.0), ("pivot", 0.2, 0.9, 0.01, 0.57),
           ("saturation", 0.0, 3.0, 0.01, 1.0), ("warmth", -1.0, 1.0, 0.01, 0.0), ("tint", -1.0, 1.0, 0.01, 0.0),
           ("fade", 0.0, 0.5, 0.005, 0.0), ("white_point", 0.5, 1.2, 0.005, 1.0), ("grain", 0.0, 1.0, 0.01, 0.0),
           ("grain_size", 0.2, 6.0, 0.05, 1.4), ("halation", 0.0, 1.0, 0.01, 0.0), ("vignette", 0.0, 0.8, 0.01, 0.0)]
# a slider's name here → the look's own key
KEY = {"saturation": "sat", "warmth": "temp", "white_point": "white"}
# what the "colour" box holds: everything a look carries that has no slider
COLOUR_KEYS = ("shadows", "highlights", "mono", "bleach", "wheels", "curves", "cdl")
CAT = "Gobo"


def _to_rgba(img: torch.Tensor) -> np.ndarray:
    """One ComfyUI picture (H×W×3 floats) as RGBA bytes."""
    a = (img.clamp(0, 1).cpu().numpy() * 255.0 + 0.5).astype(np.uint8)
    return np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], axis=2)


def _to_image(rgba: np.ndarray) -> torch.Tensor:
    return torch.from_numpy(rgba[..., :3].astype(np.float32) / 255.0)


def _take(take):
    if not isinstance(take, dict) or not take.get("path"):
        raise ValueError("Connect a take (Gobo Load Take).")
    return take


def _file_of(video, name: str = "clip") -> str:
    """A ComfyUI VIDEO (from any video node) as a file Gobo's Rust can read:
    the file it came from when it's untouched, else one written once."""
    src = video.get_stream_source()
    trim = getattr(video, "get_active_trim_window", lambda: (0.0, 0.0))()
    if isinstance(src, str) and os.path.isfile(src) and not any(trim):
        return src
    import folder_paths
    path = os.path.join(folder_paths.get_temp_directory(), f"gobo-{name}-{int(time.time() * 1000)}.mp4")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    video.save_to(path)
    return path


def _as_take(take=None, video=None, name: str = "clip"):
    """A Gobo take, or one made from a VIDEO: the nodes take either."""
    if take is not None:
        return _take(take)
    if video is None:
        raise ValueError("Connect a take (Gobo Load Take) or a video (any video node).")
    return {"path": _file_of(video, name), "kind": "video", "name": name}


def _progress():
    from comfy.utils import ProgressBar
    bar = [None]

    def tick(done, total):
        if total:
            bar[0] = bar[0] or ProgressBar(total)
            bar[0].update_absolute(done, total)
    return tick


def preset(film: str, grade: str) -> dict:
    """A stock and grade's own numbers, as the Gobo Look node shows them."""
    n = E.gobo().look({"film": film, "grade": grade})
    out = {name: n[KEY.get(name, name)] for name, *_ in SLIDERS}
    out["colour"] = {k: n[k] for k in ("shadows", "highlights", "bleach") if k in n}
    if n.get("mono"):
        out["colour"]["mono"] = n["mono"]
    return out


class GoboDrawMove:
    """Draw the move on the frame, as in Gobo's Lab: the camera pen draws where
    the camera goes (a loop reads as an orbit, a line toward the subject as a
    push-in, a rise as a crane up…), the subject pen draws where they go and
    becomes the action. Gobo's Rust (crates/dingle) reads the strokes. Wire
    `drawn` into the Gobo Director; connect a Load Image to draw on the frame."""
    CATEGORY = CAT
    RETURN_TYPES = ("GOBO_DRAWING", "STRING")
    RETURN_NAMES = ("drawn", "reading")
    FUNCTION = "read"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"strokes": ("STRING", {"default": "", "tooltip": "The drawing (the canvas fills this in)"})},
                "optional": {"image": ("IMAGE", {"tooltip": "The frame to draw on (from Load Image)"})}}

    def read(self, strokes, image=None):
        try:
            body = json.loads(strokes) if strokes else {}
        except ValueError:
            body = {}
        r = E.read_drawing(body)
        return {"ui": {"text": [r["said"]]}, "result": ({"move": r["move"], "action": r["action"]}, r["said"])}


class GoboLoadTake:
    """A take from Gobo: its link (Open in ComfyUI fills it in), or a path."""
    CATEGORY = CAT
    RETURN_TYPES = ("GOBO_TAKE", "VIDEO", "IMAGE")
    RETURN_NAMES = ("take", "video", "first frame")
    FUNCTION = "load"
    DESCRIPTION = "A clip or still from Gobo, by the link Gobo gives it (kept in ComfyUI's input folder, under gobo/)."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"link": ("STRING", {"default": "", "tooltip": "The take's link from Gobo, or a file path"})},
                "optional": {"name": ("STRING", {"default": "", "tooltip": "What Gobo calls it (shown on the node)"})}}

    @classmethod
    def IS_CHANGED(cls, link, name=""):
        return link

    def load(self, link, name=""):
        path = E.fetch(link.strip())
        kind = "video" if E.is_video(path) else "image"
        video = None
        if kind == "video":
            from comfy_api.input_impl import VideoFromFile
            video = VideoFromFile(path)
        first = E.frames_at(path, [0.0], width=100000)[0]
        return ({"path": path, "kind": kind, "name": name or os.path.basename(path)}, video, _to_image(first)[None])


class GoboLook:
    """A look, as Gobo's Lab builds one: a film stock and a grade, then the
    sliders. Picking a stock or grade here fills the sliders with its numbers,
    as the Lab does; the sliders are the look."""
    CATEGORY = CAT
    RETURN_TYPES = ("GOBO_LOOK",)
    RETURN_NAMES = ("look",)
    FUNCTION = "build"
    DESCRIPTION = "Film stock, grade and the Lab's sliders. The colour box holds the rest of a look from Gobo: wheels, curves, a CDL, split tones."

    @classmethod
    def INPUT_TYPES(cls):
        req = {"film": (FILMS, {"default": "clean"}), "grade": (GRADES, {"default": "natural"}),
               "amount": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01, "tooltip": "How much of the look reaches the picture"})}
        for name, lo, hi, step, d in SLIDERS:
            req[name] = ("FLOAT", {"default": d, "min": lo, "max": hi, "step": step})
        req["colour"] = ("STRING", {"default": "{}", "multiline": True,
                                    "tooltip": "JSON: wheels, curves, cdl, shadows, highlights, mono, bleach (Gobo fills it in)"})
        return {"required": req}

    def build(self, film, grade, amount, colour="{}", **sliders):
        try:
            extra = json.loads(colour or "{}")
        except ValueError as e:
            raise ValueError(f"The colour box isn't JSON: {e}")
        look = {"film": film, "grade": grade, "amount": amount}
        look.update({k: v for k, v in extra.items() if k in COLOUR_KEYS})
        look.update({KEY.get(k, k): float(v) for k, v in sliders.items()})
        E.gobo().look(look)                      # says now, not at the grade, if something's wrong
        return (look,)


class GoboLUT:
    """A .cube on the look: after it (a creative LUT) or first (a camera's
    log-to-video LUT, or a LUT's rest when Gobo read it into the sliders)."""
    CATEGORY = CAT
    RETURN_TYPES = ("GOBO_LOOK",)
    RETURN_NAMES = ("look",)
    FUNCTION = "add"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"look": ("GOBO_LOOK",),
                             "lut": ("STRING", {"default": "", "tooltip": "A .cube: Gobo's link to it, or a file path"}),
                             "amount": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                             "where": (["after the look", "first"], {"default": "after the look"})},
                "optional": {"name": ("STRING", {"default": ""})}}

    def add(self, look, lut, amount, where, name=""):
        path = E.fetch(lut.strip(), ".cube")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        out = E.with_luts(E.gobo(), {**look, "lut3d": {"text": text, "mix": amount, "first": where == "first"}})
        return (out,)


class GoboShotMatch:
    """A take brought to a reference's colour before the grade, so the two cut
    together: Gobo's "Grade other takes to match", as a CDL on the look."""
    CATEGORY = CAT
    RETURN_TYPES = ("GOBO_LOOK", "STRING")
    RETURN_NAMES = ("look", "report")
    FUNCTION = "match"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"look": ("GOBO_LOOK",), "take": ("GOBO_TAKE",), "reference": ("GOBO_TAKE",)}}

    def match(self, look, take, reference):
        at = [0.04, 0.5, 0.96]
        m = E.gobo().match(E.frames_at(_take(take)["path"], at, 320), E.frames_at(_take(reference)["path"], at, 320))
        report = f"{take.get('name')} was {m['before']:.1f} from {reference.get('name')}'s colour; matched, {m['after']:.1f} (ΔE, under 2 is hard to see)"
        return ({**look, "cdl": m["cdl"]}, report)


class GoboStealLook:
    """Any frame's look, read in Rust: tone, colour, grain, the nearest stock
    and grade, and the choices a cinematographer would have made to get it
    (focus, composition, stock, grade), each with what gave it away. Wire
    `direction` into the Gobo Director to shoot new footage in that look, and
    `look` into Gobo Grade to grade it the same."""
    CATEGORY = CAT
    RETURN_TYPES = ("GOBO_LOOK", "STRING", "GOBO_DIRECTION")
    RETURN_NAMES = ("look", "what it saw", "direction")
    FUNCTION = "steal"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",)}}

    def steal(self, image):
        r = E.gobo().read(_to_rgba(image[0]))
        s = r["suggest"]
        near = ", ".join(f"{n['film']} + {n['grade']} {round(n['fit'] * 100)}%" for n in s.get("nearest", [])[:3])
        why = s.get("why") or {}
        saw = "\n".join([s.get("phrase", ""), f"Nearest: {near}"]
                        + [f"{E.words()['groups'][g]['title']}: {why[g]}" for g in why if g in E.words()["groups"]])
        return (s["look"], saw, dict(s.get("direction") or {}))


class GoboDirector:
    """Gobo's shot compiler, in Rust: the scene in plain words and the choices
    a cinematographer makes (camera, lens, focal length, aperture, framing,
    angle, move, light, film stock, look, grade, composition) become the prompt
    for any video or image model, framing first and the look last, the order
    the models follow best. It's the prompt Gobo itself sends.

    Connect `direction` (from Steal the Look) and the still's choices win for
    the groups it names. `look` is the film stock and grade, for Gobo Grade,
    so the footage is graded the way the prompt asked for it to look."""
    CATEGORY = CAT
    RETURN_TYPES = ("STRING", "GOBO_LOOK", "GOBO_DIRECTION")
    RETURN_NAMES = ("prompt", "look", "direction")
    FUNCTION = "direct"

    @classmethod
    def INPUT_TYPES(cls):
        w = E.words()
        req = {"scene": ("STRING", {"multiline": True, "default": "A lighthouse keeper climbs the iron spiral stair with a lantern as the storm hammers the windows.",
                                    "tooltip": "What the camera sees: who, where, what happens. Plain words; the choices below do the cinematography."}),
               "shoot": (["video", "still"], {"tooltip": "A clip gets its camera move; a still doesn't"}),
               "cinematic": ("BOOLEAN", {"default": True, "tooltip": "Add the words that keep a model filmic (Gobo's Cinematic mode)"})}
        for g in w["order"]:
            opts = w["groups"][g]["options"]
            want = w["defaults"].get(g, "auto")
            req[g] = ([o["label"] for o in opts], {"default": next((o["label"] for o in opts if o["id"] == want), "Auto"),
                                                   "tooltip": w["groups"][g]["title"]})
        return {"required": req, "optional": {"direction": ("GOBO_DIRECTION", {"tooltip": "From Steal the Look: its choices win"}),
                                              "drawn": ("GOBO_DRAWING", {"tooltip": "From Draw the Move: its camera move wins, its action follows the scene"})}}

    def direct(self, scene, shoot, cinematic, direction=None, drawn=None, **picks):
        if isinstance(direction, dict):
            picks.update({g: v for g, v in direction.items() if g in E.words()["groups"]})
        out = E.compile_shot(scene, picks, video=shoot == "video", cinematic=cinematic,
                             drawn=drawn if isinstance(drawn, dict) else None)
        d = out["direction"]
        look = {"film": d.get("film", "clean"), "grade": d.get("grade", "natural")}
        return {"ui": {"gobo_prompt": [{"segments": out["segments"], "from_still": sorted(direction or {})}]},
                "result": (out["prompt"], look, d)}


class GoboGrade:
    """Every frame of a take through a look, in Gobo's Rust: the grain moves
    frame to frame and the sound is kept. A still comes out as an image.
    Takes a Gobo take, or a video from any video node."""
    CATEGORY = CAT
    RETURN_TYPES = ("VIDEO", "IMAGE")
    RETURN_NAMES = ("video", "first frame")
    FUNCTION = "grade"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"look": ("GOBO_LOOK",)},
                "optional": {"take": ("GOBO_TAKE",), "video": ("VIDEO", {"tooltip": "A clip from any video node, instead of a take"})}}

    def grade(self, look, take=None, video=None):
        take = _as_take(take, video)
        g = E.gobo()
        look = E.with_luts(g, look)
        if take["kind"] != "video":
            out = g.grade(E.still(take["path"]), look, 7)
            return (None, _to_image(out)[None])
        import folder_paths
        from comfy_api.input_impl import VideoFromFile
        from comfy.utils import ProgressBar
        dst = os.path.join(folder_paths.get_temp_directory(), f"gobo-graded-{int(time.time() * 1000)}.mp4")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        bar = [None]

        def tick(done, total):
            if total:
                bar[0] = bar[0] or ProgressBar(total)
                bar[0].update_absolute(done, total)
        E.grade_video(take["path"], dst, look, tick)
        return (VideoFromFile(dst), _to_image(E.frames_at(dst, [0.0], width=100000)[0])[None])


class GoboSequenceMatch:
    """A film's shots made to cut together: every shot matched to the hero
    shot's colour (a CDL worked out from frames of both, in Rust), all graded
    with one look, and cut together in order as one silent film at the hero's
    size and frame rate. The report says how far each shot was from the hero
    before and after (ΔE: under 2 is hard to see), and Gobo's script
    supervisor checks every cut."""
    CATEGORY = CAT
    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("film", "report")
    FUNCTION = "cut"
    SHOTS = 8

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"shot_1": ("VIDEO",), "hero": ("INT", {"default": 1, "min": 1, "max": cls.SHOTS,
                                                                       "tooltip": "The shot the others are matched to"}),
                             "match_colour": ("BOOLEAN", {"default": True, "tooltip": "Bring every shot to the hero's colour first"})},
                "optional": {"look": ("GOBO_LOOK", {"tooltip": "One grade on all of them: from Gobo Look, Steal Look or the Director"}),
                             **{f"shot_{i}": ("VIDEO",) for i in range(2, cls.SHOTS + 1)}}}

    def cut(self, shot_1, hero, match_colour, look=None, **more):
        shots = [shot_1] + [more[f"shot_{i}"] for i in range(2, self.SHOTS + 1) if more.get(f"shot_{i}") is not None]
        paths = [_file_of(v, f"shot{i + 1}") for i, v in enumerate(shots)]
        hero = max(1, min(int(hero), len(paths)))
        g, look = E.gobo(), dict(look or {})
        at = [0.04, 0.5, 0.96]
        info = E.video_info(paths[hero - 1])
        aspect = info["w"] / info["h"]
        # what the film shows of each shot: a shot of another shape, cropped to the middle
        fit = lambda img: E.crop_to(img, aspect)
        lines, looks = [], []
        ref = [fit(f) for f in E.frames_at(paths[hero - 1], at, 320)] if match_colour else None
        for i, p in enumerate(paths, 1):
            lk, line = dict(look), f"Shot {i}: the hero" if i == hero else f"Shot {i}"
            if match_colour and i != hero:
                m = g.match([fit(f) for f in E.frames_at(p, at, 320)], ref)
                lk["cdl"] = m["cdl"]
                line += f": {m['before']:.1f} from the hero's colour, {m['after']:.1f} matched"
            si = E.video_info(p)
            if abs(si["w"] / si["h"] - aspect) > 0.02:
                line += f" (made {si['w']}×{si['h']}: cropped to the middle to fit)"
            lines.append(line)
            looks.append(lk)
        # every cut, as the film shows it: does the next shot belong after this one?
        for i in range(1, len(paths)):
            a = g.grade(fit(E.frames_at(paths[i - 1], [0.99], 640)[0]), E.with_luts(g, looks[i - 1]), 1)
            b = g.grade(fit(E.frames_at(paths[i], [0.0], 640)[0]), E.with_luts(g, looks[i]), 1)
            r = g.compare(a, b, False)
            notes = "; ".join(r.get("notes") or []) if isinstance(r, dict) else ""
            lines.append(f"Cut {i} → {i + 1}: {r.get('verdict', '?') if isinstance(r, dict) else r}{f' ({notes})' if notes else ''}")
        import folder_paths
        from comfy_api.input_impl import VideoFromFile
        dst = os.path.join(folder_paths.get_temp_directory(), f"gobo-film-{int(time.time() * 1000)}.mp4")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        made = E.grade_sequence(paths, looks, dst, (info["w"], info["h"]), info["fps"], _progress())
        lines.append(f"One film: {len(paths)} shots, {made['frames'] / made['fps']:.1f}s at {made['width']}×{made['height']}, {made['fps']:g} fps, silent.")
        report = "\n".join(lines)
        return {"ui": {"text": [report]}, "result": (VideoFromFile(dst), report)}


class GoboGradeImages:
    """Images (any ComfyUI picture or batch of frames) through a look."""
    CATEGORY = CAT
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "grade"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"images": ("IMAGE",), "look": ("GOBO_LOOK",)}}

    def grade(self, images, look):
        g = E.gobo()
        look = E.with_luts(g, look)
        return (torch.stack([_to_image(g.grade(_to_rgba(im), look, i)) for i, im in enumerate(images)]),)


class GoboContinuity:
    """Does B pick up where A stopped? Gobo's script supervisor, in Rust: A's
    last frame against B's first, on the grade (and the picture, when B continues A)."""
    CATEGORY = CAT
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("report",)
    FUNCTION = "check"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"a": ("GOBO_TAKE",), "b": ("GOBO_TAKE",),
                             "b_continues_a": ("BOOLEAN", {"default": False})}}

    def check(self, a, b, b_continues_a):
        last = E.frames_at(_take(a)["path"], [0.99], 640)[0]
        first = E.frames_at(_take(b)["path"], [0.0], 640)[0]
        r = E.gobo().compare(last, first, b_continues_a)
        text = f"{r.get('verdict', '?')}: " + "; ".join(r.get("notes") or []) if isinstance(r, dict) else str(r)
        return {"ui": {"text": [text]}, "result": (text,)}


NODE_CLASS_MAPPINGS = {
    "GoboDirector": GoboDirector, "GoboDrawMove": GoboDrawMove, "GoboLoadTake": GoboLoadTake, "GoboLook": GoboLook, "GoboLUT": GoboLUT, "GoboShotMatch": GoboShotMatch,
    "GoboStealLook": GoboStealLook, "GoboGrade": GoboGrade, "GoboGradeImages": GoboGradeImages,
    "GoboContinuity": GoboContinuity, "GoboSequenceMatch": GoboSequenceMatch,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "GoboDirector": "Gobo Director", "GoboDrawMove": "Gobo Draw the Move", "GoboLoadTake": "Gobo Load Take", "GoboLook": "Gobo Look", "GoboLUT": "Gobo LUT", "GoboShotMatch": "Gobo Shot Match",
    "GoboStealLook": "Gobo Steal Look", "GoboGrade": "Gobo Grade", "GoboGradeImages": "Gobo Grade Images",
    "GoboContinuity": "Gobo Continuity", "GoboSequenceMatch": "Gobo Sequence Match",
}
