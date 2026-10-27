"""Gobo's ComfyUI nodes (comfyui/): they run the very gobo.wasm the app does,
and grade a picture exactly as Gobo's server grades it.

    .venv/bin/python -m pytest tests/test_comfy_nodes.py

The checks that need numpy (ComfyUI always has it) skip without it; the nodes
themselves need ComfyUI, and are run in it by hand (see comfyui/README.md).
"""
import hashlib
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
NODES = os.path.join(ROOT, "comfyui")

try:
    import numpy as np
except ImportError:
    np = None


def engine():
    spec = importlib.util.spec_from_file_location("gobo_nodes_engine", os.path.join(NODES, "engine.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Pack(unittest.TestCase):
    def test_the_nodes_run_the_apps_own_build(self):
        sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        self.assertEqual(sha(os.path.join(NODES, "gobo.wasm")), sha(os.path.join(ROOT, "studio", "static", "gobo.wasm")),
                         "comfyui/gobo.wasm is stale: run tools/build_wasm.sh")

    def test_the_menus_are_the_studios_stocks_and_grades(self):
        src = open(os.path.join(NODES, "nodes.py")).read()
        look = open(os.path.join(ROOT, "crates", "scrim", "src", "look.rs")).read()
        for name in ("FILMS", "GRADES"):
            rust = look.split(f"pub const {name}")[1].split("[", 2)[2].split("]")[0]
            want = [x.strip().strip('"') for x in rust.split(",") if x.strip()]
            got = json.loads(src.split(f"\n{name} = ")[1].split("\n")[0])
            self.assertEqual(got, want, name)


@unittest.skipUnless(np, "numpy (ComfyUI has it)")
class Engine(unittest.TestCase):
    def test_a_picture_is_graded_as_gobos_server_grades_it(self):
        from studio import rust as R
        E = engine()
        rng = np.random.default_rng(3)
        px = rng.integers(0, 256, (48, 64, 4), dtype=np.uint8)
        px[..., 3] = 255
        look = {"film": "portra", "grade": "teal", "amount": 0.8, "grain": 0.3,
                "wheels": {"lift": [0, 0, 0.03, 0], "gamma": [0, 0, 0, 0], "gain": [0, 0, 0, 0], "offset": [0, 0, 0, 0]}}
        mine = E.gobo().grade(px, look, 5)
        g = R.Wasm("gobo.wasm")
        p = g.put(px.tobytes())
        R._grade(g, p, 64, 48, look, 5)
        theirs = np.frombuffer(bytes(g.mem.read(g.store, p[0], p[0] + p[1])), dtype=np.uint8).reshape(48, 64, 4)
        self.assertTrue(np.array_equal(mine, theirs), "the same Rust, the same pixels")

    def test_a_look_names_a_lut_by_its_text(self):
        E = engine()
        swap = "TITLE \"Swap\"\nLUT_3D_SIZE 2\n" + "".join(f"{b} {g} {r}\n" for b in (0, 1) for g in (0, 1) for r in (0, 1))
        g = E.gobo()
        look = E.with_luts(g, {"lut3d": {"text": swap, "mix": 1.0}})
        self.assertTrue(look["lut3d"]["id"].startswith("lut-") and "text" not in look["lut3d"])
        px = np.zeros((2, 2, 4), np.uint8)
        px[...] = (200, 120, 40, 255)
        out = g.grade(px, look, 1)
        self.assertTrue(out[0, 0, 2] > 180 and out[0, 0, 0] < 60, out[0, 0])
