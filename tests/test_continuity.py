"""Colour from shot to shot: a clip matched to its keyframe when it finishes,
the cuts checked before the film is cut, and a drifting shot matched to the one
before it — through the Lab's own Rust, on the server."""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

TMP = tempfile.mkdtemp(prefix="cont-test-")
os.environ["STUDIO_DIR"] = os.path.join(TMP, "reels")
os.environ.update(STUDIO_ENV_FILE="", COMFY_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="",
                  STUDIO_PASSWORD="", STUDIO_ADMINS="")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from studio import continuity as C, jobs as J  # noqa: E402
from studio import routes as studio  # noqa: E402
from studio.app import app as APP  # noqa: E402

P = "/studio"
GUIDE = os.path.join(ROOT, "studio", "templates", "studio_guide.html")
SCRIPT = os.path.join(ROOT, "studio", "templates", "studio_app.js")
PAGE = os.path.join(ROOT, "studio", "templates", "studio.html")

FFMPEG = shutil.which("ffmpeg")


def scene(f=lambda c: c, size=(120, 60)) -> Image.Image:
    """A gradient with colour patches, as crates/scrim's own test draws it."""
    im = Image.new("RGB", size)
    px = im.load()
    w, h = size
    for y in range(h):
        for x in range(w):
            b = (x / (w - 1), y / (h - 1), ((x + y) % 40) / 39)
            c = f((0.15 + 0.7 * b[0], 0.1 + 0.6 * (b[0] * 0.5 + b[1] * 0.5), 0.2 + 0.5 * b[2]))
            px[x, y] = tuple(int(max(0.0, min(1.0, v)) * 255) for v in c)
    return im


def drifted(c):
    """The same scene darker, bluer and flatter, as an extension drifts."""
    return ((c[0] * 0.82 + 0.02) ** 1.12, (c[1] * 0.9) ** 1.05, (c[2] * 1.05 + 0.04) ** 0.95)


def png_bytes(im: Image.Image) -> bytes:
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def make_clip(path: str, im: Image.Image, seconds: float = 1.0) -> None:
    still = path + ".png"
    im.save(still)
    r = C.R.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-loop", "1", "-i", still, "-t", f"{seconds}",
                 "-r", "12", "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", path], timeout=60)
    assert r.returncode == 0 and os.path.exists(path), r.stderr


class Matching(unittest.TestCase):
    def test_a_match_is_made_only_when_it_is_worth_a_take(self):
        self.assertFalse(C.plan_match(0.4, 0.1)["match"])          # already matches
        self.assertFalse(C.plan_match(5.0, 4.8)["match"])          # the CDL wouldn't close the gap
        self.assertTrue(C.plan_match(7.7, 0.2)["match"])

    def test_a_drifted_frame_comes_back_to_the_reference(self):
        ref, take = C._rgba(scene(), C.SIZE), C._rgba(scene(drifted), C.SIZE)
        m = C.match([take], [ref])
        self.assertGreater(m["before"], 3.0)
        self.assertLess(m["after"], m["before"] / 3)
        self.assertEqual(sorted(m["cdl"]), ["offset", "power", "sat", "slope"])

    def test_the_cut_check_reads_colour_as_well_as_continuity(self):
        a, b = C._rgba(scene(), C.CUT_SIZE), C._rgba(scene(drifted), C.CUT_SIZE)
        self.assertEqual(C.compare(a, a)["verdict"], "match")
        with tempfile.TemporaryDirectory() as d:
            same, off = os.path.join(d, "a.png"), os.path.join(d, "b.png")
            scene().save(same)
            scene(drifted).save(off)
            r = C.check([(same, "image"), (same, "image"), (off, "image")])
        self.assertEqual([c["verdict"] for c in r["cuts"]], ["match", "drift"])
        self.assertEqual(r["off"], 1)
        self.assertGreaterEqual(r["cuts"][1]["colour"], C.COLOUR_DRIFT)
        self.assertIn("colour", " ".join(r["cuts"][1]["notes"]))

    @unittest.skipUnless(FFMPEG, "ffmpeg")
    def test_frames_of_a_clip_are_read_small(self):
        with tempfile.TemporaryDirectory() as d:
            clip = os.path.join(d, "c.mp4")
            make_clip(clip, scene(size=(640, 360)))
            frames = C.clip_frames(clip)
        self.assertEqual(len(frames), 3)
        self.assertTrue(all(f[1] == C.SIZE for f in frames), [f[1:] for f in frames])


class Board(unittest.TestCase):
    def test_a_board_matches_clips_to_keyframes_unless_switched_off(self):
        self.assertTrue(studio._clean_board({})["match"])
        self.assertTrue(studio._clean_board({"match": True})["match"])
        self.assertFalse(studio._clean_board({"match": False})["match"])


@unittest.skipUnless(FFMPEG, "ffmpeg")
class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(APP)
        studio._pump["started"] = True

    def setUp(self):
        self.reels = []

    def tearDown(self):
        # the Library counts every reel on disk: these leave none behind
        for reel in self.reels:
            shutil.rmtree(J.reel_dir(reel), ignore_errors=True)

    def reel(self):
        reel = self.c.post(f"{P}/reels", json={"name": "Cont"}).json()["id"]
        self.reels.append(reel)
        return reel

    def take(self, reel, kind, im, **fields):
        """A finished take with a real file in the reel."""
        name = f"t-{int(time.time() * 1000)}-{kind}.{'mp4' if kind == 'video' else 'png'}"
        path = J.out_path(reel, name)
        if kind == "video":
            make_clip(path, im)
        else:
            im.save(path)
        t = J.new_take(reel, 1, 0, kind=kind, model="m", label=kind, prompt="", aspect="16:9", request={}, settings={},
                       action="animate" if kind == "video" else "keyframe", **fields)
        return J.update_take(reel, t["id"], status="done", file=name, finished=time.time(), w=im.size[0], h=im.size[1])

    def wait(self, reel, tid, seconds=120):
        for _ in range(seconds * 4):
            t = J.get_take(reel, tid)
            if t.get("status") in ("done", "failed"):
                return t
            time.sleep(0.25)
        return J.get_take(reel, tid)

    def test_a_clip_is_matched_to_a_keyframe_as_a_take_on_the_same_card(self):
        reel = self.reel()
        key = self.take(reel, "image", scene(size=(320, 180)), board="card1", role="key")
        clip = self.take(reel, "video", scene(drifted, size=(320, 180)), board="card1", role="clip", anim=True)
        r = self.c.post(f"{P}/reels/{reel}/takes/{clip['id']}/match", json={"to": key["id"]}).json()
        self.assertTrue(r["match"]["match"], r)
        self.assertGreater(r["match"]["before"], r["match"]["after"])
        nt = r["take"]
        self.assertEqual((nt["action"], nt["label"]), ("grade", "Matched to keyframe"))
        self.assertEqual(J.get_take(reel, clip["id"])["match"]["to"], key["id"])
        raw = J.get_take(reel, nt["id"])
        self.assertEqual((raw["board"], raw["role"], raw.get("anim")), ("card1", "clip", True))
        self.assertEqual(raw["look"]["match_to"], key["id"])
        done = self.wait(reel, nt["id"])
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertTrue(os.path.exists(J.out_path(reel, done["file"])))
        # a clip that already matches makes no take
        again = self.c.post(f"{P}/reels/{reel}/takes/{clip['id']}/match", json={"to": clip["id"]}).json()
        self.assertIsNone(again["take"])
        self.assertEqual(again["match"]["why"], "already matches")

    def test_a_finished_card_clip_is_matched_to_its_keyframe_by_itself(self):
        reel = self.reel()
        key = self.take(reel, "image", scene(size=(320, 180)), board="card1", role="key")
        clip = self.take(reel, "video", scene(drifted, size=(320, 180)), board="card1", role="clip")
        self.c.put(f"{P}/reels/{reel}/board", json={"shots": [{"id": "card1", "scene": "x", "key": key["id"]}]})
        with mock.patch.object(studio, "_spawn", lambda fn, *a: fn(*a)):
            studio._match_after_done(reel, clip["id"])
        made = [t for t in J.list_takes(reel) if t.get("action") == "grade"]
        self.assertEqual(len(made), 1)
        self.assertEqual((made[0]["board"], made[0]["role"], made[0]["label"]), ("card1", "clip", "Matched to keyframe"))
        self.wait(reel, made[0]["id"])
        # switched off on the board: nothing is made
        self.c.put(f"{P}/reels/{reel}/board", json={"match": False, "shots": [{"id": "card1", "scene": "x", "key": key["id"]}]})
        clip2 = self.take(reel, "video", scene(drifted, size=(320, 180)), board="card1", role="clip")
        with mock.patch.object(studio, "_spawn", lambda fn, *a: fn(*a)):
            studio._match_after_done(reel, clip2["id"])
        self.assertEqual(len([t for t in J.list_takes(reel) if t.get("action") == "grade"]), 1)

    def test_the_cuts_are_checked_and_a_drifting_one_matched_to_the_shot_before(self):
        reel = self.reel()
        a = self.take(reel, "image", scene(size=(320, 180)))
        b = self.take(reel, "video", scene(drifted, size=(320, 180)), board="c2", role="clip")
        r = self.c.post(f"{P}/reels/{reel}/cuts/check", json={"takes": [a["id"], b["id"]], "continuous": [False, False]}).json()
        self.assertEqual(len(r["cuts"]), 1)
        cut = r["cuts"][0]
        self.assertEqual((cut["a"], cut["b"], cut["verdict"]), (a["id"], b["id"], "drift"))
        self.assertEqual(r["off"], 1)
        m = self.c.post(f"{P}/reels/{reel}/cuts/match", json={"pairs": [{"a": a["id"], "b": b["id"]}]}).json()
        self.assertEqual(len(m["takes"]), 1)
        self.assertEqual(m["takes"][0]["label"], "Matched to the shot before")
        self.assertEqual(J.get_take(reel, m["takes"][0]["id"])["board"], "c2")
        self.wait(reel, m["takes"][0]["id"])
        bad = self.c.post(f"{P}/reels/{reel}/cuts/check", json={"takes": [a["id"]]}).json()
        self.assertIn("error", bad)


class Page(unittest.TestCase):
    def test_the_board_the_guide_and_the_page_carry_it(self):
        js = open(SCRIPT, encoding="utf-8").read()
        for s in ('id="bMatch"', "Matching colour", "cuts/check", "cuts/match", "function cutCheckDialog", "function waitMatches", "cuts the spot when they land", "Match them, then I'll cut"):
            self.assertIn(s, js, s)
        page = open(PAGE, encoding="utf-8").read()
        self.assertIn(".cverdict", page)
        guide = open(GUIDE, encoding="utf-8").read()
        self.assertIn('id="continuity"', guide)
        self.assertIn("Match clips to keyframes", guide)


if __name__ == "__main__":
    unittest.main()
