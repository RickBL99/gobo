"""Make the tour: the short film of Gobo that plays on the sign-in and About pages.

    .venv/bin/python tools/make_tour.py [base-url] --reel REEL_ID [--only N]

base-url is a running Gobo with sign-in off (default http://127.0.0.1:8020).
The reel should be one Autopilot made and the Lab graded: the tour opens its
takes, its Board and its films. Nothing is generated and nothing is spent.

Every scene is the real app, or a slide drawn in HTML, in headless Chrome,
recorded with the DevTools screencast. The captions and the pointer are drawn
into the page itself, so they are set in the app's own type. ffmpeg joins the
scenes with short dissolves into studio/static/tour/gobo-tour.mp4 (recorded at
1920×1080, delivered at 1280×720, H.264, no sound) and saves a poster frame
beside it. --only N records scene N
alone, into the work folder, to look at.
"""
import argparse
import base64
import glob
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "studio", "static", "tour", "gobo-tour.mp4")
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
W, H, FPS, FADE = 1920, 1080, 30, 0.5
LINE = ("A lighthouse keeper's last night before the light goes automatic: he climbs the stairs in a storm, "
        "lights the lamp one final time, and watches the beam sweep the sea.")
LOGO = ('<svg viewBox="0 0 48 48"><rect x="3" y="9" width="42" height="30" rx="5" fill="none" stroke="#f2c14e" stroke-width="3"/>'
        '<circle cx="24" cy="24" r="8.5" fill="none" stroke="#ecebe8" stroke-width="3"/><circle cx="24" cy="24" r="3" fill="#f2c14e"/>'
        '<rect x="8" y="13" width="5" height="3" rx="1" fill="#ecebe8"/></svg>')
FONT = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">')


# ── the browser ──────────────────────────────────────────────────────────────
class Browser:
    def __init__(self, port=9351):
        self.prof = tempfile.mkdtemp(prefix="tour-chrome-")
        self.proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={self.prof}",
                                      "--no-first-run", "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required",
                                      "--allow-file-access-from-files", f"--remote-allow-origins=http://127.0.0.1:{port}",
                                      f"--window-size={W},{H}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                break
            except Exception:
                time.sleep(0.2)
        page = next(t for t in tabs if t["type"] == "page")
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
        self.n, self.frames, self.recording = 0, [], False
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Page.addScriptToEvaluateOnNewDocument", source=READ_ONLY)
        self.view(ui=False)

    def view(self, ui):
        """The app is laid out at 1440×810 and drawn at 4/3, so the frames are
        still 1920×1080 but its type is a third bigger and reads in a small
        player; slides are drawn at 1920×1080 as they are."""
        w, h = (1440, 810) if ui else (W, H)
        self.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=W / w, mobile=False)

    def _event(self, msg):
        if msg.get("method") == "Page.screencastFrame":
            p = msg["params"]
            if self.recording:
                self.frames.append((p["metadata"]["timestamp"], p["data"]))
            self.ws.send(json.dumps({"id": 0, "method": "Page.screencastFrameAck", "params": {"sessionId": p["sessionId"]}}))

    def call(self, method, **params):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self._event(msg)

    def wait(self, seconds):
        """Let time pass, still taking frames."""
        end = time.time() + seconds
        while time.time() < end:
            self.ws.settimeout(max(0.01, end - time.time()))
            try:
                self._event(json.loads(self.ws.recv()))
            except websocket.WebSocketTimeoutException:
                pass
        self.ws.settimeout(60)

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression="{" + expr + "\n}", awaitPromise=True, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description") or r["exceptionDetails"])
        return r.get("result", {}).get("value")

    def go(self, url, settle=2.5):
        self.call("Page.navigate", url=url)
        self.wait(settle)
        self.js("document.fonts.ready.then(() => 1)")
        self.js(OVERLAY)

    def mouse(self, kind, x, y):
        self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", buttons=1 if kind != "mouseReleased" else 0,
                  clickCount=1)

    def record(self):
        self.frames, self.recording = [], True
        self.call("Page.startScreencast", format="jpeg", quality=90, maxWidth=W, maxHeight=H, everyNthFrame=1)

    def stop(self):
        end = time.time()
        self.call("Page.stopScreencast")
        self.recording = False
        return self.frames, end

    def close(self):
        try:
            self.ws.close()
        finally:
            self.proc.terminate()
            shutil.rmtree(self.prof, ignore_errors=True)


# The tour only looks. Every page it opens answers anything but a GET with an
# empty success, before the app's own scripts run, so no take can start and
# nothing is spent or saved, whatever a scene clicks.
READ_ONLY = r"""
(() => {
  const real = window.fetch.bind(window);
  window.fetch = (u, o) => ((o && o.method) || "GET").toUpperCase() === "GET" ? real(u, o)
    : Promise.resolve(new Response("{}", {status: 200, headers: {"Content-Type": "application/json"}}));
  const send = XMLHttpRequest.prototype.send, open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, ...rest) { this._m = String(m).toUpperCase(); return open.call(this, m, ...rest); };
  XMLHttpRequest.prototype.send = function (body) { if (this._m && this._m !== "GET") { this.abort(); return; } return send.call(this, body); };
})();
"""

# The caption, the pointer and a ticker drawn into whatever page is open. They
# hang off <html>, outside <body>, so zooming the app (T.zoom scales the body)
# leaves them alone; their sizes are in screen pixels (--u), so they match
# whether the page is drawn at 1× or 4/3. The ticker repaints one corner pixel
# every frame, so the screencast keeps sending frames while nothing else moves.
# The account's balance is hidden: it's nobody's business in a tour.
OVERLAY = r"""
if (!window.T) {
  const u = 1 / devicePixelRatio, root = document.documentElement;
  root.style.setProperty("--u", u);
  const css = document.createElement("style");
  css.textContent = `
  #credits, #bal { visibility: hidden !important; }
  #tCap { position: fixed; z-index: 2147483646; max-width: calc(1060px * var(--u)); padding: calc(22px * var(--u)) calc(30px * var(--u)) calc(24px * var(--u));
    border-radius: calc(18px * var(--u)); background: rgba(9,9,11,.9); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    border: 1px solid rgba(255,255,255,.14); box-shadow: 0 24px 70px rgba(0,0,0,.65); font-family: Inter, system-ui, sans-serif;
    opacity: 0; transform: translateY(18px); transition: opacity .45s ease, transform .45s ease; pointer-events: none; }
  #tCap.on { opacity: 1; transform: none; }
  #tCap .k { color: #f2c14e; font-weight: 800; font-size: calc(20px * var(--u)); letter-spacing: .16em; text-transform: uppercase; }
  #tCap .t { color: #fff; font-weight: 700; font-size: calc(36px * var(--u)); line-height: 1.22; margin-top: calc(8px * var(--u)); letter-spacing: -.01em; }
  #tPtr { position: fixed; z-index: 2147483647; left: 0; top: 0; width: calc(34px * var(--u)); height: calc(34px * var(--u)); pointer-events: none; opacity: 0;
    transition: transform .65s cubic-bezier(.45,0,.2,1), opacity .3s; filter: drop-shadow(0 3px 7px rgba(0,0,0,.7)); }
  #tPtr svg { width: 100%; height: 100%; }
  #tPtr.on { opacity: 1; }
  #tPtr.fast { transition: transform 0s, opacity .3s; }
  #tPtr .ring { position: absolute; left: -50%; top: -50%; width: 100%; height: 100%; border-radius: 50%; border: 3px solid #f2c14e; opacity: 0; }
  #tPtr .ring.go { animation: tRing .55s ease-out; }
  @keyframes tRing { 0% { opacity: 1; transform: scale(.4); } 100% { opacity: 0; transform: scale(1.8); } }
  #tTick { position: fixed; left: 0; top: 0; width: 2px; height: 2px; z-index: 2147483647; pointer-events: none; animation: tTick .4s linear infinite; }
  @keyframes tTick { from { background: rgba(0,0,0,.01); } to { background: rgba(0,0,0,.03); } }`;
  document.head.appendChild(css);
  const cap = document.createElement("div"); cap.id = "tCap"; root.appendChild(cap);
  const ptr = document.createElement("div"); ptr.id = "tPtr";
  ptr.innerHTML = '<div class="ring"></div><svg viewBox="0 0 24 24"><path d="M4 2.5v17.2l4.6-4.3 2.9 6.6 3.1-1.4-2.9-6.4 6.3-.3z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
  root.appendChild(ptr);
  const tick = document.createElement("div"); tick.id = "tTick"; root.appendChild(tick);
  const px = n => `${n * u}px`;
  addEventListener("pointermove", e => { if (window.T && T.follow) T.at(e.clientX, e.clientY, true); }, true);
  window.T = {
    cap(k, t, where) {
      cap.innerHTML = `<div class="k">${k}</div><div class="t">${t}</div>`;
      Object.assign(cap.style, {left: "", right: "", top: "", bottom: "", marginLeft: ""});
      const at = where || "bl";
      if (at[0] === "b") cap.style.bottom = px(48); else cap.style.top = px(110);
      if (at[1] === "l") cap.style.left = px(48); else if (at[1] === "r") cap.style.right = px(48);
      else { cap.style.left = "50%"; cap.style.marginLeft = `-${cap.offsetWidth / 2}px`; }
      requestAnimationFrame(() => cap.classList.add("on"));
    },
    hide() { cap.classList.remove("on"); },
    // a camera move on the app: the element's centre travels to (x, y) on screen (default: the middle), scaled s
    zoom(sel, s, ms, x, y) {
      const e = document.querySelector(sel), b = document.body, vw = innerWidth, vh = innerHeight;
      const prev = b.style.transform; b.style.transition = "none"; b.style.transform = "none";
      const r = e.getBoundingClientRect(); b.style.transform = prev; void b.offsetWidth;
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      const tx = Math.min(0, Math.max(vw - vw * s, (x ?? vw / 2) - cx * s)), ty = Math.min(0, Math.max(vh - vh * s, (y ?? vh / 2) - cy * s));
      b.style.transformOrigin = "0 0"; b.style.transition = `transform ${ms}ms cubic-bezier(.45,0,.25,1)`;
      b.style.transform = `translate(${tx}px, ${ty}px) scale(${s})`;
    },
    unzoom(ms) { const b = document.body; b.style.transition = `transform ${ms}ms cubic-bezier(.45,0,.25,1)`; b.style.transform = "none"; },
    at(x, y, now) { ptr.classList.toggle("fast", !!now); ptr.classList.add("on"); ptr.style.transform = `translate(${x}px, ${y}px)`; },
    centre(sel) { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; },
    to(sel) { const c = T.centre(sel); if (c) T.at(c[0], c[1]); return c; },
    ring() { const r = ptr.querySelector(".ring"); r.classList.remove("go"); void r.offsetWidth; r.classList.add("go"); },
    type(sel, text, ms) {
      const el = document.querySelector(sel); el.focus(); el.value = ""; let i = 0;
      return new Promise(done => { const t = setInterval(() => {
        el.value = text.slice(0, ++i); el.dispatchEvent(new Event("input", {bubbles: true}));
        if (i >= text.length) { clearInterval(t); el.dispatchEvent(new Event("change", {bubbles: true})); done(1); } }, ms); });
    },
  };
}
1
"""


# ── scenes of the real app ───────────────────────────────────────────────────
class Tour:
    def __init__(self, b: Browser, base: str, reel: str, work: str, source: str, front_door: bool = True):
        self.b, self.base, self.reel, self.work, self.source = b, base.rstrip("/"), reel, work, source.rstrip("/")
        self.front_door = front_door              # the tour of Make a film (STUDIO_FRONT=director), or of the studio
        info = json.load(urllib.request.urlopen(f"{self.base}/studio/reels/{reel}"))
        takes = [t for t in info["takes"] if t.get("status") == "done"]
        self.graded = next(t for t in reversed(takes) if t.get("action") == "grade")
        self.cut = next(t for t in reversed(takes) if t.get("action") == "stitch")
        self.keys = [t for t in takes if t.get("kind") == "image"][:4]
        self.clips = [t for t in takes if t.get("action") == "animate"][:4]
        self.board = info.get("board") or {}

    def url(self, t):
        u = t.get("url") or f"/studio/reels/{self.reel}/file/{t['file']}"
        return self.base + u if u.startswith("/") else u

    def studio(self, query=""):
        b = self.b
        b.view(ui=True)
        b.go(f"{self.base}/studio{query}", 3.5)
        b.js(f"loadReel({json.dumps(self.reel)})")
        b.wait(2.0)
        b.js(OVERLAY)

    def click(self, sel, pause=0.75):
        """Move the pointer onto it, then click it."""
        if not self.b.js(f"T.to({json.dumps(sel)})"):
            raise RuntimeError(f"nothing on the page matches {sel}")
        self.b.wait(pause)
        self.b.js(f"T.ring(); document.querySelector({json.dumps(sel)}).click(); 1")

    # Make a film, the front door. The tour only looks, so Autopilot's run is
    # played back from the reel it made (the frames, the cut, the graded film)
    def front(self):
        b = self.b
        b.view(ui=True)
        b.go(f"{self.base}/direct", 3.5)
        b.js(f"loadReel({json.dumps(self.reel)})")
        b.wait(2.0)
        b.js("director.show('compose'); $('#dLine').value = ''; $('#dFilms').hidden = true; 1")
        b.js(OVERLAY)

    # 2 ─ Make a film: the line goes in
    def make_film(self):
        b = self.b
        self.front()
        b.js("T.at(1000, 600, true)")
        b.record()
        b.wait(0.2)
        b.js("T.cap('Make a film', 'Say it in one line. Pick a look, a length and a shape. No nodes, no settings.', 'bc')")
        b.wait(1.0)
        b.js("T.to('#dLine')")
        b.wait(0.5)
        b.js(f"T.type('#dLine', {json.dumps(LINE)}, 15)")
        b.wait(len(LINE) * 0.015 + 0.4)
        b.js("document.querySelector('#director .look[data-look=road]').scrollIntoView({behavior: 'smooth', inline: 'center', block: 'nearest'}); 1")
        b.wait(0.8)
        self.click("#director .look[data-look=road]", 0.6)
        b.wait(0.4)
        self.click('#dLen [data-v="10"]', 0.55)
        b.wait(0.5)
        b.js("T.hide()")                        # the caption sits over Roll camera
        b.wait(0.3)
        b.js("T.to('#dRoll')")
        b.wait(0.9)
        b.js("T.ring()")
        b.wait(0.8)
        return b.stop()

    # 3 ─ it rolls, each step in plain words
    def rolling(self):
        b = self.b
        self.front()
        b.js("""autopilot.run = (o, ui) => new Promise(() => {
            ui.start(o.line); ui.title(B.title || 'The Last Watch');
            [['plan', 'now', 'Writing the shot list', 0], ['plan', 'ok', '4 shots, 10 seconds', 900], ['keys', 'now', 'A keyframe for every new setup, all at once', 950],
             ['keys', 'ok', '', 2300], ['clips', 'now', 'Each shot animated from its keyframe', 2350], ['clips', 'ok', '', 4300],
             ['check', 'now', 'Reading every cut, in Rust', 4350], ['check', 'ok', 'Light and colour carry across the cuts.', 5100],
             ['cut', 'now', '', 5150], ['cut', 'ok', '', 5700], ['grade', 'now', 'Every frame through Portra 400 and Kodachrome, in Rust', 5750]]
              .forEach(([k, st, n, ms]) => setTimeout(() => ui.step(k, st, n), ms));
            setTimeout(() => { director.state.t0 = Date.now() - 98000; }, 60);
          }); 1""")
        b.js(f"$('#dLine').value = {json.dumps(LINE)}; $('#dRoll').click(); 1")
        b.wait(0.8)
        b.js("T.at(1200, 640, true)")
        b.record()
        b.wait(0.2)
        b.js("T.cap('It rolls', 'Gobo writes the shots, paints the first frames, films them, checks every cut, edits and grades, and tells you each step.', 'bc')")
        b.wait(4.6)
        b.js("T.hide()")                        # and then the frames, uncovered
        b.wait(2.0)
        return b.stop()

    # 5 ─ the film plays, with Share
    def made(self):
        b = self.b
        self.front()
        b.js(f"""autopilot.run = (o, ui) => {{ ui.start(o.line); director.state.t0 = Date.now() - 134000;
            ui.done({{ final: {json.dumps(self.graded)}, film: {json.dumps(self.cut)}, title: B.title || 'The Last Watch' }});
            return Promise.resolve({{}}); }}; 1""")
        b.js(f"$('#dLine').value = {json.dumps(LINE)}; $('#dRoll').click(); 1")
        b.wait(1.4)
        b.js("T.at(1000, 760, true)")
        b.record()
        b.wait(0.2)
        b.js("T.cap('Share it', 'It plays right there. Share it with a link, download it, or open it in the studio and change any shot.', 'tc')")
        b.wait(1.4)
        b.js("T.to('#dShare')")
        b.wait(0.9)
        b.js("T.ring()")
        b.wait(1.2)
        b.js("T.to('#dEdit')")
        b.wait(1.8)
        return b.stop()

    # 2 ─ Autopilot: the line goes in (the tour before Make a film: --front studio)
    def autopilot(self):
        b = self.b
        self.studio()
        b.js("autopilot.openDialog(); 1")
        b.wait(0.6)
        b.js("""const m = document.getElementById('apModal'), s = (q, v) => { const e = m.querySelector(q);
          const o = [...e.options].find(o => o.value === v || o.textContent.includes(v)); if (o) { e.value = o.value; e.dispatchEvent(new Event('change')); } };
          s('#apLen', '10'); s('#apClip', 'Wan 3.0'); s('#apKey', 'FLUX 3'); 1""")
        b.js("T.at(1000, 640, true)")
        b.record()
        b.wait(0.2)
        b.js("T.zoom('#apModal .box', 1.3, 1100, 720, 470)")
        b.js("T.cap('Autopilot', 'Type one line. Autopilot writes the shots, draws a keyframe for each, animates them and cuts the film.', 'tc')")
        b.wait(1.1)
        b.js("T.to('#apLine')")
        b.wait(0.5)
        b.js(f"T.type('#apLine', {json.dumps(LINE)}, 15)")
        b.wait(len(LINE) * 0.015 + 0.5)
        b.js("T.to('#apGo')")
        b.wait(0.9)
        b.js("T.ring()")
        b.wait(0.8)
        return b.stop()

    # 3 ─ the Board it made
    def board_scene(self):
        b = self.b
        self.studio()
        b.js("$('#viewSeg [data-v=board]').click(); 1")
        b.wait(1.5)
        b.js("T.at(1100, 560, true)")
        b.record()
        b.wait(0.2)
        b.js("T.zoom('#board .bcard:nth-child(2)', 1.16, 6000, 640, 330)")
        b.js("T.cap('Direct it yourself', 'Every film lands on the Board, a card a shot: keyframe, animate, continue, trim and cut. Change any of it.', 'bc')"
             if self.front_door else "T.cap('The Board', 'Every shot lands on a card: keyframe, animate, continue into the next, trim and cut. Change any of it.', 'bc')")
        b.wait(1.2)
        b.js("T.to('#board .bcard:nth-child(2) img')")
        b.wait(1.6)
        b.js("T.to('#board .bcard:nth-child(3) img')")
        b.wait(1.4)
        b.js("T.to('#bWatch')")
        b.wait(1.8)
        return b.stop()

    # 5 ─ direction, as a DP would give it
    def direction(self):
        b = self.b
        self.studio()
        b.js("""$('#viewSeg [data-v=shot]') && $('#viewSeg [data-v=shot]').click(); $('#scene').value = ''; $('#scene').dispatchEvent(new Event('input', {bubbles: true}));
          $('#shotlist .row[data-g=camera]').scrollIntoView({block: 'start'}); 1""")
        b.wait(0.8)
        b.js("T.at(700, 420, true)")
        b.record()
        b.wait(0.2)
        b.js("T.cap('Direction', 'Camera, lens, light, film stock and 28 moves become the words a DP would use. Rust writes the prompt as you choose.', 'tc')")
        self.click("#shotlist .row[data-g=camera]", 0.55)
        b.wait(0.5)
        self.click('#pop [data-p=focal][data-o="85"]', 0.5)
        b.wait(0.35)
        b.js("closePop(); 1")
        self.click("#shotlist .row[data-g=move]", 0.5)
        b.wait(0.5)
        self.click("#pop .tile[data-o=vertigo]", 0.55)
        b.wait(0.35)
        b.js("closePop(); 1")
        self.click("#shotlist .row[data-g=film]", 0.5)
        b.wait(0.6)
        self.click("#pop .tile[data-o=t500]", 0.55)
        b.wait(0.35)
        b.js("closePop(); T.hide(); 1")
        b.js("T.zoom('#segs', 1.75, 900, 900, 400)")
        b.wait(0.3)
        b.js("T.to('#segs')")
        b.wait(2.0)
        return b.stop()

    # 6 ─ the models
    def models(self):
        b = self.b
        self.studio()
        b.js("T.at(330, 330, true)")
        b.record()
        b.wait(0.2)
        self.click("#modelBtn", 0.6)
        b.wait(0.4)
        b.js("T.cap('134 models, one key', 'LTX, Wan, Seedance, Veo, Kling, MiniMax, FLUX and more on Comfy Router. Each shot is mapped onto each model’s own inputs.', 'bc')")
        b.js("""const first = document.querySelector('#modelSheet .mitem'); let s = first; while (s && s.scrollHeight <= s.clientHeight + 4) s = s.parentElement;
          window._sc = s; const t0 = performance.now(), go = () => { const k = Math.min(1, (performance.now() - t0) / 3600);
          s.scrollTop = (s.scrollHeight - s.clientHeight) * 0.45 * (k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2); if (k < 1) requestAnimationFrame(go); };
          requestAnimationFrame(go); 1""")
        b.js("T.to('#modelSheet .mitem')")
        b.wait(4.2)
        return b.stop()

    def lab(self, tab):
        self.studio(f"?lab={tab}")
        self.b.wait(1.2)
        self.b.js(OVERLAY)
        self.b.js("T.at(900, 420, true)")

    # 7 ─ steal the look
    def steal(self):
        b = self.b
        self.lab("look")
        b.record()
        b.wait(0.2)
        b.js("T.zoom('#lab', 1.12, 900)")
        b.js("T.cap('Steal the look', 'Drop any frame. Rust reads its tone, colour and grain in about 9 ms, names the film stock and grade, and hands you the .cube.', 'bl')")
        self.click("#lookTake", 0.7)
        b.wait(0.9)
        b.js("T.zoom('#lookOut', 1.32, 1000, 1080, 400); T.to('#lookOut')")
        b.wait(2.6)
        return b.stop()

    # 8 ─ grade like film
    def grade(self):
        b = self.b
        self.lab("grade")
        b.record()
        b.wait(0.2)
        b.js("T.zoom('#lab', 1.2, 900)")
        b.js("T.cap('Grade like film', 'Portra, Tungsten 500T, bleach bypass, VHS: live on the clip, with halation and moving grain. The look exports as a .cube.', 'bl')")
        b.js("T.to('#gFilm')")
        b.wait(0.6)
        for film in ("t500", "bleach", "vhs", "bw400", "portra"):
            b.js(f"const e = document.querySelector('#gFilm'); e.value = {json.dumps(film)}; e.dispatchEvent(new Event('change')); T.ring(); 1")
            b.wait(0.8)
        b.js("T.to('#gWipe')")
        b.wait(0.5)
        b.js("""const w = document.querySelector('#gWipe'), t0 = performance.now(), go = () => { const k = Math.min(1, (performance.now() - t0) / 1800);
          w.value = Math.round(Math.sin(k * Math.PI) * 70); w.dispatchEvent(new Event('input', {bubbles: true}));
          const r = w.getBoundingClientRect(); T.at(r.left + r.width * w.value / 100, r.top + r.height / 2, true); if (k < 1) requestAnimationFrame(go); };
          requestAnimationFrame(go); 1""")
        b.wait(2.1)
        return b.stop()

    # 9 ─ draw the move
    def draw(self):
        b = self.b
        self.lab("draw")
        b.record()
        b.wait(0.2)
        b.js("T.zoom('#drawCv', 1.2, 900, 1060, 360)")
        b.js("T.cap('Draw the move', 'A loop orbits. A line toward the subject pushes in. A zigzag goes handheld. The move and the action write themselves.', 'bl')")
        b.wait(1.1)
        r = b.js("const r = document.querySelector('#drawCv').getBoundingClientRect(); [r.left, r.top, r.width, r.height]")
        cx, cy, rad = r[0] + r[2] * 0.5, r[1] + r[3] * 0.52, min(r[2], r[3]) * 0.32
        pts = [(cx + rad * 1.25 * math.cos(a), cy + rad * math.sin(a)) for a in [i / 32 * 2 * math.pi * 1.05 for i in range(35)]]
        b.js(f"T.at({pts[0][0]}, {pts[0][1]})")
        b.wait(0.8)
        b.js("T.follow = true; 1")
        b.mouse("mousePressed", *pts[0])
        for x, y in pts[1:]:
            b.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, button="left", buttons=1)
            b.wait(0.05)
        b.mouse("mouseReleased", *pts[-1])
        b.js("T.follow = false; 1")
        b.wait(0.7)
        b.js("T.zoom('#drawRead', 1.3, 900, 1080, 470); T.to('#drawRead')")
        b.wait(2.4)
        return b.stop()


# ── slides ───────────────────────────────────────────────────────────────────
SLIDE_CSS = """
* { box-sizing: border-box; } html, body { margin: 0; width: 1920px; height: 1080px; overflow: hidden; background: #0b0b0d; }
body { color: #ecebe8; font-family: Inter, system-ui, sans-serif; }
.up { opacity: 0; transform: translateY(26px); animation: up .8s cubic-bezier(.2,.7,.2,1) forwards; }
@keyframes up { to { opacity: 1; transform: none; } }
em { font-style: normal; background: linear-gradient(90deg, #f2c14e, #f08a4b 60%, #e05a6a); -webkit-background-clip: text; background-clip: text; color: transparent; }
.kick { color: #f2c14e; font-weight: 800; font-size: 22px; letter-spacing: .18em; text-transform: uppercase; }
"""


def slide(work, name, body, extra_css=""):
    path = os.path.join(work, f"{name}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<!doctype html><html><head><meta charset='utf-8'>{FONT}<style>{SLIDE_CSS}{extra_css}</style></head><body>{body}</body></html>")
    return "file://" + path


def title_slide(t: Tour):
    return slide(t.work, "title", f"""
<video src="{t.url(t.graded)}" autoplay muted loop playsinline style="position:absolute;inset:-40px;width:2000px;height:1160px;object-fit:cover;filter:brightness(.42) saturate(1.05) blur(3px)"></video>
<div style="position:absolute;inset:0;background:radial-gradient(ellipse at center, rgba(11,11,13,.2), rgba(11,11,13,.85) 75%)"></div>
<div style="position:absolute;inset:0;display:grid;place-items:center;text-align:center">
  <div>
    <div class="up" style="display:flex;align-items:center;justify-content:center;gap:22px;animation-delay:.2s">
      <div style="width:96px;height:96px">{LOGO}</div><div style="font-size:92px;font-weight:800;letter-spacing:-.03em">Gobo</div></div>
    <div class="up" style="font-size:76px;font-weight:800;letter-spacing:-.035em;line-height:1.05;margin-top:34px;animation-delay:.6s">{"Say the film. <em>Gobo shoots it.</em>" if t.front_door else "Direct AI video like a <em>cinematographer.</em>"}</div>
    <div class="up" style="font-size:34px;color:#c9c7cf;margin-top:26px;animation-delay:1.1s">An open-source AI film studio on Comfy Router</div>
  </div>
</div>""")


def film_slide(t: Tour):
    steps = ["1 line typed", f"{len(t.clips) or 4} shots written", "FLUX 3 keyframes", "Wan 3.0 clips, each from its keyframe",
             "Cut, then graded in Rust", "About 2 minutes · 80¢ of Comfy credit"]
    lis = "".join(f'<li class="up" style="animation-delay:{0.9 + i * 0.35:.2f}s">{s}</li>' for i, s in enumerate(steps))
    return slide(t.work, "film", f"""
<div style="position:absolute;left:80px;top:150px;width:1240px;border-radius:18px;overflow:hidden;border:1px solid rgba(255,255,255,.14);box-shadow:0 30px 90px rgba(0,0,0,.7)">
  <video src="{t.url(t.graded)}" autoplay muted playsinline style="display:block;width:1240px;height:698px;object-fit:cover;background:#000"></video></div>
<div style="position:absolute;left:1390px;top:170px;width:460px">
  <div class="kick up" style="animation-delay:.2s">{"One line" if t.front_door else "Autopilot"} · start to finish</div>
  <div class="up" style="font-size:66px;font-weight:800;letter-spacing:-.03em;margin:14px 0 26px;animation-delay:.45s">The Last Watch</div>
  <ul style="list-style:none;padding:0;margin:0;font-size:29px;line-height:1.35;color:#d6d4cf">{lis}</ul>
</div>""", "li { padding: 10px 0 10px 30px; position: relative; border-top: 1px solid rgba(255,255,255,.09); } li::before { content: ''; position: absolute; left: 4px; top: 25px; width: 10px; height: 10px; border-radius: 50%; background: #f2c14e; }")


def wipe_slide(t: Tour):
    return slide(t.work, "wipe", f"""
<div style="position:absolute;left:160px;top:96px;width:1600px;height:900px;border-radius:18px;overflow:hidden;border:1px solid rgba(255,255,255,.14);box-shadow:0 30px 90px rgba(0,0,0,.7);background:#000">
  <video id="a" src="{t.url(t.cut)}" autoplay muted playsinline style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>
  <video id="g" src="{t.url(t.graded)}" autoplay muted playsinline style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>
  <div id="line" style="position:absolute;top:0;bottom:0;width:4px;background:#f2c14e;box-shadow:0 0 18px rgba(242,193,78,.8)"></div>
  <div style="position:absolute;left:28px;top:24px" class="tag">As shot</div>
  <div style="position:absolute;right:28px;top:24px" class="tag gold">Graded in Rust · Portra 400 + Kodachrome</div>
</div>
<script>
const g = document.getElementById('g'), line = document.getElementById('line'), t0 = performance.now();
(function go() {{ const k = ((performance.now() - t0) / 1000), x = 0.5 + 0.38 * Math.sin(k * 1.25 - 1.2);
  g.style.clipPath = `inset(0 0 0 ${{x * 100}}%)`; line.style.left = `calc(${{x * 100}}% - 2px)`; requestAnimationFrame(go); }})();
</script>""", ".tag { background: rgba(0,0,0,.7); border: 1px solid rgba(255,255,255,.18); border-radius: 12px; padding: 12px 20px; font-size: 32px; font-weight: 700; } .tag.gold { color: #f2c14e; }")


def comfy_slide(t: Tour):
    nodes, wires = [], []
    cols = [(90, "Flux3ImageNode", "FLUX 3 · keyframe", "#6b4fd8"), (700, "Wan3ImageToVideoApi", "Wan 3.0 · image to video", "#d8644f"),
            (1310, "SaveVideo", "Save video", "#3f7f5a")]
    thumbs = [t.url(k) for k in t.keys] + [""] * 4
    posters = [t.url(c).replace(".mp4", "-poster.jpg") for c in t.clips] + [""] * 4
    for i in range(4):
        y = 130 + i * 190
        for j, (x, cls, label, colour) in enumerate(cols):
            img = thumbs[i] if j == 0 else posters[i] if j == 1 else ""
            pic = f'<img src="{img}" style="width:118px;height:78px;object-fit:cover;border-radius:6px;float:right;margin-left:12px">' if img else ""
            nodes.append(f'<div class="node up" style="left:{x}px;top:{y}px;animation-delay:{0.15 + j * 0.35 + i * 0.08:.2f}s">'
                         f'<div class="hd" style="background:{colour}">{"%03d" % (i + 1)}{"AB"[min(j, 1)]} · {label}</div>'
                         f'<div class="bd">{pic}<code>{cls}</code><br><span>{["prompt · seed · 16:9", "first_frame ← keyframe", "video ← clip"][j]}</span></div></div>')
            if j:
                x0, x1, yy = cols[j - 1][0] + 520, x, y + 76
                wires.append(f'<path d="M{x0},{yy} C{x0 + 40},{yy} {x1 - 40},{yy} {x1},{yy}" style="animation-delay:{0.5 + j * 0.35 + i * 0.08:.2f}s"/>')
    return slide(t.work, "comfy", f"""
<div style="position:absolute;inset:0;background-image:radial-gradient(rgba(255,255,255,.07) 1px, transparent 1px);background-size:24px 24px"></div>
<div class="kick up" style="position:absolute;left:90px;top:52px;font-size:26px">workflows/the-last-watch · 16 nodes · ComfyUI API format</div>
<svg width="1920" height="1080" style="position:absolute;inset:0">{''.join(wires)}</svg>
{''.join(nodes)}""", """
.node { position: absolute; width: 520px; background: #2b2b30; border: 1px solid #45454d; border-radius: 10px; box-shadow: 0 14px 40px rgba(0,0,0,.5); overflow: hidden; }
.node .hd { padding: 9px 16px; font-size: 25px; font-weight: 700; color: #fff; }
.node .bd { padding: 12px 16px; font-size: 22px; color: #c9c7cf; min-height: 102px; }
.node code { font: 600 23px ui-monospace, Menlo, monospace; color: #ecebe8; } .node span { color: #9d9ba4; font-size: 21px; }
svg path { fill: none; stroke: #f2c14e; stroke-width: 4; stroke-dasharray: 700; stroke-dashoffset: 700; animation: draw 1s ease forwards; opacity: .85; }
@keyframes draw { to { stroke-dashoffset: 0; } }""")


def end_slide(t: Tour, samples):
    tiles = "".join(f'<img src="{s}">' for s in (samples * 4)[:56])
    repo = t.source.replace("https://", "")
    return slide(t.work, "end", f"""
<div class="wall">{tiles}</div>
<div style="position:absolute;inset:0;background:radial-gradient(ellipse at center, rgba(11,11,13,.55) 0%, rgba(11,11,13,.92) 60%, #0b0b0d 100%)"></div>
<div style="position:absolute;inset:0;display:grid;place-items:center;text-align:center">
  <div>
    <div class="up" style="display:flex;align-items:center;justify-content:center;gap:18px;animation-delay:.1s">
      <div style="width:96px;height:96px">{LOGO}</div><div style="font-size:88px;font-weight:800;letter-spacing:-.03em">Gobo</div></div>
    <div class="up" style="font-size:88px;font-weight:800;letter-spacing:-.035em;line-height:1.08;margin-top:30px;animation-delay:.4s">Bring your own Comfy key.<br><em>Pay only for what you run.</em></div>
    <div class="up" style="display:flex;gap:14px;justify-content:center;margin-top:40px;animation-delay:.9s">
      <span class="chip gold">try.gobo.picturenetcorp.com</span><span class="chip">{repo}</span><span class="chip">Open source · MIT or Apache 2.0</span></div>
    <div class="up" style="font-size:31px;color:#b3b1b9;margin-top:40px;animation-delay:1.3s">Built for Comfy’s Open Source Higgsfield challenge · Inspired by Higgsfield’s Cinema Studio</div>
    <div class="up" style="font-size:31px;color:#b3b1b9;margin-top:12px;animation-delay:1.5s">Made by PictureNet Corp</div>
  </div>
</div>""", """
.wall { position: absolute; inset: -12%; display: grid; grid-template-columns: repeat(8, 1fr); gap: 12px; transform: rotate(-8deg); animation: drift 14s linear forwards; }
.wall img { width: 100%; aspect-ratio: 4/3; object-fit: cover; border-radius: 10px; opacity: .6; }
@keyframes drift { to { transform: rotate(-8deg) translate3d(-3%, -6%, 0); } }
.chip { border: 1px solid rgba(255,255,255,.2); background: rgba(19,19,22,.85); border-radius: 99px; padding: 14px 28px; font-size: 34px; font-weight: 600; }
.chip.gold { border-color: rgba(242,193,78,.6); color: #f2c14e; }""")


def play_slide(t: Tour, url, seconds, cap=None):
    """A slide from its first frame: loaded once to warm its fonts and media,
    then again from a blank dark page with the recording running."""
    b = t.b
    b.view(ui=False)
    b.go(url, 1.5)
    b.go("data:text/html,<body style='margin:0;background:%230b0b0d'></body>", 0.3)
    b.record()
    b.call("Page.navigate", url=url)
    b.wait(0.5)
    b.js(OVERLAY)
    if cap:
        b.js(f"T.cap({json.dumps(cap[0])}, {json.dumps(cap[1])}, {json.dumps(cap[2])})")
    b.wait(seconds - 0.5)
    return b.stop()


# ── film ─────────────────────────────────────────────────────────────────────
def encode(frames, end, path):
    """Screencast frames (each shown until the next arrives) to a constant-rate clip."""
    if len(frames) < 2:
        raise RuntimeError(f"{os.path.basename(path)}: only {len(frames)} frame(s) recorded")
    d = path[:-4] + "-frames"
    os.makedirs(d, exist_ok=True)
    lines = []
    for i, (ts, data) in enumerate(frames):
        f = os.path.join(d, f"{i:05d}.jpg")
        with open(f, "wb") as fh:
            fh.write(base64.b64decode(data))
        nxt = frames[i + 1][0] if i + 1 < len(frames) else max(end, ts + 0.04)
        lines += [f"file '{f}'", f"duration {max(0.001, nxt - ts):.4f}"]
    lines.append(f"file '{os.path.join(d, f'{len(frames) - 1:05d}.jpg')}'")
    with open(path + ".txt", "w") as fh:
        fh.write("\n".join(lines) + "\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", path + ".txt",
                    "-vf", f"scale={W}:{H}:flags=lanczos,fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium",
                    "-crf", "16", path], check=True)
    shutil.rmtree(d, ignore_errors=True)
    os.remove(path + ".txt")
    return path


def seconds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def join(clips, out):
    """The scenes, dissolving one into the next."""
    args, chain, offset, last = [], [], 0.0, "0:v"
    for c in clips:
        args += ["-i", c]
    for i in range(1, len(clips)):
        offset += seconds(clips[i - 1]) - FADE
        tag = f"x{i}"
        chain.append(f"[{last}][{i}:v]xfade=transition=fade:duration={FADE}:offset={offset:.3f}[{tag}]")
        last = tag
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # 720p: the sign-in page shows it about 800 wide, and 1080p at the same
    # sharpness is half as big again (about 7 MB against 10.5 for 80 s)
    chain.append(f"[{last}]scale=1280:720:flags=lanczos[out]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, "-filter_complex", ";".join(chain), "-map", "[out]",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", out],
                   check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2.6", "-i", out, "-frames:v", "1", "-q:v", "3",
                    out[:-4] + ".jpg"], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8020")
    ap.add_argument("--reel", required=True, help="a reel Autopilot made, with a graded film")
    ap.add_argument("--source", default="https://github.com/RickBL99/gobo", help="where the code is, for the last card")
    ap.add_argument("--only", type=int, help="record just this scene, into the work folder")
    ap.add_argument("--front", choices=("director", "studio"), default="director",
                    help="the tour of Make a film (the default front door) or, as before it, of the studio")
    ap.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "gobo-tour"))
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    b = Browser()
    try:
        t = Tour(b, a.base, a.reel, a.work, a.source, front_door=a.front == "director")
        samples = [f"{t.base}/static/samples/{os.path.basename(p)}" for p in sorted(glob.glob(os.path.join(ROOT, "studio/static/samples/*.webp")))]
        opening = ([t.make_film, t.rolling, lambda: play_slide(t, film_slide(t), 7.5), t.made, t.board_scene] if t.front_door
                   else [t.autopilot, t.board_scene, lambda: play_slide(t, film_slide(t), 7.5)])
        scenes = [
            lambda: play_slide(t, title_slide(t), 5.0),
            *opening,
            t.direction,
            t.models,
            t.steal,
            t.grade,
            t.draw,
            lambda: play_slide(t, wipe_slide(t), 5.5, ("Grade the take", "Every frame through the same Rust, on the server. The film matches the preview byte for byte.", "bc")),
            lambda: play_slide(t, comfy_slide(t), 5.5, ("Open in ComfyUI", "Any take, or the whole film, downloads as a ComfyUI workflow that runs on the same nodes.", "bc")),
            lambda: play_slide(t, end_slide(t, samples), 6.5),
        ]
        clips = []
        for i, scene in enumerate(scenes, 1):
            if a.only and i != a.only:
                continue
            frames, end = scene()
            clip = encode(frames, end, os.path.join(a.work, f"scene-{i:02d}.mp4"))
            print(f"scene {i:2d}: {len(frames):4d} frames, {seconds(clip):5.2f}s  {clip}")
            clips.append(clip)
    finally:
        b.close()
    if a.only:
        return 0
    join(clips, OUT)
    mb = os.path.getsize(OUT) / 1e6
    print(f"{OUT}: {seconds(OUT):.1f}s, {mb:.1f} MB, poster beside it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
