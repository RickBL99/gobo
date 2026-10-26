"""The standalone shell around the studio: sign-in, the home page, the
Content Generator's address, the reference-to-prompts tool and the health
check. Hermetic like test_studio.py: no .env, no real keys, nothing sent.

    .venv/bin/python -m pytest tests/test_standalone.py
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

TMP = tempfile.mkdtemp(prefix="studio-shell-test-")
os.environ["STUDIO_DIR"] = os.path.join(TMP, "reels")
os.environ.update(STUDIO_ENV_FILE="", COMFY_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="",
                  STUDIO_PASSWORD="", STUDIO_USERS="", STUDIO_PREFIX="/studio")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from studio import jobs as J, prompts, settings, shell  # noqa: E402
from studio.app import app as APP  # noqa: E402


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(buf, "PNG")
    return buf.getvalue()


class SignIn(unittest.TestCase):
    def setUp(self):
        shell._failures.clear()
        self.p = mock.patch.object(settings, "PASSWORD", "s3cret")
        self.p.start()
        self.c = TestClient(APP)

    def tearDown(self):
        self.p.stop()

    def test_pages_ask_for_sign_in_and_the_api_says_401(self):
        r = self.c.get("/studio", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/login?next=%2Fstudio"))
        r = self.c.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual(r.headers["location"], "/login")
        self.assertEqual(self.c.get("/studio/reels").status_code, 401)
        self.assertEqual(self.c.post("/studio/reels", json={}).status_code, 401)

    def test_open_paths(self):
        self.assertEqual(self.c.get("/login").status_code, 200)
        self.assertEqual(self.c.get("/healthz").json()["sign_in"], True)
        tile = shell._samples()[0]
        self.assertEqual(self.c.get(tile).headers["content-type"], "image/webp")

    def test_env_file_comments_are_not_values(self):
        # 2026-10-25: a fresh clone's .env (copied from .env.example) made the comment the password
        path = os.path.join(tempfile.mkdtemp(prefix="studio-env-"), ".env")
        with open(path, "w") as f:
            f.write("T_BLANK=                  # one shared password\nT_SET=pw123   # the password\n"
                    "T_HASH=pw#1\nT_QUOTED=\"a # b\"\n# T_OFF=x\n")
        keys = ("T_BLANK", "T_SET", "T_HASH", "T_QUOTED", "T_OFF")
        with mock.patch.dict(os.environ, {k: "" for k in keys}):
            for k in keys:
                del os.environ[k]
            settings._load_env_file(path)
            self.assertEqual([os.environ.get(k) for k in keys], ["", "pw123", "pw#1", "a # b", None])

    def test_the_about_page_and_the_tour_need_no_sign_in(self):
        r = self.c.get("/about", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual(r.status_code, 200)
        page = r.text
        for words in ("Stewart Cohen Productions", "commercial production augmentation with real shot content",
                      "inspired by Higgsfield", "33×33×33 3D LUT", "MIT or Apache 2.0", "CC BY 4.0", settings.SOURCE_URL,
                      "Not affiliated with Comfy Org or Higgsfield"):
            self.assertIn(words.lower(), page.lower(), words)
        self.assertNotIn("Claude Code", page, "how it was built is told through the code, not the tools")
        self.assertIn('href="/login"', page)
        # Safari plays only a video it can ask for in pieces
        r = self.c.get(shell.TOUR, headers={"range": "bytes=0-99"})
        self.assertEqual((r.status_code, len(r.content), r.headers["content-type"]), (206, 100, "video/mp4"))

    def test_the_sign_in_page_shows_the_tour_and_where_gobo_comes_from(self):
        page = self.c.get("/login").text
        self.assertIn(f'src="{shell.TOUR}"', page)
        self.assertIn('poster="/static/tour/gobo-tour.jpg"', page)
        for words in ('href="/about"', settings.SOURCE_URL, "MIT or Apache 2.0", "comfy.org/open-source-higgsfield",
                      "Inspired by Higgsfield", 'class="made"'):
            self.assertIn(words, page)

    def test_the_about_pill_is_on_the_home_page_and_in_the_studio(self):
        self.c.post("/login", data={"name": "Ada", "password": "s3cret"})
        self.assertIn('class="chip about" href="/about"', self.c.get("/home").text)
        self.assertIn('href="/about"', self.c.get("/studio").text)
        self.assertIn('href="/">Open Gobo', self.c.get("/about").text, "signed in, the About page leads back in")

    def test_sign_in_files_work_under_the_name(self):
        r = self.c.post("/login", data={"name": "  Ada   Lovelace ", "password": "s3cret", "next": "/studio"},
                        follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/studio"))
        self.assertEqual(self.c.get("/studio/library").json()["me"], "Ada Lovelace")
        page = self.c.get("/home").text
        self.assertIn("Ada Lovelace", page)
        self.assertIn('href="/logout"', page)
        self.c.get("/logout")
        self.assertEqual(self.c.get("/studio/reels").status_code, 401)

    def test_wrong_password_and_no_name(self):
        r = self.c.post("/login", data={"name": "Ada", "password": "nope"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("That password isn&#39;t right", r.text)
        r = self.c.post("/login", data={"name": "", "password": "s3cret"})
        self.assertIn("Type your name", r.text)

    def test_next_never_leaves_the_app(self):
        for evil in ("https://evil.example/", "//evil.example/x"):
            r = self.c.post("/login", data={"name": "Ada", "password": "s3cret", "next": evil}, follow_redirects=False)
            self.assertEqual(r.headers["location"], "/")
            self.c.get("/logout")

    def test_too_many_tries(self):
        for _ in range(shell.MAX_FAILURES):
            self.c.post("/login", data={"name": "Ada", "password": "nope"})
        r = self.c.post("/login", data={"name": "Ada", "password": "s3cret"}, follow_redirects=False)
        self.assertEqual(r.status_code, 401)
        self.assertIn("Too many tries", r.text)

    def test_named_users(self):
        with mock.patch.object(settings, "USERS", {"Rick": "a1", "Kelly": "b2"}):
            self.assertIsNone(shell.check_login("Rick", "b2"))
            self.assertEqual(shell.check_login("rick", "a1"), "Rick")       # the account's spelling wins
            self.assertIsNone(shell.check_login("Stranger", "s3cret"))      # the shared password no longer works


class SignInOff(unittest.TestCase):
    def test_everyone_is_local(self):
        c = TestClient(APP)
        self.assertEqual(c.get("/studio/library").json()["me"], "local")
        page = c.get("/home").text
        self.assertIn("Sign-in is off", page)
        self.assertNotIn('href="/logout"', page)
        self.assertEqual(c.get("/login", follow_redirects=False).status_code, 303)


class Pages(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(APP)

    def test_home(self):
        r = self.c.get("/home")
        self.assertEqual(r.status_code, 200)
        for link in ('href="/studio"', 'href="/studio/stills"', 'href="/studio?open=board"', 'href="/studio/guide"'):
            self.assertIn(link, r.text)

    def test_the_front_door_is_the_director(self):
        # the contest's front door: "/" opens the studio page on the Director
        page = self.c.get("/").text
        self.assertIn("const DIRECTOR = true;", page)
        self.assertIn("/static/director.js", page)
        self.assertIn('href="/home"', page, "the studio's Home button goes to the page with every tool")
        self.assertIn("const DIRECTOR = false;", self.c.get("/studio").text, "the studio itself opens on the studio")
        self.assertIn("const DIRECTOR_ON = true;", self.c.get("/studio").text, "and offers the Director")
        self.assertIn("const DIRECTOR_ON = false;", self.c.get("/studio/stills").text, "the Content Generator doesn't")
        # STUDIO_FRONT=studio: everything as it was before the Director
        with mock.patch.object(settings, "FRONT", "studio"):
            home = self.c.get("/").text
            self.assertIn('href="/studio/stills"', home)
            self.assertNotIn("director.js", home)
            self.assertIn("const DIRECTOR_ON = false;", self.c.get("/studio").text)
            self.assertIn("const DIRECTOR = true;", self.c.get("/direct").text, "the Director is still at /direct")
        # switched off, the front door falls back to the home page instead of failing
        with mock.patch.dict(os.environ, {"STUDIO_ENABLED": "0"}):
            self.assertEqual(self.c.get("/").status_code, 200)
            self.assertEqual(self.c.get("/direct").status_code, 503)

    def test_the_release_is_on_the_home_page_and_the_health_check(self):
        with mock.patch.object(settings, "RELEASE", "Take 7"), mock.patch.object(settings, "RELEASE_DATE", "2026-10-25"), \
                mock.patch.object(settings, "RELEASE_ID", "take-7"):
            page = self.c.get("/home").text
            self.assertIn('class="take"', page)
            self.assertIn("Take 7", page)
            self.assertIn("2026-10-25", page)
            self.assertEqual(self.c.get("/healthz").json()["release_id"], "take-7")
        take, date = settings._release()                      # what studio/release.json says
        self.assertEqual(settings.RELEASE_ID, f"take-{take}")

    def test_content_generator_is_the_studio_in_stills(self):
        r = self.c.get("/studio/stills")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Content Generator", r.text)
        self.assertIn('href="/studio"', r.text)                 # its way over to the full studio
        self.assertIn("/static/standalone.js", r.text)
        self.assertNotIn("/api/", r.text)                       # nothing points back at the back office

    def test_studio_and_guide_have_no_back_office_links(self):
        for path in ("/", "/direct", "/studio", "/studio/guide", "/studio/app.js", "/static/director.js"):
            body = self.c.get(path).text
            self.assertNotIn("/api/", body, path)
            self.assertNotIn("back office", body.lower(), path)

    def test_kill_switch_covers_the_generator(self):
        with mock.patch.dict(os.environ, {"STUDIO_ENABLED": "0"}):
            self.assertEqual(self.c.get("/studio/stills").status_code, 503)
            self.assertEqual(self.c.post("/studio/prompt-variants", files={"file": ("a.png", png(), "image/png")}).status_code, 503)



class Shares(unittest.TestCase):
    """Share: a public page for one finished take, only when its maker asks."""
    def setUp(self):
        shell._failures.clear()
        self.p = mock.patch.object(settings, "PASSWORD", "s3cret")
        self.p.start()
        self.c = TestClient(APP)
        self.c.post("/login", data={"name": "Ada", "password": "s3cret"})
        self.reel = self.c.post("/studio/reels", json={"name": "The Last Watch"}).json()["id"]

    def tearDown(self):
        self.p.stop()

    def film(self, **kw):
        t = J.new_take(self.reel, 1, 0, kind="video", status="done", file="cut-abc.mp4", poster="cut-abc.jpg",
                       label="The cut", seconds=15.2, w=1280, h=720, by="Ada", **kw)
        os.makedirs(os.path.dirname(J.out_path(self.reel, t["file"])), exist_ok=True)
        open(J.out_path(self.reel, t["file"]), "wb").write(b"\0\0\0\x18ftypmp42 a film")
        open(J.out_path(self.reel, t["poster"]), "wb").write(png())
        return t

    def test_share_unshare_and_the_public_page(self):
        t = self.film()
        r = self.c.post(f"/studio/reels/{self.reel}/takes/{t['id']}/share", json={"title": "The Last Watch"}).json()
        url, token = r["url"], r["take"]["share"]
        self.assertEqual(url, f"/f/{token}")
        again = self.c.post(f"/studio/reels/{self.reel}/takes/{t['id']}/share", json={}).json()
        self.assertEqual(again["url"], url, "sharing again gives the same link")
        self.assertTrue(os.path.exists(os.path.join(J.reel_dir(self.reel), J.SHARED)))
        anyone = TestClient(APP)                                   # not signed in
        page = anyone.get(url)
        self.assertEqual(page.status_code, 200)
        for want in ("The Last Watch", f'src="{url}/film"', f'poster="{url}/poster"', 'property="og:video"',
                     'name="robots" content="noindex"', "Make your own film"):
            self.assertIn(want, page.text)
        self.assertEqual(anyone.get(f"{url}/film").content[4:8], b"ftyp")
        self.assertEqual(anyone.get(f"{url}/poster").headers["content-type"], "image/jpeg")
        self.assertEqual(anyone.get("/studio/reels").status_code, 401, "the link opens the film, nothing else")
        # unshared: the link is dead, and the reel loses its marker
        self.c.delete(f"/studio/reels/{self.reel}/takes/{t['id']}/share")
        self.assertEqual(anyone.get(url).status_code, 404)
        self.assertEqual(anyone.get(f"{url}/film").status_code, 404)
        self.assertFalse(os.path.exists(os.path.join(J.reel_dir(self.reel), J.SHARED)))

    def test_only_a_finished_take_and_only_a_real_token(self):
        t = J.new_take(self.reel, 1, 0, kind="video", status="running", by="Ada")
        self.assertEqual(self.c.post(f"/studio/reels/{self.reel}/takes/{t['id']}/share", json={}).status_code, 400)
        anyone = TestClient(APP)
        for bad in ("nope", "x" * 40, "AAAAAAAAAAAA", "a.b.c.d.e.f"):
            self.assertEqual(anyone.get(f"/f/{bad}").status_code, 404, bad)
        # a deleted take takes its link with it
        f = self.film()
        url = self.c.post(f"/studio/reels/{self.reel}/takes/{f['id']}/share", json={}).json()["url"]
        self.c.delete(f"/studio/reels/{self.reel}/takes/{f['id']}")
        self.assertEqual(anyone.get(url).status_code, 404)

    def test_a_shared_reel_is_the_last_the_trim_clears(self):
        old = self.c.post("/studio/reels", json={"name": "old"}).json()["id"]
        t = J.new_take(old, 1, 0, kind="video", status="done", file="a.mp4", by="Ada")
        open(J.out_path(old, "a.mp4"), "wb").write(b"x" * 3000)
        shared = self.film()
        self.c.post(f"/studio/reels/{self.reel}/takes/{shared['id']}/share", json={})
        long_ago = 1_000_000_000
        for rid in (old, self.reel):
            for d in (J.reel_dir(rid), os.path.join(J.reel_dir(rid), "takes"), os.path.join(J.reel_dir(rid), "out")):
                os.utime(d, (long_ago, long_ago))
        os.utime(J.reel_dir(self.reel), (long_ago - 10, long_ago - 10))   # the shared one is even older
        with mock.patch.object(J, "KEEP_GB", 1e-12):
            J.trim_to_size(force=True)
        self.assertFalse(os.path.isdir(J.reel_dir(old)), "the unshared reel went first")


class Prompts(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(APP)

    def ask(self, **data):
        return self.c.post("/studio/prompt-variants", files={"file": ("ref.png", png(), "image/png")},
                           data={"image_style": "photography", "num_variants": "2", "variation_level": "5", **data}).json()

    def test_no_key_says_so(self):
        self.assertIn("needs OPENAI_API_KEY, ANTHROPIC_API_KEY or COMFY_API_KEY", self.ask()["error"])

    def test_claude_when_there_is_no_openai_key(self):
        seen = {}

        def fake(system, content, schema, **kw):
            seen.update(content=content, schema=schema)
            return {"prompts": ["a red barn at dawn", "a red barn at dusk, " + prompts.TAIL], "negative_prompts": ["blurry"]}
        with mock.patch.object(settings, "ANTHROPIC_API_KEY", "k"), mock.patch.object(prompts.M, "_claude_json", fake):
            out = self.ask(user_notes="no people")
        self.assertEqual(out["by"], "claude")
        self.assertEqual(seen["content"][0]["type"], "image")
        self.assertIn("ART DIRECTOR'S NOTES", seen["content"][1]["text"])
        self.assertTrue(out["prompts"][0].endswith(prompts.TAIL))           # the closing tags are guaranteed
        self.assertEqual(out["prompts"][1].count(prompts.TAIL), 1)          # ...and not doubled
        self.assertEqual(len(out["negative_prompts"]), 2)                   # padded to one per prompt

    def test_gpt4o_when_there_is(self):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": '```json{"prompts": ["x"], "negative_prompts": ["y"]}```'}}]}
        with mock.patch.object(settings, "OPENAI_API_KEY", "k"), \
                mock.patch.object(prompts.requests, "post", return_value=R()) as post:
            out = self.ask(image_style="illustration")
        self.assertEqual((out["by"], out["prompts"]), ("gpt-4o", ["x"]))      # no photo tags on an illustration
        self.assertEqual(post.call_args.kwargs["json"]["model"], "gpt-4o")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
