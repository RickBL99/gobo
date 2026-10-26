"""Gobo's Rust in the page (crates/scrim, flag, dingle → studio/static/gobo.wasm),
run as shipped: the .wasm under Node, through the same gobo.js the page loads;
and the server running that same file (studio/rust.py) grades the same pixels.

    .venv/bin/python -m pytest tests/test_gobo_wasm.py

Skips without Node. Fails if the .wasm is older than its Rust source
(rebuild with tools/build_wasm.sh). The crates' own logic is tested by
`cargo test`; this checks the module, the ABI and the glue.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WASM = os.path.join(ROOT, "studio", "static", "gobo.wasm")
GLUE = os.path.join(ROOT, "studio", "static", "gobo.js")
CRATES = ["scrim", "flag", "dingle", "gobo-wasm"]

RUN = r"""
const fs = require("fs");
const LOOK = { film: "portra", grade: "teal", amount: 0.7, grain: 0.3, halation: 0.2, vignette: 0.3 };
const { makeGobo } = require(process.argv[1]);
const g = makeGobo(new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(process.argv[2])), {}));
const img = (w, h, f) => {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const c = f(x, y), i = (y * w + x) * 4; data.set([c[0], c[1], c[2], 255], i); }
  return { width: w, height: h, data };
};
const scene = (warm) => img(640, 360, (x, y) => {
  const bar = Math.round((360 - 640 / 2.39) / 2);
  if (y < bar || y >= 360 - bar) return [3, 3, 3];
  const l = 30 + 200 * (y - bar) / (360 - 2 * bar), t = x / 640 * 6.28;
  return [l * (1 + 0.2 * Math.sin(t)) + warm, l * (1 + 0.2 * Math.sin(t + 2.1)), l * (1 + 0.2 * Math.sin(t + 4.2)) - warm];
});
const out = {};
out.about = g.about();
let t = process.hrtime.bigint();
out.read = g.read(scene(0));
out.read_ms = Number(process.hrtime.bigint() - t) / 1e6;
const grey = img(320, 180, (x, y) => [200, 90, 40]);
t = process.hrtime.bigint();
out.grade = g.grade(grey, { grade: "noir" }, 1);
out.grade_ms = Number(process.hrtime.bigint() - t) / 1e6;
out.grey = Array.from(grey.data.slice(0, 4));
out.bad_look = g.grade(img(8, 8, () => [1, 2, 3]), { film: "nope" }, 1);
out.look = g.look({ film: "portra" });
out.cube = g.cube({ grade: "teal", title: "Teal" }, 17).split("\n").slice(0, 4);
out.cube_lines = g.cube({}, 17).split("\n").filter(l => /^[0-9]/.test(l)).length;
out.same = g.compare(scene(0), scene(0), true);
out.warm = g.compare(scene(0), scene(45), true);
const loop = []; for (let i = 0; i <= 60; i++) { const a = i / 60 * 6.283; loop.push([480 + 250 * Math.cos(a), 270 + 250 * Math.sin(a)]); }
out.loop = g.direct({ w: 960, h: 540, strokes: [{ pen: "camera", points: loop },
  { pen: "subject", label: "the keeper", points: [[100, 500], [500, 400], [900, 300]] }] });
out.junk = g.direct({ strokes: "no" });
// one frame graded in the page, for the server to grade too
const src = scene(0), mine = new Uint8ClampedArray(src.data);
g.grade(src, LOOK, 5);
fs.writeFileSync(process.argv[3] + "/src.rgba", mine);
fs.writeFileSync(process.argv[3] + "/graded.rgba", src.data);
process.stdout.write(JSON.stringify(out));
"""


def source_hash() -> str:
    h = hashlib.sha256()
    for c in CRATES:
        with open(os.path.join(ROOT, "crates", c, "Cargo.toml"), "rb") as f:
            h.update(f.read())
    for c in CRATES:
        src = os.path.join(ROOT, "crates", c, "src")
        for n in sorted(os.listdir(src)):
            if n.endswith(".rs"):
                with open(os.path.join(src, n), "rb") as f:
                    h.update(f.read())
    return h.hexdigest()


@unittest.skipUnless(shutil.which("node"), "Node runs the WebAssembly")
class GoboWasm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gobo-wasm-")
        r = subprocess.run(["node", "-e", RUN, GLUE, WASM, cls.tmp], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-2000:]
        cls.out = json.loads(r.stdout)

    def test_the_wasm_is_built_from_this_source(self):
        with open(WASM + ".src") as f:
            self.assertEqual(f.read().strip(), source_hash(), "gobo.wasm is stale: run tools/build_wasm.sh")

    def test_reading_a_scope_frame(self):
        r = self.out["read"]
        self.assertEqual(r["reading"]["frame"]["aspect_name"], "2.39:1")
        self.assertTrue(r["reading"]["frame"]["letterbox"])
        self.assertEqual(r["suggest"]["direction"]["lens"], "anamorphic")
        self.assertEqual(r["suggest"]["aspect"], "21:9")
        self.assertEqual(len(r["reading"]["palette"]), 5)
        self.assertTrue(r["suggest"]["phrase"].endswith("."))
        print(f"\nread 640×360 in {self.out['read_ms']:.1f} ms; graded 320×180 in {self.out['grade_ms']:.1f} ms")

    def test_grading_and_looks(self):
        self.assertEqual(self.out["grade"], {"ok": True})
        r, g, b, _ = self.out["grey"]
        self.assertTrue(r == g == b, self.out["grey"])
        self.assertIn("error", self.out["bad_look"])
        self.assertAlmostEqual(self.out["look"]["contrast"], 0.9, places=3)
        self.assertEqual(self.out["cube"][0], 'TITLE "Teal"')
        self.assertIn("LUT_3D_SIZE 17", self.out["cube"])
        self.assertEqual(self.out["cube_lines"], 17 ** 3)

    def test_continuity(self):
        self.assertEqual(self.out["same"]["verdict"], "match")
        self.assertNotEqual(self.out["warm"]["verdict"], "match")
        self.assertTrue(any("warmer" in n for n in self.out["warm"]["notes"]), self.out["warm"])

    def test_the_server_grades_what_the_page_grades(self):
        sys.path.insert(0, ROOT)
        from studio import rust as R
        with open(os.path.join(self.tmp, "src.rgba"), "rb") as f:
            src = f.read()
        with open(os.path.join(self.tmp, "graded.rgba"), "rb") as f:
            page = f.read()
        g = R.Wasm("gobo.wasm")
        px = g.put(src)
        R._grade(g, px, 640, 360, {"film": "portra", "grade": "teal", "amount": 0.7, "grain": 0.3,
                                   "halation": 0.2, "vignette": 0.3}, 5)
        server = bytes(g.mem.read(g.store, px[0], px[0] + px[1]))
        self.assertNotEqual(server, src)
        self.assertEqual(server, page, "wasmtime and V8 run one module: the same bytes")

    def test_drawing(self):
        d = self.out["loop"]
        self.assertEqual(d["camera"]["move"], "orbit360")
        self.assertTrue(d["action"].startswith("The keeper moves from the left foreground"), d["action"])
        self.assertIn("error", self.out["junk"])
        self.assertEqual(self.out["about"]["crates"], ["scrim", "flag", "dingle"])


if __name__ == "__main__":
    unittest.main()
