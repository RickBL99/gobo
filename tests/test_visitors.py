"""Bring your own key (STUDIO_KEYS=visitor), the public demo: a visitor signs
in with their own Comfy key, sees only their own reels, every Comfy call made
for them (from a page or from the pump) runs on their key, and the server's own
keys are never spent. Comfy itself is never called: check_key and the runs are
mocked.

    .venv/bin/python -m pytest tests/test_visitors.py
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

TMP = tempfile.mkdtemp(prefix="studio-visitor-test-")
os.environ["STUDIO_DIR"] = os.path.join(TMP, "reels")
os.environ.update(STUDIO_ENV_FILE="", COMFY_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="",
                  STUDIO_PASSWORD="", STUDIO_USERS="", STUDIO_PREFIX="/studio")

from fastapi.testclient import TestClient  # noqa: E402

from studio import comfy, jobs as J, models as M, routes, settings, shell, visitors as V  # noqa: E402
from studio.app import app as APP  # noqa: E402

KEYS = {"key-aaa": "", "key-bbb": ""}           # what Comfy accepts (check_key's "" is yes)


def check_key(key):
    return KEYS.get(key.strip(), "Comfy didn't accept that key. Check it at platform.comfy.org.")


class Visitors(unittest.TestCase):
    def setUp(self):
        shell._failures.clear()
        routes._bal.clear()
        self.patches = [mock.patch.object(settings, "VISITOR_KEYS", True),
                        mock.patch.object(settings, "COMFY_API_KEY", "THE-SERVER-KEY"),
                        mock.patch.object(comfy, "check_key", check_key)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        V._keys.clear()

    def visitor(self, key):
        c = TestClient(APP)
        r = c.post("/login", data={"key": key, "next": "/"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text[-300:])
        return c

    def test_sign_in_asks_for_a_comfy_key(self):
        c = TestClient(APP)
        r = c.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual(r.headers["location"], "/login")
        page = c.get("/login").text
        self.assertIn('name="key"', page)
        self.assertNotIn('name="password"', page)
        self.assertIn("No key yet?", page)
        about = c.get("/about", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual(about.status_code, 200, "a visitor can read about Gobo before getting a key")
        self.assertIn("bring your own Comfy API key", about.text)
        r = c.post("/login", data={"key": "nope"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("didn&#39;t accept that key", r.text)
        self.assertEqual(c.get("/healthz").json()["keys"], "visitor")

    def test_each_visitor_sees_only_their_own_reels(self):
        a, b = self.visitor("key-aaa"), self.visitor("key-bbb")
        reel = a.post("/studio/reels", json={"name": "Mine"}).json()
        self.assertEqual(reel["owner"], V.visitor_id("key-aaa"))
        self.assertEqual([r["id"] for r in a.get("/studio/reels").json()["reels"]], [reel["id"]])
        self.assertEqual(a.get(f"/studio/reels/{reel['id']}").status_code, 200)
        self.assertEqual(b.get("/studio/reels").json()["reels"], [])
        for path in (f"/studio/reels/{reel['id']}", f"/studio/reels/{reel['id']}/board",
                     f"/studio/reels/{reel['id']}/file/x.png", f"/studio/reels/{reel['id']}/download"):
            self.assertEqual(b.get(path).status_code, 404, path)
        self.assertEqual(b.patch(f"/studio/reels/{reel['id']}", json={"name": "Taken"}).status_code, 404)
        self.assertEqual(a.get(f"/studio/reels/{reel['id']}").json()["reel"]["name"], "Mine")
        self.assertEqual(b.post("/studio/library/claim", json={"items": []}).status_code, 403)
        self.assertIn(b.post("/studio/catalogue/refresh").status_code, (404, 405), "there's no catalogue to refresh")
        # the Library too, "Show everyone's" included: a visitor's work is theirs alone
        t = J.new_take(reel["id"], 1, 0, kind="image", status="done", file="001A-abcdef.jpg", by=V.visitor_id("key-aaa"))
        open(J.out_path(reel["id"], t["file"]), "wb").write(b"\xff\xd8\xff\xd9")
        J._lib.clear()
        mine = a.get("/studio/library?who=all").json()
        self.assertEqual([x["id"] for x in mine["items"]], [t["id"]], mine.get("counts"))
        theirs = b.get("/studio/library?who=all").json()
        self.assertEqual((theirs["items"], theirs["counts"]["all"]), ([], 0))
        r = b.get(f"/studio/library/download?items={reel['id']}:{t['id']}")
        self.assertNotEqual(r.headers.get("content-type"), "application/zip", "nobody downloads another visitor's take")

    def test_comfy_calls_run_on_the_visitors_key_never_the_servers(self):
        self.assertEqual(comfy.api_key(), "", "outside a visitor's request there is no key at all")
        a, b = self.visitor("key-aaa"), self.visitor("key-bbb")
        with mock.patch.object(comfy, "balance", lambda: {"key-aaa": 11.0, "key-bbb": 22.0}[comfy.api_key()]):
            self.assertEqual(a.get("/studio/credits").json()["credits"], 11.0)
            self.assertEqual(b.get("/studio/credits").json()["credits"], 22.0, "each visitor's own, not a cached one")

    def test_the_pump_runs_a_take_on_its_makers_key(self):
        a = self.visitor("key-aaa")
        vid = V.visitor_id("key-aaa")
        reel = a.post("/studio/reels", json={}).json()["id"]
        seen = []

        def submit(call):
            seen.append(comfy.api_key())
            return {"poll_url": comfy.BASE + "/v2/models/x/requests/1", "request_id": "1"}

        t = J.new_take(reel, 1, 0, model="ltx/ltx-2-5-fast", kind="video", request={}, by=vid, status="running")
        with mock.patch.object(M, "build", lambda *a, **k: {"endpoint": "x", "payload": {}}), \
                mock.patch.object(M, "submit", submit):
            routes._start(reel, t["id"])
        self.assertEqual(seen, ["key-aaa"])
        self.assertTrue(J.get_take(reel, t["id"])["poll_url"])
        # a restart forgets every key: a take that hasn't gone out says so, and nothing is charged
        V._keys.clear()
        t2 = J.new_take(reel, 2, 0, model="ltx/ltx-2-5-fast", kind="video", request={}, by=vid, status="running")
        with mock.patch.object(M, "submit", submit):
            routes._start(reel, t2["id"])
        got = J.get_take(reel, t2["id"])
        self.assertEqual(got["status"], "failed")
        self.assertIn("Sign in again", got["error"])
        self.assertEqual(seen, ["key-aaa"])
        # and one already rendering waits for them rather than polling on nothing
        with mock.patch.object(M, "poll", side_effect=AssertionError("polled without a key")):
            routes._advance(reel, t["id"])

    def test_a_forgotten_key_sends_the_visitor_back_to_sign_in(self):
        a = self.visitor("key-aaa")
        V._keys.clear()                                   # the demo restarted
        r = a.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/login"))
        r = a.get("/studio/reels")
        self.assertEqual((r.status_code, r.json().get("signin")), (401, True))
        # and the sign-in page itself must not send them back home: that was a loop
        # (2026-10-25, every returning visitor after the Take 3 restart)
        r = a.get("/login", follow_redirects=False)
        self.assertEqual(r.status_code, 200, r.headers.get("location"))
        self.assertIn('name="key"', r.text)
        r = a.get("/", headers={"accept": "text/html"})
        self.assertEqual((r.status_code, str(r.url).endswith("/login")), (200, True))
        self.assertEqual(self.visitor("key-aaa").get("/studio/reels").status_code, 200, "signing in again works")

    def test_sign_out_forgets_the_key(self):
        a = self.visitor("key-aaa")
        self.assertTrue(V.key_for(V.visitor_id("key-aaa")))
        r = a.get("/logout", follow_redirects=False)
        self.assertEqual(r.headers["location"], "/login?bye=1")
        self.assertEqual(V.key_for(V.visitor_id("key-aaa")), "")

    def test_off_the_demo_nothing_changes(self):
        with mock.patch.object(settings, "VISITOR_KEYS", False):
            self.assertEqual(comfy.api_key(), "THE-SERVER-KEY")
            self.assertTrue(V.owns(None))
            with V.acting_for("anyone") as keyed:
                self.assertTrue(keyed)
                self.assertEqual(comfy.api_key(), "THE-SERVER-KEY")


if __name__ == "__main__":
    unittest.main()
