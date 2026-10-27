"""Gobo's colour tools as ComfyUI nodes. A take comes in from Gobo (or any
file), a look is built the way Gobo's Lab builds one (a film stock and a
grade, then the sliders, a LUT, a shot match), and Gobo Grade puts every frame
through it with the same Rust Gobo uses, sound and all.

Two things flow between them: a take (GOBO_TAKE: the file and what it is) and
a look (GOBO_LOOK: the look as Gobo writes it, JSON-shaped).
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


def preset(film: str, grade: str) -> dict:
    """A stock and grade's own numbers, as the Gobo Look node shows them."""
    n = E.gobo().look({"film": film, "grade": grade})
    out = {name: n[KEY.get(name, name)] for name, *_ in SLIDERS}
    out["colour"] = {k: n[k] for k in ("shadows", "highlights", "bleach") if k in n}
    if n.get("mono"):
        out["colour"]["mono"] = n["mono"]
    return out


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
    """Any frame's look, read in Rust: tone, colour, grain, the nearest stock and grade."""
    CATEGORY = CAT
    RETURN_TYPES = ("GOBO_LOOK", "STRING")
    RETURN_NAMES = ("look", "what it saw")
    FUNCTION = "steal"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",)}}

    def steal(self, image):
        r = E.gobo().read(_to_rgba(image[0]))
        s = r["suggest"]
        near = ", ".join(f"{n['film']} + {n['grade']} {round(n['fit'] * 100)}%" for n in s.get("nearest", [])[:3])
        return (s["look"], f"{s.get('phrase', '')}\nNearest: {near}")


class GoboGrade:
    """Every frame of a take through a look, in Gobo's Rust: the grain moves
    frame to frame and the sound is kept. A still comes out as an image."""
    CATEGORY = CAT
    RETURN_TYPES = ("VIDEO", "IMAGE")
    RETURN_NAMES = ("video", "first frame")
    FUNCTION = "grade"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"take": ("GOBO_TAKE",), "look": ("GOBO_LOOK",)}}

    def grade(self, take, look):
        take = _take(take)
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
    "GoboLoadTake": GoboLoadTake, "GoboLook": GoboLook, "GoboLUT": GoboLUT, "GoboShotMatch": GoboShotMatch,
    "GoboStealLook": GoboStealLook, "GoboGrade": GoboGrade, "GoboGradeImages": GoboGradeImages,
    "GoboContinuity": GoboContinuity,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "GoboLoadTake": "Gobo Load Take", "GoboLook": "Gobo Look", "GoboLUT": "Gobo LUT", "GoboShotMatch": "Gobo Shot Match",
    "GoboStealLook": "Gobo Steal Look", "GoboGrade": "Gobo Grade", "GoboGradeImages": "Gobo Grade Images",
    "GoboContinuity": "Gobo Continuity",
}
