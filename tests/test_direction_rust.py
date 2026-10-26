"""The shot compiler is Rust, and only Rust (crates/direction → studio/static/
direction.wasm). The page runs it in the browser for its previews; the server
runs the same file through wasmtime (studio/rust.py) for the prompt it sends.

This pins its answers for 2,000 generated shots (tests/fixtures/prompts.json.gz:
first the retired Python compiler's, re-pinned since only when the words were
rewritten on purpose), through the server's own path, and
checks the page gives the server's answers on those and thousands more. The
page half runs the shipped .wasm under Node, through the same direction.js the
page loads, so what is tested is what runs.

    .venv/bin/python -m pytest tests/test_direction_rust.py

The page half skips without Node. Fails if the .wasm is older than its Rust
source (rebuild with tools/build_wasm.sh). A deliberate change to the words
re-pins the fixture: see PIN below.
"""
import gzip
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.update(STUDIO_ENV_FILE="", STUDIO_DIR=tempfile.mkdtemp(prefix="direction-test-"),
                  STUDIO_PASSWORD="", STUDIO_USERS="")

from studio import direction as D  # noqa: E402
from studio import routes  # noqa: E402

WASM = os.path.join(ROOT, "studio", "static", "direction.wasm")
GLUE = os.path.join(ROOT, "studio", "static", "direction.js")
PINNED = os.path.join(ROOT, "tests", "fixtures", "prompts.json.gz")
N = 4000
# To re-pin after changing the words on purpose:
#   .venv/bin/python -c "import tests.test_direction_rust as T; T.pin()"

SCENES = ["A boat.", "a lone rider crosses the dunes at dawn", "Two kids race down a pier!", "Is it raining?",
          "Ein Mann auf der Straße", "雨の夜の東京", "Café terrace, late", "ßpecial first letter", "x", "",
          "A dancer 💃 under neon", "ends with comma,", "  padded both sides  "]
SPACES = ["", " ", "\t", "\n", " ", "　", " ", "\u001f", "\u0085", "​"]
NAMES = ["Maya", "the bottle", "  Icebox  ", "", "   ", "Zoë", "BRAND™", None, 7, True]
COLORS = ["#AABBCC", "#a1b2c3", "#000000", "#abc", "red", "#GGGGGG", "#aabbcc\n", "aabbcc", 123, None, "#FFFFFF "]


def shot(rng: random.Random) -> dict:
    b = {}
    if rng.random() < .95:
        d = {}
        for g, spec in D.GROUPS.items():
            r = rng.random()
            if r < .55:
                d[g] = rng.choice(spec["options"])["id"]
            elif r < .65:
                d[g] = rng.choice(["zzz", "AUTO", "Wide", ""])
            elif r < .70:
                d[g] = rng.choice([50, None, True, 1.5])
        b["direction"] = d
    r = rng.random()
    if r < .85:
        b["scene"] = rng.choice(SPACES) + rng.choice(SCENES) + rng.choice(SPACES)
    elif r < .92:
        b["scene"] = None
    if rng.random() < .5:
        b["enhance"] = rng.choice([True, False, None, 0, 1, "", "no", [], {}])
    if rng.random() < .8:
        b["mode"] = rng.choice(["video", "image", "VIDEO", None])
    if rng.random() < .6:
        b["refs"] = rng.choice([None, [], ["a"], ["a", "b"], ["a", "b", "c"], [1, 2, 3, 4, 5]])
    if rng.random() < .6:
        b["ref_names"] = None if rng.random() < .1 else [rng.choice(NAMES) for _ in range(rng.randint(0, 6))]
    if rng.random() < .5:
        b["colors"] = None if rng.random() < .1 else [rng.choice(COLORS) for _ in range(rng.randint(0, 12))]
    if rng.random() < .5:
        b["main_color"] = rng.choice([None, -1, 0, 1, 2, 5, 11, True, False, 1.5, "1"])
    return b


def server_answer(b: dict):
    """What POST /studio/compile returns, by the route's own two steps."""
    kw = routes._shot_inputs(b)
    segs = D.segments(kw.pop("scene"), kw.pop("direction"), **kw)
    return {"prompt": " ".join(s["text"] for s in segs), "segments": segs}


RUN = r"""
const fs = require("fs");
const { makeCompiler } = require(process.argv[1]);      // node -e: the arguments start at argv[1]
const [wasmPath, inPath] = process.argv.slice(2);
const { catalogue, cases } = JSON.parse(fs.readFileSync(inPath, "utf8"));
const inst = new WebAssembly.Instance(new WebAssembly.Module(fs.readFileSync(wasmPath)), {});
const compile = makeCompiler(inst, catalogue);
const t = process.hrtime.bigint();
const out = cases.map(compile);
const us = Number(process.hrtime.bigint() - t) / 1000 / cases.length;
process.stdout.write(JSON.stringify({ out, us }));
"""


def pinned():
    with gzip.open(PINNED) as f:
        return json.loads(f.read())["cases"]


def pin():
    cases = [{"in": c["in"], "out": server_answer(c["in"])} for c in pinned()]
    with gzip.open(PINNED, "wb", compresslevel=9) as f:
        f.write(json.dumps({"from": "the Rust shot compiler, re-pinned", "cases": cases},
                           ensure_ascii=False, separators=(",", ":")).encode())


def page_answers(cases):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"catalogue": D.catalogue(), "cases": cases}, f)
        path = f.name
    try:
        r = subprocess.run(["node", "-e", RUN, GLUE, WASM, path], capture_output=True, text=True, timeout=120)
    finally:
        os.unlink(path)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


class OneCompiler(unittest.TestCase):
    def test_the_wasm_is_built_from_this_source(self):
        src = os.path.join(ROOT, "crates", "direction")
        h = hashlib.sha256()
        for name in ["Cargo.toml"] + sorted(f"src/{n}" for n in os.listdir(os.path.join(src, "src")) if n.endswith(".rs")):
            with open(os.path.join(src, name), "rb") as f:
                h.update(f.read())
        with open(WASM + ".src") as f:
            self.assertEqual(f.read().strip(), h.hexdigest(), "direction.wasm is stale: run tools/build_wasm.sh")

    def test_the_server_writes_what_the_python_wrote(self):
        bad = [(c["in"], c["out"], server_answer(c["in"])) for c in pinned()]
        bad = [x for x in bad if x[1] != x[2]]
        for c, w, g in bad[:3]:
            print("\nINPUT ", json.dumps(c, ensure_ascii=False), "\nPINNED", w["prompt"], "\nNOW   ", g["prompt"])
        self.assertEqual(len(bad), 0, f"{len(bad)} pinned shots read differently")

    @unittest.skipUnless(shutil.which("node"), "Node runs the WebAssembly")
    def test_the_page_says_what_the_server_says(self):
        rng = random.Random(20261025)
        cases, expected = [], []
        for c in pinned():
            cases.append(c["in"]); expected.append(c["out"])
        while len(cases) < N:
            b = shot(rng)
            try:
                want = server_answer(b)
            except Exception:                   # the server would answer 500; nothing to compare
                continue
            cases.append(b); expected.append(want)
        res = page_answers(cases)
        bad = [(c, w, g) for c, w, g in zip(cases, expected, res["out"]) if w != g]
        for c, w, g in bad[:3]:
            print("\nINPUT ", json.dumps(c, ensure_ascii=False), "\nSERVER", w["prompt"], "\nPAGE  ", g.get("prompt", g))
        self.assertEqual(len(bad), 0, f"{len(bad)} of {len(cases)} shots differ")
        print(f"\n{len(cases)} shots identical; {res['us']:.1f} µs a shot in the page")


if __name__ == "__main__":
    unittest.main(verbosity=1)
