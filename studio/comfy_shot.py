"""The shot on the panel, opened in ComfyUI as Gobo's nodes.

"Open in ComfyUI" by the prompt opens the colourist's own ComfyUI with the Gobo
Director (comfyui/nodes.py) holding the panel's scene and every choice, its
prompt wired into the model's own ComfyUI node where it has one (the same
mapping Gobo's workflow export uses, studio/comfy_export.py), any starting
frame or reference fetched from Gobo by a signed link, and a save. A model
ComfyUI has no node for gets the Director and its prompt, to wire to any.
"""
import os
from typing import Dict, Optional

from . import comfy_colour as CC, comfy_export as CX, direction as D, jobs as J


def _label(group: str, oid) -> str:
    """The Director node shows each choice by its label."""
    for o in D.GROUPS[group]["options"]:
        if o["id"] == oid:
            return o["label"]
    return "Auto"


class _Linked(CX.Graph):
    """The export's graph, but a frame or reference is fetched from Gobo by a
    signed link (Gobo Load Take) instead of shipped in a zip."""

    def __init__(self, link):
        super().__init__()
        self.link = link

    def image(self, path: str, name: str, title: str):
        return [self.add("GoboLoadTake", {"link": self.link(path), "name": title}, title), 2]


def workflow(site: str, reel: Optional[str], b: Dict) -> Dict:
    site = site.rstrip("/")
    d = b.get("direction") if isinstance(b.get("direction"), dict) else {}
    video = b.get("mode") != "image"

    def where(name: str) -> Optional[str]:
        """A frame's file, as the run would find it: a take's file, or an upload."""
        if not reel or not name:
            return None
        n = os.path.basename(name)
        if os.path.isfile(J.out_path(reel, n)):
            return n
        if os.path.isfile(J.ref_path(reel, n)):
            return f"refs/{n}"
        return None

    g = _Linked(lambda signed: f"{site}/t/{CC.sign(reel, signed)}")
    director = g.add("GoboDirector", {"scene": str(b.get("scene") or ""), "shoot": "video" if video else "still",
                                      "cinematic": b.get("pmode") != "plain",
                                      **{grp: _label(grp, d.get(grp, "auto")) for grp in D.GROUPS}}, "Gobo Director")
    model = str(b.get("model") or "")
    title = "Gobo Director"
    if CX.supported(model):
        req = {k: b.get(k) for k in ("aspect", "resolution", "duration", "audio", "seed", "negative_prompt")}
        req.update({k: b.get(k) for k in ("first_frame", "last_frame") if b.get(k)})
        req["refs"] = [r for r in (b.get("refs") or []) if r]
        node = CX._shot(g, {"model": model, "prompt": "", "request": req}, where, "Shot")
        ins = g.nodes[node]["inputs"]
        key = "prompt" if "prompt" in ins else "model.prompt" if "model.prompt" in ins else None
        if key:
            ins[key] = [director, 0]
            out_kind = "image" if g.nodes[node]["class_type"] in ("Flux3ImageNode", "GeminiNanoBanana2V2") else "video"
            if out_kind == "video":
                g.add("SaveVideo", {"video": [node, 0], "filename_prefix": "gobo/shot", "format": "mp4"}, "Save")
            else:
                g.add("SaveImage", {"images": [node, 0], "filename_prefix": "gobo/shot"}, "Save")
            title = f"Gobo Director · {g.nodes[node].get('_meta', {}).get('title', '').split(' · ')[-1]}"
            return {"workflow": g.nodes, "title": title}
        del g.nodes[node]
    g.add("PreviewAny", {"source": [director, 0]}, "The prompt: wire it into any model's prompt")
    return {"workflow": g.nodes, "title": title}
