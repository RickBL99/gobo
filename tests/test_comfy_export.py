"""Open in ComfyUI: every exported workflow checked against the partner nodes'
real signatures (tests/fixtures/comfy_nodes.json, from ComfyUI's own
define_schema() at a pinned commit): the inputs a node needs are there, every
dropdown value is one it offers (with the right JSON type: "8" is not 8), no
input is made up, and every link points at a node output of the right type.

    .venv/bin/python -m pytest tests/test_comfy_export.py
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

from studio import comfy_export as CX  # noqa: E402

with open(os.path.join(ROOT, "tests", "fixtures", "comfy_nodes.json")) as f:
    NODES = json.load(f)["nodes"]
TYPES = {"STRING": str, "INT": int, "FLOAT": (int, float), "BOOLEAN": bool}
TMP = tempfile.mkdtemp(prefix="comfy-export-")
FRAME = os.path.join(TMP, "key.png")
Image.new("RGB", (64, 36), (30, 40, 60)).save(FRAME)


def problems(wf: dict) -> list:
    out = []
    for nid, node in wf.items():
        cls, given = node["class_type"], node["inputs"]
        sig = NODES.get(cls)
        if not sig:
            out.append(f"{nid}: unknown node {cls}")
            continue
        def active(i):
            return all(given.get(k) == v for k, v in (i.get("when") or {}).items())
        live = [i for i in sig["inputs"] if active(i)]
        ids = {i["id"] for i in live}
        for k, v in given.items():
            spec = next((i for i in live if i["id"] == k), None)
            if spec is None:
                slot = next((i for i in live if i["id"].endswith(".<slot>") and k.startswith(i["id"][:-len("<slot>")])), None)
                if not slot:
                    out.append(f"{nid} {cls}: no input {k!r} (have {sorted(ids)})")
                continue
            if isinstance(v, list):                       # a link
                src = wf.get(v[0])
                if not src or v[1] >= len(NODES.get(src["class_type"], {}).get("outputs", [])):
                    out.append(f"{nid} {cls}.{k}: link to nothing {v}")
                elif NODES[src["class_type"]]["outputs"][v[1]] != spec["type"]:
                    out.append(f"{nid} {cls}.{k}: {NODES[src['class_type']]['outputs'][v[1]]} into {spec['type']}")
                continue
            if spec["type"] == "COMBO" and isinstance(spec.get("options"), list) and v not in spec["options"]:
                out.append(f"{nid} {cls}.{k}: {v!r} not in {spec['options'][:12]}")
            if spec["type"] in TYPES and not isinstance(v, TYPES[spec["type"]]) or (spec["type"] == "INT" and isinstance(v, bool)):
                out.append(f"{nid} {cls}.{k}: {v!r} isn't {spec['type']}")
            if spec["type"] in ("INT", "FLOAT") and isinstance(v, (int, float)):
                if spec.get("min") is not None and v < spec["min"] or spec.get("max") is not None and v > spec["max"]:
                    out.append(f"{nid} {cls}.{k}: {v} outside {spec.get('min')}..{spec.get('max')}")
        for i in live:
            if i["id"].endswith(".<slot>"):                # an image group: any slot fills it
                if i["required"] and not any(k.startswith(i["id"][:-len("<slot>")]) for k in given):
                    out.append(f"{nid} {cls}: missing required {i['id']}")
                continue
            # strict: the editor fills a missing widget from its default, an API run doesn't
            if i["required"] and i["id"] not in given:
                out.append(f"{nid} {cls}: missing required {i['id']}")
    return out


def take(model, kind="video", shot=1, letter="A", **req):
    return {"id": f"t{shot}{letter}", "model": model, "kind": kind, "shot": shot, "letter": letter, "status": "done",
            "prompt": "A lighthouse keeper lights the lamp as the storm hits.", "file": f"{shot:03d}{letter}.png",
            "request": {"aspect": "16:9", "resolution": "720p", "duration": 5, "audio": True, "seed": "123", **req}}


CASES = [
    take("ltx/ltx-2-5-fast"), take("ltx/ltx-2-5-pro", first_frame="key.png", resolution="1080p", duration=12),
    take("ltx/ltx-2-5-fast", first_frame="key.png", last_frame="key.png", aspect="9:16", duration=16),
    take("bfl/flux-3-image", kind="image", resolution="2k", refs=["key.png"]),
    take("bfl/flux-3-video"), take("bfl/flux-3-video", first_frame="key.png", duration=9),
    take("byteplus/dreamina-seedance-2-5-260628"), take("byteplus/dreamina-seedance-2-0-fast-260128", first_frame="key.png", resolution="1080p"),
    take("wan/wan3.0-video", first_frame="key.png", resolution="480p", duration=3), take("wan/wan3.0-video", refs=["key.png"]),
    take("veo/veo-3.1-fast-generate-001", duration=5), take("veo/veo-3.1-generate-001", first_frame="key.png", last_frame="key.png", aspect="9:16"),
    take("minimax/minimax-h3"), take("minimax/minimax-h3", first_frame="key.png", resolution="1080p", duration=20),
    take("kling/kling-v3-omni", aspect="4:5"), take("kling/kling-v3-omni", first_frame="key.png", last_frame="key.png"),
    take("vertexai/gemini-nano-banana-2.1", kind="image", resolution="4k", refs=["key.png", "key.png"]),
]


class Export(unittest.TestCase):
    def find(self, name):
        return FRAME if name else None

    def test_every_supported_model_exports_a_valid_workflow(self):
        for t in CASES:
            wf, files, skipped = CX.workflow([t], self.find)
            self.assertFalse(skipped, t["model"])
            self.assertEqual(problems(wf), [], f"{t['model']} {t['request']}")
            saves = [n for n in wf.values() if n["class_type"] in ("SaveVideo", "SaveImage")]
            self.assertEqual(len(saves), 1, t["model"])
            if t["request"].get("first_frame"):
                self.assertTrue(files, "the keyframe travels in input/")

    def test_a_film_wires_its_keyframes_into_its_clips(self):
        key = take("bfl/flux-3-image", kind="image", shot=1, letter="A")
        clip = take("wan/wan3.0-video", shot=1, letter="B", first_frame=key["file"])
        wf, files, _ = CX.workflow([clip, key], lambda n: None)
        self.assertEqual(problems(wf), [])
        wan = next(n for n in wf.values() if n["class_type"] == "Wan3ImageToVideoApi")
        flux = next(k for k, n in wf.items() if n["class_type"] == "Flux3ImageNode")
        self.assertEqual(wan["inputs"]["first_frame"], [flux, 0])
        self.assertEqual(files, {})

    def test_the_shipped_example_workflows_are_valid(self):
        found = 0
        for d in sorted(os.listdir(os.path.join(ROOT, "workflows"))):
            path = os.path.join(ROOT, "workflows", d, "workflow_api.json")
            if os.path.exists(path):
                with open(path) as f:
                    self.assertEqual(problems(json.load(f)), [], d)
                found += 1
        self.assertGreaterEqual(found, 2)

    def test_the_zip(self):
        data, skipped = CX.bundle([CASES[1], take("openai/gpt-image-2.5-sunburst", kind="image", shot=2)], self.find, "Last Watch")
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
        self.assertIn("workflow_api.json", names)
        self.assertTrue(any(n.startswith("input/gobo_001A_first_frame") for n in names), names)
        self.assertEqual(len(skipped), 1)
        self.assertIn("Left out", z.read("README.txt").decode())
        wf = json.loads(z.read("workflow_api.json"))
        self.assertTrue(all("class_type" in n for n in wf.values()), "every value has a class_type, so ComfyUI reads it as API JSON")
        self.assertTrue(CX.supported("ltx/ltx-2-5-pro") and not CX.supported("openai/gpt-image-2.5-sunburst"))


if __name__ == "__main__":
    unittest.main()
