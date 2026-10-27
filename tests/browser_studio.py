"""Gobo's studio page in a real browser (headless Chrome, DevTools
protocol). Nothing is generated and nothing is spent: the checks drive the
controls, and the stage is fed takes in memory.

    python3 tests/browser_studio.py [base-url] [screenshot-dir]

base-url defaults to the local app (http://127.0.0.1:8020). With a password
set it signs in first (STUDIO_PASSWORD, or the one in .env). Exit 0 = every check passed.
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8020").rstrip("/")
SHOTS = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="studio-shots-")
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PORT = 9333


def _login_password():
    """The password to sign the browser in with: STUDIO_PASSWORD, from the
    environment or the .env the server reads."""
    pw = os.environ.get("STUDIO_PASSWORD", "")
    if not pw and os.path.isfile(".env"):
        for line in open(".env", encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            if k.strip() == "STUDIO_PASSWORD":
                pw = v.strip().strip('"').strip("'")
    return pw


class Tab:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.n = 0
        self.errors = []

    def call(self, method, **params):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                self.errors.append((d.get("exception") or {}).get("description") or d.get("text"))
            if msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] == "error":
                self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in msg["params"]["args"]))
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def js(self, expr):
        # each snippet in its own block, so its consts don't collide with the last one's
        r = self.call("Runtime.evaluate", expression="{" + expr + "\n}", awaitPromise=True, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description") or r["exceptionDetails"])
        return r.get("result", {}).get("value")

    def shot(self, name, w=1600, h=1000):
        self.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 700)
        time.sleep(0.4)
        data = self.call("Page.captureScreenshot", format="png")["data"]
        path = os.path.join(SHOTS, f"{name}.png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path


RESULTS = []


def _bars_clip(vf: str = "") -> str:
    """Two seconds of colour bars as an MP4, base64, for the Colour checks (`vf`: an
    ffmpeg filter that puts the bars off, as an extension drifts)."""
    out = os.path.join(tempfile.mkdtemp(prefix="bars-"), "bars.mp4")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=2",
                    *(["-vf", vf] if vf else []), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-movflags", "+faststart", out], check=True)
    with open(out, "rb") as f:
        return base64.b64encode(f.read()).decode()


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))


def main():
    os.makedirs(SHOTS, exist_ok=True)
    prof = tempfile.mkdtemp(prefix="studio-chrome-")
    chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                               "--no-first-run", "--disable-gpu", f"--remote-allow-origins=http://127.0.0.1:{PORT}", "--window-size=1600,1000", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except Exception:
                time.sleep(0.2)
        page = next(t for t in tabs if t["type"] == "page")
        tab = Tab(page["webSocketDebuggerUrl"])
        tab.call("Runtime.enable")
        tab.call("Page.enable")
        tab.call("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
        if _login_password():
            tab.call("Page.navigate", url=f"{BASE}/login")
            time.sleep(1.5)
            tab.js("fetch('/login', {method: 'POST', redirect: 'manual', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: 'name=browser-test&password=' + encodeURIComponent(" + json.dumps(_login_password()) + ")}).then(r => r.type)")
        tab.call("Page.navigate", url=f"{BASE}/studio")
        time.sleep(3)
        tab.js("localStorage.clear(); 1")
        tab.call("Page.reload")
        time.sleep(3)

        n_img = tab.js("byKind('image').length")
        n_vid = tab.js("byKind('video').length")
        check("catalogue loaded", n_img > 50 and n_vid > 40, f"{n_img} image, {n_vid} video")
        bar = json.loads(tab.js("""JSON.stringify((() => { const b = [...document.body.children].find(e => !/^(STYLE|SCRIPT)$/.test(e.tagName)), r = $('.ssbar') && $('.ssbar').getBoundingClientRect();
          return {first: !!b && b.classList.contains('ssbar'), href: $('.ssbar') && $('.ssbar').href, text: $('.ssbar') && $('.ssbar').textContent.replace(/\\s+/g, ' ').trim(), top: r && r.top, h: r && r.height}; })())"""))
        check("the top strip: Need real video? Visit SuperStock", bar["first"] and bar["top"] == 0 and bar["h"] >= 26
              and "superstock.com" in (bar["href"] or "") and bar["text"] == "Need real video? Visit SuperStock →", bar)
        check("opens in video", tab.js("S.mode") == "video" and tab.js("$('#modeVideo').classList.contains('on') && model().kind === 'video'"))
        # a mode saved before video became the default is overridden once, then a choice sticks
        tab.js("localStorage.setItem('studio.shot', JSON.stringify({mode: 'image'})); localStorage.removeItem('studio.videoDefault'); 1")
        tab.call("Page.reload"); time.sleep(3)
        check("old saved image mode lands on video once", tab.js("S.mode") == "video")
        tab.js("$('#modeImage').click(); saveLocal(); 1")
        tab.call("Page.reload"); time.sleep(3)
        check("a chosen mode is remembered", tab.js("S.mode") == "image" and tab.js("$('#modeImage').classList.contains('on')"))
        check("a model is chosen", tab.js("!!model() && model().kind === 'image'"), tab.js("model() && model().label"))
        # the balance is live, says what's committed, and turns amber when low (2026-10-24: it showed the balance from when the page opened)
        tab.js("""window._cf = window.fetch; window._cr = {credits: 51.03, rendering: 7.5, waiting: 2.5, left: 41.03, low: true, low_at: 50};
          window.fetch = async (u, o) => String(u).includes('/credits') ? new Response(JSON.stringify(_cr), {headers: {'Content-Type': 'application/json'}}) : _cf(u, o);
          loadCredits(true); 1""")
        time.sleep(0.3)
        cr = tab.js("$('#credits').textContent + ' | ' + $('#credits').title")
        check("credits: live, with what's in progress, amber with a top-up link when low", "Comfy $51.03 · ≈$10.00 in progress" in cr and "top up" in cr
              and "about $41.03 is left" in cr and tab.js("$('#credits').classList.contains('low') && !!$('#credits a[href*=comfy]')"), cr)
        tab.js("_cr = {credits: 120, rendering: 0, waiting: 0, left: 120, low: false, low_at: 50}; $('#credits').click(); 1"); time.sleep(0.3)
        check("credits: a click asks again; nothing pending, nothing low", tab.js("$('#credits').textContent") == "Comfy $120.00"
              and tab.js("!$('#credits').classList.contains('low')"), tab.js("$('#credits').textContent"))
        tab.js("window.fetch = window._cf; 1")
        check("generate waits for a scene", tab.js("$('#genBtn').disabled"))
        check("direction rows", tab.js("$$('#shotlist .row').length") == 8, tab.js("$$('#shotlist .row').length"))

        # type a scene → compile shows up
        tab.js("const s=$('#scene'); s.value='A lighthouse keeper climbs the spiral stairs at dusk.'; s.dispatchEvent(new Event('input')); 1")
        time.sleep(0.8)
        seg = tab.js("$('#segs').textContent")
        check("prompt compiles live", "lighthouse keeper" in seg and "Shot on a large-format cinema camera" in seg, seg[:120])
        check("generate enabled", tab.js("!$('#genBtn').disabled"))
        check("still-or-video is the first control", tab.js("$('#panel').firstElementChild.matches('.seg.big') && $('#modeImage').classList.contains('on')"))
        check("generate says what it makes", "Generate image" in tab.js("$('#genBtn').textContent"), tab.js("$('#genBtn').textContent"))
        check("cost line", "≈" in tab.js("$('#costline').textContent") or "varies" in tab.js("$('#costline').textContent"),
              tab.js("$('#costline').textContent"))

        tab.js("$('#tPlus').click(); 1")
        cost = tab.js("$('#costline').textContent")
        check("takes stepper prices the shot", "2 takes" in cost and "$0.00" not in cost.replace("$0.00", "$0.00", 0) and not cost.endswith("$0.000"), cost)
        tab.js("$('#tMinus').click(); 1")

        # camera popover: pick 85mm + 16mm film
        tab.js("$('#shotlist .row[data-g=camera]').click(); 1")
        time.sleep(0.3)
        check("camera popover opens", tab.js("!$('#pop').classList.contains('hidden') && $$('#pop .camopt').length > 20"))
        tab.shot("02-camera")
        tab.js("$('#pop [data-p=focal][data-o=\"85\"]').click(); $('#pop [data-p=body][data-o=film16]').click(); 1")
        time.sleep(0.8)
        seg = tab.js("$('#segs').textContent")
        check("camera choice reaches the prompt", "85mm" in seg and "grainy 16mm film" in seg, seg[:160])
        tab.js("closePop(); 1")

        # framing tiles
        tab.js("$('#shotlist .row[data-g=shot]').click(); 1")
        time.sleep(0.3)
        check("framing tiles drawn", tab.js("$$('#pop .tile svg').length") >= 10)
        tab.shot("03-framing")
        tab.js("$('#pop .tile[data-o=xwide]').click(); closePop(); 1")
        time.sleep(0.6)
        check("framing leads the prompt", tab.js("$('#segs').textContent").startswith("Extreme wide shot"))

        # grade popover has the palette editor
        tab.js("$('#shotlist .row[data-g=grade]').click(); 1")
        time.sleep(0.3)
        check("grade has palette tools", tab.js("!!$('#palFromImg')"))
        tab.js("closePop(); 1")

        # light / film / look show rendered samples (tools/build_studio_samples.py)
        for g in ("light", "film", "look"):
            tab.js(f"$('#shotlist .row[data-g={g}]').click(); 1")
            time.sleep(1.5)
            n_opts = tab.js(f"CAT.groups.{g}.options.length")
            got = tab.js("$$('#pop .tile img').filter(i => i.complete && i.naturalWidth > 0).length")
            check(f"{g} tiles are samples", got == n_opts, f"{got} of {n_opts}")
            if g == "light":
                tab.shot("04-light-samples")
            tab.js("closePop(); 1")
        check("row thumb uses the sample", "/static/samples/light-" in tab.js("$('#shotlist .row[data-g=light] .th').innerHTML"))

        tab.shot("01-image-mode")

        # model sheet: search + filters
        tab.js("$('#modelBtn').click(); 1")
        time.sleep(0.3)
        total = tab.js("$$('#modelSheet .mitem').length")
        tab.js("const q=$('#mq'); q.value='flux'; q.dispatchEvent(new Event('input')); 1")
        time.sleep(0.3)
        flux = tab.js("$$('#modelSheet .mitem').length")
        check("model search narrows", 0 < flux < total, f"{flux} of {total}")
        tab.js("const q=$('#mq'); q.value=''; q.dispatchEvent(new Event('input')); 1")
        time.sleep(0.2)
        tab.js("$('#modelSheet [data-f=refs]').click(); 1")
        time.sleep(0.3)
        refs_only = tab.js("$$('#modelSheet .mitem').length")
        check("reference filter", 0 < refs_only < total, f"{refs_only}")
        tab.shot("04-model-sheet")
        tab.js("$('#modelSheet [data-f=refs]').click(); 1")
        tab.js("$('#modelSheet [data-m=\"bfl/flux-kontext-max\"]') ? 1 : 0")
        # each row's ? opens that model's entry in the guide's directory, without choosing it
        tab.js("window._opened = []; window._open0 = window.open; window.open = (u, n) => { window._opened.push([u, n]); return null; }; 1")
        was = tab.js("S.model.image")
        tab.js("$('#modelSheet [data-m=\"bfl/flux-kontext-max\"] [data-about]').click(); 1"); time.sleep(0.2)
        check("picker: ? opens the model's directory entry", tab.js("window._opened") == [["/studio/guide#m-bfl-flux-kontext-max", "studioguide"]]
              and tab.js("S.model.image") == was, tab.js("window._opened"))
        check("picker: links the whole directory", tab.js("$('#modelSheet a[href$=\"#directory\"]') !== null"))
        tab.js("window.open = window._open0; 1")
        tab.js("closePop(); 1")

        # an edit-only model blocks until a reference is attached
        edit = tab.js("(byKind('image').find(m => m.requires_ref) || {}).id")
        if edit:
            tab.js(f"S.model.image = {json.dumps(edit)}; onShotChange(); 1")
            time.sleep(0.3)
            check("edit model asks for a reference", tab.js("$('#genBtn').disabled") and "reference" in tab.js("$('#blocknote').textContent"),
                  tab.js("$('#blocknote').textContent"))
            tab.js("S.model.image = byKind('image')[0].id; onShotChange(); 1")

        # shootout
        tab.js("openModelSheet(true); 1")
        time.sleep(0.3)
        tab.js("$$('#modelSheet .mitem:not(.on)').slice(0,2).forEach(b => b.click()); 1")
        time.sleep(0.3)
        tab.js("closePop(); onShotChange(); 1")
        check("shootout button", "Run shootout · 3 models" in tab.js("$('#genBtn').textContent"), tab.js("$('#genBtn').textContent"))
        tab.js("S.shootout.image = []; onShotChange(); 1")

        # the model sheet's Video tab switches the studio to video, and back
        tab.js("$('#modelBtn').click(); 1"); time.sleep(0.3)
        tab.js("$('#mKind [data-k=video]').click(); 1"); time.sleep(0.3)
        check("model sheet video tab switches mode", tab.js("S.mode") == "video" and tab.js("$('#modeVideo').classList.contains('on')")
              and tab.js("$$('#modelSheet [data-m]').length") > 50, tab.js("$$('#modelSheet [data-m]').length"))
        check("video mode says clip", "Generate clip" in tab.js("$('#genBtn').textContent"), tab.js("$('#genBtn').textContent"))
        tab.js("$('#mKind [data-k=image]').click(); 1"); time.sleep(0.3)
        check("and back to image", tab.js("S.mode") == "image")
        tab.js("closePop(); 1")

        # video mode
        tab.js("$('#modeVideo').click(); 1")
        time.sleep(0.6)
        check("video model", tab.js("model().kind") == "video", tab.js("model().label"))
        check("movement row appears", tab.js("!!$('#shotlist .row[data-g=move]')"))
        check("duration control", tab.js("!!$('#fDur')"))
        check("starting-frame slot", "Starting frame" in tab.js("$('#refrow').textContent"), tab.js("$('#refrow').textContent"))
        tab.js("$('#shotlist .row[data-g=move]').click(); 1")
        time.sleep(0.3)
        tab.js("$('#pop .tile[data-o=crane]').click(); closePop(); 1")
        time.sleep(0.8)
        check("movement reaches the prompt", "Camera movement: crane shot rising up." in tab.js("$('#segs').textContent"))
        check("video enhance phrase", "continuous motion" in tab.js("$('#segs').textContent"))
        tab.shot("05-video-mode")

        # stage with a fake reel: an image and a clip, compare + zoom
        img = "data:image/svg+xml;base64," + base64.b64encode(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900"><rect width="1600" height="900" fill="#345"/>'
            b'<circle cx="800" cy="450" r="200" fill="#f2c14e"/></svg>').decode()
        img2 = "data:image/svg+xml;base64," + base64.b64encode(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900"><rect width="1600" height="900" fill="#523"/>'
            b'<rect x="600" y="250" width="400" height="400" fill="#9ad1ff"/></svg>').decode()
        fake = [
            {"id": "0000000000001-aaaaaa", "reel": "r", "shot": 1, "letter": "A", "kind": "image", "status": "done", "label": "Seedream 5.0 Pro",
             "model": "seedream-5-pro", "aspect": "16:9", "w": 1600, "h": 900, "url": img, "thumb_url": img, "prompt": "p", "elapsed": 12.1,
             "settings": {"scene": "x"}, "created": 1},
            {"id": "0000000000002-bbbbbb", "reel": "r", "shot": 1, "letter": "B", "kind": "image", "status": "done", "label": "FLUX 3",
             "model": "flux-3", "aspect": "16:9", "w": 1600, "h": 900, "url": img2, "thumb_url": img2, "prompt": "p", "created": 2},
            {"id": "0000000000003-cccccc", "reel": "r", "shot": 2, "letter": "A", "kind": "video", "status": "running", "label": "Veo 3.1",
             "model": "veo-3.1", "aspect": "16:9", "prompt": "p", "created": time.time() - 40, "started": time.time() - 40},
            {"id": "0000000000004-dddddd", "reel": "r", "shot": 2, "letter": "B", "kind": "video", "status": "failed", "reason": "policy",
             "label": "Seedance 2.5", "model": "seedance-2.5", "aspect": "16:9", "prompt": "p", "error": "Blocked.", "created": 3},
        ]
        tab.js(f"S.reel = {{id: 'r', name: 'Test'}}; S.takes = {json.dumps(fake)}; S.current = '0000000000001-aaaaaa'; renderStrip(); renderStage(); 1")
        time.sleep(0.5)
        check("strip groups by shot", tab.js("$$('#strip .shotgrp').length") == 2)
        check("stage shows the take", tab.js("!!$('#mainImg')"))
        acts = tab.js("$$('#actions [data-a]').map(b => b.dataset.a).join(',')")
        for a in ("again", "asref", "animate", "upscale", "cutout", "compare", "zoom"):
            check(f"action {a}", a in acts, acts)
        tab.js("const sel=$('#actions select[data-a=compare]'); sel.value='0000000000002-bbbbbb'; sel.dispatchEvent(new Event('change')); 1")
        time.sleep(0.4)
        check("compare slider", tab.js("!!$('#cmp .handle')"))
        tab.shot("06-compare")
        tab.js("select('0000000000003-cccccc'); 1")
        time.sleep(1.2)
        check("rendering state with clock", tab.js("!!$('#elapsed') && $('#elapsed').textContent !== '0:00'"), tab.js("$('#elapsed') && $('#elapsed').textContent"))
        check("slow-note after 30s", tab.js("!$('#slownote').classList.contains('hidden')"))
        tab.js("select('0000000000004-dddddd'); 1")
        time.sleep(0.3)
        check("blocked notice", "content policy" in tab.js("$('#frame').textContent"), tab.js("$('#frame').textContent")[:100])
        check("try on another model", "Try on" in tab.js("$('#frame').textContent"))
        check("no check-again on a policy block", not tab.js("!!$('#actions [data-a=recheck]')"))
        stopped = {"id": "0000000000009-iiiiii", "reel": "r", "shot": 2, "letter": "C", "kind": "video", "status": "cancelled",
                   "label": "Seedance 2.5", "model": "seedance-2.5", "aspect": "16:9", "prompt": "p", "request_id": "req-9",
                   "error": "You stopped this take.", "created": 4}
        tab.js(f"S.takes.push({json.dumps(stopped)}); select('0000000000009-iiiiii'); 1")
        time.sleep(0.3)
        check("a stopped take offers check again", tab.js("!!$('#actions [data-a=recheck]')"), tab.js("$('#actions').textContent"))
        tab.js("S.takes = S.takes.filter(t => t.id !== '0000000000009-iiiiii'); 1")
        tab.shot("07-failed")
        tab.js("select('0000000000001-aaaaaa'); 1")

        # the bin: two stills (shot 1), a running + a failed clip (shot 2), three finished clips (shots 3-4)
        clip = lambda tid, shot, letter, **kw: dict({"id": tid, "reel": "r", "shot": shot, "letter": letter, "kind": "video",
                                                       "status": "done", "label": "Kling 3.0", "model": "kling-3-pro", "aspect": "16:9",
                                                       "url": f"/nowhere/{tid}.mp4", "thumb_url": img2, "seconds": 5, "prompt": "p"}, **kw)
        clips = [clip("0000000000005-eeeeee", 3, "A"), clip("0000000000006-ffffff", 3, "B", starred=True),
                 clip("0000000000007-gggggg", 4, "A")]
        tk = lambda i: f"$('#strip .tk[data-t=\"{i}\"]').click()"
        tab.js(f"S.takes = S.takes.concat({json.dumps(clips)}); renderStrip(); 1")
        tab.js("$('#stitchBtn').click(); 1")
        time.sleep(0.3)
        check("stitch pre-picks one take per shot, stills too", sorted(tab.js("[...S.picked]")) == ["0000000000002-bbbbbb", "0000000000006-ffffff", "0000000000007-gggggg"],
              tab.js("[...S.picked]"))
        check("selecting says what to do", tab.js("!$('#stitchHint').classList.contains('hidden') && $('#dlAll').classList.contains('hidden')"))
        check("stitch counts, stills get a hold", tab.js("$('#stitchGo').textContent") == "Stitch 3" and tab.js("!$('#holdWrap').classList.contains('hidden')"),
              tab.js("$('#stitchGo').textContent"))
        tab.js(tk("0000000000003-cccccc") + "; 1")
        check("a take still rendering can't be picked", tab.js("S.picked.size") == 3)
        tab.js(tk("0000000000004-dddddd") + "; 1")
        check("a failed take can be picked, to delete, not to stitch",
              tab.js("S.picked.size") == 4 and tab.js("$('#stitchGo').textContent") == "Stitch 3" and tab.js("$('#selDel').textContent") == "Delete 4")
        tab.js("$('#stitchAll').click(); 1")
        check("select all", tab.js("S.picked.size") == 6 and tab.js("$('#stitchGo').textContent") == "Stitch 5"
              and tab.js("$('#selDl').textContent") == "Download 5" and "takes=" in tab.js("$('#selDl').href"), tab.js("$('#selDl').href"))
        tab.js("$('#stitchCancel').click(); 1")
        check("done leaves selecting", not tab.js("S.picking") and tab.js("$('#selDel').classList.contains('hidden')"))
        tab.js("document.querySelector('#strip .tk[data-t=\"0000000000007-gggggg\"]').dispatchEvent(new MouseEvent('click', {bubbles: true, metaKey: true})); 1")
        check("⌘-click starts a selection", tab.js("S.picking") and tab.js("[...S.picked]") == ["0000000000007-gggggg"])
        tab.js(tk("0000000000005-eeeeee") + "; " + tk("0000000000006-ffffff") + "; 1")
        time.sleep(0.2)
        check("order badges", tab.js("$$('#strip .tk .ord').map(e => e.textContent).join(',')") == "1,2,3",
              tab.js("$$('#strip .tk .ord').map(e => e.textContent).join(',')"))
        tab.shot("09-make-film")
        film = clip("0000000000008-hhhhhh", 5, "A", label="Stitched · 3 clips", action="stitch", seconds=15)
        tab.js("window._api = api; api = async (u, o) => u.endsWith('/stitch') ? (window._sent = o.json, {take: " + json.dumps(film)
               + "}) : u.endsWith('/takes/delete') ? (window._del = o.json, {deleted: o.json.takes, kept: []}) : window._api(u, o); 1")
        tab.js("$('#stitchGo').click(); 1")
        time.sleep(0.5)
        check("stitch sends sequence order", tab.js("window._sent.takes") == ["0000000000005-eeeeee", "0000000000006-ffffff", "0000000000007-gggggg"]
              and tab.js("window._sent.still_seconds") == 3, tab.js("window._sent"))
        check("the film is on stage", tab.js("S.current") == "0000000000008-hhhhhh" and not tab.js("S.picking"))
        check("download film appears", tab.js("!$('#dlFilm').classList.contains('hidden') && $('#dlFilm').href.endsWith('/nowhere/0000000000008-hhhhhh.mp4?download=1')"),
              tab.js("$('#dlFilm').href"))
        check("take bar downloads the film", tab.js("$('#takebar a.iconbtn').getAttribute('href')") == "/nowhere/0000000000008-hhhhhh.mp4?download=1",
              tab.js("$('#takebar a.iconbtn') && $('#takebar a.iconbtn').getAttribute('href')"))
        # delete from the bin
        tab.js("window.confirm = () => true; $('#selBtn').click(); " + tk("0000000000004-dddddd") + "; " + tk("0000000000008-hhhhhh") + "; $('#selDel').click(); 1")
        time.sleep(0.5)
        check("delete removes the selection", sorted(tab.js("window._del.takes")) == ["0000000000004-dddddd", "0000000000008-hhhhhh"]
              and not tab.js("S.takes.some(t => t.id === '0000000000004-dddddd' || t.id === '0000000000008-hhhhhh')") and not tab.js("S.picking"),
              tab.js("S.takes.map(t => t.id.slice(-6)).join(',')"))
        tab.js("api = window._api; select('0000000000001-aaaaaa'); 1")

        # starters
        tab.js("$('#starterBtn').click(); 1")
        time.sleep(0.3)
        tab.js("$('#pop [data-s=wok]').click(); 1")
        time.sleep(0.8)
        check("starter fills the shot", "street cook" in tab.js("$('#scene').value") and tab.js("S.direction.focal") == "50")
        check("starter switches back to image", tab.js("S.mode") == "image")

        # phone width
        tab.shot("08-phone", w=390, h=844)
        overflow = tab.js("document.documentElement.scrollWidth - window.innerWidth")
        check("no sideways scroll on a phone", overflow <= 1, overflow)
        tab.call("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)

        # ── the library: every take you've made, across reels ────────────────
        now = time.time()
        lib = [
            {"id": "1792800000003-aaaaaa", "reel": "ra", "reel_name": "Alpha", "kind": "video", "status": "done", "label": "Seedance 2.5",
             "owner": "Tester", "yours": True, "url": "/nowhere/a.mp4", "thumb_url": img, "seconds": 5.04, "w": 1920, "h": 1080,
             "prompt": "Rain on a window", "shot": 1, "letter": "A", "created": now, "cost": 2.36},
            {"id": "1792800000002-bbbbbb", "reel": "rb", "reel_name": "Beta", "kind": "image", "status": "done", "label": "FLUX 3",
             "owner": "Tester", "yours": True, "url": img2, "thumb_url": img2, "w": 1600, "h": 900, "prompt": "A red barn",
             "shot": 1, "letter": "A", "created": now - 60, "starred": True},
            {"id": "1792800000001-cccccc", "reel": "rb", "reel_name": "Beta", "kind": "image", "status": "done", "label": "Seedream",
             "owner": "Tester", "yours": True, "url": img, "thumb_url": img, "w": 1600, "h": 900, "prompt": "A pier",
             "shot": 2, "letter": "A", "created": now - 86400},
            {"id": "1792700000002-dddddd", "reel": "ra", "reel_name": "Alpha", "kind": "image", "status": "done", "label": "FLUX 3",
             "owner": "Bob", "yours": False, "url": img2, "thumb_url": img2, "prompt": "Bob's lighthouse", "shot": 3, "letter": "A",
             "created": now - 3 * 86400},
            {"id": "1792700000001-eeeeee", "reel": "rc", "reel_name": "Old", "kind": "image", "status": "done", "label": "Seedream",
             "owner": "", "yours": True, "url": img, "thumb_url": img, "prompt": "Before names", "shot": 1, "letter": "A",
             "created": now - 10 * 86400},
        ]
        tab.js(f"""window._lf = window.fetch; window._libq = []; window._libdel = null; window._LIB = {json.dumps(lib)};
          window.confirm = () => true;
          window.fetch = async (u, o) => {{
            const j = d => new Response(JSON.stringify(d), {{headers: {{'Content-Type': 'application/json'}}}});
            u = String(u);
            if (u.includes('/library/delete')) {{ const b = JSON.parse(o.body); window._libdel = b; return j({{deleted: b.items, kept: []}}); }}
            if (u.includes('/library/claim')) {{ const b = JSON.parse(o.body); window._claim = b; return j({{claimed: b.items, me: 'Tester'}}); }}
            if (u.includes('/library?')) {{
              const q = new URL(u, location.href).searchParams; window._libq.push(Object.fromEntries(q));
              const gone = new Set((window._libdel || {{items: []}}).items);
              let it = _LIB.filter(t => !gone.has(t.reel + ':' + t.id) && (q.get('who') === 'all' || t.owner === 'Tester'));
              if (q.get('kind')) it = it.filter(t => t.kind === q.get('kind'));
              if (q.get('q')) it = it.filter(t => t.prompt.toLowerCase().includes(q.get('q')));
              return j({{items: it.slice(+q.get('offset'), +q.get('offset') + +q.get('limit')), total: it.length,
                         counts: {{mine: 3, all: 5, video: 1, image: 2, starred: 1}}, me: 'Tester'}});
            }}
            if (u.endsWith('/star')) return j({{take: {{starred: JSON.parse(o.body).starred}}}});
            if (u.endsWith('/reels/rb')) return j({{reel: {{id: 'rb', name: 'Beta'}}, takes: _LIB.filter(t => t.reel === 'rb')}});
            return window._lf(u, o);
          }}; 1""")
        tab.js("$('#libBtn').click(); 1")
        time.sleep(0.8)
        check("library opens on your takes", tab.js("!$('#libSheet').classList.contains('hidden') && $$('#libSheet .lc').length") == 3
              and tab.js("_libq[0].who") == "mine" and tab.js("_libq[0].app") == "studio", tab.js("_libq[0]"))
        check("library groups by day", tab.js("$$('#libSheet .libday').map(e => e.textContent)") == ["Today", "Yesterday"],
              tab.js("$$('#libSheet .libday').map(e => e.textContent)"))
        check("library tabs carry counts", tab.js("$('#libWho [data-w=all] i').textContent.trim()") == "5"
              and tab.js("$('#libWho [data-w=mine]').classList.contains('on')"))
        check("a clip says how long", "5s" in tab.js("$('#libSheet .lc .badge').textContent"))
        tab.js("$('#libSheet .lc').dispatchEvent(new MouseEvent('mouseenter')); 1")
        check("a clip plays under the pointer", tab.js("!!$('#libSheet .lc video.hov')"))
        tab.js("$('#libSheet .lc').dispatchEvent(new MouseEvent('mouseleave')); 1")
        check("and stops when it leaves", not tab.js("!!$('#libSheet .lc video.hov')"))
        tab.shot("13-library")
        tab.js("$('#libWho [data-w=all]').click(); 1"); time.sleep(0.5)
        check("everyone's, with names", tab.js("$$('#libSheet .lc').length") == 5 and "Bob" in tab.js("$('#libBody').textContent")
              and tab.js("JSON.parse(localStorage.getItem('studio.lib.who'))") == "all")
        tab.js("$('#libKind [data-k=video]').click(); 1"); time.sleep(0.5)
        check("clips only", tab.js("$$('#libSheet .lc').length") == 1 and tab.js("_libq[_libq.length - 1].kind") == "video")
        tab.js("$('#libKind [data-k=\"\"]').click(); 1"); time.sleep(0.5)
        tab.js("const q = $('#libQ'); q.value = 'barn'; q.dispatchEvent(new Event('input')); 1"); time.sleep(0.8)
        check("search", tab.js("$$('#libSheet .lc').length") == 1 and tab.js("_libq[_libq.length - 1].q") == "barn")
        tab.js("const q = $('#libQ'); q.value = ''; q.dispatchEvent(new Event('input')); 1"); time.sleep(0.8)
        # the viewer
        tab.js("$('#libSheet .lc').click(); 1"); time.sleep(0.4)
        check("viewer plays the clip", tab.js("!!$('#libView video') && $('#libView video').getAttribute('src')") == "/nowhere/a.mp4"
              and "Open as a clip" in tab.js("$('#libView').textContent") and "$2.36" in tab.js("$('#libView').textContent"))
        tab.shot("14-library-viewer")
        tab.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight'})); 1"); time.sleep(0.3)
        check("→ steps to the next take", tab.js("LIB.view") == 1 and tab.js("!!$('#libView img')"))
        tab.js("$('#lvStar').click(); 1"); time.sleep(0.3)
        check("star from the viewer", tab.js("LIB.items[1].starred") is False and "☆ Star" in tab.js("$('#lvStar').textContent"))
        tab.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); 1"); time.sleep(0.2)
        check("Esc leaves the viewer, not the library", not tab.js("!!$('#libView')") and tab.js("!$('#libSheet').classList.contains('hidden')"))
        check("someone else's take can't be deleted", tab.js("LIB.items[3].owner") == "Bob" and "Delete" not in (
            tab.js("libView(3), $('#libView .vacts').textContent")), tab.js("$('#libView .vacts').textContent"))
        tab.js("libClose(); 1")
        # select across reels
        tab.js("$$('#libSheet .lc .pick')[0].click(); $$('#libSheet .lc .pick')[3].click(); 1"); time.sleep(0.2)
        check("selection footer", "2 selected" in tab.js("$('#libFoot').textContent") and tab.js("$('#libDel').textContent") == "Delete 1",
              tab.js("$('#libFoot').textContent"))
        check("download spans reels", "items=ra:1792800000003-aaaaaa,ra:1792700000002-dddddd" in tab.js("$('#libDl').href"), tab.js("$('#libDl').href"))
        tab.js("$('#libDel').click(); 1"); time.sleep(0.5)
        check("delete sends only yours", tab.js("_libdel.items") == ["ra:1792800000003-aaaaaa"]
              and tab.js("$$('#libSheet .lc').length") == 4 and tab.js("LIB.picked.size") == 0, tab.js("_libdel"))
        # a take from before names were kept: mark it as mine
        tab.js("$$('#libSheet .lc').find(e => LIB.items[+e.dataset.i].id === '1792700000001-eeeeee').querySelector('.pick').click(); 1"); time.sleep(0.2)
        check("unnamed takes can be claimed", tab.js("$('#libClaim') && $('#libClaim').textContent") == "Mark 1 as mine",
              tab.js("$('#libFoot').textContent"))
        tab.js("$('#libClaim').click(); 1"); time.sleep(0.4)
        check("claim names them yours", tab.js("_claim.items") == ["rc:1792700000001-eeeeee"]
              and tab.js("LIB.items.find(t => t.id === '1792700000001-eeeeee').owner") == "Tester" and tab.js("LIB.picked.size") == 0)
        check("a named take offers no claim", not tab.js("libView(LIB.items.findIndex(t => t.owner === 'Bob')), !!$('#lvClaim')"))
        tab.js("libClose(); 1")
        check("each take offers As a clip, and On its board when it has a card", tab.js(
            "$$('#libSheet .lc').every(e => !!e.querySelector('[data-o=clip]') && !!e.querySelector('[data-o=board]') === !!LIB.items[+e.dataset.i].board)"))
        # open a take in its reel
        tab.js("$$('#libSheet .lc').find(e => LIB.items[+e.dataset.i].id === '1792800000001-cccccc').click(); 1"); time.sleep(0.3)
        tab.js("setView('board'); $('#lvOpen').click(); 1"); time.sleep(0.8)
        check("open as a clip: its reel, on the stage, even from the board", tab.js("S.reel.id") == "rb" and tab.js("S.current") == "1792800000001-cccccc"
              and not tab.js("boardOn()") and tab.js("$('#libSheet').classList.contains('hidden')"), (tab.js("S.reel && S.reel.id"), tab.js("S.current")))
        tab.js("$('#libBtn').click(); 1"); time.sleep(0.6)
        tab.shot("15-library-phone", w=390, h=844)
        check("library: no sideways scroll on a phone", tab.js("document.documentElement.scrollWidth - window.innerWidth") <= 1)
        tab.call("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
        tab.js("closePop(); window.fetch = window._lf; localStorage.removeItem('studio.lib.who'); 1")

        # ── the board: a spot, shot by shot (every paid call stubbed) ────────
        tab.js("""window._bf = window.fetch; window._bd = {runs: [], frames: [], stitch: null, plan: null, saved: null, copies: 0};
          window.fetch = async (u, o) => {
            const j = d => new Response(JSON.stringify(d), {headers: {'Content-Type': 'application/json'}});
            u = String(u); const m = (o && o.method) || 'GET', body = o && o.body && typeof o.body === 'string' ? JSON.parse(o.body) : null;
            if (u.endsWith('/reels') && m === 'POST') return j({id: 'bdtest00', name: 'Spot', app: 'studio'});
            if (u.endsWith('/reels/bdtest00/board') && m === 'PUT') { _bd.saved = body; return j({board: body}); }
            if (u.endsWith('/reels/bdtest00') && m === 'GET') return j({reel: {id: 'bdtest00', name: 'Spot', board: true}, takes: S.takes, board: _bd.saved});
            if (u.endsWith('/board/plan')) { _bd.plan = body; return j({title: 'Ice cold', notes: 'Three beats.', total: 15, target: body.target, shots: [
              {scene: 'Maya jogs past the pier at dawn.', shot: 'wide', move: 'track', duration: 6, elements: ['maya'], start: 'cut', vo: 'Every morning.', super: ''},
              {scene: 'Maya stops and drinks.', shot: 'close', move: 'static', duration: 5, elements: ['Maya'], start: 'continue', vo: '', super: ''},
              {scene: 'The bottle on the rail.', shot: 'insert', move: 'push', duration: 4, elements: [], start: 'cut', vo: '', super: 'ICEBOX'}]}); }
            if (u.endsWith('/refs/copy')) { _bd.copies++; return j({name: 'ref-maya.jpg', kind: 'image', url: '/nowhere/ref-maya.jpg'}); }
            if (u.endsWith('/run')) { _bd.runs.push(body); const n = _bd.runs.length;
              return j({shot: body.shot, takes: Array.from({length: body.takes || 1}, (_, i) => ({id: `17930000000${n}${i}-bd${n}${i}`, reel: 'bdtest00', shot: body.shot,
                letter: 'ABCD'[i], kind: body.mode, status: 'done', label: body.mode === 'video' ? 'Clip model' : 'Key model', board: body.board, role: body.role,
                file: body.mode === 'video' ? `c${n}.mp4` : `k${n}${i}.jpg`, url: body.mode === 'video' ? '/nowhere/c.mp4' : IMG, thumb_url: IMG, seconds: body.mode === 'video' ? 6 : null}))}); }
            if (u.includes('/frame')) { _bd.frames.push(body); return j({take: {id: '17939999999999-frame1', reel: 'bdtest00', shot: body.shot, letter: 'A', kind: 'image',
              status: 'done', label: 'Last frame', board: body.board, role: 'key', file: 'f1.jpg', url: IMG, thumb_url: IMG}}); }
            if (u.endsWith('/stitch')) { _bd.stitch = body; return j({take: {id: '17939999999999-cut001', reel: 'bdtest00', shot: 99, letter: 'A', kind: 'video', status: 'done',
              label: body.label, action: 'stitch', url: '/nowhere/cut.mp4', seconds: 12.5}}); }
            return window._bf(u, o);
          }; 1""".replace("IMG", json.dumps(img)))
        tab.js("newReel(); $('#viewSeg [data-v=board]').click(); 1"); time.sleep(0.6)
        check("board: the view switches", tab.js("boardOn() && !$('#board').offsetParent === false && $('#stage').offsetParent === null && $('#kitCard').offsetParent !== null"))
        check("board: keyframe and clip models", tab.js("$$('#bModels .mrow').length") == 2 and "Keyframes" in tab.js("$('#bModels').textContent"))
        check("board: framing and move live on the cards", tab.js("!$('#shotlist .row[data-g=shot]') && !$('#shotlist .row[data-g=move]') && !!$('#shotlist .row[data-g=light]')")
              and not tab.js("!!$('#fDur')"))
        check("board: a glance makes no reel", tab.js("S.reel") is None and tab.js("_bd.saved") is None)
        tab.js("kitFromTake('other00000:1792800000002-bbbbbb', 'Maya'); 1"); time.sleep(0.8)
        check("board: a library still joins the kit", tab.js("B.elements.length") == 1 and tab.js("B.elements[0].name") == "Maya"
              and tab.js("_bd.copies") == 1 and tab.js("S.reel && S.reel.id") == "bdtest00")
        tab.js("const b = $('#bBrief'); b.value = 'A :15 for Icebox bottles. Maya, a runner.'; b.dispatchEvent(new Event('input')); $('#bTarget [data-n=\"15\"]').click(); 1")
        tab.js("$('#bPlan').click(); 1"); time.sleep(1.0)
        plan = tab.js("_bd.plan")
        check("board: the planner gets the brief, length, model and kit", plan and plan["target"] == 15 and plan["elements"] == [{"name": "Maya", "kind": "cast"}]
              and plan["model"] == tab.js("clipModel().id"), plan)
        check("board: the shot list becomes cards", tab.js("$$('#board .bcard').length") == 3 and tab.js("B.shots[0].elements.length") == 1
              and tab.js("B.shots[1].start") == "continue" and "Ice cold" in tab.js("$('#bNotes').textContent"))
        check("board: running time against the target", "of 0:15" in tab.js("$('#bTime').textContent") and tab.js("$$('#bTime .seg2').length") == 3,
              tab.js("$('#bTime').textContent"))
        tab.shot("16-board-planned")
        # keyframe shot 1: the kit goes in, named, on the card's own shot and seed
        tab.js("$('#board .bcard [data-act=key]').click(); 1"); time.sleep(0.8)
        k = tab.js("_bd.runs[0]")
        check("board: keyframe carries the kit by name", k and k["mode"] == "image" and k["refs"] == ["ref-maya.jpg"] and k["ref_names"] == ["Maya"]
              and k["role"] == "key" and k["board"] == tab.js("B.shots[0].id") and k["shot"] == tab.js("B.shots[0].n"), k)
        check("board: the card's framing, the panel's look", k["direction"]["shot"] == "wide" and k["direction"]["move"] == "track"
              and k["direction"]["light"] == tab.js("S.direction.light"), k and k["direction"])
        check("board: a spot seed per shot", k["seed"] == tab.js("shotSeed(B.shots[0])") or k["seed"] == "", (k["seed"], tab.js("shotSeed(B.shots[0])")))
        check("board: the keyframe lands on its card", "Keyframe A" in tab.js("$('#board .bcard [data-frame]').textContent"))
        tab.js("$$('#board .bcard')[0].querySelector('[data-act=clip]').click(); 1"); time.sleep(0.8)
        c = tab.js("_bd.runs[1]")
        check("board: animate starts from the keyframe", c and c["mode"] == "video" and c["first_frame"] == "k10.jpg" and c["refs"] == [] and c["role"] == "clip", c)
        check("board: the clip's length is one the model makes", c["duration"] in (tab.js("clipDurations()") or [None]), (c["duration"], tab.js("clipDurations()")))
        tab.js("const i = $$('#board .bcard')[0].querySelectorAll('[data-trim]'); i[0].value = '1'; i[0].dispatchEvent(new Event('change', {bubbles: true})); 1")
        time.sleep(0.3)
        check("board: trimming changes the running time", tab.js("B.shots[0].trim") == [1, 6] and "trimmed" in tab.js("$$('#board .bcard')[0].textContent"),
              tab.js("B.shots[0].trim"))
        # shot 2 continues shot 1: its keyframe is shot 1's last frame
        tab.js("$$('#board .bcard')[1].querySelector('[data-act=key]').click(); 1"); time.sleep(0.6)
        check("board: continue reads the last frame", tab.js("_bd.frames[0]") == {"at": "last", "shot": tab.js("B.shots[1].n"), "board": tab.js("B.shots[1].id")}
              and tab.js("B.shots[1].key") == "17939999999999-frame1", tab.js("_bd.frames[0]"))
        tab.js("$('#bCut').click(); 1"); time.sleep(0.6)
        st = tab.js("_bd.stitch")
        check("board: the cut runs in board order with trims", st and st["takes"] == [tab.js("pickOf(B.shots[0], 'clip').id"), "17939999999999-frame1"]
              and st["trims"][st["takes"][0]] == [1, 6] and st["label"] == "Spot cut", st)
        check("board: the last cut is a click away", "Spot cut" in tab.js("$('#bWatch').textContent") and not tab.js("$('#bWatch').classList.contains('hidden')"))
        tab.shot("17-board-shot")
        tab.js("saveBoardNow(); 1"); time.sleep(0.6)
        tab.js("B = null; loadReel('bdtest00'); 1"); time.sleep(0.8)
        check("board: it comes back with the reel", tab.js("B && B.shots.length") == 3 and tab.js("B.elements[0].name") == "Maya" and tab.js("B.target") == 15
              and tab.js("B.shots[0].trim") == [1, 6])
        # drag a card by its top bar: the first shot to after the last
        tab.js("""window._order = B.shots.map(s => s.id); window._shots = JSON.stringify(B.shots);
          const cards = $$('#board .bcard'), bar = cards[0].querySelector('.bct'), a = bar.getBoundingClientRect(), z = cards[2].getBoundingClientRect();
          const ev = (t, x, y) => bar.dispatchEvent(new PointerEvent(t, {bubbles: true, button: 0, pointerId: 7, clientX: x, clientY: y}));
          ev('pointerdown', a.left + 30, a.top + 10); ev('pointermove', a.left + 60, a.top + 30);
          ev('pointermove', z.right - 20, z.top + z.height / 2);
          window._mid = {line: !!$('.bcard.dropa'), ghost: !!$('.bghost'), dim: cards[0].classList.contains('dragging')};
          ev('pointerup', z.right - 20, z.top + z.height / 2); 1"""); time.sleep(0.4)
        check("board: dragging a card shows where it lands", tab.js("_mid") == {"line": True, "ghost": True, "dim": True}, tab.js("_mid"))
        check("board: and drops it there, the film reordered", tab.js("B.shots.map(s => s.id)") == [tab.js("_order[1]"), tab.js("_order[2]"), tab.js("_order[0]")]
              and tab.js("B.shots[0].start") == "cut" and not tab.js("!!$('.bghost')") and tab.js("$$('#board .bcard')[2].dataset.s") == tab.js("_order[0]"))
        tab.js("""const bar = $$('#board .bcard')[0].querySelector('.bct'), a = bar.getBoundingClientRect();
          const ev = (t, x, y) => bar.dispatchEvent(new PointerEvent(t, {bubbles: true, button: 0, pointerId: 8, clientX: x, clientY: y}));
          ev('pointerdown', a.left + 30, a.top + 10); ev('pointermove', a.left + 400, a.top + 40);
          document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); ev('pointerup', a.left + 400, a.top + 40); 1"""); time.sleep(0.3)
        check("board: Esc puts a dragged card back", tab.js("B.shots.map(s => s.id)") == [tab.js("_order[1]"), tab.js("_order[2]"), tab.js("_order[0]")])
        tab.js("B.shots = JSON.parse(_shots); saveBoard(); renderBoard(); 1"); time.sleep(0.3)
        tab.shot("18-board-phone", w=390, h=844)
        check("board: no sideways scroll on a phone", tab.js("document.documentElement.scrollWidth - window.innerWidth") <= 1)
        tab.call("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
        tab.js("$('#viewSeg [data-v=shot]').click(); 1"); time.sleep(0.3)
        check("board: back to the Shot view", tab.js("!boardOn() && $('#stage').offsetParent !== null"))
        tab.js("window.fetch = window._bf; newReel(); localStorage.removeItem('studio.view'); localStorage.removeItem('studio.reel'); 1")

        # ── footage: a clip someone shot, changed, extended, led into (stubbed) ─
        tab.js("""window._ff = window.fetch; window._ft = {runs: [], frames: [], plan: null, up: null};
          const IMGX = __IMG__;
          window.fetch = async (u, o) => {
            const j = d => new Response(JSON.stringify(d), {headers: {'Content-Type': 'application/json'}});
            u = String(u); const m = (o && o.method) || 'GET', body = o && o.body && typeof o.body === 'string' ? JSON.parse(o.body) : null;
            if (u.endsWith('/reels') && m === 'POST') return j({id: 'fttest00', name: 'Shoot', app: 'studio'});
            if (u.endsWith('/reels/fttest00/board') && m === 'PUT') return j({board: body});
            if (u.endsWith('/board/plan')) { _ft.plan = body; return j({title: 'Mixed', notes: '', total: 12, target: 15, shots: [
              {scene: 'Drone over the pier.', shot: 'xwide', move: 'drone', duration: 4, elements: [], start: 'cut', vo: '', super: '', footage: ''},
              {footage: body.footage[0].id, duration: body.footage[0].seconds, scene: body.footage[0].notes, start: 'cut'}]}); }
            if (u.endsWith('/run') && _ft.refuse) return new Response(JSON.stringify({error: _ft.refuse}), {status: 400, headers: {'Content-Type': 'application/json'}});
            if (u.endsWith('/run')) { _ft.runs.push(body); const n = _ft.runs.length;
              return j({shot: body.shot, takes: [{id: `17940000000${n}00-ft${n}00`, reel: 'fttest00', shot: body.shot, letter: 'ABCDEFG'[n], kind: body.mode,
                status: 'done', label: 'Tool', board: body.board, role: body.role, action: body.action, aspect: '16:9', file: `r${n}.mp4`,
                url: '/nowhere/r.mp4', thumb_url: IMGX, seconds: body.action === 'extend' ? 7 : 4, new_from: body.action === 'extend' ? 4 : 0}]}); }
            if (u.includes('/frame')) { _ft.frames.push(body); return j({take: {id: '17949999999999-ffram1', reel: 'fttest00', shot: body.shot, letter: 'Z', kind: 'image',
              status: 'done', label: 'Frame grab', board: body.board, role: 'key', file: 'ff.jpg', url: IMGX, thumb_url: IMGX, parent: u.split('/takes/')[1].split('/')[0]}}); }
            return window._ff(u, o);
          };
          window._upl = upload;
          upload = async (path, fd, onp) => { _ft.up = {path, shot: fd.get('shot'), board: fd.get('board'), name: fd.get('file').name}; onp(.5); onp(1);
            return {take: {id: '17940000000000-foot00', reel: 'fttest00', shot: +fd.get('shot'), letter: 'A', kind: 'video', status: 'done', label: 'Footage · pier',
                    action: 'footage', board: fd.get('board'), role: 'clip', aspect: '16:9', file: 'footage-1.mp4', url: '/nowhere/f.mp4', thumb_url: IMGX, seconds: 4},
                    key: {id: '17940000000000-fkey00', reel: 'fttest00', shot: +fd.get('shot'), letter: 'B', kind: 'image', status: 'done', label: 'First frame',
                    action: 'frame', board: fd.get('board'), role: 'key', parent: '17940000000000-foot00', file: 'first-1.jpg', url: IMGX, thumb_url: IMGX}}; };
          1""".replace("__IMG__", json.dumps(img)))
        tab.js("newReel(); $('#viewSeg [data-v=board]').click(); 1"); time.sleep(0.5)
        check("footage: the board offers it", tab.js("!!$('#bFoot') && !!$('#bAdd')"))
        tab.js("$('#bAdd').click(); addFootageCard(new File([new Uint8Array(64)], 'pier.mov', {type: 'video/quicktime'})); 1"); time.sleep(0.8)
        check("footage: it uploads onto its card", tab.js("_ft.up && _ft.up.board") == tab.js("B.shots[1].id") and tab.js("_ft.up.shot") == str(tab.js("B.shots[1].n"))
              and tab.js("B.shots[1].type") == "footage" and tab.js("B.shots[1].clip") == "17940000000000-foot00", tab.js("_ft.up"))
        card = "$$('#board .bcard')[1]"
        check("footage: a card of its own", "Footage" in tab.js(f"{card}.querySelector('.tag').textContent") and "Footage A" in tab.js(f"{card}.querySelector('[data-frame]').textContent")
              and tab.js("$$('#bTime .seg2.real').length") == 1)
        opts = tab.js(f"[...{card}.querySelectorAll('[data-f=tool] optgroup')].map(g => g.label + ':' + g.children.length)")
        check("footage: change tools, and extend tools when the catalogue has one", opts[0].startswith("Change it:")
              and (len(opts) == 2 and opts[1].startswith("Extend it:")) == tab.js("!!defaultExtend()"), opts)
        check("footage: Google's tools sort last and say so", tab.js(f"(() => {{ const o = [...{card}.querySelectorAll('[data-f=tool] optgroup')[0].children].map(x => x.textContent); const g = o.findIndex(x => /no real people/.test(x)); return g < 0 || o.slice(g).every(x => /no real people/.test(x)); }})()"))
        tab.js(f"const ch = {card}.querySelector('[data-f=change]'); ch.value = 'Make it a snowy winter afternoon'; ch.dispatchEvent(new Event('input')); 1")
        tab.js(f"const ti = {card}.querySelectorAll('[data-trim]'); ti[1].value = '3'; ti[1].dispatchEvent(new Event('change', {{bubbles: true}})); 1"); time.sleep(0.2)
        tab.js(f"{card}.querySelector('[data-act=tool]').click(); 1"); time.sleep(0.6)
        ch = tab.js("_ft.runs[0]")
        check("footage: change runs on the trimmed footage, as written", ch and ch["model"] == tab.js("defaultChange().id") and ch["source_video"] == "footage-1.mp4"
              and ch["source_trim"] == [0, 3] and ch["prompt_override"] == "Make it a snowy winter afternoon" and ch["direction"] == {} and ch["enhance"] is False
              and ch["role"] == "clip" and ch["board"] == tab.js("B.shots[1].id"), ch)
        check("footage: the change is the card's new version", "Changed" in tab.js(f"{card}.querySelector('[data-frame]').textContent"))
        # prod, 2026-10-23: a kit picture named in the instruction but not ticked never went, and a
        # 11.9 s clip went to a 10-second edit tool with its default 5 s length — it made nothing, twice
        kling = tab.js("(byKind('video').find(m => m.id === 'wan/wan2.7-videoedit') || {}).id")
        if kling:
            tab.js(f"""window._n0 = _ft.runs.length; window._cl = pickOf(B.shots[1], 'clip'); window._cs = _cl.seconds; _cl.seconds = 20;
              B.elements.push({{id: 'eBl', name: 'Olive Blouse', kind: 'product', file: 'ref-blouse.jpg'}}); B.shots[1].tool = '{kling}';
              B.shots[1].change = 'Replace her shirt with the olive blouse'; B.shots[1].trim = null; renderBoard(); 1""")
            tab.js(f"{card}.querySelector('[data-act=tool]').click(); 1"); time.sleep(0.5)
            check("footage: a stretch longer than the model takes is stopped, and it stays said", tab.js("_ft.runs.length") == tab.js("_n0")
                  and "works on up to 10 s of footage, and this stretch is 20.0 s" in tab.js("$('#fails').textContent") and tab.js("!$('#fails').classList.contains('hidden')"),
                  tab.js("$('#fails').textContent"))
            tab.js(f"{card}.querySelector('[data-act=tool]').click(); 1"); time.sleep(0.3)
            check("failures: clicking again doesn't stack the same refusal", tab.js("$$('#fails .fail').length") == 1, tab.js("$$('#fails .fail').length"))
            tab.js("$('#fails [data-fx]').click(); 1")
            check("failures: a refusal can be dismissed", tab.js("$('#fails').classList.contains('hidden') && !refusals.length"))
            tab.js(f"const tk = {card}.querySelectorAll('[data-trim]'); tk[0].value = '2'; tk[0].dispatchEvent(new Event('change', {{bubbles: true}})); tk[1].value = '7'; tk[1].dispatchEvent(new Event('change', {{bubbles: true}})); 1")
            time.sleep(0.2)
            tab.js(f"{card}.querySelector('[data-act=tool]').click(); 1"); time.sleep(0.6)
            kr = tab.js("_ft.runs[_n0]")
            check("footage: a kit item named in the instruction goes with it", kr and kr["refs"] == ["ref-blouse.jpg"] and "image 1 is Olive Blouse" in kr["prompt_override"]
                  and tab.js("B.shots[1].elements").count("eBl") == 1, kr)
            check("footage: the model is told the stretch's length", kr and kr["duration"] == 5 and kr["source_trim"] == [2, 7], kr and (kr["duration"], kr["source_trim"]))
            tab.js("B.elements = B.elements.filter(e => e.id !== 'eBl'); B.shots[1].elements = []; B.shots[1].tool = defaultChange().id; B.shots[1].trim = [0, 3]; B.shots[1].trim_for = _cl.id; _cl.seconds = _cs; S.takes = S.takes.filter(t => !_ft.runs.slice(_n0).some((r, i) => t.id === `17940000000${_n0 + i + 1}00-ft${_n0 + i + 1}00`)); B.shots[1].clip = _cl.id; _ft.runs.splice(_n0); renderBoard(); 1")
        # failures stay on screen until read: a panel, and the card itself (prod 2026-10-23: a failed
        # Change on a footage card showed nothing but a red letter, because the card had its clip to show)
        tab.js("""window._fid = '17959999999999-fail99'; const s = B.shots[1], now = Date.now() / 1000;
          mergeTakes([{id: _fid, reel: 'fttest00', shot: s.n, letter: 'F', kind: 'video', status: 'failed', label: 'Wan 2.7 Video Edit', board: s.id,
            role: 'clip', action: 'change', aspect: '16:9', reason: 'validation', created: now - 40, finished: now,
            error: 'The model rejected these inputs: Prediction failed: The model did not produce an output.',
            hint: 'Wan 2.7 Video Edit ran but made nothing. That usually means it could not use the footage. Set In and Out closer together.'}]); 1""")
        time.sleep(0.3)
        panel = tab.js("$('#fails').textContent")
        check("failures: a failed run waits in the panel, in plain English", tab.js("!$('#fails').classList.contains('hidden')")
              and "Change failed · Wan 2.7 Video Edit" in panel and "ran but made nothing" in panel and "What the model said" in panel and "Show me" in panel, panel)
        tab.js("$('#fails details').open = true; renderFails(); S.takes = S.takes.slice(); renderFails(); 1")
        check("failures: polling leaves an opened explanation open", tab.js("$('#fails details').open"))
        ov = tab.js(f"({card}.querySelector('.st.fail') || {{}}).textContent || ''")
        check("failures: the card says so over its clip", "Change F failed" in ov and "ran but made nothing" in ov and "Set In and Out" not in ov
              and tab.js(f"!!{card}.querySelector('[data-frame] video')"), ov)
        tab.js(f"{card}.querySelector('[data-fseen]').click(); 1"); time.sleep(0.2)
        check("failures: dismissed on the card, gone from both and remembered", tab.js(f"!{card}.querySelector('.st.fail')")
              and tab.js("$('#fails').classList.contains('hidden')") and tab.js("LS.get('failSeen', []).includes(_fid)"))
        tab.js(f"{card}.querySelector(`[data-pick='${{_fid}}']`).click(); 1"); time.sleep(0.2)
        check("failures: its red letter brings the explanation back", tab.js("!!$(`#fails [data-fail='${_fid}']`)") and tab.js(f"!!{card}.querySelector('.st.fail')"))
        tab.js("setView('shot'); select(_fid); 1"); time.sleep(0.3)
        note = tab.js("$('#frame .notice').textContent")
        check("failures: the Shot view gives the reason and the model's own words", "ran but made nothing" in note and "What the model said: The model rejected" in note, note)
        tab.js("$(`#fails [data-fail='${_fid}'] [data-fshow]`).click(); 1"); time.sleep(0.4)
        check("failures: Show me goes to the card", tab.js("boardOn()") and tab.js(f"{card}.classList.contains('flash')"))
        tab.js("$(`#fails [data-fail='${_fid}'] .acts [data-fx]`).click(); 1"); time.sleep(0.2)
        check("failures: Got it clears it", tab.js("$('#fails').classList.contains('hidden')"))
        # the line: a clip waiting for its provider (Luma runs four at a time) says so, not "Rendering"
        tab.js("""window._qid = '17959999999998-queue1'; const s = B.shots[1];
          mergeTakes([{id: _qid, reel: 'fttest00', shot: s.n, letter: 'G', kind: 'video', status: 'queued', label: 'Luma Ray 3.2', board: s.id,
            role: 'clip', action: 'change', aspect: '16:9', created: Date.now() / 1000, ahead: 1, lane: 'Luma', lane_cap: 4}]); 1""")
        time.sleep(0.3)
        st = tab.js(f"(({card}.querySelector('[data-frame] .st') || {{}}).textContent || '') + ' | ' + ({card}.querySelector('[data-frame] .st') || {{getAttribute: () => ''}}).getAttribute('title')")
        check("line: a waiting clip says it's in line on its card", "In line · 1 ahead" in st and "Luma makes 4 at a time" in st, st)
        tab.js("setView('shot'); select(_qid); 1"); time.sleep(0.3)
        stage = tab.js("$('#frame').textContent")
        check("line: the Shot view says it's waiting its turn, and why", "waiting its turn" in stage and "Luma makes 4 at a time · 1 ahead of it" in stage
              and "Don't run it" in stage and "Rendering" not in stage, stage)
        check("line: the strip says In line", "In line" in tab.js(f"$(`#strip .tk[data-t='${{_qid}}']`).textContent"))
        tab.js("S.takes = S.takes.filter(t => t.id !== _qid); setView('board'); refreshBoard(); 1"); time.sleep(0.3)
        # a grade runs in the background as a take of its own and says which frame it's on
        tab.js("""window._gid = '17959999999997-grade1'; const s = B.shots[1];
          mergeTakes([{id: _gid, reel: 'fttest00', shot: s.n, letter: 'H', kind: 'video', status: 'running', model: 'local', label: 'Graded · Noir',
            action: 'grade', aspect: '16:9', created: Date.now() / 1000, started: Date.now() / 1000, progress: 'frame 12 of 48'}]);
          setView('shot'); select(_gid); 1""")
        time.sleep(0.3)
        stage = tab.js("$('#frame').textContent")
        check("grade: the Shot view says it's grading, which frame, and offers Stop grading", "Grading shot" in stage and "frame 12 of 48" in stage
              and "Stop grading" in stage and "Rendering" not in stage, stage)
        tab.js("S.takes.find(t => t.id === _gid).progress = 'frame 30 of 48'; tickStage(); 1")
        check("grade: the frame count moves without redrawing the stage", tab.js("$('#gradeprog').textContent") == "frame 30 of 48")
        # takes that didn't make it: slivers, with why on hover, and Clear N failed takes them out
        tab.js("""setView('shot'); window._df = window.fetch; window._cleared = null;
          window.fetch = (u, o) => String(u).endsWith('/takes/delete') ? (window._cleared = JSON.parse(o.body).takes,
            Promise.resolve(new Response(JSON.stringify({deleted: window._cleared, kept: []}), {headers: {'Content-Type': 'application/json'}}))) : _df(u, o);
          const now = Date.now() / 1000;
          mergeTakes([{id: '17950000000001-dud001', reel: S.reel.id, shot: 99, letter: 'B', kind: 'video', status: 'failed', model: 'x', label: 'MiniMax H3',
                       hint: 'The provider was busy. Try again in a minute.', created: now, finished: now},
                      {id: '17950000000002-dud002', reel: S.reel.id, shot: 99, letter: 'C', kind: 'video', status: 'cancelled', model: 'x', label: 'MiniMax H3', created: now}]);
          window._confirm = window.confirm; window.confirm = () => true; 1"""); time.sleep(0.3)
        d = json.loads(tab.js("""JSON.stringify((() => { const b = document.querySelector('#strip .tk[data-t="17950000000001-dud001"]');
          return {dud: b.classList.contains('dud'), w: b.getBoundingClientRect().width, title: b.title, mark: b.querySelector('.st').textContent,
                  clear: !$('#clearFails').classList.contains('hidden') && $('#clearFails').textContent}; })())"""))
        check("strip: a failed take is a sliver, with why on hover", d["dud"] and d["w"] < 40 and d["mark"] == "✕"
              and "The provider was busy." in d["title"] and "MiniMax H3" in d["title"], d)
        check("strip: Clear N failed is offered", (d["clear"] or "").startswith("Clear ") and "failed" in d["clear"], d)
        tab.js("$('#clearFails').click(); 1"); time.sleep(0.5)
        check("strip: Clear failed takes them out of the reel", "17950000000001-dud001" in (tab.js("_cleared") or [])
              and "17950000000002-dud002" in (tab.js("_cleared") or []) and not tab.js("S.takes.some(t => /-dud00[12]$/.test(t.id))")
              and tab.js("$('#clearFails').classList.contains('hidden')") == (tab.js("!S.takes.some(t => t.status === 'failed' || t.status === 'cancelled')")),
              tab.js("JSON.stringify({cleared: _cleared, left: S.takes.filter(t => /-dud00[12]$/.test(t.id)).map(t => t.id), hidden: $('#clearFails').classList.contains('hidden'), duds: S.takes.filter(t => t.status === 'failed' || t.status === 'cancelled').map(t => t.id), picking: S.picking})"))
        tab.js("window.fetch = _df; window.confirm = _confirm; 1")
        # Upscale asks where to: 1080p, 1440p or 4K for a clip, each with the size it lands at, and which upscaler
        tab.js("""setView('shot'); window._uf = window.fetch; window._ups = null;
          window.fetch = (u, o) => /\\/upscale$/.test(String(u)) ? (window._ups = JSON.parse(o.body),
            Promise.resolve(new Response(JSON.stringify({error: 'Not in the test.'}), {status: 400, headers: {'Content-Type': 'application/json'}}))) : _uf(u, o);
          mergeTakes([{id: '17950000000003-upclip', reel: S.reel.id, shot: 98, letter: 'A', kind: 'video', status: 'done', model: byKind('video')[0].id,
                       label: 'Test clip', url: '/nowhere/u.mp4', w: 1280, h: 720, created: Date.now() / 1000}]);
          select('17950000000003-upclip'); 1"""); time.sleep(0.3)
        tab.js("$('#actions [data-a=upscale]').click(); 1"); time.sleep(0.3)
        u = json.loads(tab.js("""JSON.stringify({to: $$('#upTo button').map(b => b.textContent.replace(/\\s+/g, ' ').trim()),
          models: $$('#upModel option').length, go: ($('[data-up=go]') || {}).textContent})"""))
        check("upscale: a clip asks where to, with the size each lands at", u["to"] == ["1080p 1920×1080", "1440p 2560×1440", "4K 3840×2160"]
              and u["models"] >= 1 and u["go"] == "Upscale to 4K", u)
        tab.js("$$('#upTo button')[1].click(); 1"); time.sleep(0.2)
        tab.js("$('[data-up=go]').click(); 1"); time.sleep(0.6)
        check("upscale: it sends where to and on which upscaler", (tab.js("_ups") or {}).get("to") == "1440p"
              and (tab.js("_ups") or {}).get("model") == tab.js("JSON.parse(localStorage.getItem('studio.up.model.video'))"), tab.js("_ups"))
        tab.js("window.fetch = _uf; S.takes = S.takes.filter(t => t.id !== '17950000000003-upclip'); S.upAsk = null; renderStrip(); 1")
        # Draw the move's Shoot it: the take again, on the clip model picked on the left, not the one it was made with
        tab.js("""window._keep = JSON.stringify({scene: S.scene, direction: S.direction, model: S.model, mode: S.mode});
          window._gen = generate; window._gens = [];
          generate = o => { _gens.push({o, model: S.model.video, move: S.direction.move}); return Promise.resolve(); };
          const made = byKind('video')[0], picked = byKind('video').find(m => m.id !== made.id);
          window._want = picked.id; S.model.video = picked.id;
          STUDIO.redo({id: '17950000000000-redo00', kind: 'video', status: 'done', model: made.id, settings: {mode: 'video', scene: 'A wave.'}}, 'orbit', ''); 1""")
        time.sleep(0.3)
        check("draw: Shoot it shoots on the model picked on the left", tab.js("_gens.length") == 1 and tab.js("_gens[0].model") == tab.js("_want")
              and tab.js("_gens[0].move") == "orbit" and tab.js("_gens[0].o.parent") == "17950000000000-redo00", tab.js("_gens"))
        tab.js("""generate = _gen; const k = JSON.parse(_keep); S.scene = k.scene; $('#scene').value = k.scene;
          S.direction = k.direction; S.model = k.model; setMode(k.mode, true); onShotChange(); 1""")
        tab.js("S.takes = S.takes.filter(t => t.id !== _gid); setView('board'); refreshBoard(); 1"); time.sleep(0.3)
        tab.js("S.takes = S.takes.filter(t => t.id !== _fid); _ft.refuse = 'The server said no.'; refreshBoard(); 1")
        tab.js(f"{card}.querySelector('[data-act=tool]').click(); 1"); time.sleep(0.5)
        check("failures: a run the server refuses stays said, with the model's name", "not run" in tab.js("$('#fails').textContent")
              and "The server said no." in tab.js("$('#fails').textContent"), tab.js("$('#fails').textContent"))
        tab.js("_ft.refuse = null; const fx = $('#fails [data-fx]'); if (fx) fx.click(); 1")
        # extend: the result is the next card, playing only its new seconds (when the catalogue has an extend tool)
        if not tab.js("!!defaultExtend()"):
            check("footage: no extend tool, so none is offered", "Extend it" not in tab.js(f"{card}.textContent"))
        else:
            tab.js(f"const sel = {card}.querySelector('[data-f=tool]'); sel.value = defaultExtend().id; sel.dispatchEvent(new Event('change')); 1"); time.sleep(0.4)
            tab.js(f"const ch2 = {card}.querySelector('[data-f=change]'); ch2.value = 'She keeps walking'; ch2.dispatchEvent(new Event('input')); {card}.querySelector('[data-act=tool]').click(); 1")
            time.sleep(0.8)
            ex = tab.js("_ft.runs[1]")
            check("footage: extend makes the next card", ex and ex["action"] == "extend" and ex["board"] == tab.js("B.shots[2].id") and tab.js("B.shots.length") == 3
                  and ex["source_video"] == "r1.mp4" and ex["duration"] in tab.js("defaultExtend().durations"), ex)
            check("footage: the extension plays only its new part", tab.js("B.shots[2].trim") == [4, 7] and "Extension of shot 2" in tab.js("$$('#board .bcard')[2].textContent"),
                  tab.js("B.shots[2].trim"))
        # lead-in: the AI shot before it ends on the first frame of the version the card uses
        tab.js("B.shots[0].scene = 'Drone over the pier toward her.'; B.shots[0].land = true; renderBoard(); 1"); time.sleep(0.3)
        check("footage: the default clip model can land on a frame", tab.js("!!framed(clipModel()).last_frame"), tab.js("clipModel().label"))
        if tab.js("framed(clipModel()).last_frame"):
            tab.js("const kk = {id: '17940000000099-aikey0', reel: 'fttest00', shot: B.shots[0].n, letter: 'A', kind: 'image', status: 'done', board: B.shots[0].id, role: 'key', file: 'ai.jpg', url: '', thumb_url: ''}; mergeTakes([kk]); animate(B.shots[0]); 1")
            time.sleep(0.8)
            li = tab.js("_ft.runs[_ft.runs.length - 1]")
            check("footage: a lead-in lands on the chosen version's first frame", tab.js("_ft.frames[0]") == {"at": 0, "shot": tab.js("B.shots[1].n"), "board": tab.js("B.shots[1].id")}
                  and li["first_frame"] == "ai.jpg" and li["last_frame"] == "ff.jpg", (tab.js("_ft.frames"), li))
        foot_id, foot_len = tab.js("B.shots[1].id"), tab.js("lenOf(B.shots[1])")     # the version the card plays now
        tab.js("const b = $('#bBrief'); b.value = 'A :15 around the pier footage.'; b.dispatchEvent(new Event('input')); window.confirm = () => true; $('#bPlan').click(); 1"); time.sleep(0.8)
        check("footage: Gobo plans around it, the card kept as it was", tab.js("_ft.plan.footage") == [{"id": foot_id, "seconds": foot_len, "notes": ""}]
              and tab.js("B.shots.map(s => s.type)") == ["shot", "footage"] and tab.js("B.shots[1].id") == foot_id, tab.js("_ft.plan && _ft.plan.footage"))
        tab.shot("19-board-footage")
        tab.js("$('#viewSeg [data-v=shot]').click(); select('17940000000000-foot00'); 1"); time.sleep(0.4)
        acts = tab.js("$('#actions').textContent")
        check("footage: the Shot view can change and lead into a clip, and extend it when a tool can",
              all(a in acts for a in ("Change clip", "Lead into it")) and ("Extend clip" in acts) == tab.js("!!defaultExtend()"), acts)
        tab.js("window.fetch = window._ff; upload = window._upl; newReel(); localStorage.removeItem('studio.view'); localStorage.removeItem('studio.reel'); 1")

        # ── the Content Generator: same page, stills only, batch tools ───────
        tab.call("Page.navigate", url=f"{BASE}/studio/stills")
        time.sleep(3)
        tab.js("localStorage.clear(); 1")
        tab.call("Page.reload")
        time.sleep(3)
        check("cg: titled, stills only", tab.js("document.title").startswith("Content Generator") and tab.js("STILLS") is True
              and tab.js("S.mode") == "image" and tab.js("$('.seg.big').classList.contains('hidden')"))
        check("cg: every model is a still model", tab.js("MODELS.every(m => m.kind === 'image' || m.kind === 'upscale')")
              and tab.js("byKind('image').length") > 50, tab.js("byKind('image').length"))
        check("cg: plain, undirected by default", tab.js("S.pmode") == "plain"
              and tab.js("Object.values(S.direction).every(v => v === 'auto')"))
        tab.js("const s=$('#scene'); s.value='A red tractor in a wheat field.'; s.dispatchEvent(new Event('input')); 1")
        time.sleep(0.8)
        check("cg: the prompt is exactly what you wrote", tab.js("$('#segs').textContent.trim()") == "A red tractor in a wheat field.",
              tab.js("$('#segs').textContent"))
        check("cg: keeps its own storage", tab.js("Object.keys(localStorage).every(k => !k.startsWith('studio.'))")
              and tab.js("Object.keys(localStorage).some(k => k.startsWith('cg.'))"))
        tab.js("$('#modelBtn').click(); 1"); time.sleep(0.3)
        check("cg: no video tab in the picker", not tab.js("!!$('#mKind')"))
        tab.js("closePop(); 1")
        tab.js("for (let i = 0; i < 25; i++) $('#tPlus').click(); 1")
        check("cg: batches up to 20", tab.js("S.ntakes") == 20 and "Images" in tab.js("$('#fmt').textContent"),
              tab.js("S.ntakes"))
        tab.js("$('#tMinus').click(); 1")
        # stub the network: prompts from GPT-4o and every /run — nothing is generated or spent
        tab.js("""window._fetch = window.fetch; window._runs = [];
          window.fetch = async (u, o) => {
            const j = d => new Response(JSON.stringify(d), {headers: {'Content-Type': 'application/json'}});
            u = String(u);
            if (u.includes('prompt-variants')) return j({prompts: ['P one', 'P two', 'P three', 'P four'], negative_prompts: ['n1', 'n2', 'n3', 'n4']});
            if (u.endsWith('/reels') && o && o.method === 'POST') return j({id: 'cgtest00', name: 'Test', app: 'cg'});
            if (u.endsWith('/run')) { const b = JSON.parse(o.body); window._runs.push(b);
              return j({shot: _runs.length, takes: b.prompts.map((p, i) => ({id: `00000000000${_runs.length}${i}-run${i}`, reel: 'cgtest00', shot: _runs.length, letter: 'ABCD'[i], kind: 'image', status: 'done', label: 'x', prompt: p.scene}))}); }
            if (u.includes('/reels/cgtest00')) return j({reel: {id: 'cgtest00', name: 'Test'}, takes: S.takes});
            return window._fetch(u, o);
          }; 1""")
        tab.js("setRefFiles([new File([new Uint8Array([255,216,255])], 'ref.jpg', {type: 'image/jpeg'})]); 1")
        check("cg: a reference arms Write prompts", not tab.js("$('#refWrite').disabled") and tab.js("$('#refWrite').textContent") == "Write 4 prompts")
        tab.js("$('#refWrite').click(); 1")
        time.sleep(1.5)
        check("cg: prompts come back editable", tab.js("$$('#refList textarea').length") == 4, tab.js("$$('#refList textarea').length"))
        tab.js("$('#refList [data-x=\"3\"]').click(); const t = $('#refList textarea'); t.value = 'P one, edited'; t.dispatchEvent(new Event('input')); 1")
        tab.shot("10-cg-prompts")
        tab.js("$('#refGen').click(); 1")
        time.sleep(1)
        run = tab.js("window._runs[0]")
        check("cg: Generate sends the batch as one shot", run and [p["scene"] for p in run["prompts"]] == ["P one, edited", "P two", "P three"]
              and run["prompts"][1]["negative_prompt"] == "n2" and run["models"] == [run["model"]] and run.get("prompt_override") is None, run)
        tab.js("setRefFiles([1, 2].map(i => new File([new Uint8Array([255,216,255])], `img${i}.jpg`, {type: 'image/jpeg'})), 'Shoot'); 1")
        check("cg: a folder becomes a batch", tab.js("$('#refWrite').textContent") == "Run 2 images × 4" and "Shoot · 2 images" in tab.js("$('#refName').textContent"))
        tab.js("$('#refWrite').click(); 1")
        time.sleep(3)
        check("cg: one shot per image, its prompts each", tab.js("window._runs.length") == 3 and tab.js("window._runs[2].prompts.length") == 4
              and "Done" in tab.js("$('#refStatus').textContent"), tab.js("$('#refStatus').textContent"))
        tab.js("$('#dlSize').value = '4000'; $('#dlSize').dispatchEvent(new Event('change')); 1")
        check("cg: downloads at the chosen size", "size=4000" in tab.js("$('#dlAll').href"), tab.js("$('#dlAll').href"))
        tab.shot("11-cg")
        tab.shot("12-cg-phone", w=390, h=844)
        check("cg: no sideways scroll on a phone", tab.js("document.documentElement.scrollWidth - window.innerWidth") <= 1)
        tab.call("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
        tab.js("window.fetch = window._fetch; localStorage.clear(); 1")

        # ── the guide: every feature, searchable ───────────────────────────
        check("guide: linked from the header", tab.js("$('#guideBtn').getAttribute('href')") == "/studio/guide")
        n_catalog = tab.js("fetch('/studio/catalogue').then(r => r.json()).then(d => d.models.length)")
        tab.call("Page.navigate", url=f"{BASE}/studio/guide"); time.sleep(2)
        n_topics = tab.js("$$('.topic').length")
        n_models = tab.js("$$('.topic.model').length")
        check("guide: contents built from the page", n_topics - n_models >= 40 and tab.js("$$('#toc a[data-t]').length") == n_topics - n_models
              and tab.js("$$('#toc a[data-g]').length") == tab.js("$$('.dgroup').length"), n_topics)
        tab.js("const q = $('#q'); q.value = 'trim'; q.dispatchEvent(new Event('input')); 1"); time.sleep(0.5)
        shown = tab.js("$$('.topic').filter(t => !t.classList.contains('hidden')).map(t => t.id)")
        check("guide: search narrows to the matching topics", 0 < len(shown) < n_topics and "trim" in shown and "footage-board" in shown, shown)
        check("guide: matches are highlighted", tab.js("$$('#guide mark').length") > 0 and tab.js("$('#count').textContent").endswith("topics"))
        tab.js("const q = $('#q'); q.value = 'zzzqqq'; q.dispatchEvent(new Event('input')); 1"); time.sleep(0.4)
        check("guide: says when nothing matches", not tab.js("$('#none').classList.contains('hidden')"))
        tab.js("$('#q').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); 1"); time.sleep(0.3)
        check("guide: Esc clears the search", tab.js("$$('.topic.hidden').length") == 0 and tab.js("$$('#guide mark').length") == 0)
        tab.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: '/'})); 1")
        check("guide: / focuses the search", tab.js("document.activeElement === $('#q')"))
        tab.call("Page.navigate", url=f"{BASE}/studio/guide?q=extend%20footage"); time.sleep(2)
        check("guide: a search can be linked to", tab.js("$('#q').value") == "extend footage" and tab.js("!$('#footage-extend').classList.contains('hidden')"))
        # the model directory: every model, searchable, with two filters
        seen = tab.js("$$('.topic.model').filter(t => !t.classList.contains('hidden')).length")
        check("guide: the directory lists every model", n_models == n_catalog and tab.js("$('#dirN').textContent") == f"{seen} model{'' if seen == 1 else 's'} shown",
              (n_models, n_catalog, seen, tab.js("$('#dirN').textContent")))
        tab.js("const q = $('#q'); q.value = 'kling'; q.dispatchEvent(new Event('input')); 1"); time.sleep(0.5)
        kl = tab.js("$$('.topic.model').filter(t => !t.classList.contains('hidden')).map(t => t.id)")
        check("guide: a maker's name finds its models", 3 < len(kl) < n_models and all(x.startswith("m-") for x in kl) and "m-kling-kling-v3-omni" in kl, len(kl))
        check("guide: empty directory groups fold away", tab.js("$$('.dgroup').every(g => g.classList.contains('hidden') === $$('.model', g).every(t => t.classList.contains('hidden')))"))
        tab.js("$('#q').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); 1"); time.sleep(0.3)
        tab.js("$('#fPicks').click(); 1"); time.sleep(0.3)
        picks = tab.js("$$('.topic.model').filter(t => !t.classList.contains('hidden')).map(t => t.dataset.pick)")
        check("guide: Our picks only", 20 < len(picks) < n_models and set(picks) == {"1"} and tab.js("!$('#reading-entry').classList.contains('hidden')"), len(picks))
        tab.js("$('#fPicks').click(); $('#fCurrent').click(); 1"); time.sleep(0.3)
        st = tab.js("$$('.topic.model').filter(t => !t.classList.contains('hidden')).map(t => t.dataset.status)")
        check("guide: Hide older versions (none are written up as older)", len(st) == n_models and set(st) == {"current"}, len(st))
        tab.js("$('#fCurrent').click(); 1"); time.sleep(0.3)
        check("guide: filters off shows them all again", tab.js("$$('.topic.model.hidden').length") == 0)
        tab.call("Page.navigate", url=f"{BASE}/studio/guide#m-wan-wan2-7-videoedit"); time.sleep(2)
        check("guide: a model link lands on its entry", abs(tab.js("$('#m-wan-wan2-7-videoedit').getBoundingClientRect().top") - 84) < 40,
              tab.js("$('#m-wan-wan2-7-videoedit').getBoundingClientRect().top"))
        tab.shot("22-directory")
        tab.call("Page.navigate", url=f"{BASE}/studio/guide"); time.sleep(2)
        tab.shot("20-guide")
        tab.shot("21-guide-phone", w=390, h=844)
        check("guide: no sideways scroll on a phone", tab.js("document.documentElement.scrollWidth - window.innerWidth") <= 1)
        tab.call("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)

        # ── the Director: one line in, a film out (/direct opens it whatever the front door is) ──
        tab.call("Page.navigate", url=f"{BASE}/direct"); time.sleep(3)
        tab.js("localStorage.clear(); 1"); tab.call("Page.reload"); time.sleep(3)
        check("director: opens on the line", tab.js("!$('#director').classList.contains('off')") and "film" in tab.js("$('#director h1').textContent"))
        check("director: the SuperStock strip is across its top too", tab.js("!!$('#director > .ssbar') && $('#director > .ssbar').getBoundingClientRect().top === 0"))
        check("director: a way home, where the studio's Home goes", tab.js("$('#dHome').getAttribute('href')") == tab.js("$('.homebtn').getAttribute('href')")
              and tab.js("$('#director .dlogo').getAttribute('href')") == tab.js("$('.homebtn').getAttribute('href')"), tab.js("$('#dHome').getAttribute('href')"))
        check("director: 13 looks, Let Gobo decide first", tab.js("$$('#director .look img').length") == 13
              and tab.js("$('#director .look.on').dataset.look") == "auto")
        c0 = tab.js("$('#dCost').textContent")
        check("director: the cost and the wait are shown", "$" in c0 and "minutes" in c0, c0)
        tab.shot("30-director")
        tab.js("$('#director .look[data-look=western]').click(); $('#dLen [data-v=\"30\"]').click();"
               "$('#dShape [data-v=\"9:16\"]').click(); $('#dQual [data-v=best]').click(); 1")
        c1 = tab.js("$('#dCost').textContent")
        check("director: Best and 30 seconds cost more", float(c1.split("$")[1].split()[0]) > float(c0.split("$")[1].split()[0]), f"{c0} → {c1}")
        tab.js("window._runs = []; window.autopilot.run = (o, ui) => { _runs.push(o); window._ui = ui; return new Promise(r => { window._fin = r; }); }; 1")
        # Roll camera won't start a film the balance can't pay for: the test's balance, not the real account's
        tab.js("""window._cf = window.fetch; window.fetch = (u, o) => String(u).endsWith('/credits')
          ? Promise.resolve(new Response(JSON.stringify({credits: 1000}), {headers: {'Content-Type': 'application/json'}})) : _cf(u, o); 1""")
        tab.js("$('#dLine').value = ''; $('#dRoll').click(); 1"); time.sleep(0.5)
        check("director: no line, no run", not tab.js("$('#dWarn').hidden") and tab.js("_runs.length") == 0)
        tab.js("$('#director .ideas button').click(); $('#dRoll').click(); 1"); time.sleep(1.2)
        o = tab.js("_runs[0]") or {}
        check("director: Roll camera hands Autopilot the choices", (o.get("seconds"), o.get("look"), o.get("grade")) == (30, "shot", 0.65)
              and o.get("clip") == tab.js("director.models(director.quality[2]).clip.id"), o)
        check("director: the look and shape are on the shot", tab.js("[S.direction.look, S.direction.film, S.direction.grade, S.direction.lens, S.aspect].join()")
              == "western,slide,desert,auto,9:16", tab.js("[S.direction.look, S.direction.film, S.direction.grade, S.aspect].join()"))
        tab.js("_ui.start('A test film'); _ui.step('plan', 'ok', '3 shots'); _ui.step('keys', 'now', ''); 1"); time.sleep(1.2)
        check("director: the shoot shows each step and a clock", not tab.js("$('#dShoot').hidden") and tab.js("$('#dSteps li.now span').textContent") == "Painting the first frames"
              and "Rolling" in tab.js("$('#dClock').textContent"))
        tab.js("$('#dSelf').click(); 1"); time.sleep(0.3)
        check("director: Direct it yourself opens the studio, which says it's rolling",
              tab.js("$('#director').classList.contains('off')") and "rolling" in tab.js("$('#dirBtn').textContent"))
        tab.js("$('#dirBtn').click(); 1"); time.sleep(0.3)
        check("director: and its button comes back to the shoot", not tab.js("$('#dShoot').hidden"))
        tab.js("_ui.step('clips', 'bad', 'Shot 2: the provider said no'); _fin(undefined); 1"); time.sleep(0.8)
        check("director: a failed film says why and offers Try again", "the provider said no" in tab.js("$('#dFail').textContent")
              and tab.js("!!$('#dAgain')"))
        film = {"id": "17940000000000-film00", "reel": tab.js("S.reel && S.reel.id") or "abcdef123456", "kind": "video", "status": "done",
                "url": "/static/tour/gobo-tour.mp4", "seconds": 15, "label": "The cut"}
        tab.js(f"_ui.done({{ final: {json.dumps(film)}, film: {json.dumps(film)}, title: 'A test film' }}); 1"); time.sleep(1)
        check("director: the finished film plays, with Share and Download", not tab.js("$('#dDone').hidden")
              and tab.js("$('#dDone video').getAttribute('src')") == film["url"] and tab.js("!!$('#dShare') && $('#dShareBox').hidden"))
        tab.shot("31-director-film")
        tab.js("$('#dNew').click(); 1"); time.sleep(0.6)
        check("director: Make another goes back to the line", not tab.js("$('#dCompose').hidden"))
        tab.shot("32-director-phone", w=390, h=844)
        check("director: no sideways scroll on a phone", tab.js("$('#director').scrollWidth <= innerWidth + 1"))
        tab.js("window.fetch = window._cf; 1")
        tab.call("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)

        # ── Colour: the Lab's Grade, advanced, on a real clip the server keeps ──
        tab.call("Page.navigate", url=f"{BASE}/studio"); time.sleep(3)
        made = tab.js(f"""(async () => {{
          const r = await api('/reels', {{method: 'POST', json: {{name: 'Colour check'}}}});
          const bin = Uint8Array.from(atob('{_bars_clip()}'), c => c.charCodeAt(0));
          const fd = new FormData(); fd.append('file', new Blob([bin], {{type: 'video/mp4'}}), 'bars.mp4');
          const t = await api(`/reels/${{r.id}}/footage`, {{method: 'POST', body: fd}});
          await loadReel(r.id); select(t.take.id); return [r.id, t.take.id];
        }})()""")
        tab.js("$('#labBtn').click(); $('#lab [data-tab=grade]').click(); 1"); time.sleep(2)
        tab.js("$('#gAdv').click(); 1"); time.sleep(3)
        check("colour: Advanced opens the Colour page on the take", tab.js("!$('#colour').classList.contains('hidden') && $('#cCv').width > 0")
              and "take A" in tab.js("$('#cWhat').textContent"), tab.js("$('#cWhat').textContent"))
        lit = "(() => { const c = $('#cScope'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; })()"
        check("colour: the waveform draws the picture", tab.js(lit) > 1000, tab.js(lit))
        tab.js("""const c = $('#cWheels [data-wheel=lift] canvas'), b = c.getBoundingClientRect(), r = b.width / 2;
          const at = (t, f) => c.dispatchEvent(new PointerEvent(t, {clientX: b.left + r + f[0] * r, clientY: b.top + r + f[1] * r, pointerId: 7, bubbles: true}));
          at('pointerdown', [0.85, 0.1]); at('pointerup', [0.85, 0.1]); 1""")
        lift = tab.js("goboGrade.look.wheels && goboGrade.look.wheels.lift")
        check("colour: dragging the lift wheel toward blue lifts the blue", bool(lift) and lift[2] > 0.05 and lift[0] < 0, lift)
        tab.js("""const c = $('#cCurve'), b = c.getBoundingClientRect(), pad = 14 * b.width / c.width, span = b.width - 2 * pad;
          const ev = t => c.dispatchEvent(new PointerEvent(t, {clientX: b.left + pad + 0.5 * span, clientY: b.top + pad + 0.3 * span, pointerId: 8, bubbles: true}));
          ev('pointerdown'); ev('pointerup'); 1""")
        check("colour: a click on the curve adds a point", tab.js("goboGrade.look.curves && goboGrade.look.curves.y.length") == 3, tab.js("JSON.stringify(goboGrade.look.curves)"))
        tab.js("$('#cScopes [data-scope=vector]').click(); 1"); time.sleep(0.8)
        check("colour: the vectorscope", tab.js("$('#cScope').width") == 220 and tab.js(lit) > 50)
        swap = "TITLE \\\"Swap\\\"\\nLUT_3D_SIZE 2\\n" + "".join(f"{b} {g} {r}\\n" for b in (0, 1) for g in (0, 1) for r in (0, 1))
        tab.js(f"""const dt = new DataTransfer(); dt.items.add(new File(["{swap}"], 'swap.cube'));
          $('#cLutFile').files = dt.files; $('#cLutFile').dispatchEvent(new Event('change')); 1""")
        time.sleep(2.5)
        lut = tab.js("goboGrade.look.lut3d")
        check("colour: a .cube imports, uploads to the reel and goes on the look", bool(lut) and lut["id"].startswith("lut-")
              and tab.js("$('#cLut').selectedOptions[0].textContent") == "Swap" and not tab.js("$('#cLutOpts').classList.contains('hidden')"), lut)
        swap_id = (lut or {}).get("id")
        # a warm, contrasty LUT is read into the sliders: they move, and the picture is still the LUT's
        warm = "TITLE \\\"Warm\\\"\\nLUT_3D_SIZE 9\\n" + "".join(
            "%.5f %.5f %.5f\\n" % tuple(min(1, max(0, v)) for v in ((r / 8) ** 0.85 * 1.04, (g / 8) ** 0.95, (b / 8) * 0.86))
            for b in range(9) for g in range(9) for r in range(9))
        before = tab.js("JSON.stringify({temp: goboGrade.look.temp, lift: (goboGrade.look.wheels || {}).lift})")
        tab.js(f"""const dt = new DataTransfer(); dt.items.add(new File(["{warm}"], 'warm.cube'));
          $('#cLutFile').files = dt.files; $('#cLutFile').dispatchEvent(new Event('change')); 1""")
        time.sleep(3)
        got = tab.js("JSON.stringify({lut: goboGrade.look.lut3d, temp: goboGrade.look.temp, gain: (goboGrade.look.wheels || {}).gain, slider: $('#cBasics [data-k=temp]').value})")
        gl = json.loads(got)
        check("colour: a LUT is read into the sliders, its rest kept first", (gl["lut"] or {}).get("of", "").startswith("lut-") and gl["lut"]["first"]
              and (gl["temp"] > 0.1 or any(abs(v) > 0.005 for v in (gl["gain"] or [])))
              and abs(float(gl["slider"]) - gl["temp"]) < 0.011
              and tab.js("$('#cLut').selectedOptions[0].textContent") == "Warm" and tab.js("$('#cLutOpts').classList.contains('hidden')")
              and "Keep it as one LUT instead" in tab.js("$('#cLutNote').textContent"), got)
        tab.js("$('#cLutNote [data-lut=whole]').click(); 1"); time.sleep(1)
        back = tab.js("JSON.stringify({temp: goboGrade.look.temp, lift: (goboGrade.look.wheels || {}).lift})")
        check("colour: Keep it as one LUT puts the sliders back", back == before and not tab.js("goboGrade.look.lut3d.of")
              and tab.js("goboGrade.look.lut3d.first") is False and "Read it into the sliders" in tab.js("$('#cLutNote').textContent"), [before, back])
        tab.js("$('#cLutNote [data-lut=read]').click(); 1"); time.sleep(2.5)
        check("colour: Read it into the sliders again", bool(tab.js("goboGrade.look.lut3d.of")))
        tab.js(f"$('#cLut').value = '{swap_id}'; $('#cLut').dispatchEvent(new Event('change')); 1"); time.sleep(2)
        check("colour: a LUT no slider can follow stays whole", tab.js("goboGrade.look.lut3d.id") == swap_id and not tab.js("goboGrade.look.lut3d.of"))
        # a CDL's slope, offset and power land on the wheels, exactly
        cdl = ('<ColorDecisionList><ColorDecision><ColorCorrection id=\\"a\\"><SOPNode><Slope>1.1 1.0 0.9</Slope><Offset>0.02 0 -0.01</Offset>'
               '<Power>1.0 0.95 1.05</Power></SOPNode><SatNode><Saturation>1.0</Saturation></SatNode></ColorCorrection></ColorDecision></ColorDecisionList>')
        tab.js(f"""const dt = new DataTransfer(); dt.items.add(new File(["{cdl}"], 'shot.cdl'));
          $('#cLutFile').files = dt.files; $('#cLutFile').dispatchEvent(new Event('change')); 1""")
        time.sleep(1.5)
        same = tab.js("""(async () => { const g = await goboReady, w = goboGrade.look.wheels;
          const px = () => { const d = new Uint8ClampedArray(64 * 4); for (let i = 0; i < 64; i++) d.set([i * 4, 255 - i * 3, (i * 37) % 256, 255], i * 4); return {width: 64, height: 1, data: d}; };
          const a = px(), b = px();
          g.grade(a, {cdl: {slope: [1.1, 1, 0.9], offset: [0.02, 0, -0.01], power: [1, 0.95, 1.05], sat: 1}}, 1);
          g.grade(b, {wheels: w}, 1);
          let worst = 0; for (let i = 0; i < a.data.length; i++) worst = Math.max(worst, Math.abs(a.data[i] - b.data[i]));
          return [worst, !goboGrade.look.cdl, w.gain[0] > w.gain[2]]; })()""")
        check("colour: a CDL goes on the wheels, exactly", same and same[0] <= 2 and same[1] and same[2], same)
        tab.shot("33-colour")
        tab.js("$('#cSimple').click(); 1"); time.sleep(1.5)
        check("colour: Simple goes back to the Lab's Grade, the grade kept", tab.js("$('#colour').classList.contains('hidden')")
              and not tab.js("$('#lab').classList.contains('hidden')") and tab.js("$('#gLut').value") == (lut or {}).get("id")
              and tab.js("!!goboGrade.look.wheels"))
        tab.js("$('#gAdv').click(); 1"); time.sleep(2.5)
        tab.js("$('#cBake').click(); 1")
        graded = None
        for _ in range(60):
            time.sleep(0.5)
            graded = tab.js("(S.takes.find(t => t.action === 'grade') || {}).status")
            if graded in ("done", "failed"):
                break
            tab.js("loadReel(S.reel.id); 1")
        g = tab.js("JSON.stringify(S.takes.find(t => t.action === 'grade') || {})")
        check("colour: Grade the take grades it on the server with the LUT and the wheels", graded == "done" and '"lut3d"' in g and '"wheels"' in g, g[:300])
        tab.js("$('#gAdv').click(); 1"); time.sleep(2)
        tab.shot("34-colour-phone", w=390, h=844)
        check("colour: no sideways scroll on a phone", tab.js("$('#colour').scrollWidth <= innerWidth + 1"))
        tab.call("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
        tab.js("$('#cDone').click(); 1")

        # ── several takes, one grade: a drifted take matched to take A, then graded alike ──
        tab.js(f"""(async () => {{
          const bin = Uint8Array.from(atob('{_bars_clip("eq=brightness=-0.07:saturation=0.7,colorbalance=bs=0.25:rs=-0.15")}'), c => c.charCodeAt(0));
          const fd = new FormData(); fd.append('file', new Blob([bin], {{type: 'video/mp4'}}), 'drift.mp4');
          const t = await api(`/reels/${{S.reel.id}}/footage`, {{method: 'POST', body: fd}});
          await loadReel(S.reel.id); window._drift = t.take.id;
          select(S.takes.find(x => x.action !== 'grade' && x.id !== t.take.id && x.kind === 'video').id); return 1; }})()""")
        time.sleep(1)
        tab.js("setView('shot'); $('#labBtn').click(); $('#lab [data-tab=grade]').click(); 1"); time.sleep(2)
        tab.js("$('#gMatch').click(); 1"); time.sleep(0.5)
        listed = tab.js("$$('#gMatchBox .mt input').map(i => i.value.split(':')[1])")
        check("match: the other takes are offered, not the graded ones", tab.js("window._drift") in listed
              and not any(t for t in tab.js("S.takes.filter(t => t.action === 'grade').map(t => t.id)") if t in listed), listed)
        tick = """$$('#gMatchBox .mt input').forEach(i => { const want = i.value.endsWith(':' + window._want);
          if (i.checked !== want) { i.checked = want; i.dispatchEvent(new Event('change')); } }); 1"""
        tab.js("window._want = window._drift; 1"); tab.js(tick); tab.js("$('#gMatchGo').click(); 1")
        for _ in range(40):
            time.sleep(0.5)
            if "→" in (tab.js("$('#gMatchOut').textContent") or ""):
                break
        made = tab.js("JSON.stringify(S.takes.filter(t => t.action === 'grade' && t.parent === window._drift).map(t => ({label: t.label, cdl: !!(t.look || {}).cdl})))")
        note = tab.js("$('#gMatchOut').textContent")
        check("match: the drifted take is matched to A's colour and graded like it", '"cdl":true' in made and "Matched to" in made and "→" in note, [made, note])
        tab.shot("36-match")
        # a take from another reel, from the Library: copied in, then matched and graded here
        tab.js(f"""window._away = null; (async () => {{
          const bin = Uint8Array.from(atob('{_bars_clip("eq=brightness=0.05:saturation=1.3")}'), c => c.charCodeAt(0));
          const r = await api('/reels', {{method: 'POST', json: {{name: 'Elsewhere'}}}});
          const fd = new FormData(); fd.append('file', new Blob([bin], {{type: 'video/mp4'}}), 'away.mp4');
          const t = await api(`/reels/${{r.id}}/footage`, {{method: 'POST', body: fd}});
          window._away = {{reel: r.id, id: t.take.id}}; }})(); 1""")
        for _ in range(20):
            time.sleep(0.5)
            if tab.js("!!window._away"):
                break
        tab.js("$('#gMatch').click(); 1"); time.sleep(0.5)
        tab.js("$('#gMatchFrom [data-f=lib]').click(); 1"); time.sleep(1.5)
        away = tab.js("$$('#gMatchBox .mt input').map(i => i.value)")
        check("match: the Library offers takes from other reels", f"{tab.js('_away.reel')}:{tab.js('_away.id')}" in away, away)
        tab.js("window._want = _away.id; 1"); tab.js(tick); tab.js("$('#gMatchGo').click(); 1")
        for _ in range(40):
            time.sleep(0.5)
            if "→" in (tab.js("$('#gMatchOut').textContent") or ""):
                break
        got = json.loads(tab.js("""JSON.stringify((() => { const c = S.takes.find(t => t.copied_from === `${_away.reel}:${_away.id}`);
          return {copied: !!c, graded: !!c && S.takes.some(t => t.action === 'grade' && t.parent === c.id && (t.look || {}).cdl)}; })())"""))
        check("match: a take from another reel is copied in, then matched and graded here", got == {"copied": True, "graded": True},
              [got, tab.js("$('#gMatchOut').textContent")])
        # ── Open in ComfyUI: the first time asks where it is, then hands it the workflow ──
        tab.js("try { localStorage.removeItem('gobo.comfyui'); } catch (e) {} window._open0 = window.open; window._opened = []; window.open = u => { window._opened.push(u); return null; }; 1")
        tab.js("$('#gComfy').click(); 1"); time.sleep(0.5)
        check("comfyui: the first time, it asks where ComfyUI is and how to add Gobo's nodes",
              tab.js("!!$('.comfyask') && !!$('.comfyask a[href=\\'/comfyui/gobo-nodes.zip\\']')"))
        tab.shot("37-comfyui-ask")
        tab.js("$('#caAddr').value = 'http://127.0.0.1:8188/'; $('#caGo').click(); 1"); time.sleep(2)
        url = tab.js("window._opened[window._opened.length - 1] || ''")
        sent = url.split("gobo=")[1] if "gobo=" in url else ""
        flow = json.loads(base64.urlsafe_b64decode(sent + "=" * (-len(sent) % 4))) if sent else {}
        classes = [n["class_type"] for n in flow.get("workflow", {}).values()]
        check("comfyui: it opens a tab that hands ComfyUI the take and its look as Gobo's nodes",
              url.startswith("/comfyui/open#to=http%3A%2F%2F127.0.0.1%3A8188&gobo=")
              and classes[:2] == ["GoboLoadTake", "GoboLook"] and "GoboGrade" in classes and "SaveVideo" in classes, [url[:60], classes])
        comfy_url = url
        tab.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); 1"); time.sleep(0.3)
        # the shot on the panel: the Gobo Director with its scene and choices, wired to the model's own node
        tab.js("setView('shot'); setMode('video', true); S.scene = 'A ferry leaves a foggy pier.'; S.direction.shot = 'wide'; S.direction.move = 'push'; "
               "S.model.video = 'kling/kling-v3-omni'; onShotChange(); $('#shotComfy').click(); 1"); time.sleep(2)
        url = tab.js("window._opened[window._opened.length - 1] || ''")
        sent = url.split("gobo=")[1] if "gobo=" in url else ""
        flow = json.loads(base64.urlsafe_b64decode(sent + "=" * (-len(sent) % 4))) if sent else {}
        nodes = flow.get("workflow", {})
        d = next((n for n in nodes.values() if n["class_type"] == "GoboDirector"), {})
        k = next((n for n in nodes.values() if n["class_type"].startswith("KlingOmniPro")), {})
        check("comfyui: the shot opens as the Gobo Director, wired to the model's own node",
              (d.get("inputs") or {}).get("scene") == "A ferry leaves a foggy pier." and (d.get("inputs") or {}).get("shot") == "Wide"
              and (d.get("inputs") or {}).get("move") == "Push in" and isinstance((k.get("inputs") or {}).get("prompt"), list)
              and "SaveVideo" in [n["class_type"] for n in nodes.values()], [flow.get("title"), sorted(n["class_type"] for n in nodes.values())])
        tab.js("window.open = window._open0; 1")

        # ── the library to a board: takes from anywhere, a card each, in film order ──
        tab.js("window._prompt = window.prompt; window.prompt = () => 'Library check'; LIB.kind = ''; LIB.starred = false; LIB.q = ''; 1")
        tab.js("$('#libBtn').click(); 1"); time.sleep(2)
        tab.js("$$('#libSheet .lc .pick').slice(0, 2).forEach(p => { p.checked = true; p.dispatchEvent(new Event('change')); }); 1")
        check("library: Put on a board, for a selection", tab.js("!!$('#libBoard')"))
        tab.js("$('#libBoard').click(); 1"); time.sleep(0.3)
        tab.js("$('#lbNew').click(); 1"); time.sleep(3)
        got = tab.js("JSON.stringify({board: boardOn(), name: S.reel && S.reel.name, cards: B.shots.length, "
                     "filled: B.shots.filter(s => pickOf(s, 'clip') || pickOf(s, 'key')).length, shown: $$('.bcard').length})")
        gl = json.loads(got)
        check("library: they land on a new board, a card each, ready to cut", gl["board"] and gl["name"] == "Library check"
              and gl["cards"] == 2 and gl["filled"] == 2 and gl["shown"] >= 2, got)
        tab.shot("35-library-board")
        tab.js("setView('shot'); $('#libBtn').click(); 1"); time.sleep(2)
        tab.js("$('#libSheet .lc').click(); 1"); time.sleep(0.8)          # the newest: a card's take
        had = tab.js("!!$('#lvBoard')")
        tab.js("$('#lvBoard') && $('#lvBoard').click(); 1"); time.sleep(2)
        check("library: Open on its board goes to the board its card is on, the card showing that take", had and tab.js("boardOn()")
              and tab.js("$('#libSheet').classList.contains('hidden')")
              and tab.js("(() => { const t = LIB.items[0], s = B.shots.find(x => x.id === t.board); return !!s && pickOf(s, t.role).id === t.id; })()")
              and tab.js("!!$('.bcard.found')"))
        # the card was using another take of its own: it switches, and says what it had
        tab.js("""window._sw = (async () => {
            const t = LIB.items[0], s = B.shots.find(x => x.id === t.board);
            const other = Object.assign({}, S.takes.find(x => x.id === t.id), {id: t.id + 'z', letter: 'Z'});
            S.takes.push(other); s[t.role] = other.id; refreshBoard();
            await libOpen(t, true);
            return {shows: pickOf(s, t.role).id === t.id, said: $('#toast').textContent};
          })(); 1"""); time.sleep(1.5)
        tab.js("_sw.then(r => window._swr = r); 1"); time.sleep(0.3)
        check("library: a card using another take switches to the one opened, and says which it had",
              tab.js("_swr && _swr.shows") and "it had Z" in (tab.js("_swr && _swr.said") or ""), tab.js("_swr && _swr.said"))
        tab.js("$('#libBtn').click(); 1"); time.sleep(2)
        tab.js("$('#libSheet .lc [data-o=clip]').click(); 1"); time.sleep(1.5)
        check("library: As a clip on a take in the grid puts it on the stage", not tab.js("boardOn()")
              and tab.js("S.current") == tab.js("LIB.items[0].id") and tab.js("$('#libSheet').classList.contains('hidden')"))
        tab.js("window.prompt = window._prompt; 1")

        # ── Open in ComfyUI's tab waits for ComfyUI: nothing at the address yet, then it starts ──
        import http.server, socket, threading
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
        tab.call("Page.navigate", url=BASE + comfy_url.replace("to=http%3A%2F%2F127.0.0.1%3A8188", f"to=http%3A%2F%2F127.0.0.1%3A{port}")); time.sleep(4.5)
        w = json.loads(tab.js("""JSON.stringify({say: $('#say').textContent, where: $('#where').textContent, what: $('#what').textContent,
          now: $('#now').classList.contains('go'), help: $('#help').textContent, strip: !!$('.ssbar')})"""))
        check("comfyui tab: with nothing at the address, it waits and says how to start ComfyUI",
              w["say"] == "Waiting for ComfyUI to start…" and w["where"] == f"http://127.0.0.1:{port}" and w["now"] and w["strip"]
              and w["what"] and "python main.py" in w["help"], w)
        tab.shot("38-comfyui-waiting")

        class Comfy(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(b"<title>ComfyUI</title>up")

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Comfy)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(3)
        href = tab.js("location.href") or ""
        check("comfyui tab: once ComfyUI answers, it opens there with the workflow",
              href.startswith(f"http://127.0.0.1:{port}/#gobo=") and href.split("gobo=")[1] == comfy_url.split("gobo=")[1], href[:90])
        srv.shutdown()

        check("no script errors", not tab.errors, tab.errors[:3])
    finally:
        chrome.terminate()

    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" :: {detail}"))
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed · screenshots in {SHOTS}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
