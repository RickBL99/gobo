"""Gobo's ComfyUI nodes (comfyui/): they run the very gobo.wasm and
direction.wasm the app does, grade a picture exactly as Gobo's server grades
it, and the Director writes the prompt Gobo would send.

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


def installer():
    spec = importlib.util.spec_from_file_location("gobo_nodes_install", os.path.join(NODES, "install.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Install(unittest.TestCase):
    """Nobody runs pip by hand: the pack installs wasmtime into ComfyUI's own Python on its first start."""

    def test_it_asks_for_what_requirements_txt_lists(self):
        with open(os.path.join(NODES, "requirements.txt")) as f:
            listed = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        self.assertEqual([spec for _, spec in installer().NEEDS], listed)

    def test_nothing_to_do_when_it_imports(self):
        calls = []
        self.assertEqual(installer().ensure((("json", "json"),), run=calls.append), [])
        self.assertEqual(calls, [])

    def test_a_missing_module_is_pip_installed_into_this_python(self):
        calls = []
        got = installer().ensure((("gobo_not_a_module", "gobo-not-a-module>=1"),), run=calls.append)
        self.assertEqual(got, ["gobo-not-a-module>=1"])
        self.assertEqual(calls, [[sys.executable, "-m", "pip", "install", "gobo-not-a-module>=1"]])

    def test_no_pip_gets_ensurepip_first(self):
        import subprocess
        calls = []

        def run(cmd):
            calls.append(cmd[1:3])
            if len(calls) == 1:
                raise subprocess.CalledProcessError(1, cmd)
        installer().ensure((("gobo_not_a_module", "x"),), run=run)
        self.assertEqual(calls, [["-m", "pip"], ["-m", "ensurepip"], ["-m", "pip"]])

    def test_when_it_cant_it_says_the_one_command(self):
        import subprocess

        def run(cmd):
            raise subprocess.CalledProcessError(1, cmd)
        with self.assertRaises(ImportError) as e:
            installer().ensure((("gobo_not_a_module", "wasmtime>=30"),), run=run)
        self.assertIn(f'"{sys.executable}" -m pip install "wasmtime>=30"', str(e.exception))


class Pack(unittest.TestCase):
    def test_the_nodes_run_the_apps_own_build(self):
        sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        self.assertEqual(sha(os.path.join(NODES, "gobo.wasm")), sha(os.path.join(ROOT, "studio", "static", "gobo.wasm")),
                         "comfyui/gobo.wasm is stale: run tools/build_wasm.sh")

    def test_the_director_runs_the_apps_compiler_and_words(self):
        sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        self.assertEqual(sha(os.path.join(NODES, "direction.wasm")), sha(os.path.join(ROOT, "studio", "static", "direction.wasm")),
                         "comfyui/direction.wasm is stale: run tools/build_wasm.sh")
        spec = importlib.util.spec_from_file_location("vocab", os.path.join(ROOT, "tools", "build_comfy_vocabulary.py"))
        v = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(v)
        with open(os.path.join(NODES, "direction.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), v.vocabulary(), "comfyui/direction.json is stale: run tools/build_wasm.sh")

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

    def test_the_director_writes_the_prompt_gobo_sends(self):
        from studio import direction as D
        E = engine()
        w = E.words()["groups"]
        label = lambda g, i: next(o["label"] for o in w[g]["options"] if o["id"] == i)
        shots = [("A lighthouse keeper climbs the stair as the storm hits.", {"shot": "wide", "move": "dolly", "lens": "anamorphic", "grade": "nordic"}, True),
                 ("Two kids race between apple trees.", {"body": "film16", "focal": "35", "light": "golden", "film": "portra", "look": "doc"}, False),
                 ("A cook tosses noodles over a wok.", {}, True)]
        for scene, d, video in shots:
            # the node shows labels; the compiler gets ids; the words are Gobo's
            mine = E.compile_shot(scene, {g: label(g, i) for g, i in d.items()}, video=video)
            self.assertEqual(mine["prompt"], D.compile_prompt(scene, d, video=video), scene)
            self.assertEqual(mine["direction"], d)
        self.assertEqual(E.choice("move", "Nonsense"), "auto")

    def test_a_drawing_directs_the_shot(self):
        import math
        E = engine()
        loop = [[320 + 150 * math.cos(t / 30 * 2 * math.pi), 180 + 80 * math.sin(t / 30 * 2 * math.pi), t * 40] for t in range(31)]
        walk = [[100 + 20 * i, 300, i * 30] for i in range(21)]
        r = E.read_drawing({"w": 640, "h": 360, "strokes": [{"pen": "camera", "label": "", "points": loop},
                                                             {"pen": "subject", "label": "the girl", "points": walk}]})
        self.assertEqual(r["move"], "orbit360")
        self.assertIn("The girl moves", r["action"])
        out = E.compile_shot("A girl dribbles a ball.", {"move": "Push in"}, drawn=r)
        self.assertEqual(out["direction"]["move"], "orbit360", "the drawn move wins over the picked one")
        self.assertIn("A girl dribbles a ball. The girl moves", out["prompt"])
        self.assertEqual(E.read_drawing({}), {"move": "", "action": "", "said": "Nothing drawn yet."})

    def test_a_sequence_is_cut_together_at_the_heros_size_and_rate(self):
        try:
            import av  # noqa: F401  (ComfyUI has PyAV)
        except ImportError:
            self.skipTest("PyAV (ComfyUI has it)")
        import subprocess
        import tempfile
        E = engine()
        d = tempfile.mkdtemp(prefix="gobo-seq-")
        clips = []
        for name, size, rate in (("a", "320x180", 24), ("b", "180x320", 30)):
            p = os.path.join(d, f"{name}.mp4")
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={size}:r={rate}:d=1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", p], check=True)
            clips.append(p)
        out = os.path.join(d, "film.mp4")
        made = E.grade_sequence(clips, [{"film": "portra"}, {"film": "portra", "grade": "teal"}], out, (320, 180), 24)
        info = E.video_info(out)
        # a 30 fps second is still a second: 24 + 24 frames, at the first shot's size
        self.assertEqual((made["frames"], info["w"], info["h"]), (48, 320, 180))
        self.assertAlmostEqual(info["seconds"], 2.0, delta=0.1)
        tall = np.zeros((320, 180, 4), np.uint8)
        self.assertEqual(E.crop_to(tall, 16 / 9).shape[:2], (101, 180))
        self.assertEqual(E.crop_to(np.zeros((180, 320, 4), np.uint8), 9 / 16).shape[:2], (180, 101))

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
