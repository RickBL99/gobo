"""
Tests for Gobo's studio (/studio): the prompt compiler, the
schema-driven mapping of one shot onto every Comfy Router model, the on-disk reel
store, and the HTTP flow end to end. Hermetic: Comfy Router, its storage and
the planner are mocked; ffmpeg runs for real (frames, posters, stitching).

    python3 tests/test_studio.py

The compiler cases are Comfy's Cinematic Studio outputs, reproduced word for
word: a shot directed there must read the same here.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

TMP = tempfile.mkdtemp(prefix="studio-test-")
os.environ["STUDIO_DIR"] = os.path.join(TMP, "reels")
# hermetic: no .env, no real keys, no sign-in
os.environ.update(STUDIO_ENV_FILE="", COMFY_API_KEY="", ANTHROPIC_API_KEY="",
                  OPENAI_API_KEY="", STUDIO_PASSWORD="", STUDIO_USERS="", STUDIO_PREFIX="/studio")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from studio import comfy, direction as D, jobs as J, models as M  # noqa: E402
from studio import routes as studio  # noqa: E402  (the older tests call it `studio`)
from studio.media import probe  # noqa: E402
from studio.app import app as APP  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def jpeg(size=(320, 180), color=(200, 120, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def clip(path, seconds=1.0, color="red", size="320x180", audio=False):
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
           f"color=c={color}:s={size}:d={seconds}:r=24"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)


# ── the compiler ────────────────────────────────────────────────────────────
class Compiler(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(
            D.compile_prompt("A detective lights a cigarette under a flickering sign.", D.DEFAULTS),
            "Medium shot. A detective lights a cigarette under a flickering sign. Cinematic film still, "
            "natural texture. Shot on a large-format cinema camera, anamorphic lenses with oval bokeh, "
            "50mm, f/2.8. Night lit only by practical lamps and signs, tungsten-balanced 500T film stock, "
            "neo-noir mood, teal shadows against warm orange skin tones.")

    def test_starters(self):
        want = {
            "lighthouse": "Wide shot taking in the whole scene. A lighthouse keeper climbs the iron spiral stair with a "
                          "lantern as the storm hammers the windows. Cinematic film still, natural texture. Shot on a "
                          "large-format cinema camera, anamorphic lenses with oval bokeh, 24mm, f/2.8. Night lit only "
                          "by practical lamps and signs, tungsten-balanced 500T film stock, period-drama mood, a cold "
                          "blue-grey palette.",
            "wok": "Medium shot. A street cook tosses noodles over a roaring wok, steam rising into the market's "
                   "neon signs. Cinematic film still, natural texture. Shot on 35mm motion picture film, soft "
                   "vintage glass, 50mm, f/2.8. Neon signs washing the scene in colour, tungsten-balanced 500T "
                   "film stock, neo-noir mood, a magenta and cyan palette.",
            "orchard": "Wide shot taking in the whole scene. Two kids race between rows of apple trees, late sun "
                       "flaring through the leaves. Cinematic film still, natural texture. Shot on grainy 16mm film, "
                       "clean spherical primes, 35mm, f/2.8. Low golden-hour sun, Kodak Portra 400 film, "
                       "observational documentary feel, Kodachrome-style colour with deep reds and blues.",
        }
        for s in D.STARTERS:
            if s["id"] in want:
                self.assertEqual(D.compile_prompt(s["scene"], {**D.DEFAULTS, **s["direction"]}), want[s["id"]], s["id"])

    def test_samples_cover_light_film_look(self):
        # the shipped tiles: every option of these pickers has one, and only real groups are listed
        s = D.samples()
        for g in ("light", "film", "look"):
            self.assertEqual(set(s.get(g, {})), {o["id"] for o in D.GROUPS[g]["options"]}, g)
        self.assertTrue(set(s) <= set(D.GROUPS))
        self.assertRegex(s["light"]["golden"], r"^/static/samples/light-golden\.webp\?v=\d+$")
        self.assertTrue(D.catalogue()["samples"]["film"]["vhs"].startswith("/static/"))

    def test_all_auto_plain_is_just_the_scene(self):
        self.assertEqual(D.compile_prompt("just this.", {k: "auto" for k in D.DEFAULTS}, enhance=False), "just this.")

    def test_video_movement_and_palette(self):
        p = D.compile_prompt("She turns toward the camera.", {**D.DEFAULTS, "move": "push"}, video=True,
                             colors=["#0f2a36", "#e0a15e", "#f2c28b"], main_color=1)
        self.assertIn("Cinematic film shot, continuous motion, natural texture.", p)
        self.assertIn("Camera movement: slow push-in toward the subject.", p)
        self.assertNotIn("teal shadows", p)                    # a palette replaces the preset grade
        self.assertIn("Palette priority: #e0a15e is the main color", p)

    def test_movement_is_video_only(self):
        self.assertNotIn("push-in", D.compile_prompt("x.", {**D.DEFAULTS, "move": "push"}))

    def test_references_and_unknown_ids(self):
        self.assertTrue(D.compile_prompt("x.", D.DEFAULTS, refs=1).endswith("Keep the character from reference image 1."))
        self.assertIn("reference images", D.compile_prompt("x.", D.DEFAULTS, refs=3))
        self.assertEqual(D.option("light", {"light": "nonsense"})["id"], "auto")

    def test_bad_palette_values_are_dropped(self):
        self.assertNotIn("Color grade", D.compile_prompt("x.", D.DEFAULTS, colors=["red", "#12345", None]))
        line = next(s["text"] for s in D.segments("x.", D.DEFAULTS, colors=[f"#0000{i:02d}" for i in range(12)])
                    if s["text"].startswith("Color grade"))
        self.assertEqual(line.count("#"), 8)


# ── mapping one shot onto Comfy Router models ───────────────────────────────
FIND = staticmethod(lambda n: "/x/" + n)


class Mapping(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(comfy, "host", lambda path: "https://hosted/" + os.path.basename(path))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def build(self, mid, **req):
        return M.build(mid, req, lambda n: "/x/" + n)

    def test_catalogue_is_comfy_routers_and_the_picks_resolve(self):
        pm = M.public_models()
        kinds = {k: sum(1 for m in pm if m["kind"] == k) for k in ("image", "video", "upscale", "vupscale")}
        self.assertGreater(kinds["image"], 50, kinds)
        self.assertGreater(kinds["video"], 40, kinds)
        self.assertTrue(kinds["upscale"] and kinds["vupscale"], kinds)
        self.assertTrue(all(m["engine"] == "comfy" for m in pm))
        for mid in (M.DEFAULT_UPSCALER, M.DEFAULT_VIDEO_UPSCALER, M.CUTOUT):
            self.assertIn(mid, M.MODELS, mid)

    def test_aspect_and_size(self):
        self.assertEqual(self.build("byteplus/seedream-5-0-260128", prompt="P", aspect="21:9", resolution="2K")["payload"],
                         {"prompt": "P", "size": "3024x1296", "watermark": False})
        # no 16:9 option: the nearest LANDSCAPE, never a square
        w, h = map(int, self.build("openai/gpt-image-2.5-sunburst", prompt="P", aspect="16:9", resolution="2K")["payload"]["size"].split("x"))
        self.assertGreater(w, h)

    def test_video_frames_audio_and_length(self):
        p = self.build("veo/veo-3.1-generate-001", prompt="P", aspect="16:9", resolution="1080p", duration=7, audio=True)["payload"]["parameters"]
        self.assertEqual((p["durationSeconds"], p["generateAudio"], p["aspectRatio"]), (8, True, "16:9"))   # 7 s: the nearest Veo makes
        c = self.build("kling/kling-v3-omni", prompt="P", duration=10, first_frame="f.jpg")["payload"]
        self.assertEqual(c["image_list"], [{"image_url": "https://hosted/f.jpg", "type": "first_frame"}])
        self.assertEqual(c["duration"], "10")          # the model's own type (a string)

    def test_keyframes_list_for_flux3_video(self):
        self.assertEqual(self.build("bfl/flux-3-video", prompt="P", first_frame="f.jpg")["payload"]["keyframes"], ["https://hosted/f.jpg"])

    def test_refusals_say_what_is_missing(self):
        needs = next(m["id"] for m in M.public_models() if m["kind"] == "image" and m.get("requires_ref"))
        with self.assertRaises(M.RunError) as cm:
            self.build(needs, prompt="P")
        self.assertIn("add a reference", str(cm.exception))
        with self.assertRaises(M.RunError) as cm:
            self.build("wan/wan3.0-video", prompt="P", source_video="v.mp4")
        self.assertIn("doesn't take a video", str(cm.exception))
        with self.assertRaises(M.RunError) as cm:
            self.build("runway/gen4_turbo", prompt="P")         # starts from a still
        self.assertIn("add a starting frame", str(cm.exception))

    def test_negative_seed_types(self):
        flat = [mid for mid, m in M.MODELS.items() if m["kind"] == "image" and m["roles"]["t"].get("negative")
                and not m["roles"]["t"].get("refs_min")
                and m["roles"]["t"]["negative"] in self.build(mid, prompt="P", negative_prompt="x")["payload"]]
        self.assertTrue(flat)
        R = M.MODELS[flat[0]]["roles"]["t"]
        c = self.build(flat[0], prompt="P", negative_prompt="text, watermark", seed="42")["payload"]
        self.assertEqual(c[R["negative"]], "text, watermark")
        if R.get("seed") in c:
            self.assertEqual(c[R["seed"]], 42)            # an int, not the string the page sent

    def test_upscalers_and_tools(self):
        self.assertEqual(self.build(M.DEFAULT_UPSCALER, source="x.jpg", scale=2)["payload"],
                         {"image_url": "https://hosted/x.jpg", "scale_factor": 2.0})
        self.assertEqual(self.build(M.DEFAULT_VIDEO_UPSCALER, source="v.mp4")["payload"]["input_video"], "https://hosted/v.mp4")
        self.assertEqual(M.MODELS[M.CUTOUT]["kind"], "upscale")

    def test_every_drivable_model_builds_a_payload(self):
        """The registry must never offer a model whose payload then fails to
        build from an ordinary request."""
        bad = []
        for pm in M.public_models():
            req = {"prompt": "P", "aspect": "16:9", "resolution": (pm["resolutions"] or [""])[-1],
                   "duration": (pm.get("durations") or [5])[0], "audio": pm.get("audio")}
            if pm["kind"] in ("upscale", "vupscale"):
                req = {"source": "s.mp4" if pm["kind"] == "vupscale" else "s.jpg", "prompt": "P"}
            if pm.get("requires_ref"):
                req["refs"] = ["r.jpg"]
            if pm.get("requires_frame"):
                req["first_frame"] = "f.jpg"
            if pm.get("requires_source"):
                req["source_video"] = "v.mp4"
            try:
                c = self.build(pm["id"], **req)
                json.dumps(c["payload"])
            except Exception as e:
                bad.append((pm["id"], str(e)[:80]))
        self.assertEqual(bad, [])

    def test_elide_hides_signed_links(self):
        e = M.elide({"image": "https://storage.example/c/x.jpg?Expires=1&Signature=SECRET", "p": "x" * 500})
        self.assertNotIn("SECRET", json.dumps(e))
        self.assertLess(len(e["p"]), 200)


class Errors(unittest.TestCase):
    def test_a_busy_provider_names_its_limit(self):
        luma = 'Generation request failed: {"detail":"Concurrent generation limit reached (4)"}'
        self.assertEqual((M.busy_limit(luma), M.busy_limit("too many concurrent tasks"), M.busy_limit("duration must be 5")), (4, None, None))

    def test_footage_lengths(self):
        k, f = "wan/wan2.7-videoedit", "bfl/video-edit-v1"
        self.assertIsNone(M.fit_source(k, 10.7)[0])
        self.assertIn("10 s or less apart", M.fit_source(k, 10.7)[1])
        self.assertIn("at least 2 s", M.fit_source(k, 1.0)[1])
        self.assertEqual(M.fit_source(k, 7.3), (7, ""))                  # told the stretch's length
        self.assertEqual(M.fit_source(k, 7.3, 8), (8, ""))               # a length the page chose stands
        self.assertEqual(M.fit_source(k, 7.3, "5.6"), (6, ""))           # sent as text, still read
        self.assertEqual(M.fit_source(k, 7.3, "soon"), (7, ""))          # nonsense is ignored, not a crash
        self.assertEqual(M.fit_source(f, 12.0), (None, ""))              # no length setting: nothing to say
        self.assertIn("up to 15 s", M.fit_source(f, 16.0)[1])
        self.assertEqual(M.fit_source("no-such-model", 99.0), (None, ""))
        pm = {m["id"]: m for m in M.public_models()}
        self.assertEqual((pm[k]["source_min"], pm[k]["source_max"]), (2, 10))

    def test_failures_in_plain_english(self):
        nothing = "The model rejected these inputs: Prediction failed: The model did not produce an output. Adjust the input or retry."
        h = M.failure_hint({"label": "Wan 2.7 Video Edit", "model": "wan/wan2.7-videoedit", "reason": "validation", "error": nothing,
                            "request": {"source_video": "f.mp4"}})
        self.assertIn("made nothing", h); self.assertIn("up to 10 s", h); self.assertIn("In and Out", h)
        self.assertIn("simpler prompt", M.failure_hint({"label": "X", "reason": "validation", "error": nothing, "request": {}}))
        g = M.failure_hint({"label": "Veo 3.1", "model": "veo/veo-3.1-generate-001", "reason": "policy", "error": "blocked", "request": {}})
        self.assertIn("Google's models refuse real people", g)
        self.assertNotIn("Google", M.failure_hint({"label": "Kling", "model": "kling/kling-v3-omni", "reason": "policy", "error": "x", "request": {}}))
        self.assertIn("Check again", M.failure_hint({"label": "X", "reason": "timeout", "error": "", "request": {}}))
        self.assertIn("Plan & Credits", M.failure_hint({"label": "X", "reason": "credits", "error": "", "request": {}}))
        for reason in ("unavailable", "rateLimit", "network", "validation", "provider", "client", ""):
            hint = M.failure_hint({"label": "X", "reason": reason, "error": "y", "request": {}})
            self.assertTrue(hint and not any(w in hint.lower() for w in BANNED), (reason, hint))


# ── the reel store ──────────────────────────────────────────────────────────
class Store(unittest.TestCase):
    def test_round_trip_and_order(self):
        r = J.new_reel("Test")
        a = J.new_take(r["id"], 1, 0, kind="image", model="m")
        time.sleep(0.002)
        b = J.new_take(r["id"], 1, 1, kind="image", model="m")
        self.assertEqual([t["id"] for t in J.list_takes(r["id"])], [a["id"], b["id"]])
        self.assertEqual([t["letter"] for t in J.list_takes(r["id"])], ["A", "B"])
        J.update_take(r["id"], a["id"], status="done")
        self.assertEqual(J.get_take(r["id"], a["id"])["status"], "done")
        self.assertEqual(J.next_shot(r["id"]), 2)

    def test_stale_sync_take_fails_async_waits(self):
        r = J.new_reel()
        t = J.new_take(r["id"], 1, 0, kind="image", model="m", status="running")
        old = time.time() - J.SYNC_STALE - 5
        J._write(J._take_path(r["id"], t["id"]), {**t, "beat": old})
        self.assertEqual(J.list_takes(r["id"])[0]["status"], "failed")
        J._write(J._take_path(r["id"], t["id"]), {**t, "beat": old, "poll_url": "x"})
        self.assertEqual(J.list_takes(r["id"])[0]["status"], "running")

    def test_claim_is_exclusive(self):
        r = J.new_reel()
        t = J.new_take(r["id"], 1, 0)
        self.assertTrue(J.claim(r["id"], t["id"]))
        self.assertFalse(J.claim(r["id"], t["id"]))
        J.release(r["id"], t["id"])
        self.assertTrue(J.claim(r["id"], t["id"]))
        J.release(r["id"], t["id"])

    def test_ids_are_checked(self):
        for bad in ("../etc", "a/b", "", "x" * 80):
            with self.assertRaises(ValueError):
                J.reel_dir(bad)

    def test_purge_spares_internal_dirs(self):
        J.mark_active("abcdef123456", "0000000000000-aaaaaa")
        os.utime(J.ACTIVE, (1, 1))
        J.purge_old()
        self.assertTrue(os.path.isdir(J.ACTIVE))
        J.unmark_active("abcdef123456", "0000000000000-aaaaaa")

    def test_purge_keeps_finished_work(self):
        # a take stays until its maker deletes it; only reels with nothing made in them age out
        kept, empty = J.new_reel("Kept"), J.new_reel("Empty")
        open(J.out_path(kept["id"], "001A-abcdef.jpg"), "wb").write(jpeg())
        for r in (kept, empty):
            os.utime(os.path.join(J.reel_dir(r["id"]), "takes"), (1, 1))
        J.purge_old()
        self.assertIsNotNone(J.get_reel(kept["id"]))
        self.assertFalse(os.path.isdir(J.reel_dir(empty["id"])))

    def test_a_ceiling_clears_the_reels_touched_longest_ago(self):
        # the demo's STUDIO_KEEP_GB: strangers' films must never fill the storage our app shares
        old, older, fresh = J.new_reel("Old"), J.new_reel("Older"), J.new_reel("Fresh")
        for r, age in ((old, 2 * 86400), (older, 5 * 86400), (fresh, 0)):
            open(J.out_path(r["id"], "001B-abcdef.mp4"), "wb").write(b"x" * 400_000)
            then = time.time() - age
            for p in ("", "takes", "out"):
                os.utime(os.path.join(J.reel_dir(r["id"]), p), (then, then))
        with mock.patch.object(J, "KEEP_GB", 0):
            self.assertEqual(J.trim_to_size(force=True), 0, "no ceiling: nothing goes")
        with mock.patch.object(J, "KEEP_GB", 0.0009):          # 900 KB: two of the three fit
            self.assertEqual(J.trim_to_size(force=True), 1)
        self.assertFalse(os.path.isdir(J.reel_dir(older["id"])), "the one touched longest ago goes first")
        self.assertIsNotNone(J.get_reel(old["id"]))
        with mock.patch.object(J, "KEEP_GB", 0.0001):          # nothing fits, but a reel in use this hour stays
            J.trim_to_size(force=True)
        self.assertFalse(os.path.isdir(J.reel_dir(old["id"])))
        self.assertIsNotNone(J.get_reel(fresh["id"]))


# ── HTTP, end to end ────────────────────────────────────────────────────────
PRICED = "ltx/ltx-2-5-fast"                 # a clip model Router publishes a price for


class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(APP)
        studio._pump["started"] = True                 # tests drive _advance themselves

    def setUp(self):
        self.submitted = []
        # each test starts with an empty line and no clips holding provider slots
        for d in (J.QUEUE, J.ACTIVE):
            shutil.rmtree(d, ignore_errors=True)
        for f in (J.LANES, os.path.join(J.BASE, "_dispatch.lock")):
            if os.path.exists(f):
                os.remove(f)
        self.patches = [
            mock.patch.object(comfy, "host", lambda p: "https://hosted/" + os.path.basename(p)),
            mock.patch.object(M, "submit", self.fake_submit),
            mock.patch.object(studio, "_balance", lambda max_age=10.0: self.balance),
        ]
        self.balance = 1000.0                          # Comfy is never asked in tests
        for p in self.patches:
            p.start()
        # background work in tests: run it inline so the order is deterministic
        self.tp = mock.patch.object(studio, "_spawn", lambda fn, *a: fn(*a))
        self.tp.start()

    def tearDown(self):
        self.tp.stop()
        for p in self.patches:
            p.stop()

    def fake_submit(self, call):
        self.submitted.append(call)
        return {"request_id": f"req-{len(self.submitted)}", "poll_url": f"https://seg/requests/req-{len(self.submitted)}/status"}

    def reel(self):
        return self.c.post("/studio/reels", json={"name": "Flow"}).json()["id"]

    def test_a_direct_run_that_times_out_is_asked_again_under_its_key(self):
        """Router's 504 on a direct run doesn't stop the generation: a new key
        would start (and bill) a second one, the same key collects the first."""
        reel = self.reel()
        sent = []

        def flaky(call):
            sent.append(dict(call))
            if len(sent) == 1:
                raise M.RunError("Comfy Router is briefly unavailable; the take goes again shortly.", "rateLimit")
            return {"url": "https://out.jpg"}
        with mock.patch.object(M, "submit", flaky), mock.patch.object(comfy, "go_sync", lambda: True), \
                mock.patch.object(M, "fetch_output", lambda res: (jpeg((64, 64)), ".jpg")), mock.patch.object(time, "sleep", lambda s: None):
            t = self.c.post(f"/studio/reels/{reel}/run", json={
                "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "A beach."}).json()["takes"][0]
        self.assertEqual([(c["sync"], c["key"]) for c in sent], [(True, sent[0]["key"])] * 2)
        self.assertEqual(J.get_take(reel, t["id"])["status"], "done")
        self.assertIsNone(J.get_call(reel, t["id"]), "the call is dropped once it lands")

    def test_a_direct_run_whose_worker_died_is_collected_under_its_key(self):
        reel = self.reel()
        call = {"endpoint": "x/y", "payload": {"prompt": "p"}, "sync": True, "key": "k-123", "kind": "image"}
        ids = []
        for quiet in (studio.DEAD + 5, 10):                 # one worker died; one is just slow
            t = J.new_take(reel, 1, 0, kind="image", model="byteplus/seedream-5-0-260128", status="running")
            J.put_call(reel, t["id"], call)
            J._write(J._take_path(reel, t["id"]), {**J.get_take(reel, t["id"]), "beat": time.time() - quiet})
            J.mark_active(reel, t["id"])
            ids.append(t["id"])
        sent = []
        with mock.patch.object(M, "submit", lambda c: sent.append(c) or {"url": "https://out.jpg"}), \
                mock.patch.object(M, "fetch_output", lambda res: (jpeg((64, 64)), ".jpg")):
            studio._pump_once()
        self.assertEqual([(c["key"], c["sync"], c["payload"]) for c in sent], [("k-123", True, {"prompt": "p"})])
        dead, slow = (J.get_take(reel, i) for i in ids)
        self.assertEqual((dead["status"], dead["resends"]), ("done", 1))
        self.assertIsNone(J.get_call(reel, ids[0]))
        self.assertEqual((slow["status"], slow.get("resends")), ("running", None))

    def test_a_take_opens_in_comfyui_as_gobos_nodes(self):
        """Open in ComfyUI: the take, its look as Gobo's nodes, a LUT, a shot match,
        the grade and a save; ComfyUI fetches the files by signed links."""
        reel = self.reel()
        ref = self.c.post(f"/studio/reels/{reel}/run", json={"mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "A harbour."}).json()["takes"][0]
        self.finish(reel, ref["id"], jpeg((64, 64), (200, 120, 40)), ".jpg")
        t = self.c.post(f"/studio/reels/{reel}/run", json={"mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "A harbour."}).json()["takes"][0]
        self.finish(reel, t["id"], jpeg((64, 64), (180, 130, 60)), ".jpg")
        swap = "TITLE \"Swap\"\nLUT_3D_SIZE 2\n" + "".join(f"{b} {g} {r}\n" for b in (0, 1) for g in (0, 1) for r in (0, 1))
        lut = self.c.post(f"/studio/reels/{reel}/luts", files={"file": ("swap.cube", swap.encode(), "text/plain")}).json()["lut"]
        look = {"film": "portra", "grade": "teal", "amount": 0.8, "contrast": 1.2,
                "wheels": {"lift": [0, 0, 0.02, 0], "gamma": [0, 0, 0, 0], "gain": [0, 0, 0, 0], "offset": [0, 0, 0, 0]},
                "lut3d": {"id": lut["id"], "mix": 0.5, "first": False}}
        r = self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/comfyui/colour",
                        json={"look": look, "film": "portra", "grade": "teal", "reference": ref["id"]}).json()
        flow = r["workflow"]
        self.assertEqual([n["class_type"] for n in flow.values()],
                         ["GoboLoadTake", "GoboLook", "GoboLUT", "GoboLoadTake", "GoboShotMatch", "GoboGrade", "SaveImage"])
        lk = flow["2"]["inputs"]
        self.assertEqual((lk["film"], lk["grade"], lk["amount"], lk["contrast"]), ("portra", "teal", 0.8, 1.2))
        self.assertEqual(set(lk) - {"film", "grade", "amount", "colour"},
                         {"exposure", "contrast", "pivot", "saturation", "warmth", "tint", "fade", "white_point", "grain", "grain_size", "halation", "vignette"})
        colour = json.loads(lk["colour"])
        self.assertEqual(colour["wheels"]["lift"][2], 0.02)
        self.assertIn("highlights", colour, "the grade's split tones ride in the colour box")
        self.assertEqual((flow["3"]["inputs"]["amount"], flow["3"]["inputs"]["where"], flow["3"]["inputs"]["name"]), (0.5, "after the look", "Swap"))
        self.assertEqual(flow["6"]["inputs"], {"take": ["1", 0], "look": ["5", 0]})
        self.assertTrue(r["nodes"].endswith("/comfyui/gobo-nodes.zip"))
        # the links work without signing in, for this file only, and not once tampered with
        outside = TestClient(APP)
        got = outside.get(flow["1"]["inputs"]["link"].split("testserver", 1)[1])
        t = J.get_take(reel, t["id"])
        self.assertEqual(got.content, open(J.out_path(reel, t["file"]), "rb").read())
        self.assertEqual(outside.get(flow["3"]["inputs"]["lut"].split("testserver", 1)[1]).text, swap)
        token = flow["1"]["inputs"]["link"].rsplit("/", 1)[1]
        self.assertEqual(outside.get("/t/" + token[:-2] + ("00" if not token.endswith("00") else "11")).status_code, 404)
        from studio import comfy_colour as CC
        stale = CC.sign(reel, t["file"], days=-1)
        self.assertEqual(outside.get(f"/t/{stale}").status_code, 404)
        self.assertIsNone(CC.path_of({"r": reel, "n": "../../reel.json"}))
        # the nodes themselves, one folder for ComfyUI/custom_nodes
        z = zipfile.ZipFile(io.BytesIO(outside.get("/comfyui/gobo-nodes.zip").content))
        names = set(z.namelist())
        self.assertTrue({"gobo/__init__.py", "gobo/nodes.py", "gobo/engine.py", "gobo/gobo.wasm", "gobo/web/gobo.js", "gobo/requirements.txt"} <= names, names)
        self.assertFalse(any("__pycache__" in n for n in names))
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/comfyui/colour", json={"look": {"film": "nope"}}).status_code, 400)

    def test_a_take_from_another_reel_is_brought_in(self):
        """The Lab's match grades a take from anywhere in the library: it's copied
        into the reel being graded first, file and all."""
        a, b = self.reel(), self.reel()
        src = os.path.join(TMP, "bring.mp4"); clip(src, 1.0, "green")
        with open(src, "rb") as f:
            mp4 = f.read()
        t = self.c.post(f"/studio/reels/{a}/run", json={"mode": "video", "model": "byteplus/dreamina-seedance-2-5-260628",
                                                         "scene": "Over there.", "duration": 5}).json()["takes"][0]
        self.finish(a, t["id"], mp4, ".mp4")
        r = self.c.post(f"/studio/reels/{b}/takes/bring", json={"item": f"{a}:{t['id']}"})
        self.assertEqual(r.status_code, 200, r.text)
        nt = r.json()["take"]
        self.assertEqual((nt["reel"], nt["copied_from"], nt["status"]), (b, f"{a}:{t['id']}", "done"))
        self.assertTrue(os.path.exists(J.out_path(b, nt["file"])))
        self.assertEqual(J.get_take(a, t["id"])["status"], "done", "the original stays")
        # one already here is itself; one still rendering, or nobody's, isn't brought
        self.assertEqual(self.c.post(f"/studio/reels/{b}/takes/bring", json={"item": f"{b}:{nt['id']}"}).json()["take"]["id"], nt["id"])
        busy = self.c.post(f"/studio/reels/{a}/run", json={"mode": "video", "model": "byteplus/dreamina-seedance-2-5-260628",
                                                            "scene": "Not yet.", "duration": 5}).json()["takes"][0]
        self.assertEqual(self.c.post(f"/studio/reels/{b}/takes/bring", json={"item": f"{a}:{busy['id']}"}).status_code, 400)
        self.assertEqual(self.c.post(f"/studio/reels/{b}/takes/bring", json={"item": f"{a}:1790000000000-zzzzzz"}).status_code, 404)
        self.assertEqual(self.c.post(f"/studio/reels/{b}/takes/bring", json={"item": "../etc"}).status_code, 404)

    def test_a_release_reaches_the_browser_at_once(self):
        """The page's scripts and Gobo's Rust are checked with the server on every
        load, so nobody grades with last release's scopes from a kept copy."""
        for f in ("gobo.wasm", "gobo.js", "gobo_colour.js"):
            r = self.c.get(f"/static/{f}")
            self.assertEqual((r.status_code, r.headers.get("cache-control")), (200, "no-cache"), f)

    def test_library_takes_go_on_a_board(self):
        """Clips made for a board whose page was closed, and a still from another
        reel, put on a board from the library: in film order, the stranger copied in."""
        a = self.reel()
        self.c.put(f"/studio/reels/{a}/board", json={"shots": [{"id": f"c{i}", "n": i + 1, "scene": f"Shot {i + 1}"} for i in range(3)]})
        src = os.path.join(TMP, "lib-board.mp4"); clip(src, 1.0, "blue")
        with open(src, "rb") as f:
            mp4 = f.read()
        made = {}
        for i in (2, 0, 1):                     # Autopilot runs them side by side: they finish in any order
            t = self.c.post(f"/studio/reels/{a}/run", json={"mode": "video", "model": "byteplus/dreamina-seedance-2-5-260628",
                                                            "scene": f"Shot {i + 1}.", "duration": 5, "board": f"c{i}", "role": "clip"}).json()["takes"][0]
            self.finish(a, t["id"], mp4, ".mp4")
            made[i] = t["id"]
        other = self.reel()
        still = self.c.post(f"/studio/reels/{other}/run", json={"mode": "image", "model": "byteplus/seedream-5-0-260128",
                                                                "scene": "A lighthouse."}).json()["takes"][0]
        self.finish(other, still["id"], jpeg(), ".jpg")
        busy = self.c.post(f"/studio/reels/{other}/run", json={"mode": "image", "model": "byteplus/seedream-5-0-260128",
                                                               "scene": "Still rendering."}).json()["takes"][0]
        items = [f"{other}:{still['id']}", f"{a}:{made[1]}", f"{a}:{made[0]}", f"{a}:{made[2]}", f"{other}:{busy['id']}"]
        r = self.c.post("/studio/library/board", json={"items": items, "name": "The rescue"}).json()
        new = r["reel"]["id"]
        self.assertEqual((r["reel"]["name"], r["added"], r["left"]), ("The rescue", 4, [f"{other}:{busy['id']}"]))
        board = self.c.get(f"/studio/reels/{new}/board").json()["board"]
        takes = {t["id"]: t for t in self.c.get(f"/studio/reels/{new}").json()["takes"]}
        cards = board["shots"]
        # the board's own order (shots 1, 2, 3), then the still, which was made after them
        self.assertEqual([takes[c["clip"] or c["key"]].get("copied_from") for c in cards],
                         [f"{a}:{made[0]}", f"{a}:{made[1]}", f"{a}:{made[2]}", f"{other}:{still['id']}"])
        self.assertEqual([c["scene"] for c in cards[:3]], ["Shot 1.", "Shot 2.", "Shot 3."])
        self.assertEqual([c["n"] for c in cards], [1, 2, 3, 4])
        for c in cards:
            t = takes[c["clip"] or c["key"]]
            self.assertEqual((t["board"], t["role"], t["status"]), (c["id"], "clip" if c["clip"] else "key", "done"))
            self.assertTrue(os.path.exists(J.out_path(new, t["file"])))
        self.assertTrue(cards[3]["key"] and not cards[3]["clip"], "a still is the card's keyframe")
        # the originals stay where they were
        self.assertEqual(J.get_take(a, made[0])["board"], "c0")
        # onto an existing board: the one already on it is left out, the rest added after its cards
        again = self.c.post("/studio/library/board", json={"items": [f"{a}:{made[0]}", f"{new}:{cards[0]['clip']}"], "reel": new}).json()
        self.assertEqual((again["added"], again["left"]), (1, [f"{new}:{cards[0]['clip']}"]))
        self.assertEqual(len(self.c.get(f"/studio/reels/{new}/board").json()["board"]["shots"]), 5)
        # a take made from a starting frame brings the frame with it, so it can be shot again
        up = self.c.post(f"/studio/reels/{a}/refs", files={"file": ("start.jpg", jpeg(), "image/jpeg")}).json()
        fr = self.c.post(f"/studio/reels/{a}/run", json={"mode": "video", "model": "byteplus/dreamina-seedance-2-5-260628", "scene": "From a frame.",
                                                         "duration": 5, "first_frame": up["name"]}).json()["takes"][0]
        self.finish(a, fr["id"], mp4, ".mp4")
        moved = self.c.post("/studio/library/board", json={"items": [f"{a}:{fr['id']}"], "name": "Framed"}).json()["reel"]["id"]
        self.assertTrue(os.path.exists(J.ref_path(moved, up["name"])), "the starting frame came with it")
        # a copy made before frames came along finds its frame in the reel it was copied from
        os.remove(J.ref_path(moved, up["name"]))
        self.assertEqual(self.c.get(f"/studio/reels/{moved}/file/{up['name']}").status_code, 200)
        self.assertTrue(os.path.exists(J.ref_path(moved, up["name"])))
        self.assertEqual(self.c.get(f"/studio/reels/{moved}/file/nosuch.jpg").status_code, 404)
        # takes already copied onto a board, onto another one: a copy of a copy
        third = self.c.post("/studio/library/board", json={"items": [f"{new}:{c['clip'] or c['key']}" for c in cards], "name": "Recut"})
        self.assertEqual(third.status_code, 200, third.text)
        recut = third.json()["reel"]["id"]
        self.assertEqual(sorted(t.get("copied_from") for t in self.c.get(f"/studio/reels/{recut}").json()["takes"]),
                         sorted(f"{new}:{c['clip'] or c['key']}" for c in cards))
        self.assertEqual(self.c.post("/studio/library/board", json={"items": [f"{other}:{busy['id']}"]}).status_code, 400)
        self.assertEqual(self.c.post("/studio/library/board", json={"items": items, "reel": "nosuchreel1"}).status_code, 404)

    def test_a_lut_from_elsewhere_grades_the_take(self):
        """A .cube kept with the reel, named by a look, used by the server's grader."""
        reel = self.reel()
        swap = "TITLE \"Swap\"\nLUT_3D_SIZE 2\n" + "".join(f"{b} {g} {r}\n" for b in (0, 1) for g in (0, 1) for r in (0, 1))
        up = self.c.post(f"/studio/reels/{reel}/luts", files={"file": ("swap.cube", swap.encode(), "application/octet-stream")}).json()["lut"]
        self.assertEqual((up["title"], up["kind"], up["size"], up["name"]), ("Swap", "3D", 2, "swap.cube"))
        self.assertTrue(up["id"].startswith("lut-") and up["id"] == J.lut_id(swap), "one file, one id")
        self.assertEqual([m["id"] for m in self.c.get(f"/studio/reels/{reel}/luts").json()["luts"]], [up["id"]])
        self.assertEqual(self.c.get(f"/studio/reels/{reel}/luts/{up['id']}").text, swap)
        self.assertEqual(self.c.get(f"/studio/reels/{reel}/luts/lut-000000000000").status_code, 404)
        bad = self.c.post(f"/studio/reels/{reel}/luts", files={"file": ("x.cube", b"LUT_3D_SIZE 3\n0 0 0\n", "text/plain")})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("27 rows", bad.json()["error"])
        still = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "A beach."}).json()["takes"][0]
        self.finish(reel, still["id"], jpeg((64, 64), (200, 120, 40)), ".jpg")
        gs = self.c.post(f"/studio/reels/{reel}/takes/{still['id']}/grade",
                         json={"look": {"lut3d": {"id": up["id"]}, "wheels": {"lift": [0, 0, 0, 0]}}}).json()
        r, g, b = Image.open(J.out_path(reel, gs["take"]["file"])).getpixel((32, 32))
        self.assertTrue(b > 180 and r < 60, f"red and blue swapped: {(r, g, b)}")
        gone = self.c.post(f"/studio/reels/{reel}/takes/{still['id']}/grade", json={"look": {"lut3d": {"id": "lut-0123456789ab"}}})
        self.assertIn("isn't in this reel", gone.json()["error"])
        # a LUT's rest (what the sliders can't show, worked out in the page) says whose rest it is
        rest = swap.replace("Swap", "Swap (the rest)")
        r = self.c.post(f"/studio/reels/{reel}/luts", data={"of": up["id"]}, files={"file": ("rest.cube", rest.encode(), "text/plain")}).json()["lut"]
        self.assertEqual(r["of"], up["id"])
        self.assertEqual({m["id"]: m.get("of") for m in self.c.get(f"/studio/reels/{reel}/luts").json()["luts"]}, {up["id"]: None, r["id"]: up["id"]})
        orphan = self.c.post(f"/studio/reels/{reel}/luts", data={"of": "lut-0123456789ab"}, files={"file": ("rest.cube", rest.encode(), "text/plain")})
        self.assertEqual(orphan.status_code, 400)

    def settle(self, reel, tid, wait=60):
        """A take made in the background (a grade), once it stops running."""
        deadline = time.time() + wait
        while time.time() < deadline:
            t = J.get_take(reel, tid)
            if t and t.get("status") not in ("queued", "running"):
                return t
            time.sleep(0.1)
        return J.get_take(reel, tid)

    def finish(self, reel, tid, data, ext, cost=0.05):
        with mock.patch.object(M, "poll", lambda url, model="": {"url": "https://out" + ext, "cost": cost}), \
             mock.patch.object(M, "fetch_output", lambda res: (data, ext)):
            studio._advance(reel, tid)

    def test_page_and_catalogue_render(self):
        r = self.c.get("/studio")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<title>Gobo</title>", r.text)
        self.assertIn('"kind": "video"', r.text)
        self.assertEqual(self.c.get("/studio/app.js").status_code, 200)
        cat = self.c.get("/studio/catalogue").json()
        self.assertIn("groups", cat["direction"])

    def test_guide_renders(self):
        r = self.c.get("/studio/guide")
        self.assertEqual(r.status_code, 200)
        from studio import settings as version
        for want in ('id="board"', 'id="footage"', 'id="kit"', 'id="trim"', 'id="trouble"', 'id="q"', version.RELEASE):
            self.assertIn(want, r.text)
        self.assertIn('href="/studio/guide"', self.c.get("/studio").text)

    def test_kill_switch(self):
        with mock.patch.dict(os.environ, {"STUDIO_ENABLED": "0"}):
            self.assertEqual(self.c.get("/studio").status_code, 503)
            self.assertEqual(self.c.post("/studio/reels", json={}).status_code, 503)
        self.assertEqual(self.c.get("/studio").status_code, 200)

    def test_compile_endpoint(self):
        r = self.c.post("/studio/compile", json={"scene": "A boat.", "direction": D.DEFAULTS,
                                                           "enhance": True, "mode": "image"}).json()
        self.assertTrue(r["prompt"].startswith("Medium shot. A boat."))
        self.assertEqual({s["source"] for s in r["segments"]}, {"direction", "scene", "enhance"})

    def test_image_takes_run_and_land(self):
        reel = self.reel()
        r = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "A boat at dawn.", "direction": D.DEFAULTS,
            "aspect": "16:9", "resolution": "2K", "takes": 3, "seed": "100"}).json()
        self.assertEqual([t["letter"] for t in r["takes"]], ["A", "B", "C"])
        self.assertEqual(len(self.submitted), 3)
        seeds = [c["payload"].get("seed") for c in self.submitted]
        self.assertTrue(all(s is None for s in seeds) or len(set(seeds)) == 3, seeds)
        takes = self.c.get(f"/studio/reels/{reel}").json()["takes"]
        self.assertTrue(all(t["status"] == "running" for t in takes))
        self.assertTrue(all("poll_url" not in t for t in takes))      # never sent to the page
        self.assertEqual(len([a for a in J.active() if a[0] == reel]), 3)
        for t in takes:
            self.finish(reel, t["id"], jpeg(), ".jpg")
        takes = self.c.get(f"/studio/reels/{reel}").json()["takes"]
        self.assertTrue(all(t["status"] == "done" and t["w"] == 320 for t in takes), takes)
        self.assertEqual([a for a in J.active() if a[0] == reel], [])
        self.assertEqual(takes[0]["cost"], 0.05)
        img = self.c.get(takes[0]["url"])
        self.assertEqual(img.headers["content-type"], "image/jpeg")
        self.assertEqual(self.c.get(takes[0]["thumb_url"]).status_code, 200)
        z = zipfile.ZipFile(io.BytesIO(self.c.get(f"/studio/reels/{reel}/download").content))
        self.assertIn("shots.json", z.namelist())
        self.assertEqual(len(z.namelist()), 4)

    def test_shootout_one_take_per_model(self):
        reel = self.reel()
        r = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "models": ["byteplus/seedream-5-0-260128", "bfl/flux-3-image", "openai/gpt-image-2.5-sunburst"],
            "scene": "A boat.", "takes": 4}).json()
        self.assertEqual(len(r["takes"]), 3)
        self.assertEqual({t["model"] for t in r["takes"]}, {"byteplus/seedream-5-0-260128", "bfl/flux-3-image", "openai/gpt-image-2.5-sunburst"})
        self.assertEqual(len({t["shot"] for t in r["takes"]}), 1)

    def test_wrong_mode_and_missing_ref_are_refused(self):
        reel = self.reel()
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "veo/veo-3.1-generate-001", "scene": "x"}).status_code, 400)
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "bfl/flux-3-image", "scene": "x", "refs": ["ref-nothere.jpg"]}).status_code, 400)

    def test_model_failure_is_named(self):
        reel = self.reel()
        with mock.patch.object(M, "submit", side_effect=M.RunError("blocked", "policy")):
            t = self.c.post(f"/studio/reels/{reel}/run", json={
                "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "x"}).json()["takes"][0]
        t = J.get_take(reel, t["id"])
        self.assertEqual((t["status"], t["reason"]), ("failed", "policy"))
        nt = self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/retry", json={"model": "bfl/flux-3-image"}).json()["take"]
        self.assertEqual(nt["model"], "bfl/flux-3-image")
        self.assertEqual(nt["parent"], t["id"])

    def test_cancel_stays_cancelled_when_result_arrives(self):
        reel = self.reel()
        t = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "x"}).json()["takes"][0]
        self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/cancel")
        self.finish(reel, t["id"], jpeg(), ".jpg")
        self.assertEqual(J.get_take(reel, t["id"])["status"], "cancelled")

    def test_reference_upload_and_use(self):
        reel = self.reel()
        bad = self.c.post(f"/studio/reels/{reel}/refs", files={"file": ("x.txt", b"hi", "text/plain")})
        self.assertEqual(bad.status_code, 400)
        up = self.c.post(f"/studio/reels/{reel}/refs", files={"file": ("a.jpg", jpeg(), "image/jpeg")}).json()
        self.assertEqual((up["kind"], up["w"]), ("image", 320))
        self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "bfl/flux-3-image", "scene": "Her, in Paris.", "refs": [up["name"]]})
        call = self.submitted[-1]
        self.assertEqual(call["endpoint"], "bfl/flux-3-image")           # FLUX 3 on Router takes references itself
        self.assertEqual(call["payload"]["images"], ["https://hosted/" + up["name"]])
        self.assertTrue(call["payload"]["prompt"].endswith("Keep the character from reference image 1."))

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
    def test_video_frame_continue_and_stitch(self):
        reel = self.reel()
        a = os.path.join(TMP, "a.mp4"); clip(a, 1.0, "red", audio=True)
        b = os.path.join(TMP, "b.mp4"); clip(b, 1.0, "blue", size="640x360")
        ids = []
        for path in (a, b):
            t = self.c.post(f"/studio/reels/{reel}/run", json={
                "mode": "video", "model": "byteplus/dreamina-seedance-2-5-260628", "scene": "Waves.", "duration": 5}).json()["takes"][0]
            with open(path, "rb") as f:
                self.finish(reel, t["id"], f.read(), ".mp4", cost=0.3)
            ids.append(t["id"])
        got = J.get_take(reel, ids[0])
        self.assertEqual((got["status"], got["w"]), ("done", 320))
        self.assertTrue(got.get("poster"))
        last = self.c.post(f"/studio/reels/{reel}/takes/{ids[0]}/frame", json={"at": "last"}).json()["take"]
        self.assertEqual((last["kind"], last["action"], last["w"]), ("image", "frame", 320))
        # continue the shot: the grabbed frame starts the next clip
        self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "video", "model": "kling/kling-v3-omni", "scene": "The wave breaks.", "first_frame": last["file"], "duration": 5})
        self.assertEqual(self.submitted[-1]["endpoint"], "kling/kling-v3-omni")
        st = self.c.post(f"/studio/reels/{reel}/stitch", json={"takes": ids}).json()["take"]
        self.assertEqual((st["kind"], st["action"], st["w"], st["h"]), ("video", "stitch", 320, 180))
        self.assertAlmostEqual(float(st["seconds"]), 2.0, delta=0.3)
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/stitch", json={"takes": ids[:1]}).status_code, 400)
        # the grade, in Rust: every frame of the film through a look, a new take beside it
        # (in the background: the answer is the new take, running, and it says which frame it's on)
        gr = self.c.post(f"/studio/reels/{reel}/takes/{st['id']}/grade", json={"look": {"grade": "noir"}, "label": "Noir"}).json()
        self.assertEqual((gr["take"]["kind"], gr["take"]["action"], gr["take"]["parent"], gr["take"]["label"], gr["take"]["status"]),
                         ("video", "grade", st["id"], "Noir", "running"))
        done = self.settle(reel, gr["take"]["id"])
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertAlmostEqual(done["frames"], 48, delta=2)
        self.assertTrue(done.get("poster"))
        self.assertTrue(probe(J.out_path(reel, done["file"]))["has_audio"], "the sound comes along")
        png = os.path.join(TMP, "graded.png")
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", J.out_path(reel, done["file"]),
                        "-frames:v", "1", png], check=True)
        r, g, b = Image.open(png).convert("RGB").getpixel((160, 90))
        self.assertTrue(max(r, g, b) - min(r, g, b) <= 3, f"the red clip graded noir is grey: {(r, g, b)}")
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/takes/{st['id']}/grade", json={"look": {"film": "nope"}}).status_code, 400)
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/takes/{st['id']}/grade", json={}).status_code, 400)
        # Stop grading: the take says so, and no half-made file is left behind
        # (another grade holds the cores, so this one is still waiting when it's stopped)
        graded = lambda: {f for f in os.listdir(os.path.dirname(J.out_path(reel, "x"))) if f.startswith("graded-")}
        before = graded()
        with studio._grading, mock.patch.object(studio, "_spawn", lambda fn, *a: threading.Thread(target=fn, args=a, daemon=True).start()):
            stop = self.c.post(f"/studio/reels/{reel}/takes/{st['id']}/grade", json={"look": {"grade": "teal"}}).json()["take"]
            self.assertEqual(self.c.post(f"/studio/reels/{reel}/takes/{stop['id']}/cancel").json()["take"]["status"], "cancelled")
        deadline = time.time() + 30
        time.sleep(0.3)
        while (studio._grading.locked() or graded() != before) and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual((J.get_take(reel, stop["id"])["status"], graded()), ("cancelled", before))
        # a grade the studio restarted in the middle of says so, not "still rendering" for ever
        lost = {**J.get_take(reel, stop["id"]), "status": "running", "beat": time.time() - J.LOCAL_STALE - 5}
        self.assertEqual((J.stale_check(lost)["status"], lost["reason"]), ("failed", "restart"))
        # stills stitch too, held for still_seconds, and the frame follows the first CLIP
        still = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "A beach."}).json()["takes"][0]
        self.finish(reel, still["id"], jpeg((640, 640)), ".jpg")
        mix = self.c.post(f"/studio/reels/{reel}/stitch",
                          json={"takes": [still["id"], ids[0]], "still_seconds": 2}).json()["take"]
        self.assertEqual((mix["w"], mix["h"], mix["label"]), (320, 180, "Stitched · 1 clip + 1 still"))
        self.assertAlmostEqual(float(mix["seconds"]), 3.0, delta=0.3)
        gs = self.c.post(f"/studio/reels/{reel}/takes/{still['id']}/grade", json={"look": {"grade": "noir", "amount": 1}}).json()["take"]
        self.assertEqual((gs["kind"], gs["w"], gs["h"]), ("image", 640, 640))
        r, g, b = Image.open(J.out_path(reel, gs["file"])).getpixel((320, 320))
        self.assertTrue(max(r, g, b) - min(r, g, b) <= 3, (r, g, b))
        # the bin: download just a selection, delete several at once, never one still rendering
        z = zipfile.ZipFile(io.BytesIO(self.c.get(f"/studio/reels/{reel}/download?takes={still['id']},{ids[1]}").content))
        self.assertEqual(sorted(n.rsplit(".", 1)[-1] for n in z.namelist()), ["jpg", "json", "mp4"])
        busy = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "Still going."}).json()["takes"][0]
        gone = self.c.post(f"/studio/reels/{reel}/takes/delete",
                           json={"takes": [still["id"], mix["id"], busy["id"]]}).json()
        self.assertEqual((sorted(gone["deleted"]), gone["kept"]), (sorted([still["id"], mix["id"]]), [busy["id"]]))
        self.assertIsNone(J.get_take(reel, still["id"]))
        # deleting the film takes its poster with it
        film = J.get_take(reel, st["id"])
        files = [J.out_path(reel, film[k]) for k in ("file", "poster")]
        self.assertTrue(all(os.path.exists(p) for p in files))
        self.c.delete(f"/studio/reels/{reel}/takes/{st['id']}")
        self.assertFalse(any(os.path.exists(p) for p in files))

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
    def test_hevc_is_made_playable(self):
        reel = self.reel()
        src = os.path.join(TMP, "h.mp4")
        r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=320x180:d=1:r=24",
                            "-c:v", "libx265", "-tag:v", "hvc1", src], stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            self.skipTest("no libx265 here")
        t = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "video", "model": "byteplus/dreamina-seedance-2-5-260628", "scene": "x", "duration": 5}).json()["takes"][0]
        with open(src, "rb") as f:
            self.finish(reel, t["id"], f.read(), ".mp4")
        self.assertTrue(J.get_take(reel, t["id"])["file"].endswith("-h264.mp4"))

    def test_upscale_and_cutout_chain(self):
        reel = self.reel()
        t = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "x"}).json()["takes"][0]
        self.finish(reel, t["id"], jpeg(), ".jpg")
        up = self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/upscale", json={}).json()["take"]
        self.assertEqual((up["model"], up["action"]), (M.DEFAULT_UPSCALER, "upscale"))
        self.assertTrue(self.submitted[-1]["payload"]["image_url"].startswith("https://hosted/"))
        cut = self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/upscale", json={"tool": "cutout"}).json()["take"]
        self.assertEqual((cut["model"], cut["action"]), (M.CUTOUT, "cutout"))

    # ── the Content Generator (stills mode) ─────────────────────────────────
    def test_stills_page_has_only_still_models(self):
        r = self.c.get("/studio?stills=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<title>Content Generator · Gobo</title>", r.text)
        self.assertIn("const STILLS = true;", r.text)
        models = json.loads(r.text.split("const MODELS = ", 1)[1].split(";\n", 1)[0])
        self.assertTrue(models and all(m["kind"] in ("image", "upscale") for m in models))
        self.assertGreater(sum(m["kind"] == "image" for m in models), 50)
        self.assertIn("const STILLS = false;", self.c.get("/studio").text)

    def test_a_batch_of_prompts(self):
        reel = self.c.post("/studio/reels", json={"name": "Batch", "app": "cg"}).json()["id"]
        r = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "enhance": False,
            "prompts": [{"scene": "A red door.", "negative_prompt": "blur"}, "A blue door.", {"scene": "  "}]}).json()
        self.assertEqual([t["letter"] for t in r["takes"]], ["A", "B"])
        self.assertEqual(len({t["shot"] for t in r["takes"]}), 1)
        reqs = [J.get_take(reel, t["id"])["request"] for t in r["takes"]]
        self.assertEqual([(q["prompt"], q["negative_prompt"]) for q in reqs], [("A red door.", "blur"), ("A blue door.", "")])
        self.assertEqual(J.get_take(reel, r["takes"][1]["id"])["settings"]["scene"], "A blue door.")
        # one still model only
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "models": ["byteplus/seedream-5-0-260128", "bfl/flux-3-image"], "prompts": ["x"]}).status_code, 400)
        # twenty is the cap, lettered past H
        r = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "A pier.", "takes": 50}).json()
        self.assertEqual(len(r["takes"]), 20)
        self.assertEqual(r["takes"][-1]["letter"], "T")
        # each page lists its own reels
        self.assertIn(reel, [x["id"] for x in self.c.get("/studio/reels?app=cg").json()["reels"]])
        self.assertNotIn(reel, [x["id"] for x in self.c.get("/studio/reels?app=studio").json()["reels"]])

    def test_download_upsized_for_filing(self):
        reel = self.reel()
        t = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "A pier."}).json()["takes"][0]
        self.finish(reel, t["id"], jpeg((640, 360)), ".jpg")
        r = self.c.get(f"/studio/reels/{reel}/download?size=4000")
        self.assertIn("-4k.zip", r.headers["content-disposition"])
        z = zipfile.ZipFile(io.BytesIO(r.content))
        name = next(n for n in z.namelist() if n.endswith(".jpg"))
        with Image.open(io.BytesIO(z.read(name))) as im:
            self.assertEqual((im.size, round(im.info["dpi"][0])), ((4000, 2250), 300))

    def test_a_running_clip_is_never_dropped(self):
        # prod, 2026-10-23: a read that landed mid-rewrite on the shared disk made the
        # pump drop two live clips; both finished (and billed) and were never collected
        reel = self.reel()
        t = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "video", "model": "byteplus/dreamina-seedance-2-5-260628", "scene": "Rain on a window.", "duration": 5}).json()["takes"][0]
        self.assertTrue(J.is_active(reel, t["id"]))
        with mock.patch.object(J, "get_take", lambda r, x: None):       # unreadable, but there
            studio._pump_once()
        self.assertTrue(J.is_active(reel, t["id"]))
        # an unreadable take is never overwritten with a stub
        path = J._take_path(reel, t["id"])
        good = open(path).read()
        with mock.patch.object(J, "_read", lambda p: None):
            with self.assertRaises(RuntimeError):
                J.update_take(reel, t["id"], progress="x")
        self.assertEqual(open(path).read(), good)
        # fell off the list anyway → opening the reel puts it back
        J.unmark_active(reel, t["id"])
        self.c.get(f"/studio/reels/{reel}")
        self.assertTrue(J.is_active(reel, t["id"]))
        # really gone → leaves the list
        os.remove(path)
        studio._pump_once()
        self.assertFalse(J.is_active(reel, t["id"]))

    def test_check_again_collects_a_stopped_take(self):
        reel = self.reel()
        t = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "A pier."}).json()["takes"][0]
        self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/cancel")
        studio._pump_once()                                        # stopped: off the list
        self.assertFalse(J.is_active(reel, t["id"]))
        # still going at the provider → resumes
        with mock.patch.object(M, "poll", lambda url, model="": {"pending": True, "progress": "processing"}):
            r = self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/recheck").json()["take"]
        self.assertEqual(r["status"], "running")
        self.assertTrue(J.is_active(reel, t["id"]))
        # stopped again, then it had finished → collected
        self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/cancel")
        with mock.patch.object(M, "poll", lambda url, model="": {"url": "https://out.jpg", "cost": 0.04}), \
             mock.patch.object(M, "fetch_output", lambda res: (jpeg(), ".jpg")):
            r = self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/recheck").json()["take"]
        self.assertEqual((r["status"], r["cost"], r.get("error")), ("done", 0.04, None))
        self.assertTrue(os.path.exists(J.out_path(reel, r["file"])))
        # a finished take can't be rechecked
        self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/cancel")
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/recheck").status_code, 400)

    def test_star_rename_delete_and_list(self):
        reel = self.reel()
        t = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "x"}).json()["takes"][0]
        self.finish(reel, t["id"], jpeg(), ".jpg")
        self.assertTrue(self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/star", json={"starred": True}).json()["take"]["starred"])
        self.assertEqual(self.c.patch(f"/studio/reels/{reel}", json={"name": "Renamed"}).json()["name"], "Renamed")
        listed = self.c.get("/studio/reels").json()["reels"]
        self.assertTrue(any(r["id"] == reel and r["cover"] for r in listed))
        self.c.delete(f"/studio/reels/{reel}/takes/{t['id']}")
        self.assertEqual(J.list_takes(reel), [])

    def test_library_is_everything_you_made(self):
        def as_(who):
            return mock.patch.object(studio, "_who", lambda request: who)

        def shoot(reel, scene, done=True):
            t = self.c.post(f"/studio/reels/{reel}/run", json={
                "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": scene}).json()["takes"][0]
            if done:
                self.finish(reel, t["id"], jpeg(), ".jpg")
            return t["id"]

        with as_("Alice"):
            a1 = self.c.post("/studio/reels", json={"name": "Alpha"}).json()["id"]
            barn = shoot(a1, "A red barn at dusk.")
            busy = shoot(a1, "Still rendering.", done=False)
            a2 = self.c.post("/studio/reels", json={"name": "Beta"}).json()["id"]
            pier = shoot(a2, "A pier in fog.")
            cg = self.c.post("/studio/reels", json={"name": "Stills", "app": "cg"}).json()["id"]
            shoot(cg, "A teacup.")
        with as_("Bob"):
            bobs = shoot(a1, "Bob's lighthouse.")           # Bob shooting in Alice's reel: his take
        with as_(""):                                        # like everything made before takes carried a name
            old = self.c.post("/studio/reels", json={"name": "Before names"}).json()
            legacy = shoot(old["id"], "Old frame.")
        mine_ids = lambda r: [x["id"] for x in r["items"]]

        with as_("Alice"):
            r = self.c.get("/studio/library?app=studio").json()
            # across reels, finished only, newest first; the Content Generator keeps its own
            self.assertEqual(mine_ids(r), [pier, barn])
            self.assertEqual([x["reel_name"] for x in r["items"]], ["Beta", "Alpha"])
            self.assertTrue(all(x["yours"] and x["url"] and x["thumb_url"] for x in r["items"]))
            self.assertNotIn("payload", r["items"][0])
            self.assertEqual((r["counts"]["mine"], r["me"]), (2, "Alice"))
            every = self.c.get("/studio/library?who=all&limit=200").json()   # other tests' takes too
            self.assertTrue({pier, barn, bobs, legacy} <= set(mine_ids(every)))
            self.assertNotIn(busy, mine_ids(every))
            self.assertEqual({x["id"]: x["yours"] for x in every["items"]}[bobs], False)
            self.assertEqual({x["id"]: x["owner"] for x in every["items"]}[legacy], "")
            self.assertEqual(mine_ids(self.c.get("/studio/library?q=barn dusk").json()), [barn])
            self.assertEqual(self.c.get("/studio/library?kind=video").json()["items"], [])
            self.assertEqual(len(self.c.get("/studio/library?app=cg").json()["items"]), 1)
            self.c.post(f"/studio/reels/{a2}/takes/{pier}/star", json={"starred": True})
            self.assertEqual(mine_ids(self.c.get("/studio/library?starred=1").json()), [pier])
            page = self.c.get("/studio/library?limit=1&offset=1").json()
            self.assertEqual((mine_ids(page), page["total"]), ([barn], 2))

            # one zip across reels, a folder per reel
            z = zipfile.ZipFile(io.BytesIO(self.c.get(
                f"/studio/library/download?items={a1}:{barn},{a2}:{pier},{a1}:{busy},junk").content))
            files = sorted(n for n in z.namelist() if n.endswith(".jpg"))
            self.assertEqual([n.split("/")[0] for n in files], [f"Alpha-{a1[:4]}", f"Beta-{a2[:4]}"])
            self.assertEqual(len(json.loads(z.read("shots.json"))), 2)

            # delete: yours and nobody's go; Bob's and a take still rendering stay
            r = self.c.post("/studio/library/delete", json={"items": [
                f"{a1}:{barn}", f"{old['id']}:{legacy}", f"{a1}:{bobs}", f"{a1}:{busy}", "../x:y"]}).json()
            self.assertEqual(sorted(r["deleted"]), sorted([f"{a1}:{barn}", f"{old['id']}:{legacy}"]))
            self.assertEqual(sorted(r["kept"]), sorted([f"{a1}:{bobs}", f"{a1}:{busy}"]))
            self.assertEqual(mine_ids(self.c.get("/studio/library").json()), [pier])
            self.assertIsNotNone(J.get_take(a1, bobs))
        # a take with no name can be claimed by whoever's signed in; a named one keeps its name
        with as_(""):
            orphan = shoot(old["id"], "Another old frame.")
            self.assertEqual(self.c.post("/studio/library/claim", json={"items": [f"{old['id']}:{orphan}"]}).status_code, 401)
        with as_("Alice"):
            r = self.c.post("/studio/library/claim", json={"items": [f"{old['id']}:{orphan}", f"{a1}:{bobs}", "nope"]}).json()
            self.assertEqual(r["claimed"], [f"{old['id']}:{orphan}"])
            self.assertIn(orphan, mine_ids(self.c.get("/studio/library").json()))
            self.assertEqual(J.get_take(a1, bobs)["by"], "Bob")
        # no one signed in: an empty Mine, never someone else's
        with as_(""):
            r = self.c.get("/studio/library").json()
            self.assertEqual((r["items"], r["me"]), ([], ""))

    def test_board_saves_cleans_and_never_reads_as_empty(self):
        reel = self.reel()
        self.assertEqual(self.c.get(f"/studio/reels/{reel}").json()["board"], {})
        b = self.c.put(f"/studio/reels/{reel}/board", json={
            "brief": "A :30 for a water bottle.", "target": 30, "seed": "1234", "variants": 9, "junk": 1,
            "look": {"direction": {"light": "golden"}, "aspect": "16:9", "evil": 1},
            "elements": [{"id": "e1", "name": "Maya", "kind": "cast", "file": "../../ref-a.jpg"}, {"id": "e2", "name": "no file"}],
            "shots": [{"id": "s1", "n": 1, "scene": "Maya runs.", "elements": ["e1", "ghost"], "start": "continue",
                       "trim": [0.5, 3], "duration": 5}, {"nope": True}] + [{"id": f"x{i}", "n": i} for i in range(60)]}).json()["board"]
        self.assertEqual((b["seed"], b["variants"], b["target"]), (1234, 4, 30))
        self.assertNotIn("junk", b)
        self.assertEqual(b["look"], {"direction": {"light": "golden"}, "aspect": "16:9"})
        self.assertEqual(b["elements"], [{"id": "e1", "name": "Maya", "kind": "cast", "file": "ref-a.jpg"}])
        self.assertEqual((b["shots"][0]["elements"], b["shots"][0]["trim"]), (["e1"], [0.5, 3.0]))
        self.assertEqual(len(b["shots"]), studio.BOARD_SHOTS)
        r = self.c.get(f"/studio/reels/{reel}").json()
        self.assertTrue(r["reel"]["board"])
        self.assertEqual(r["board"]["brief"], "A :30 for a water bottle.")
        # the board's shot numbers are taken even before they have takes
        self.assertEqual(J.next_shot(reel), 39)          # kept cards are numbered up to 38
        # there is a board but it can't be read: say so, never hand back an empty one to save over it
        with mock.patch.object(J, "_read", lambda p: None if p.endswith("board.json") else json.load(open(p))):
            self.assertIsNone(self.c.get(f"/studio/reels/{reel}").json()["board"])
            self.assertEqual(self.c.get(f"/studio/reels/{reel}/board").status_code, 503)

    def test_board_cards_shoot_onto_their_shot(self):
        reel = self.reel()
        src = self.reel()
        maya = self.c.post(f"/studio/reels/{src}/run", json={
            "mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "Portrait of Maya."}).json()["takes"][0]
        self.finish(src, maya["id"], jpeg(), ".jpg")
        # a still from another reel comes in as this reel's own reference file
        ref = self.c.post(f"/studio/reels/{reel}/refs/copy", json={"item": f"{src}:{maya['id']}"}).json()
        self.assertTrue(ref["name"].startswith("ref-") and os.path.exists(J.ref_path(reel, ref["name"])))
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/refs/copy", json={"item": "nope"}).status_code, 404)
        self.c.put(f"/studio/reels/{reel}/board", json={"shots": [{"id": "card1", "n": 7}]})
        # keyframes: two variants on shot 7, the kit named in the prompt
        r = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "image", "model": "bfl/flux-3-image", "scene": "Maya opens the fridge.", "takes": 2,
            "refs": [ref["name"]], "ref_names": ["Maya"], "shot": 7, "board": "card1", "role": "key", "seed": "77"}).json()["takes"]
        self.assertEqual([(t["shot"], t["letter"], t["board"], t["role"]) for t in r], [(7, "A", "card1", "key"), (7, "B", "card1", "key")])
        self.assertIn("Reference images: image 1 is Maya. Keep it exactly as shown.", r[0]["prompt"])
        self.assertEqual(J.get_take(reel, r[0]["id"])["settings"]["ref_names"], ["Maya"])
        # the clip letters on from the keyframes
        self.finish(reel, r[0]["id"], jpeg(), ".jpg")
        c = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "video", "model": "byteplus/dreamina-seedance-2-5-260628", "scene": "Maya opens the fridge.", "duration": 5,
            "first_frame": J.get_take(reel, r[0]["id"])["file"], "shot": 7, "board": "card1", "role": "clip"}).json()["takes"][0]
        self.assertEqual((c["shot"], c["letter"], c["role"]), (7, "C", "clip"))
        # an ordinary Generate takes the next free number, past the board's
        o = self.c.post(f"/studio/reels/{reel}/run", json={"mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": "x"}).json()["takes"][0]
        self.assertEqual((o["shot"], o.get("board")), (8, None))

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
    def test_board_continue_trim_and_animatic(self):
        reel = self.reel()
        a = os.path.join(TMP, "bd-a.mp4"); clip(a, 2.0, "red", audio=True)
        t = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "video", "model": "byteplus/dreamina-seedance-2-5-260628", "scene": "A run.", "duration": 5, "shot": 1, "board": "c1", "role": "clip"}).json()["takes"][0]
        with open(a, "rb") as f:
            self.finish(reel, t["id"], f.read(), ".mp4")
        # "continue": the next card's keyframe is the last frame of this clip, on the next card's shot
        k = self.c.post(f"/studio/reels/{reel}/takes/{t['id']}/frame",
                        json={"at": "last", "shot": 2, "board": "c2"}).json()["take"]
        self.assertEqual((k["shot"], k["board"], k["role"], k["kind"]), (2, "c2", "key", "image"))
        # the cut: the clip trimmed to 0.5–1.5 s, the keyframe held 1.5 s
        cut = self.c.post(f"/studio/reels/{reel}/stitch", json={
            "takes": [t["id"], k["id"]], "trims": {t["id"]: [0.5, 1.5], k["id"]: [0, 1.5]}, "label": "Spot cut"}).json()["take"]
        self.assertEqual(cut["label"], "Spot cut")
        self.assertAlmostEqual(float(cut["seconds"]), 2.5, delta=0.25)
        # nonsense trims are ignored, not obeyed
        whole = self.c.post(f"/studio/reels/{reel}/stitch", json={
            "takes": [t["id"], k["id"]], "trims": {t["id"]: [3, 1], k["id"]: "x"}, "still_seconds": 1}).json()["take"]
        self.assertAlmostEqual(float(whole["seconds"]), 3.0, delta=0.25)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
    def test_footage_comes_in_as_a_take(self):
        reel = self.reel()
        mov = os.path.join(TMP, "shoot.mov")
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=640x360:d=3:r=50",
                        "-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", mov],
                       check=True, stdin=subprocess.DEVNULL)
        self.c.put(f"/studio/reels/{reel}/board", json={"shots": [{"id": "f1", "n": 4, "type": "footage"}]})
        with open(mov, "rb") as f:
            r = self.c.post(f"/studio/reels/{reel}/footage", files={"file": ("Stewart pier.mov", f, "video/quicktime")},
                            data={"shot": "4", "board": "f1"}).json()
        t, k = r["take"], r["key"]
        self.assertEqual((t["kind"], t["action"], t["label"], t["shot"], t["board"], t["role"], t["aspect"]),
                         ("video", "footage", "Footage · Stewart pier", 4, "f1", "clip", "16:9"))
        self.assertAlmostEqual(float(t["seconds"]), 3.0, delta=0.15)
        from studio.media import probe
        self.assertEqual(probe(J.out_path(reel, t["file"]))["fps"], 25.0)        # 50 fps comes in at 25
        self.assertTrue(os.path.exists(J.ref_path(reel, J.get_take(reel, t["id"])["source"]["file"])))   # the original is kept
        self.assertEqual((k["kind"], k["role"], k["parent"], k["letter"]), ("image", "key", t["id"], "B"))
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/footage", files={"file": ("notes.txt", b"hi", "text/plain")}).status_code, 400)
        with mock.patch.object(studio, "MAX_VIDEO_BYTES", 10):
            with open(mov, "rb") as f:
                self.assertIn("100 MB", self.c.post(f"/studio/reels/{reel}/footage", files={"file": ("a.mov", f, "video/quicktime")}).json()["error"])
        # a change runs on the trimmed stretch, not the whole clip
        c = self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "video", "model": "bfl/video-edit-v1", "scene": "Make it winter.", "prompt_override": "Make it winter.",
            "source_video": t["file"], "source_trim": [0.5, 2.0], "shot": 4, "board": "f1", "role": "clip", "action": "change"}).json()["takes"][0]
        sent = J.get_take(reel, c["id"])["request"]["source_video"]
        self.assertTrue(sent.startswith("cut-"))
        self.assertAlmostEqual(probe(J.ref_path(reel, sent))["duration"], 1.5, delta=0.15)
        self.assertEqual(self.submitted[-1]["payload"]["prompt"], "Make it winter.")

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
    def test_footage_a_tool_cant_take_is_stopped_before_it_is_sent(self):
        # prod, 2026-10-23: a 10.7 s clip went whole to a 10-second edit tool with no length;
        # it made nothing, three times. The server now checks, whatever page sent it.
        reel = self.reel()
        src = os.path.join(TMP, "k-src.mp4"); clip(src, 10.7, "gray")
        with open(src, "rb") as f:
            foot = self.c.post(f"/studio/reels/{reel}/footage", files={"file": ("dog.mp4", f, "video/mp4")}).json()["take"]
        body = {"mode": "video", "model": "wan/wan2.7-videoedit", "scene": "x", "prompt_override": "Have the woman holding an alligator.",
                "source_video": foot["file"], "duration": None, "action": "change", "role": "clip"}
        n = len(self.submitted)
        r = self.c.post(f"/studio/reels/{reel}/run", json=body)
        self.assertEqual(r.status_code, 400)
        self.assertIn("up to 10 s of footage, and this stretch is 10.7 s", r.json()["error"])
        self.assertEqual(len(self.submitted), n)                                    # nothing spent
        # a stretch it takes goes, told its length
        t = self.c.post(f"/studio/reels/{reel}/run", json={**body, "source_trim": [2, 9.2]}).json()["takes"][0]
        self.assertEqual(J.get_take(reel, t["id"])["request"]["duration"], 7)
        self.assertEqual(self.submitted[-1]["payload"]["parameters"]["duration"], 7)
        # Try again on a take that slipped through checks it too
        old = J.new_take(reel, 9, 0, kind="video", model="wan/wan2.7-videoedit", label="Wan 2.7 Video Edit", prompt="p",
                         aspect="16:9", request={"prompt": "p", "duration": None, "source_video": foot["file"], "refs": []}, settings={})
        J.update_take(reel, old["id"], status="failed", reason="validation",
                      error="The model rejected these inputs: Prediction failed: The model did not produce an output. Adjust the input or retry.")
        r = self.c.post(f"/studio/reels/{reel}/takes/{old['id']}/retry", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("up to 10 s", r.json()["error"])
        # and the failed take explains itself
        seen = next(x for x in self.c.get(f"/studio/reels/{reel}").json()["takes"] if x["id"] == old["id"])
        self.assertIn("ran but made nothing", seen["hint"])
        self.assertIn("takes up to 10 s", seen["hint"])

    # ── the line: a provider gets only so many runs at once ──
    def clips(self, reel, n, model="luma_2/ray-3.2"):
        return [self.c.post(f"/studio/reels/{reel}/run", json={
            "mode": "video", "model": model, "scene": f"Shot {i}.", "prompt_override": f"Shot {i}.", "shot": i + 1}).json()["takes"][0]
            for i in range(n)]

    def test_a_whole_board_waits_its_turn(self):
        # prod, 2026-10-24: Animate on a six-shot board sent six Luma clips at once; Luma ran four
        # and refused two ("Concurrent generation limit reached (4)")
        reel = self.reel()
        takes = self.clips(reel, 6)
        self.assertEqual(len(self.submitted), 4)
        waiting = [J.get_take(reel, t["id"]) for t in takes[4:]]
        self.assertEqual([(w["status"], w["ahead"], w["lane"], w["lane_cap"]) for w in waiting],
                         [("queued", 0, "Comfy · Luma", 4), ("queued", 1, "Comfy · Luma", 4)])
        # waiting its turn is not "lost": the stale check leaves it alone however long it waits
        old = {**waiting[0], "beat": time.time() - 3600}
        self.assertEqual(J.stale_check(old)["status"], "queued")
        # one lands, the next in line goes
        self.finish(reel, takes[0]["id"], b"v", ".mp4")
        studio._dispatch()
        self.assertEqual(len(self.submitted), 5)
        self.assertEqual(J.get_take(reel, takes[4]["id"])["status"], "running")
        self.assertEqual(J.get_take(reel, takes[5]["id"])["ahead"], 0)
        # another provider's clip doesn't wait behind Luma's; stills aren't held back
        n = len(self.submitted)
        self.clips(reel, 1, "veo/veo-3.1-generate-001")
        self.assertEqual(len(self.submitted), n + 1)
        n += 1
        for i in range(6):
            self.c.post(f"/studio/reels/{reel}/run", json={"mode": "image", "model": "byteplus/seedream-5-0-260128", "scene": f"Still {i}."})
        self.assertEqual(len(self.submitted), n + 6)
        # stopped while waiting: never sent
        self.c.post(f"/studio/reels/{reel}/takes/{takes[5]['id']}/cancel")
        self.finish(reel, takes[1]["id"], b"v", ".mp4")
        studio._dispatch()
        self.assertEqual(len(self.submitted), n + 6)
        self.assertFalse(J.is_queued(reel, takes[5]["id"]))
        self.assertIn("Nothing was spent", J.get_take(reel, takes[5]["id"])["error"])

    def test_the_balance_is_live_and_says_whats_committed(self):
        # 2026-10-24: the header showed the balance read when the page opened, not what was left
        reel = self.reel()
        self.clips(reel, 6, PRICED)                           # four rendering, two in line
        p = studio._price(PRICED)
        self.assertGreater(p, 0)
        self.balance = 5.03
        c = self.c.get("/studio/credits?fresh=1").json()
        self.assertEqual((c["credits"], c["rendering"], c["waiting"], c["bank"]), (5.03, round(4 * p, 2), round(2 * p, 2), "Comfy"))
        self.assertAlmostEqual(c["left"], 5.03 - 6 * p, places=2)
        self.assertTrue(c["low"])                             # under $5 once what's committed is paid
        self.balance = 500.0
        self.assertFalse(self.c.get("/studio/credits").json()["low"])
        self.balance = None
        self.assertEqual(self.c.get("/studio/credits").status_code, 502)

    def test_a_run_the_account_cant_cover_is_refused_before_it_is_sent(self):
        reel = self.reel()
        p = studio._price(PRICED)
        self.balance = round(p * 1.5, 2)
        self.clips(reel, 3, PRICED)                           # what's rendering may already be held: only the line counts
        self.assertEqual(len(self.submitted), 3)
        w = self.clips(reel, 1, PRICED)[0]
        J.update_take(reel, w["id"], status="queued")
        J.unmark_active(reel, w["id"]); J.enqueue(reel, w["id"])   # one waiting in line, not yet charged
        r = self.c.post(f"/studio/reels/{reel}/run", json={"mode": "video", "model": PRICED, "scene": "x", "prompt_override": "x"})
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.json()["reason"], "credits")
        self.assertIn(f"Comfy has ${self.balance:.2f} and about ${p:.2f} is already waiting in line, and this needs about ${p:.2f}",
                      r.json()["error"])
        self.assertEqual(len(self.submitted), 4)
        self.balance = None                                   # Comfy can't say: the run decides
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/run", json={"mode": "video", "model": PRICED, "scene": "x", "prompt_override": "x"}).status_code, 200)

    def test_busy_goes_back_in_line_and_the_lane_learns(self):
        reel = self.reel()
        t = self.clips(reel, 1)[0]
        luma = M.RunError('Generation request failed: {"detail":"Concurrent generation limit reached (3)"}', "busy")
        with mock.patch.object(M, "poll", side_effect=luma):
            studio._advance(reel, t["id"])
        w = J.get_take(reel, t["id"])
        self.assertEqual((w["status"], w["waits"], w["wait_reason"], w.get("poll_url")), ("queued", 1, "busy", None))
        self.assertTrue(J.is_queued(reel, t["id"]) and not J.is_active(reel, t["id"]))
        self.assertEqual(J.lane_caps()["Comfy · Luma clips"], 3)                      # the limit it named
        self.assertNotIn("error", w)
        studio._dispatch()
        self.assertEqual(len(self.submitted), 1)                              # not before its wait is up
        J.update_take(reel, t["id"], not_before=0)
        studio._dispatch()
        self.assertEqual((len(self.submitted), J.get_take(reel, t["id"])["status"]), (2, "running"))
        # a slow-down on sending waits too, and teaches no limit
        with mock.patch.object(M, "submit", side_effect=M.RunError("Too many runs", "rateLimit")):
            s = self.clips(reel, 1, "veo/veo-3.1-generate-001")[0]
        self.assertEqual(J.get_take(reel, s["id"])["wait_reason"], "rateLimit")
        self.assertEqual(set(J.lane_caps()), {"Comfy · Luma clips"})
        # a provider that stays full: the take fails in the end, and says why
        J.update_take(reel, t["id"], waits=studio.MAX_WAITS)
        with mock.patch.object(M, "poll", side_effect=luma):
            studio._advance(reel, t["id"])
        f = J.get_take(reel, t["id"])
        self.assertEqual((f["status"], f["reason"]), ("failed", "busy"))
        self.assertIn("stayed full", f["error"])
        self.assertIn("waited in line", M.failure_hint(f))

    def test_plan_endpoint(self):
        reel = self.reel()
        self.assertEqual(self.c.post(f"/studio/reels/{reel}/board/plan", json={"brief": " "}).status_code, 400)
        with mock.patch.object(M, "plan_spot", lambda brief, target, model, els, d, footage=None: {"shots": [{"scene": brief}], "total": 5, "target": target}):
            r = self.c.post(f"/studio/reels/{reel}/board/plan", json={"brief": "Bottle", "target": 15}).json()
        self.assertEqual((r["shots"][0]["scene"], r["target"]), ("Bottle", 15.0))

    def test_director_endpoint(self):
        with mock.patch.object(M, "director_rewrite", lambda brief, model, video=False, notes="": {
                "prompt": "REWRITTEN " + brief[:10], "negative_prompt": "", "notes": "n", "brief": brief}):
            r = self.c.post("/studio/direct", json={"scene": "A boat.", "direction": D.DEFAULTS,
                                                              "model": "veo/veo-3.1-generate-001", "mode": "video"}).json()
        self.assertTrue(r["prompt"].startswith("REWRITTEN Medium sho"))
        self.assertIn("continuous motion", r["brief"])


class Planner(unittest.TestCase):
    def test_plan_is_held_to_what_can_be_shot(self):
        seen = {}

        def fake(system, user, schema, effort="low", max_tokens=4000):
            seen.update(user=user, schema=schema)
            return {"title": "Cold", "notes": "n", "shots": [
                {"scene": "Maya runs.", "shot": "wide", "move": "track", "duration": 7.4, "elements": ["maya", "Ghost"],
                 "start": "continue", "vo": "", "super": ""},
                {"scene": "The bottle.", "shot": "insert", "move": "push", "duration": 30, "elements": ["The Bottle"],
                 "start": "continue", "vo": "Stay cold.", "super": "ICEBOX"}]}
        with mock.patch.object(M, "_claude_json", fake):
            out = M.plan_spot("A :15 for a bottle.", 15, "veo/veo-3.1-generate-001", [{"name": "Maya", "kind": "cast"}, {"name": "The bottle", "kind": "product"}],
                              {"light": "golden", "film": "d250"})
        # Veo makes 4, 6 or 8 seconds: the plan is snapped to them
        self.assertEqual([s["duration"] for s in out["shots"]], [8, 8])
        self.assertEqual([s["elements"] for s in out["shots"]], [["Maya"], ["The bottle"]])
        self.assertEqual([s["start"] for s in out["shots"]], ["cut", "continue"])     # nothing before shot 1 to continue
        self.assertEqual((out["total"], out["target"], out["title"]), (16, 15, "Cold"))
        self.assertIn("allowed shot durations: 4, 6, 8 seconds", seen["user"])
        self.assertIn("- The bottle (product)", seen["user"])
        self.assertIn("low golden-hour sun", seen["user"].lower())
        self.assertIn("insert", seen["schema"]["properties"]["shots"]["items"]["properties"]["shot"]["enum"])


class PlannerFootage(unittest.TestCase):
    def test_footage_is_fixed_and_planned_around(self):
        seen = {}

        def fake(system, user, schema, effort="low", max_tokens=4000):
            seen["user"] = user
            return {"title": "t", "notes": "n", "shots": [
                {"scene": "Wide of the pier.", "shot": "wide", "move": "drone", "duration": 4, "elements": [], "start": "cut", "vo": "", "super": "", "footage": ""},
                {"scene": "ignored", "shot": "auto", "move": "auto", "duration": 99, "elements": [], "start": "continue", "vo": "", "super": "", "footage": "fA"},
                {"scene": "dup", "shot": "auto", "move": "auto", "duration": 1, "elements": [], "start": "cut", "vo": "", "super": "", "footage": "fA"},
                {"scene": "She turns the corner.", "shot": "medium", "move": "track", "duration": 4, "elements": [], "start": "continue", "vo": "", "super": "", "footage": ""}]}
        with mock.patch.object(M, "_claude_json", fake):
            out = M.plan_spot("A :15", 15, "veo/veo-3.1-generate-001", [], {}, [{"id": "fA", "seconds": 6.2, "notes": "Woman jogs on the pier"},
                                                             {"id": "fB", "seconds": 3, "notes": "Bottle on a rail"}])
        self.assertIn("- fA (6.2 s): Woman jogs on the pier", seen["user"])
        self.assertEqual([(s["footage"], s["duration"]) for s in out["shots"]], [("", 4), ("fA", 6.2), ("", 4), ("fB", 3.0)])
        self.assertEqual(out["shots"][1]["scene"], "Woman jogs on the pier")      # footage keeps its own description
        self.assertEqual(out["total"], 17.2)

    def test_models_say_what_they_need(self):
        pm = {m["id"]: m for m in M.public_models()}
        e = pm["bfl/video-edit-v1"]
        self.assertEqual((e["category"], e["requires_source"], e["takes_prompt"]), ("video-edit", True, True))


class Inline:
    """threading.Thread stand-in that runs its target on start()."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.t, self.a, self.k = target, args, kwargs or {}

    def start(self):
        self.t(*self.a, **self.k)


# ── the model directory ─────────────────────────────────────────────────────
BANNED = ("cutting-edge", "state-of-the-art", "sota", "seamless", "stunning", "revolutionary", "unleash", "elevate",
          "robust", "leverage", "empower", "effortless", "breathtaking", "groundbreaking", "best-in-class",
          "game-changer", "next-generation", "unparalleled", "world-class")


class Directory(unittest.TestCase):
    def notes(self, d):
        path = os.path.join(TMP, "notes.json")
        with open(path, "w") as f:
            json.dump(d, f)
        os.utime(path, (time.time(), time.time() + 1))
        return mock.patch.multiple(M, NOTES_FILE=path, _notes={"mt": None, "d": {"models": {}, "makers": {}}})

    def entries(self, d=None):
        d = d or M.directory()
        return [m for s in d["sections"] for g in s["groups"] for m in g["models"]]

    def test_every_model_once(self):
        es = self.entries()
        ids = [e["id"] for e in es]
        self.assertEqual(sorted(ids), sorted(m["id"] for m in M.public_models()))
        self.assertEqual(len({e["anchor"] for e in es}), len(es))
        self.assertEqual(M.anchor("veo/veo-3.1-generate-001"), "m-veo-veo-3-1-generate-001")

    def test_facts_said_plainly(self):
        pub = {m["id"]: m for m in M.public_models()}
        veo = M.model_facts(pub["veo/veo-3.1-generate-001"])
        for want in ("no real people", "can start from a still", "makes sound"):
            self.assertIn(want, veo)
        priced = next(m for m in pub.values() if m["kind"] == "video" and m.get("price"))
        self.assertTrue(M.model_facts(priced)[0].startswith("≈$"), M.model_facts(priced))
        edit = M.model_facts(pub["wan/wan2.7-videoedit"])
        self.assertIn("works on a clip you give it", edit)
        self.assertNotIn("no real people", edit)
        needs = next(m for m in pub.values() if m["kind"] == "image" and m.get("requires_ref"))
        self.assertIn("needs a picture to work on", M.model_facts(needs))

    def test_notes_laid_over_the_catalogue(self):
        V, F = "veo/veo-3.1-generate-001", "veo/veo-3.1-fast-generate-001"
        d = {"models": {V: {"summary": "Google's clip model.", "good": ["Sound"], "limits": ["No real people"],
                            "best_for": "Clips with dialogue", "status": "current"},
                        F: {"summary": "Quicker.", "status": "older", "newer": V},
                        "wan/wan3.0-video": {"summary": "x", "status": "older", "newer": "no-such-model"}},
             "makers": {"Google": "Search, Android, and DeepMind's models."}}
        with self.notes(d):
            es = {e["id"]: e for e in self.entries()}
            v = es[V]
            self.assertEqual((v["good"], v["limits"], v["best_for"], v["written"]), (["Sound"], ["No real people"], "Clips with dialogue", True))
            self.assertEqual(v["maker_anchor"], "maker-google")
            self.assertEqual((es[F]["newer_label"], es[F]["newer_anchor"]), ("Veo 3.1", "m-veo-veo-3-1-generate-001"))
            self.assertIsNone(es["wan/wan3.0-video"]["newer"])     # a successor that isn't in the list is dropped
            self.assertFalse(es["bfl/flux-3-image"]["written"])    # no note yet: still listed
            self.assertEqual(next(m for m in M.public_models() if m["id"] == V)["about"], "Clips with dialogue")
            html = self.c.get("/studio/guide").text
        for want in ('id="directory"', 'id="dir-video"', 'id="m-veo-veo-3-1-generate-001"', "Clips with dialogue",
                     'href="#m-veo-veo-3-1-generate-001"',
                     'id="maker-google"', 'id="fPicks"', "Not reviewed yet"):
            self.assertIn(want, html)

    def test_picks_lead_their_section(self):
        d = M.directory()
        video = next(s for s in d["sections"] if s["id"] == "dir-video")
        self.assertTrue(video["groups"][0]["pick"])
        self.assertEqual(video["groups"][0]["models"][0]["id"], "byteplus/dreamina-seedance-2-5-260628")
        footage = next(s for s in d["sections"] if s["id"] == "dir-footage")
        self.assertTrue(all(e["kind"] == "video" for g in footage["groups"] for e in g["models"]))

    def test_every_guide_link_lands(self):
        import re
        html = self.c.get("/studio/guide").text
        ids = set(re.findall(r'\bid="([^"]+)"', html))
        dead = sorted({h for h in re.findall(r'href="#([^"$]+)"', html) if h not in ids})   # $ = the script's own templates
        self.assertEqual(dead, [])
        self.assertIn('href="#m-vertexai-gemini-nano-banana-2-1"', html)   # the quick picks point into the directory

    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(APP)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
