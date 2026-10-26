"""The Comfy Router engine: the catalogue snapshot, each family's native body,
the queue and its errors. Hermetic: no .env, no real key, nothing sent —
Router and Comfy's storage are mocked.

    .venv/bin/python -m pytest tests/test_comfy.py
"""
import base64
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

TMP = tempfile.mkdtemp(prefix="comfy-test-")
os.environ["STUDIO_DIR"] = os.path.join(TMP, "reels")
os.environ.update(STUDIO_ENV_FILE="", COMFY_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="",
                  STUDIO_PASSWORD="", STUDIO_USERS="")

from PIL import Image  # noqa: E402

from studio import comfy, settings  # noqa: E402
from studio import models as M  # noqa: E402

FRAME = os.path.join(TMP, "frame.png")
Image.new("RGB", (640, 360), (40, 60, 90)).save(FRAME)
CLIP = os.path.join(TMP, "clip.mp4")
with open(CLIP, "wb") as f:
    f.write(b"\x00\x00\x00\x18ftypmp42")
FILES = {"f.png": FRAME, "c.mp4": CLIP}
HOSTED = "https://storage.example/gobo-abc.png?Expires=9999999999"


class Resp:
    def __init__(self, status=200, body=None, headers=None, content=b""):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.content = content
        self.text = "" if body is None else str(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def comfy_only():
    """A Comfy key, and files 'hosted' without a network."""
    ps = [mock.patch.object(settings, "COMFY_API_KEY", "ck-test"), mock.patch.object(comfy, "host", lambda p: HOSTED)]
    for p in ps:
        p.start()
    return ps


class Engines(unittest.TestCase):
    def test_comfy_router_is_the_one_catalogue(self):
        # with a key or without one (the page still shows what you could run)
        for key in ("", "ck-test"):
            with mock.patch.object(settings, "COMFY_API_KEY", key):
                pm = M.public_models()
                self.assertTrue(len(pm) > 100 and all(m["engine"] == "comfy" for m in pm), len(pm))


class Catalogue(unittest.TestCase):
    def setUp(self):
        self.ps = comfy_only()

    def tearDown(self):
        for p in self.ps:
            p.stop()

    def test_heroes_lead_and_every_one_is_driveable(self):
        pm = M.public_models()
        self.assertTrue(pm and all(m["engine"] == "comfy" for m in pm))
        heroes = [mid for g in comfy.HEROES.values() for _, items in g for mid, _ in items]
        self.assertEqual([h for h in heroes if h not in M.MODELS], [])
        self.assertEqual(pm[0]["id"], heroes[0])
        self.assertTrue(pm[0]["featured"])
        self.assertTrue(any(m["group"].startswith("Comfy · ") and not m["featured"] for m in pm))

    def test_prose_options(self):
        self.assertEqual(comfy._prose_options("aspect_ratio", "Options: adaptive (default), 21:9, 16:9, 1:1, 9:16."),
                         ["21:9", "16:9", "1:1", "9:16"])
        self.assertEqual(comfy._prose_options("aspect_ratio", "Supported values: 9x16, 16x9, 1x1."), ["9x16", "16x9", "1x1"])
        self.assertEqual(comfy._prose_options("resolution", "Output resolution class: 768sq, 1k, 1.5k, 2k, or 4k."),
                         ["768sq", "1k", "1.5k", "2k", "4k"])
        self.assertIsNone(comfy._prose_options("resolution", "Output resolution."))


class Bodies(unittest.TestCase):
    """Each family's native request, from the studio's one request."""

    def setUp(self):
        self.ps = comfy_only()

    def tearDown(self):
        for p in self.ps:
            p.stop()

    def body(self, mid, **req):
        req = {"prompt": "a lighthouse in a storm", "aspect": "16:9", **req}
        call = M.build(mid, req, FILES.get)
        self.assertEqual((call["engine"], call["endpoint"]), ("comfy", mid))
        return call["payload"]

    def test_content_family_seedance(self):
        b = self.body("byteplus/dreamina-seedance-2-5-260628", first_frame="f.png", duration=6, resolution="720p")
        self.assertEqual(b["content"][0], {"type": "text", "text": "a lighthouse in a storm"})
        self.assertEqual(b["content"][1], {"type": "image_url", "image_url": {"url": HOSTED}, "role": "first_frame"})
        self.assertEqual((b["duration"], b["ratio"], b["watermark"]), (6, "16:9", False))

    def test_veo_sends_frames_as_base64(self):
        b = self.body("veo/veo-3.1-fast-generate-001", first_frame="f.png", duration=5, audio=True)
        inst, par = b["instances"][0], b["parameters"]
        self.assertEqual(inst["image"]["mimeType"], "image/png")
        self.assertTrue(base64.b64decode(inst["image"]["bytesBase64Encoded"]).startswith(b"\x89PNG"))
        self.assertEqual((par["durationSeconds"], par["generateAudio"], par["aspectRatio"]), (6, True, "16:9"))

    def test_wan_media_list(self):
        b = self.body("wan/wan3.0-video", first_frame="f.png", duration=4, resolution="720p")
        self.assertEqual(b["input"]["media"], [{"type": "first_frame", "url": HOSTED}])
        self.assertEqual(b["parameters"]["resolution"], "720P")
        self.assertNotIn("size", b["parameters"])
        self.assertFalse(b["parameters"]["prompt_extend"])

    def test_luma_nests_the_clip(self):
        b = self.body("luma_2/ray-3.2", first_frame="f.png", duration=8, resolution="720p")
        self.assertEqual(b["type"], "video")
        self.assertEqual(b["video"], {"duration": "10s", "resolution": "720p", "start_frame": {"url": HOSTED}})

    def test_kling_omni_tags_frames(self):
        b = self.body("kling/kling-v3-omni", first_frame="f.png", resolution="720p", audio=True)
        self.assertEqual(b["image_list"], [{"image_url": HOSTED, "type": "first_frame"}])
        self.assertEqual((b["mode"], b["sound"]), ("std", "on"))
        self.assertNotIn("aspect_ratio", b)            # the frame sets the shape

    def test_flat_aliases_and_switches(self):
        b = self.body("runway/gen4_turbo", first_frame="f.png", duration=5)
        self.assertEqual((b["promptImage"], b["ratio"]), (HOSTED, "1280:720"))
        self.assertNotIn("first_frame", b)
        b = self.body("bfl/flux-3-video", first_frame="f.png")
        self.assertEqual((b["keyframes"], b["mode"]), ([HOSTED], "i2v"))
        self.assertEqual(self.body("bfl/flux-3-video")["mode"], "t2v")

    def test_sizes_the_schema_only_describes(self):
        self.assertEqual(self.body("openai/gpt-image-2.5-sunburst", aspect="9:16")["size"], "1024x1536")
        self.assertEqual(self.body("byteplus/seedream-5-0-260128", resolution="2K")["size"], "2560x1440")
        self.assertEqual(self.body("ideogram/p-image-ideogram")["aspect_ratio"], "16x9")
        self.assertEqual(self.body("minimax/minimax-h3", aspect="9:16", duration=6, resolution="768p")["ratio"], "9:16")

    def test_gemini_parts(self):
        b = self.body("vertexai/gemini-nano-banana-2.1", refs=["f.png"], resolution="2K")
        self.assertEqual(b["contents"][0]["parts"][1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(b["generationConfig"]["imageConfig"], {"aspectRatio": "16:9", "imageSize": "2K"})


class Queue(unittest.TestCase):
    def setUp(self):
        self.ps = comfy_only()

    def tearDown(self):
        for p in self.ps:
            p.stop()

    def test_submit_is_idempotent_and_keyed(self):
        seen = {}

        def post(url, headers=None, json=None, timeout=None):
            seen.update(url=url, headers=headers)
            return Resp(201, {"request_id": "r1", "status": "IN_QUEUE",
                              "status_url": "https://api.comfy.org/v2/models/ltx/ltx-2-5-fast/requests/r1/status"})
        with mock.patch.object(comfy.requests, "post", post):
            out = M.submit({"engine": "comfy", "endpoint": "ltx/ltx-2-5-fast", "payload": {"prompt": "x"}})
        self.assertEqual(seen["url"], "https://api.comfy.org/v2/models/ltx/ltx-2-5-fast/requests")
        self.assertEqual(seen["headers"]["X-API-Key"], "ck-test")
        self.assertTrue(seen["headers"]["Idempotency-Key"])
        self.assertEqual(out["request_id"], "r1")

    def poll(self, status, result=None, kind="ltx/ltx-2-5-fast"):
        url = f"https://api.comfy.org/v2/models/{kind}/requests/r1/status"

        def get(u, headers=None, timeout=None):
            return status if u == url else result
        with mock.patch.object(comfy.requests, "get", get):
            return M.poll(url, kind)

    def test_waiting_then_a_clip(self):
        r = self.poll(Resp(200, {"status": "IN_QUEUE", "queue_position": 3}))
        self.assertEqual(r, {"pending": True, "progress": "in queue (3 ahead)"})
        r = self.poll(Resp(200, {"status": "COMPLETED", "response_url": "https://api.comfy.org/x/r1"}),
                      Resp(200, {"video": {"url": "https://cdn.example/out.mp4"},
                                 "thumbnail": "https://cdn.example/poster.jpg"}, {"X-Comfy-Credits-Used": "42"}))
        self.assertEqual(r, {"url": "https://cdn.example/out.mp4", "credits": "42", "cost": 0.1991})

    def test_inline_and_binary_results(self):
        png = io.BytesIO()
        Image.new("RGB", (4, 4)).save(png, "PNG")
        b64 = base64.b64encode(png.getvalue()).decode() * 40     # long enough to be a file
        r = self.poll(Resp(200, {"status": "COMPLETED"}),
                      Resp(200, {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png",
                                                                                       "data": b64}}]}}]}),
                      kind="vertexai/gemini-nano-banana-2.1")
        self.assertEqual(r["ext"], ".png")
        r = self.poll(Resp(200, {"status": "COMPLETED"}), Resp(200, None, {"Content-Type": "video/mp4"}, b"MP4DATA"))
        self.assertEqual((r["data"], r["ext"]), (b"MP4DATA", ".mp4"))
        self.assertEqual(M.fetch_output(r), (b"MP4DATA", ".mp4"))

    def test_failures_say_why(self):
        with self.assertRaises(M.RunError) as e:
            self.poll(Resp(200, {"status": "COMPLETED", "error_type": "content_policy_violation", "error": "blocked"}))
        self.assertEqual(e.exception.reason, "policy")
        for resp, reason in ((Resp(402, {"detail": "insufficient_credits"}), "credits"),
                             (Resp(409, {"detail": "x"}, {"X-Comfy-Error-Type": "concurrency_limit_exceeded"}), "busy"),
                             (Resp(422, {"detail": "duration must be 2..20"}), "validation"),
                             (Resp(401, {"detail": "bad key"}), "unavailable")):
            self.assertEqual(comfy._error(resp).reason, reason, resp.status_code)

    def test_a_hiccup_waits_and_a_stop_reaches_router(self):
        self.assertEqual(comfy._error(Resp(503, {"detail": "temporarily unavailable"})).reason, "rateLimit")
        url = "https://api.comfy.org/v2/models/bfl/flux-3-image/requests/r9/status"
        seen = []
        with mock.patch.object(comfy.requests, "put", lambda u, headers=None, timeout=None: seen.append(u) or Resp(202, {})), \
                mock.patch.object(comfy.requests, "get", lambda u, headers=None, timeout=None:
                                  Resp(200, {"status": "COMPLETED", "error_type": "cancelled"})):
            self.assertEqual(comfy.cancel(url), "cancelled")
        self.assertEqual(seen, ["https://api.comfy.org/v2/models/bfl/flux-3-image/requests/r9/cancel"])
        with mock.patch.object(comfy.requests, "put", lambda u, headers=None, timeout=None: Resp(409, {})):
            self.assertEqual(comfy.cancel(url), "finished")

    def test_a_stuck_queue_goes_direct(self):
        url = "https://api.comfy.org/v2/models/ltx/ltx-2-5-fast/requests/r5/status"
        comfy._line.clear()
        comfy._sync_until[0] = 0.0
        with mock.patch.object(comfy, "cancel", lambda u: "cancelled") as _, \
                mock.patch.object(comfy.requests, "get", lambda u, headers=None, timeout=None:
                                  Resp(200, {"status": "IN_QUEUE", "queue_position": 4})):
            self.assertTrue(M.poll(url, "ltx/ltx-2-5-fast")["pending"])
            comfy._line[url] = (4, comfy.time.time() - comfy.STALL - 1)     # four minutes on the same spot
            with self.assertRaises(M.RunError) as e:
                M.poll(url, "ltx/ltx-2-5-fast")
        self.assertEqual(e.exception.reason, "stalled")
        self.assertTrue(comfy.go_sync())
        seen = {}

        def post(u, headers=None, json=None, timeout=None):
            seen["url"] = u
            return Resp(200, {"video": {"url": "https://cdn.example/v.mp4"}}, {"X-Comfy-Credits-Used": "54.31"})
        with mock.patch.object(comfy._direct, "post", post):        # the direct route's keepalive session
            out = M.submit({"engine": "comfy", "endpoint": "ltx/ltx-2-5-fast", "payload": {"prompt": "x"}, "kind": "video"})
        self.assertEqual(seen["url"], "https://api.comfy.org/v2/models/ltx/ltx-2-5-fast")
        self.assertEqual(out, {"url": "https://cdn.example/v.mp4", "credits": "54.31", "cost": 0.2574})
        self.assertNotIn("poll_url", out)
        comfy._sync_until[0] = 0.0

    def test_a_clip_beats_its_poster(self):
        body = {"output": {"cover_url": "https://x/c.jpg", "video_url": "https://x/v.mp4"}}
        self.assertEqual(comfy.extract(body, "video"), {"url": "https://x/v.mp4"})
        self.assertEqual(comfy.extract({"data": [{"url": "https://x/i.png"}]}, "image"), {"url": "https://x/i.png"})


if __name__ == "__main__":
    unittest.main()
