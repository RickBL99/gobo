"""
The Animation board (studio/animation.py and its routes): the style bible,
how the pictures a model gets are chosen within its cap, the prompts that
cite them the way each model reads a reference, the cast line-up, the
storyboard reader, and the Grok and Seedream reference wiring. Hermetic:
Router, its storage and the vision model are mocked; PIL draws for real.

    python3 tests/test_animation.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

TMP = tempfile.mkdtemp(prefix="anim-test-")
os.environ["STUDIO_DIR"] = os.path.join(TMP, "reels")
os.environ.update(STUDIO_ENV_FILE="", COMFY_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="",
                  STUDIO_PASSWORD="", STUDIO_USERS="", STUDIO_PREFIX="/studio")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from studio import animation as A, comfy, jobs as J, models as M  # noqa: E402
from studio import routes as studio  # noqa: E402
from studio.app import app as APP  # noqa: E402

P = "/studio"


def png(size=(320, 240), color=(200, 120, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def board(**kw):
    b = {"anim": {"on": True, "style": "cel", "style_notes": "", "clips_from": "keyframe", "lineup": "lineup-1.png"},
         "elements": [
             {"id": "maya", "name": "Maya", "kind": "cast", "file": "sheet-maya.png", "sheet": "sheet-maya.png", "hero": "hero-maya.png", "files": ["d1.png", "d2.png"], "look": "a girl of ten"},
             {"id": "tom", "name": "Tom", "kind": "cast", "file": "sheet-tom.png", "sheet": "sheet-tom.png"},
             {"id": "dog", "name": "The dog", "kind": "cast", "file": "sheet-dog.png", "sheet": "sheet-dog.png"},
             {"id": "cafe", "name": "The café", "kind": "place", "file": "plate-cafe.png"},
             {"id": "sty", "name": "Style", "kind": "style", "file": "style.png"}]}
    b.update(kw)
    return b


CARD = {"id": "c1", "scene": "Maya waves to Tom across the café", "shot": "medium", "move": "push", "duration": 5,
        "elements": ["maya", "tom", "cafe"], "panel": "panel-1.png", "start": "cut"}


class Refs(unittest.TestCase):
    def test_a_keyframe_takes_the_sheets_then_the_panel_the_plate_and_the_style(self):
        refs = A.plan_refs("key", 6, board(), CARD)
        self.assertEqual([(r["kind"], r["file"]) for r in refs],
                         [("sheet", "sheet-maya.png"), ("sheet", "sheet-tom.png"), ("panel", "panel-1.png"),
                          ("plate", "plate-cafe.png"), ("style", "style.png")])

    def test_the_lineup_stands_in_when_the_sheets_do_not_fit(self):
        card = dict(CARD, elements=["maya", "tom", "dog", "cafe"])
        refs = A.plan_refs("key", 4, board(), card)
        self.assertEqual([r["kind"] for r in refs], ["lineup", "panel", "plate", "style"])
        self.assertEqual(refs[0]["name"], "Maya, Tom, The dog")
        # with room, every sheet goes in on its own
        self.assertEqual([r["kind"] for r in A.plan_refs("key", 6, board(), card)], ["sheet", "sheet", "sheet", "panel", "plate", "style"])
        # no line-up drawn yet: the sheets go in as far as they fit
        b = board(); b["anim"]["lineup"] = ""
        self.assertEqual([r["kind"] for r in A.plan_refs("key", 4, b, card)], ["sheet", "sheet", "sheet", "panel"])

    def test_a_clip_from_the_keyframe_gets_pictures_only_where_the_model_takes_both(self):
        self.assertEqual(A.plan_refs("clip_from_key", 4, board(), CARD), [])
        refs = A.plan_refs("clip_from_key", 4, board(), CARD, refs_with_frame=True)
        self.assertEqual([r["kind"] for r in refs], ["sheet", "sheet", "plate", "style"], "no panel: the frame is the composition")

    def test_a_character_is_built_from_their_own_drawings_and_the_style(self):
        maya = board()["elements"][0]
        self.assertEqual([r["kind"] for r in A.plan_refs("hero", 6, board(), element=maya)], ["original", "original", "style"])
        self.assertEqual([(r["kind"], r["file"]) for r in A.plan_refs("sheet", 3, board(), element=maya)],
                         [("original", "hero-maya.png"), ("original", "d1.png"), ("style", "style.png")], "the design leads, then the drawings that fit")
        self.assertEqual(A.plan_refs("plate", 2, board(), element=board()["elements"][3]), [{"file": "style.png", "name": "Style", "kind": "style"}])

    def test_nothing_goes_in_twice_and_a_cap_of_zero_gives_nothing(self):
        self.assertEqual(A.plan_refs("key", 0, board(), CARD), [])
        b = board(); b["elements"][1]["sheet"] = "sheet-maya.png"; b["elements"][1]["file"] = "sheet-maya.png"
        self.assertEqual(len(A.plan_refs("key", 6, b, CARD)), 4)


class Prompts(unittest.TestCase):
    def test_each_family_cites_a_picture_its_own_way(self):
        self.assertEqual(A.cite("byteplus/dreamina-seedance-2-5-260628", 2), "@Image 2")
        self.assertEqual(A.cite("kling/kling-v3-omni", 1), "@Image1")
        self.assertEqual(A.cite("xai/grok-imagine-video-1.5", 3), "@Image3")
        self.assertEqual(A.cite("wan/wan2.7-r2v", 1), "Image1")
        self.assertEqual(A.cite("vertexai/gemini-3-pro-image", 4), "image 4")

    def test_a_keyframe_prompt_says_the_style_the_scene_and_what_each_picture_is(self):
        pm = {"id": "vertexai/gemini-3-pro-image", "label": "Nano Banana Pro", "refs": 6}
        out = A.plan("key", pm, board(), CARD)
        p = out["prompt"]
        self.assertTrue(p.startswith("A frame of an animated film, in 2D cel animation"))
        self.assertIn("Maya waves to Tom across the café.", p)
        self.assertIn("Medium shot.", p)
        self.assertIn("image 1 is the character model sheet for Maya", p)
        self.assertIn("image 3 is the storyboard panel", p)
        self.assertIn("image 4 is the background art for The café", p)
        self.assertIn("image 5 shows the film's drawn style", p)
        self.assertEqual(out["refs"], ["sheet-maya.png", "sheet-tom.png", "panel-1.png", "plate-cafe.png", "style.png"])
        self.assertEqual(out["ref_kinds"], ["sheet", "sheet", "panel", "plate", "style"])
        self.assertIn("photorealistic", out["negative"])
        self.assertEqual(out["note"], "")

    def test_a_clip_prompt_moves_and_holds_the_cast(self):
        pm = {"id": "byteplus/dreamina-seedance-2-5-260628", "label": "Seedance 2.5", "refs": 4}
        b = board(); b["anim"]["clips_from"] = "refs"
        out = A.plan("clip_from_refs", pm, b, CARD)
        self.assertIn("One continuous shot, camera movement: slow push-in toward the subject.", out["prompt"])
        self.assertIn("@Image 1 is the character model sheet for Maya", out["prompt"])
        self.assertIn("stay on model", out["prompt"])
        key = A.plan("clip_from_key", pm, b, CARD)
        self.assertEqual(key["refs"], [], "Seedance takes references or a frame, not both")
        self.assertIn("The clip starts on the frame given", key["prompt"])
        self.assertIn("the keyframe carries the cast", key["note"])
        kling = A.plan("clip_from_key", {"id": "kling/kling-v3-omni", "label": "Kling 3 Omni", "refs": 4}, b, CARD)
        self.assertEqual(kling["ref_kinds"], ["sheet", "sheet", "plate", "style"])

    def test_style_notes_and_the_cg_negative(self):
        b = board(); b["anim"].update(style="cg", style_notes="chunky clay look")
        out = A.plan("plate", {"id": "vertexai/gemini-3-pro-image", "refs": 6}, b, element=b["elements"][3])
        self.assertIn("stylised 3D CG animation", out["prompt"])
        self.assertIn("; chunky clay look", out["prompt"])
        self.assertTrue(out["prompt"].startswith("Background art plate"))
        self.assertNotIn("photorealistic,", out["negative"])

    def test_a_sheet_prompt_asks_for_the_turnaround_and_keeps_the_drawings(self):
        out = A.plan("sheet", {"id": "vertexai/gemini-3-pro-image", "refs": 6}, board(), element=board()["elements"][0])
        self.assertIn("Character model sheet", out["prompt"])
        self.assertIn("Maya: a girl of ten", out["prompt"])
        self.assertIn("front, three-quarter, side and back", out["prompt"])
        self.assertIn("image 1 is a drawing of Maya: keep that design faithfully.", out["prompt"])
        self.assertEqual(out["refs"], ["hero-maya.png", "d1.png", "d2.png", "style.png"])

    def test_an_unknown_role_is_refused(self):
        with self.assertRaises(ValueError):
            A.plan("poster", {"id": "x", "refs": 1}, board())


class Lineup(unittest.TestCase):
    def test_sheets_side_by_side_and_labelled(self):
        d = tempfile.mkdtemp(dir=TMP)
        items = []
        for i, c in enumerate([(220, 40, 40), (40, 200, 60), (40, 60, 220)]):
            p = os.path.join(d, f"s{i}.png"); Image.new("RGB", (300 + 40 * i, 400), c).save(p); items.append((f"Name {i}", p))
        name = A.lineup(items, d, height=200)
        self.assertTrue(name.startswith("lineup-") and name.endswith(".png"))
        with Image.open(os.path.join(d, name)) as im:
            self.assertGreater(im.width, 3 * 150)
            self.assertEqual(im.getpixel((2, 2)), (255, 255, 255))
            self.assertEqual(im.getpixel((40, 40)), (220, 40, 40), "the first sheet, scaled to the row height")
        self.assertEqual(A.lineup(items, d, height=200), name, "named by content: the same sheets give the same file")
        with self.assertRaises(ValueError):
            A.lineup([], d)


class Reader(unittest.TestCase):
    def test_panels_become_cards_held_to_the_lists(self):
        d = tempfile.mkdtemp(dir=TMP)
        paths = []
        for i in range(3):
            p = os.path.join(d, f"p{i}.png"); Image.new("RGB", (1200, 700), (250, 250, 250)).save(p); paths.append(p)
        seen = {}

        def ask(system, content, schema):
            seen["images"] = sum(1 for c in content if c.get("type") == "image")
            seen["text"] = "\n".join(c["text"] for c in content if c.get("type") == "text")
            return {"title": "The café", "cast": [{"name": "Tom", "kind": "cast", "look": "a boy"}, {"name": "Maya", "kind": "cast", "look": "dup"}],
                    "cards": [{"scene": "Maya enters.", "shot": "wide", "move": "static", "duration": 7, "elements": ["maya", "Tom"], "start": "continue", "vo": "Hi!", "super": "", "notes": ""},
                              {"scene": "Tom turns.", "shot": "nope", "move": "push", "duration": 99, "elements": ["Nobody"], "start": "continue", "vo": "", "super": "", "notes": "SFX: bell"}]}
        out = A.read_storyboard(paths, ["Maya"], [4, 5, 6, 8, 10], ask)
        self.assertEqual(seen["images"], 3)
        self.assertIn("Kit:\n- Maya", seen["text"])
        self.assertEqual(out["title"], "The café")
        self.assertEqual([c["name"] for c in out["cast"]], ["Tom"], "a name already in the kit isn't cast again")
        c = out["cards"]
        self.assertEqual(len(c), 3, "a panel the reader skipped still gets a card")
        self.assertEqual((c[0]["start"], c[0]["duration"], c[0]["elements"], c[0]["panel"]), ("cut", 6, ["Maya", "Tom"], "p0.png"))
        self.assertEqual((c[1]["shot"], c[1]["move"], c[1]["duration"], c[1]["elements"], c[1]["start"]), ("auto", "push", 10, [], "continue"))
        self.assertEqual((c[2]["scene"], c[2]["panel"]), ("", "p2.png"))


class Wiring(unittest.TestCase):
    """Grok Imagine Video 1.5 and Seedream take their references now."""

    def setUp(self):
        self.p = mock.patch.object(comfy, "host", lambda path: "https://hosted/" + os.path.basename(path))
        self.p.start()
        self.files = {}
        for n in ("a.png", "b.png", "k.png"):
            p = os.path.join(TMP, n); Image.new("RGB", (64, 64)).save(p); self.files[n] = p

    def tearDown(self):
        self.p.stop()

    def test_grok_takes_a_cast_as_reference_objects_or_a_frame_not_both(self):
        pm = {m["id"]: m for m in M.public_models()}["xai/grok-imagine-video-1.5"]
        self.assertEqual((pm["refs"], pm["first_frame"]), (7, True))
        call = M.build("xai/grok-imagine-video-1.5", {"prompt": "@Image1 walks", "refs": ["a.png", "b.png"], "duration": 6}, self.files.get)
        self.assertEqual(call["payload"]["reference_images"], [{"url": "https://hosted/a.png"}, {"url": "https://hosted/b.png"}])
        self.assertEqual(call["payload"]["model"], "grok-imagine-video-1.5")
        self.assertNotIn("image", call["payload"])
        framed = M.build("xai/grok-imagine-video-1.5", {"prompt": "p", "refs": ["a.png"], "first_frame": "k.png"}, self.files.get)
        self.assertEqual(framed["payload"]["image"], {"url": "https://hosted/k.png", "type": "image_url"})
        self.assertNotIn("reference_images", framed["payload"], "a frame wins over references: Grok refuses both")

    def test_seedream_takes_its_references_as_its_image_list(self):
        pm = {m["id"]: m for m in M.public_models()}
        self.assertEqual(pm["byteplus/seedream-5-0-pro-260628"]["refs"], 10)
        self.assertEqual(pm["byteplus/seedream-5-0-260128"]["refs"], 14)
        call = M.build("byteplus/seedream-5-0-260128", {"prompt": "p", "refs": ["a.png", "b.png"], "aspect": "16:9"}, self.files.get)
        self.assertEqual(call["payload"]["image"], ["https://hosted/a.png", "https://hosted/b.png"])


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(APP)
        studio._pump["started"] = True

    def setUp(self):
        self.patches = [mock.patch.object(comfy, "host", lambda p: "https://hosted/" + os.path.basename(p)),
                        mock.patch.object(studio, "_balance", lambda max_age=10.0: 1000.0)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def reel(self):
        return self.c.post("/studio/reels", json={"name": "Anim"}).json()["id"]

    def ref(self, reel, name="d.png", color=(200, 120, 40)):
        return self.c.post(f"/studio/reels/{reel}/refs", files={"file": (name, png(color=color), "image/png")}).json()["name"]

    def test_the_catalogue_names_the_models_that_hold_a_cast(self):
        r = self.c.get("/studio/anim/catalogue").json()
        self.assertIn("cel", [s["id"] for s in r["styles"]])
        keys = {m["id"]: m for m in r["keyframe_models"]}
        self.assertEqual(keys["vertexai/gemini-3-pro-image"]["refs"], 6)
        clips = {m["id"]: m for m in r["clip_models"]}
        self.assertTrue(clips["kling/kling-v3-omni"]["refs_with_frame"])
        self.assertFalse(clips["byteplus/dreamina-seedance-2-5-260628"]["refs_with_frame"])
        self.assertEqual(clips["xai/grok-imagine-video-1.5"]["cite"], "@Image1")

    def test_the_board_keeps_the_animation_fields(self):
        reel = self.reel()
        b = {"anim": {"on": True, "style": "retro", "style_notes": "grainy", "clips_from": "refs", "lineup": "x/../lineup-1.png"},
             "elements": [{"id": "e1", "name": "Maya", "kind": "cast", "files": ["a.png", "../b.png"], "hero": "h.png", "sheet": "s.png", "look": "a girl"},
                          {"id": "e2", "name": "Style", "kind": "style", "file": "st.png"},
                          {"id": "e3", "name": "Drawings only", "kind": "cast", "files": ["z.png"]}],
             "shots": [{"id": "c1", "n": 1, "scene": "Maya waves.", "panel": "../p.png", "notes": "SFX bell"}]}
        out = self.c.put(f"/studio/reels/{reel}/board", json=b).json()["board"]
        self.assertEqual(out["anim"], {"on": True, "style": "retro", "style_notes": "grainy", "clips_from": "refs", "lineup": "lineup-1.png", "lineup_key": ""})
        e = {x["id"]: x for x in out["elements"]}
        self.assertEqual((e["e1"]["files"], e["e1"]["hero"], e["e1"]["sheet"], e["e1"]["kind"]), (["a.png", "b.png"], "h.png", "s.png", "cast"))
        self.assertEqual(e["e2"]["kind"], "style")
        self.assertIn("e3", e, "a character with drawings and no picture yet stays in the kit")
        self.assertEqual((out["shots"][0]["panel"], out["shots"][0]["notes"]), ("p.png", "SFX bell"))
        plain = self.c.put(f"/studio/reels/{reel}/board", json={"shots": []}).json()["board"]
        self.assertNotIn("anim", plain)
        self.assertEqual(self.c.put(f"/studio/reels/{reel}/board", json={"anim": {"style": "nope"}}).json()["board"]["anim"]["style"], "cel")

    def test_a_prompt_for_a_keyframe_checks_its_pictures_exist(self):
        reel = self.reel()
        sheet, panel = self.ref(reel, "sheet.png"), self.ref(reel, "panel.png")
        b = {"anim": {"on": True, "style": "cel"}, "elements": [{"id": "m", "name": "Maya", "kind": "cast", "file": sheet, "sheet": sheet}]}
        card = {"id": "c1", "scene": "Maya waves.", "shot": "close", "move": "static", "elements": ["m"], "panel": panel}
        r = self.c.post(f"/studio/reels/{reel}/anim/prompt", json={"role": "key", "model": "vertexai/gemini-3-pro-image", "board": b, "card": card}).json()
        self.assertEqual(r["refs"], [sheet, panel])
        self.assertIn("image 1 is the character model sheet for Maya", r["prompt"])
        self.assertIn("Close-up.", r["prompt"])
        card["panel"] = "gone.png"
        self.assertIn("missing", self.c.post(f"/studio/reels/{reel}/anim/prompt", json={"role": "key", "model": "vertexai/gemini-3-pro-image", "board": b, "card": card}).json()["error"])
        self.assertIn("Pick a model", self.c.post(f"/studio/reels/{reel}/anim/prompt", json={"role": "key", "model": "nope/x", "board": b, "card": card}).json()["error"])
        self.assertIn("What is this run for", self.c.post(f"/studio/reels/{reel}/anim/prompt", json={"role": "x", "model": "vertexai/gemini-3-pro-image", "board": b}).json()["error"])

    def test_a_lineup_is_drawn_from_the_sheets_in_the_reel(self):
        reel = self.reel()
        a, b = self.ref(reel, "a.png", (220, 40, 40)), self.ref(reel, "b.png", (40, 40, 220))
        self.assertIn("at least two", self.c.post(f"/studio/reels/{reel}/anim/lineup", json={"elements": [{"name": "Maya", "sheet": a}]}).json()["error"])
        r = self.c.post(f"/studio/reels/{reel}/anim/lineup", json={"elements": [{"name": "Maya", "sheet": a}, {"name": "Tom", "sheet": b}, {"name": "Ghost", "sheet": "none.png"}]}).json()
        self.assertEqual(r["count"], 2)
        self.assertTrue(os.path.exists(J.ref_path(reel, r["name"])))
        self.assertEqual(self.c.get(r["url"]).status_code, 200)

    def test_panels_are_read_into_cards_through_the_vision_model(self):
        reel = self.reel()
        p1, p2 = self.ref(reel, "p1.png", (250, 250, 250)), self.ref(reel, "p2.png", (240, 240, 240))
        self.assertIn("isn't set up", self.c.post(f"/studio/reels/{reel}/anim/storyboard", json={"panels": [p1, p2]}).json()["error"])
        got = {}

        def fake(system, user, schema, effort="low", max_tokens=4000):
            got["images"] = sum(1 for c in user if c.get("type") == "image")
            return {"title": "T", "cast": [], "cards": [{"scene": "A.", "shot": "wide", "move": "static", "duration": 5, "elements": ["Maya"], "start": "cut", "vo": "", "super": "", "notes": ""},
                                                        {"scene": "B.", "shot": "close", "move": "push", "duration": 8, "elements": [], "start": "continue", "vo": "Go!", "super": "", "notes": ""}]}
        with mock.patch.object(M, "claude_on", lambda: True), mock.patch.object(M, "_claude_json", fake):
            r = self.c.post(f"/studio/reels/{reel}/anim/storyboard", json={"panels": [p1, p2], "elements": [{"name": "Maya", "kind": "cast"}], "model": "byteplus/dreamina-seedance-2-5-260628"}).json()
        self.assertEqual(got["images"], 2)
        self.assertEqual([c["panel"] for c in r["cards"]], [p1, p2])
        self.assertEqual((r["cards"][0]["elements"], r["cards"][1]["vo"], r["cards"][1]["duration"]), (["Maya"], "Go!", 8))
        self.assertIn("Upload the storyboard panels", self.c.post(f"/studio/reels/{reel}/anim/storyboard", json={"panels": ["gone.png"]}).json()["error"])

    def test_the_page_the_guide_and_the_script_carry_the_animation_mode(self):
        html = self.c.get("/studio").text
        self.assertIn("/static/animation.js", html)
        for css in (".amode", ".astage", "#libBlade", ".abadge", ".aeditin", ".aviewin", ".awarn"):
            self.assertIn(css, html + open(os.path.join(ROOT, "studio", "templates", "studio_app.js"), encoding="utf-8").read(), css)
        self.assertEqual(self.c.get("/static/animation.js").status_code, 200)
        # nothing stops quietly: the bulk buttons say where they stopped, a sheet
        # that isn't ready is named, and a thrown error reaches the screen
        app_js = open(os.path.join(ROOT, "studio", "templates", "studio_app.js"), encoding="utf-8").read()
        ours = open(os.path.join(ROOT, "studio", "static", "animation.js"), encoding="utf-8").read()
        for s in ("function waitTake(", "Keyframing stopped at shot", "Animating stopped at shot", 'addEventListener("unhandledrejection"'):
            self.assertIn(s, app_js, s)
        for s in ("sheet isn't ready", "the keyframe didn't start"):
            self.assertIn(s, ours, s)
        guide = self.c.get("/studio/guide").text
        self.assertIn('id="animation"', guide)
        self.assertIn("a suite for animators", guide)
        self.assertIn("Why a sheet and not a seed", guide)
        self.assertIn("pop-up editor", guide)
        for page, text in (("/", "Animation"), ("/about", "A suite for animators")):
            r = self.c.get(page, follow_redirects=True)
            self.assertIn(text, r.text, page)
        readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        self.assertIn("## Animation", readme)
        self.assertIn("suite for animators", readme)
    def test_the_library_has_an_animation_blade(self):
        """What the Animation board made, by what it is; everything else stays under Everything."""
        reel = self.reel()
        mk = lambda action, kind, anim: J.new_take(reel, 1, 0, kind=kind, model="m", label=action, prompt="", aspect="16:9", request={}, settings={},
                                                    action=action, anim=anim, status="done", file=f"{action}-{kind}.{'mp4' if kind == 'video' else 'png'}", by="")
        for action, kind, anim in (("sheet", "image", True), ("design", "image", True), ("plate", "image", True), ("keyframe", "image", True),
                                   ("animate", "video", True), ("stitch", "video", True), ("generate", "image", False), ("keyframe", "image", False)):
            mk(action, kind, anim)
        r = self.c.get(f"{P}/library?who=all").json()
        self.assertEqual((r["total"], r["counts"]["anim"]), (8, 6))
        self.assertEqual(r["counts"]["anim_parts"], {"design": 1, "sheet": 1, "plate": 1, "keyframe": 1, "clip": 1, "cut": 1})
        blade = self.c.get(f"{P}/library?who=all&blade=anim").json()
        self.assertEqual(blade["total"], 6)
        self.assertTrue(all(t["anim"] for t in blade["items"]))
        self.assertEqual([t["action"] for t in self.c.get(f"{P}/library?who=all&blade=anim&part=sheet").json()["items"]], ["sheet"])
        self.assertEqual([t["action"] for t in self.c.get(f"{P}/library?who=all&blade=anim&part=clip").json()["items"]], ["animate"])
        self.assertEqual(self.c.get(f"{P}/library?who=all&blade=anim&part=nope").json()["total"], 6)



if __name__ == "__main__":
    unittest.main(verbosity=1)
