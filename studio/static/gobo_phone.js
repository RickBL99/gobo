// Gobo on a phone. A phone (a small screen you touch) gets body.phone:
//
//   the studio    one row of header buttons that scrolls, and a Grade button
//                 on every finished take. "Desktop layout" at the end of the
//                 row turns it off; a small screen that isn't a phone gets
//                 "Phone layout" to turn it on.
//   the grader    the Colour page rebuilt for a thumb, on the same look, the
//                 same gobo.wasm and the same Grade the take: the picture full
//                 width (hold it for the original, drag across it to move the
//                 control you're on, tap to play), and one tool at a time
//                 under it: Looks (this shot in every stock and grade), Light,
//                 Colour, Wheels, Curves, Film, and Match, which grades the
//                 take to a photo from the camera roll.
(function () {
  "use strict";
  const ST = () => window.STUDIO, GG = () => window.goboGrade, GC = () => window.goboColour;
  const q = (sel, el) => (el || document).querySelector(sel);
  const qa = (sel, el) => Array.from((el || document).querySelectorAll(sel));
  const h = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const say = (m, bad) => (ST() && ST().toast ? ST().toast(m, bad) : console.log(m));
  if (typeof CAT === "undefined" || !window.goboReady || !GG() || !GC() || !GC().tools) return;
  const T = GC().tools;

  // ── phone or not ────────────────────────────────────────────────────────
  // a phone upright or on its side; an iPad gets the whole studio
  const touchSmall = matchMedia("(pointer: coarse) and (max-width: 760px), (pointer: coarse) and (max-height: 500px)");
  const narrow = matchMedia("(max-width: 760px)");
  const KEY = "gobo.layout";
  let held = null;                                         // the choice, when the browser keeps nothing
  const pref = () => { try { return localStorage.getItem(KEY) || ""; } catch (e) { return held || ""; } };
  const setPref = v => { held = v; try { if (v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY); } catch (e) { /* this visit only */ } };
  const isPhone = () => pref() === "phone" || (pref() !== "desktop" && touchSmall.matches);

  const style = document.createElement("style");
  style.textContent = `
body.phone header { flex-wrap: nowrap; overflow-x: auto; gap: 8px; padding: 8px 12px; scrollbar-width: none; }
body.phone header::-webkit-scrollbar { display: none; }
body.phone header > * { flex: none; }
body.phone header .grow, body.phone header .back, body.phone #inspBtn { display: none; }
body.phone header h1 { font-size: .95rem; }
body.phone header .reelname { width: 8.5em; }
body.pgopen { overflow: hidden; }
#pg { position: fixed; inset: 0; z-index: 75; background: #000; color: var(--text); display: flex; flex-direction: column;
  height: 100dvh; padding-top: env(safe-area-inset-top); -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; }
#pg.hidden { display: none; }
#pg button { touch-action: manipulation; font-family: inherit; }
#pg .pghead { display: flex; align-items: center; gap: 8px; padding: 8px 10px; }
#pg .pghead .what { flex: 1; min-width: 0; font-size: .86rem; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#pg .pgic { min-width: 40px; height: 38px; border-radius: 10px; border: 1px solid var(--line); background: var(--panel2); color: var(--text); font-size: .86rem; padding: 0 12px; }
#pg .pgic.on { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
#pg .pgview { position: relative; flex: none; height: min(44vh, 66vw); display: flex; align-items: center; justify-content: center; background: #000; touch-action: none; overflow: hidden; }
#pg .pgview canvas.pic { max-width: 100%; max-height: 100%; display: block; }
#pg .pgscope { position: absolute; right: 8px; bottom: 8px; width: 44%; max-width: 220px; background: rgba(0,0,0,.66); border-radius: 8px; border: 1px solid rgba(255,255,255,.14); pointer-events: none; }
#pg .pgtag { position: absolute; top: 10px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,.72); padding: 6px 14px; border-radius: 99px;
  font-size: .84rem; font-variant-numeric: tabular-nums; pointer-events: none; white-space: nowrap; }
#pg .pgmsg { color: var(--muted); padding: 24px; text-align: center; font-size: .9rem; }
#pg .pgbar { display: flex; align-items: center; gap: 10px; padding: 6px 12px; min-height: 30px; font-size: .74rem; color: var(--dim); }
#pg .pgbar input { flex: 1; }
#pg .pgtool { flex: 1; min-height: 0; overflow-y: auto; padding: 6px 14px 12px; background: #0b0b0d; touch-action: pan-y; }
#pg .pgtabs { display: flex; gap: 3px; overflow-x: auto; padding: 8px 8px; background: #0b0b0d; border-top: 1px solid var(--line); scrollbar-width: none; flex: none; }
#pg .pgtabs::-webkit-scrollbar { display: none; }
#pg .pgtabs button { flex: 1 0 auto; padding: 9px 5px; border-radius: 99px; border: 1px solid transparent; background: transparent; color: var(--muted); font-size: .8rem; font-weight: 600; }
#pg .pgtabs button.on { background: var(--text); color: #000; border-color: var(--text); }
#pg .pgfoot { display: flex; gap: 8px; padding: 8px 12px calc(8px + env(safe-area-inset-bottom)); background: #0b0b0d; flex: none; }
#pg .pgfoot .btn { flex: 1; padding: 13px; font-size: .96rem; }
#pg .pgsl { padding: 8px 10px 2px; border-radius: 12px; border: 1px solid transparent; margin: 2px 0; }
#pg .pgsl.on { border-color: var(--line2); background: var(--panel2); }
#pg .pgsl .top { display: flex; align-items: center; gap: 8px; font-size: .86rem; min-height: 26px; }
#pg .pgsl output { margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums; }
#pg .pgsl .rs { border: 0; background: transparent; color: var(--accent); font-size: 1rem; padding: 0 4px; }
#pg input[type=range] { width: 100%; height: 34px; margin: 0; }
#pg .pgseg { display: flex; gap: 6px; margin: 6px 0 10px; }
#pg .pgseg button { flex: 1; padding: 9px 4px; border-radius: 10px; border: 1px solid var(--line); background: transparent; color: var(--muted); font-size: .82rem; font-weight: 600; }
#pg .pgseg button.on { background: var(--panel2); color: var(--text); border-color: var(--line2); }
#pg .pgthumbs { display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: 10px; }
#pg .pgthumb { background: transparent; border: 0; padding: 0; color: var(--muted); font-size: .74rem; text-align: center; }
#pg .pgthumb canvas { width: 100%; aspect-ratio: 16 / 9; object-fit: cover; display: block; border-radius: 8px; background: #111; border: 2px solid transparent; margin-bottom: 4px; }
#pg .pgthumb.on { color: var(--text); font-weight: 600; }
#pg .pgthumb.on canvas { border-color: var(--accent); }
#pg .pglist { display: flex; flex-direction: column; gap: 6px; }
#pg .pgrow { text-align: left; padding: 12px; border-radius: 10px; border: 1px solid var(--line); background: transparent; color: var(--text); font-size: .88rem; }
#pg .pgrow.on { border-color: var(--accent); }
#pg .pgwheel canvas { width: min(240px, 62vw); height: auto; aspect-ratio: 1; display: block; margin: 4px auto 2px; touch-action: none; }
#pg .pgcurve canvas { width: min(300px, 78vw); height: auto; aspect-ratio: 1; display: block; margin: 4px auto; touch-action: none; background: #0d0d10; border-radius: 10px; border: 1px solid var(--line); }
#pg .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 10px 0; }
#pg .pgp { font-size: .86rem; color: var(--muted); line-height: 1.45; margin: 6px 0 10px; }
#pg .pgh { font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: var(--dim); margin: 18px 0 4px; }
#pg .pgmatched { display: flex; gap: 10px; align-items: center; margin: 12px 0; font-size: .84rem; color: var(--muted); }
#pg .pgmatched img { width: 84px; height: 56px; object-fit: cover; border-radius: 8px; flex: none; }
#pg .pgmenu { position: absolute; top: calc(52px + env(safe-area-inset-top)); right: 10px; background: var(--panel); border: 1px solid var(--line2); border-radius: 12px;
  padding: 6px; display: flex; flex-direction: column; min-width: 230px; box-shadow: 0 12px 40px #000a; z-index: 2; }
#pg .pgmenu.hidden { display: none; }
#pg .pgmenu button { text-align: left; padding: 12px; background: transparent; border: 0; color: var(--text); font-size: .92rem; border-radius: 8px; }
@media (max-height: 500px) {
  #pg { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(260px, 1fr); grid-template-rows: auto minmax(0, 1fr) auto auto;
    grid-template-areas: "head head" "view tool" "bar tabs" "bar foot"; }
  #pg .pghead { grid-area: head; } #pg .pgview { grid-area: view; height: auto; } #pg .pgbar { grid-area: bar; }
  #pg .pgtool { grid-area: tool; } #pg .pgtabs { grid-area: tabs; } #pg .pgfoot { grid-area: foot; }
}`;
  document.head.appendChild(style);

  const TABS = [["looks", "Looks"], ["light", "Light"], ["colour", "Colour"], ["wheels", "Wheels"], ["curves", "Curves"], ["film", "Film"], ["match", "Match"]];
  const GROUPS = { light: ["exposure", "contrast", "pivot", "fade", "white"], colour: ["temp", "tint", "sat"], film: ["grain", "grain_size", "halation", "vignette", "amount"] };
  const CTRL = Object.fromEntries(T.CONTROLS.map(([k, label, mn, mx, st]) => [k, { k, label, mn, mx, st }]));
  const SIGNED = new Set(["exposure", "temp", "tint"]);
  const fmt = (k, v) => (SIGNED.has(k) && v > 0 ? "+" : "") + Number(v).toFixed(k === "fade" || k === "white" ? 3 : 2);
  const WHERE = { lift: "shadows", gamma: "mid-tones", gain: "highlights", offset: "whole picture" };

  const el = document.createElement("section");
  el.id = "pg"; el.className = "hidden"; el.setAttribute("role", "dialog"); el.setAttribute("aria-label", "Grade");
  el.innerHTML = `
  <div class="pghead"><button class="pgic" id="pgX" aria-label="Close">✕</button><span class="what" id="pgWhat"></span>
    <button class="pgic" id="pgScopeBtn" aria-pressed="false" title="A waveform on the picture">Scope</button>
    <button class="pgic" id="pgMoreBtn" aria-label="More">⋯</button></div>
  <div class="pgview" id="pgView"><canvas class="pic" id="pgCv"></canvas><canvas class="pgscope hidden" id="pgScope" width="240" height="120"></canvas>
    <span class="pgtag hidden" id="pgTag"></span><div class="pgmsg hidden" id="pgMsg"></div></div>
  <div class="pgbar"><button class="pgic hidden" id="pgPlay" aria-label="Play or pause">❚❚</button>
    <input type="range" class="hidden" id="pgSeek" min="0" max="1000" value="0" aria-label="Scrub"><span id="pgHint">Hold the picture for the original · drag across it to adjust</span></div>
  <div class="pgtool" id="pgTool"></div>
  <div class="pgtabs" id="pgTabs" role="tablist">${TABS.map(([k, l]) => `<button data-tab="${k}" role="tab">${l}</button>`).join("")}</div>
  <div class="pgfoot"><button class="btn primary" id="pgBake" title="Every frame through this grade, on the server, as a new take beside the original">Grade the take</button></div>
  <div class="pgmenu hidden" id="pgMenu">
    <button data-m="still">Save a graded still</button><button data-m="cube">Download the .cube</button>
    <button data-m="reset">Start the grade over</button><button data-m="desk">The full Colour page</button><button data-m="layout">Desktop layout</button></div>`;
  document.body.appendChild(el);
  const pane = q("#pgTool", el), cv = q("#pgCv", el), view = q("#pgView", el);

  const L = () => GG().look;
  let tab = "looks", looksKind = "film", wheelKey = "lift", curveCh = "y", curvePt = -1;
  let active = { light: "exposure", colour: "temp", film: "grain" }, lastKey = "exposure", base = null;
  let src = null, vid = null, raf = 0, busy = false, again = false, frameNo = 0, orig = false, matched = null;
  let scopeOn = (() => { try { return localStorage.getItem("gobo.phone.scope") === "1"; } catch (e) { return false; } })();

  const work = document.createElement("canvas"), wctx = work.getContext("2d", { willReadFrequently: true });
  function grab(s, maxW) {
    const w0 = s.naturalWidth || s.videoWidth || s.width, h0 = s.naturalHeight || s.videoHeight || s.height;
    const k = Math.min(1, maxW / w0), w = Math.max(1, Math.round(w0 * k)), hh = Math.max(1, Math.round(h0 * k));
    if (work.width !== w || work.height !== hh) { work.width = w; work.height = hh; }
    wctx.drawImage(s, 0, 0, w, hh);
    return wctx.getImageData(0, 0, w, hh);
  }
  const capture = (c, e) => { try { c.setPointerCapture(e.pointerId); } catch (err) { /* nothing to capture */ } };
  const kick = () => { if (!vid || vid.paused) render(); };
  function touched() { GG().edited(); kick(); }
  // what each control is when the stock and grade are all there is: ↺ goes back to it
  async function rebase() { const p = GG().preset(); base = await GG().lookWith(p.film, p.grade); }

  // ── the picture ─────────────────────────────────────────────────────────
  async function render() {
    const g = await window.goboReady;
    if (!g || !src || !L() || el.classList.contains("hidden")) return;
    if (busy) { again = true; return; }
    if (vid && vid.readyState < 2) return;
    busy = true;
    try {
      if (L().lut3d) await GG().ensureLut(L());
      const pic = grab(src, vid ? 720 : 1280);
      if (!orig) {
        const r = g.grade(pic, L(), vid ? ++frameNo : 7);
        if (r && r.error) { say(r.error, true); return; }
      }
      if (scopeOn && (!vid || frameNo % 2 === 0)) drawScope(g, pic);
      if (cv.width !== pic.width || cv.height !== pic.height) { cv.width = pic.width; cv.height = pic.height; }
      cv.getContext("2d").putImageData(pic, 0, 0);
      if (vid && !vid.paused && vid.duration) q("#pgSeek", el).value = Math.round(vid.currentTime / vid.duration * 1000);
    } finally {
      busy = false;
      if (again) { again = false; kick(); }
    }
  }
  function drawScope(g, img) {
    const sc = q("#pgScope", el), W = sc.width, H = sc.height, x = sc.getContext("2d");
    x.putImageData(g.scope(img, "waveform", W, H), 0, 0);
    x.strokeStyle = "rgba(255,255,255,.16)"; x.lineWidth = 1;
    [25, 50, 75].forEach(v => { const y = Math.round((1 - v / 100) * (H - 1)) + 0.5; x.beginPath(); x.moveTo(0, y); x.lineTo(W, y); x.stroke(); });
  }
  function stop() { cancelAnimationFrame(raf); raf = 0; if (vid) { vid.pause(); vid.removeAttribute("src"); vid.load(); vid = null; } src = null; }
  const playIcon = () => { q("#pgPlay", el).textContent = vid && !vid.paused ? "❚❚" : "▶"; };
  async function loadSource() {
    stop();
    const t = GG().source(), msg = q("#pgMsg", el);
    q("#pgWhat", el).textContent = t ? `${t.shot || ""}${t.letter || ""} · ${t.label || ""}` : "";
    const ok = t && t.status === "done" && t.url;
    msg.classList.toggle("hidden", !!ok); cv.classList.toggle("hidden", !ok);
    if (!ok) { msg.textContent = "Pick a finished take in the studio, then Grade."; return; }
    const moving = t.kind === "video";
    q("#pgPlay", el).classList.toggle("hidden", !moving); q("#pgSeek", el).classList.toggle("hidden", !moving);
    q("#pgHint", el).classList.toggle("hidden", moving);
    if (moving) {
      vid = document.createElement("video");
      Object.assign(vid, { muted: true, loop: true, playsInline: true, preload: "auto", src: t.url });
      vid.setAttribute("playsinline", ""); vid.setAttribute("muted", "");           // what iOS reads
      vid.onloadeddata = vid.onseeked = () => render();
      await vid.play().catch(() => {});
      src = vid; playIcon();
      const tick = () => { if (vid && !vid.paused) render(); raf = requestAnimationFrame(tick); };
      raf = requestAnimationFrame(tick);
    } else {
      src = await new Promise((ok2, no) => { const i = new Image(); i.onload = () => ok2(i); i.onerror = () => no(new Error("That picture didn't load.")); i.src = t.url; })
        .catch(e => { say(e.message, true); return null; });
      render();
    }
  }
  function togglePlay() { if (!vid) return; if (vid.paused) vid.play().catch(() => {}); else vid.pause(); playIcon(); render(); }
  q("#pgPlay", el).onclick = togglePlay;
  q("#pgSeek", el).oninput = e => { if (vid && vid.duration) { vid.pause(); playIcon(); vid.currentTime = Number(e.target.value) / 1000 * vid.duration; } };

  // hold for the original, drag across to move a control, tap to play or pause
  let tagT = 0;
  function tag(text, ms) {
    const t = q("#pgTag", el); clearTimeout(tagT);
    t.textContent = text; t.classList.toggle("hidden", !text);
    if (text && ms) tagT = setTimeout(() => t.classList.add("hidden"), ms);
  }
  const dragKey = () => GROUPS[tab] ? active[tab] : lastKey;
  let press = null;
  view.addEventListener("pointerdown", e => {
    if (!src) return;
    capture(view, e);
    press = { id: e.pointerId, x: e.clientX, y: e.clientY, at: performance.now(), mode: "", w: Math.max(1, view.clientWidth) };
    press.hold = setTimeout(() => { if (press && !press.mode) { press.mode = "orig"; orig = true; tag("Original"); kick(); } }, 280);
  });
  view.addEventListener("pointermove", e => {
    if (!press || e.pointerId !== press.id) return;
    const dx = e.clientX - press.x, dy = e.clientY - press.y;
    if (!press.mode && Math.hypot(dx, dy) > 10) {
      clearTimeout(press.hold);
      press.mode = "drag"; press.k = dragKey(); press.v = Number(L()[press.k]);
    }
    if (press.mode !== "drag") return;
    const c = CTRL[press.k];
    const v = Math.min(c.mx, Math.max(c.mn, press.v + dx / press.w * (c.mx - c.mn) * 0.75));
    L()[press.k] = Number((Math.round(v / c.st) * c.st).toFixed(4));
    tag(`${c.label} ${fmt(press.k, L()[press.k])}`); syncRow(press.k); touched();
  });
  const lift = e => {
    if (!press || e.pointerId !== press.id) return;
    clearTimeout(press.hold);
    if (press.mode === "orig") { orig = false; tag(""); kick(); }
    else if (press.mode === "drag") tag(q("#pgTag", el).textContent, 700);
    else if (performance.now() - press.at < 280) togglePlay();
    press = null;
  };
  view.addEventListener("pointerup", lift);
  view.addEventListener("pointercancel", lift);
  view.addEventListener("contextmenu", e => e.preventDefault());

  // ── the tools ───────────────────────────────────────────────────────────
  function showTab(k) {
    tab = k;
    qa("#pgTabs button", el).forEach(b => b.classList.toggle("on", b.dataset.tab === k));
    q("#pgMenu", el).classList.add("hidden");
    if (GROUPS[k]) slidersTool(k);
    else ({ looks: looksTool, wheels: wheelsTool, curves: curvesTool, match: matchTool })[k]();
    pane.scrollTop = 0;
  }
  qa("#pgTabs button", el).forEach(b => b.onclick = () => showTab(b.dataset.tab));
  const seg = (items, on, attr) => `<div class="pgseg">${items.map(([k, l]) => `<button data-${attr}="${k}" class="${k === on ? "on" : ""}">${l}</button>`).join("")}</div>`;

  // one control a row; the one picked is the one a drag on the picture moves
  function slidersTool(k) {
    pane.innerHTML = GROUPS[k].map(key => { const c = CTRL[key]; return `<div class="pgsl ${key === active[k] ? "on" : ""}" data-k="${key}">
      <div class="top"><span>${h(c.label)}</span><output></output><button class="rs hidden" data-rs="${key}" aria-label="${h(c.label)} back to the stock and grade's">↺</button></div>
      <input type="range" data-k="${key}" min="${c.mn}" max="${c.mx}" step="${c.st}" aria-label="${h(c.label)}"></div>`; }).join("");
    GROUPS[k].forEach(syncRow);
  }
  function syncRow(k) {
    const row = q(`.pgsl[data-k="${k}"]`, pane);
    if (!row || !L()) return;
    const v = Number(L()[k]);
    q("input", row).value = v; q("output", row).textContent = fmt(k, v);
    q(".rs", row).classList.toggle("hidden", !base || Math.abs(v - Number(base[k])) < 1e-6);
  }
  function pick(k) {
    if (!GROUPS[tab]) return;
    active[tab] = lastKey = k;
    qa(".pgsl", pane).forEach(r => r.classList.toggle("on", r.dataset.k === k));
  }
  pane.addEventListener("input", e => {
    const i = e.target;
    if (i.matches(".pgsl input[data-k]")) { pick(i.dataset.k); L()[i.dataset.k] = Number(i.value); syncRow(i.dataset.k); touched(); }
  });
  pane.addEventListener("click", e => {
    const rs = e.target.closest("[data-rs]");
    if (rs && base) { const k = rs.dataset.rs; L()[k] = Number(base[k]); pick(k); syncRow(k); touched(); return; }
    const row = e.target.closest(".pgsl[data-k]");
    if (row && !e.target.matches("input")) pick(row.dataset.k);
  });

  // Looks: this frame through every stock (or grade), the rest of the look kept
  const presetOn = (p, kind) => p[kind] === "auto" ? (kind === "film" ? "clean" : "natural") : p[kind];
  async function looksTool() {
    pane.innerHTML = seg([["film", "Film stock"], ["grade", "Grade"], ["lut", "LUT"]], looksKind, "lk") + `<div id="pgLooks"></div>`;
    qa("[data-lk]", pane).forEach(b => b.onclick = () => { looksKind = b.dataset.lk; looksTool(); });
    const box = q("#pgLooks", pane);
    if (looksKind === "lut") return lutTool(box);
    const p = GG().preset(), on = presetOn(p, looksKind);
    const opts = ((CAT.groups[looksKind] || {}).options || []).filter(o => o.id !== "auto");
    box.innerHTML = `<div class="pgthumbs">${opts.map(o => `<button class="pgthumb ${o.id === on ? "on" : ""}" data-look="${h(o.id)}"><canvas></canvas>${h(o.label)}</button>`).join("")}</div>`;
    qa("[data-look]", box).forEach(b => b.onclick = async () => {
      const film = looksKind === "film" ? b.dataset.look : p.film, grade = looksKind === "grade" ? b.dataset.look : p.grade;
      await GG().setPreset(film, grade); await rebase(); touched();
      qa("[data-look]", box).forEach(x => x.classList.toggle("on", x === b));
      tag(`${b.textContent.trim()}`, 900);
    });
    const g = await window.goboReady;
    if (vid && vid.readyState < 2) await new Promise(ok => { vid.addEventListener("loadeddata", ok, { once: true }); setTimeout(ok, 4000); });
    if (!g || !src || (vid && vid.readyState < 2) || tab !== "looks") return;
    const frame = grab(src, 224), data = new Uint8ClampedArray(frame.data);
    for (const b of qa("[data-look]", box)) {
      const look = await GG().lookWith(looksKind === "film" ? b.dataset.look : p.film, looksKind === "grade" ? b.dataset.look : p.grade);
      if (tab !== "looks" || !b.isConnected) return;                  // moved on
      const img = new ImageData(new Uint8ClampedArray(data), frame.width, frame.height);
      g.grade(img, look, 7);
      const c = q("canvas", b); c.width = img.width; c.height = img.height; c.getContext("2d").putImageData(img, 0, 0);
    }
  }
  async function lutTool(box) {
    const list = await GG().listLuts().catch(() => []), cur = L().lut3d, curId = cur ? cur.of || cur.id : "";
    box.innerHTML = `<div class="pglist"><button class="pgrow ${curId ? "" : "on"}" data-lutid="">No LUT</button>
      ${list.map(m => `<button class="pgrow ${m.id === curId ? "on" : ""}" data-lutid="${h(m.id)}">${h(m.title || m.name)}${m.kind === "1D" ? " (1D)" : ""}</button>`).join("")}</div>
      <div class="row"><button class="btn small" id="pgLutPick">Import a LUT or CDL</button><input type="file" id="pgLutFile" hidden></div>
      ${cur && !cur.of ? `<div class="pgsl on"><div class="top"><span>LUT amount</span><output>${Number(cur.mix == null ? 1 : cur.mix).toFixed(2)}</output></div>
        <input type="range" id="pgLutMix" min="0" max="1" step="0.01" value="${cur.mix == null ? 1 : cur.mix}"></div>` : ""}
      <p class="pgp" id="pgLutNote">${GG().lutNote()}</p>`;
    const done = async msg => { await rebase(); touched(); looksTool(); if (msg) say(msg); };
    qa("[data-lutid]", box).forEach(b => b.onclick = async () => { try { await done(await GG().useLut(b.dataset.lutid)); } catch (e) { say(e.message, true); } });
    q("#pgLutPick", box).onclick = () => q("#pgLutFile", box).click();
    q("#pgLutFile", box).onchange = async e => {
      const f = e.target.files[0]; e.target.value = "";
      if (f) try { await done(await GG().importGrade(f)); } catch (err) { say(err.message, true); }
    };
    q("#pgLutNote", box).onclick = async e => {
      const b = e.target.closest("[data-lut]");
      if (b) try { await done(await GG().lutAction(b.dataset.lut)); } catch (err) { say(err.message, true); }
    };
    const mix = q("#pgLutMix", box);
    if (mix) mix.oninput = () => { L().lut3d.mix = Number(mix.value); mix.previousElementSibling.querySelector("output").textContent = Number(mix.value).toFixed(2); touched(); };
  }

  // Wheels: one at a time, big enough for a thumb
  function wheelsTool() {
    const [, label, , range] = T.WHEELS.find(w => w[0] === wheelKey);
    pane.innerHTML = seg(T.WHEELS.map(([k, l]) => [k, l]), wheelKey, "wk") + `
      <div class="pgwheel"><canvas width="480" height="480" aria-label="${h(label)} wheel"></canvas></div>
      <div class="pgsl on"><div class="top"><span>${h(label)}: darker or brighter</span><output></output></div>
        <input type="range" id="pgMaster" min="${-range}" max="${range}" step="0.005"></div>
      <div class="row"><button class="btn small" id="pgCentre">Centre this wheel</button><button class="btn small" id="pgWheelsOff">Reset all four</button></div>
      <p class="pgp">Drag toward a colour to push it into the ${WHERE[wheelKey]}.</p>`;
    qa("[data-wk]", pane).forEach(b => b.onclick = () => { wheelKey = b.dataset.wk; wheelsTool(); });
    const c = q(".pgwheel canvas", pane), m = q("#pgMaster", pane), rim = T.WHEELS.find(w => w[0] === wheelKey)[2];
    const at = e => {
      const b = c.getBoundingClientRect(), r = b.width / 2 - 2;
      let u = (e.clientX - b.left - b.width / 2) / r, v = (b.top + b.height / 2 - e.clientY) / r;
      const d = Math.hypot(u, v); if (d > 1) { u /= d; v /= d; }
      const rgb = T.toRgb(u * rim, v * rim), w = T.wheelsOf();
      w[wheelKey] = [rgb[0], rgb[1], rgb[2], w[wheelKey][3]];
      drawWheel(); touched();
    };
    c.onpointerdown = e => { capture(c, e); at(e); c.onpointermove = at; };
    c.onpointerup = c.onpointercancel = () => { c.onpointermove = null; T.tidyWheels(); };
    m.oninput = () => { const w = T.wheelsOf(); w[wheelKey][3] = Number(m.value); drawWheel(); touched(); };
    q("#pgCentre", pane).onclick = () => { const w = T.wheelsOf(); w[wheelKey] = [0, 0, 0, 0]; T.tidyWheels(); drawWheel(); touched(); };
    q("#pgWheelsOff", pane).onclick = () => { delete L().wheels; drawWheel(); touched(); };
    drawWheel();
  }
  function drawWheel() {
    const c = q(".pgwheel canvas", pane);
    if (!c) return;
    const x = c.getContext("2d"), s = c.width, rim = T.WHEELS.find(w => w[0] === wheelKey)[2];
    const w = (L().wheels || {})[wheelKey] || [0, 0, 0, 0];
    x.clearRect(0, 0, s, s); x.drawImage(T.ringImage(s), 0, 0);
    x.strokeStyle = "rgba(0,0,0,.35)"; x.lineWidth = 2;
    x.beginPath(); x.moveTo(s / 2, 12); x.lineTo(s / 2, s - 12); x.moveTo(12, s / 2); x.lineTo(s - 12, s / 2); x.stroke();
    const [cb, cr] = T.toCbCr(w), r = s / 2 - 2;
    const px = s / 2 + Math.max(-1, Math.min(1, cb / rim)) * r, py = s / 2 - Math.max(-1, Math.min(1, cr / rim)) * r;
    x.lineWidth = 3; x.strokeStyle = "#fff"; x.fillStyle = "rgba(0,0,0,.5)";
    x.beginPath(); x.arc(px, py, 13, 0, Math.PI * 2); x.fill(); x.stroke();
    const m = q("#pgMaster", pane);
    if (m) { m.value = w[3]; m.previousElementSibling.querySelector("output").textContent = (w[3] > 0 ? "+" : "") + w[3].toFixed(3); }
  }

  // Curves: tap to add a point, drag to bend, pick a point to take it out
  const PAD = 22;
  function curvesTool() {
    pane.innerHTML = seg(T.CURVES.map(([k, l]) => [k, l]), curveCh, "ch") + `
      <div class="pgcurve"><canvas width="600" height="600" aria-label="Curve"></canvas></div>
      <div class="row"><button class="btn small" id="pgPtOff" disabled>Take this point out</button><button class="btn small" id="pgCurveOff">Reset the curve</button></div>
      <p class="pgp">Tap to add a point, drag to bend. Y moves all three channels; R, G and B one each.</p>`;
    qa("[data-ch]", pane).forEach(b => b.onclick = () => { curveCh = b.dataset.ch; curvePt = -1; curvesTool(); });
    const c = q(".pgcurve canvas", pane), span = c.width - 2 * PAD;
    const at = e => { const b = c.getBoundingClientRect(), k = c.width / b.width;
      return [Math.min(1, Math.max(0, ((e.clientX - b.left) * k - PAD) / span)), Math.min(1, Math.max(0, 1 - ((e.clientY - b.top) * k - PAD) / span)), k]; };
    const near = (pts, [x, y, k]) => pts.findIndex(p => Math.hypot((p[0] - x) * span, (p[1] - y) * span) < 26 * Math.max(1, k));
    let drag = -1;
    c.onpointerdown = e => {
      capture(c, e);
      const pts = T.curveOf(curveCh), p = at(e);
      drag = near(pts, p);
      if (drag < 0) {
        if (pts.length >= 16) return say("A curve takes 16 points at most.", true);
        pts.push([p[0], p[1]]); pts.sort((a, b) => a[0] - b[0]);
        drag = pts.findIndex(v => v[0] === p[0] && v[1] === p[1]);
        T.setCurve(curveCh, pts); touched();
      }
      curvePt = drag; drawCurve();
    };
    c.onpointermove = e => {
      if (drag < 0) return;
      const pts = T.curveOf(curveCh), [x, y] = at(e), last = pts.length - 1;
      const lo = drag === 0 ? 0 : pts[drag - 1][0] + 0.01, hi = drag === last ? 1 : pts[drag + 1][0] - 0.01;
      pts[drag] = [drag === 0 ? 0 : drag === last ? 1 : Math.min(hi, Math.max(lo, x)), y];
      T.setCurve(curveCh, pts); drawCurve(); touched();
    };
    c.onpointerup = c.onpointercancel = () => { drag = -1; };
    q("#pgPtOff", pane).onclick = () => {
      const pts = T.curveOf(curveCh);
      if (curvePt > 0 && curvePt < pts.length - 1) { pts.splice(curvePt, 1); T.setCurve(curveCh, pts); curvePt = -1; drawCurve(); touched(); }
    };
    q("#pgCurveOff", pane).onclick = () => { T.setCurve(curveCh, [[0, 0], [1, 1]]); curvePt = -1; drawCurve(); touched(); };
    drawCurve();
  }
  function drawCurve() {
    const c = q(".pgcurve canvas", pane);
    if (!c) return;
    const x = c.getContext("2d"), s = c.width, span = s - 2 * PAD, X = v => PAD + v * span, Y = v => PAD + (1 - v) * span;
    x.clearRect(0, 0, s, s);
    x.strokeStyle = "rgba(255,255,255,.08)"; x.lineWidth = 1.5;
    for (let i = 0; i <= 4; i++) { const g = PAD + i / 4 * span; x.beginPath(); x.moveTo(g, PAD); x.lineTo(g, s - PAD); x.moveTo(PAD, g); x.lineTo(s - PAD, g); x.stroke(); }
    x.beginPath(); x.moveTo(X(0), Y(0)); x.lineTo(X(1), Y(1)); x.stroke();
    T.CURVES.forEach(([k, , colour]) => {
      const pts = T.curveOf(k), on = k === curveCh;
      if (!on && !(L().curves || {})[k]) return;
      const f = T.monotone(pts);
      x.strokeStyle = colour; x.globalAlpha = on ? 1 : 0.35; x.lineWidth = on ? 3 : 2;
      x.beginPath(); for (let i = 0; i <= 200; i++) { const v = i / 200; i ? x.lineTo(X(v), Y(f(v))) : x.moveTo(X(v), Y(f(v))); } x.stroke();
      if (on) pts.forEach((p, i) => { x.beginPath(); x.arc(X(p[0]), Y(p[1]), i === curvePt ? 11 : 8, 0, Math.PI * 2); x.fillStyle = i === curvePt ? colour : "#0d0d10"; x.fill(); x.stroke(); });
      x.globalAlpha = 1;
    });
    const off = q("#pgPtOff", pane);
    if (off) off.disabled = !(curvePt > 0 && curvePt < T.curveOf(curveCh).length - 1);
  }

  // Match: the take graded to a photo's colour, or other takes to this one's
  function matchTool() {
    pane.innerHTML = `<p class="pgp">Grade this take to match a photo: a film still, last night's sunset, the shot it cuts with.</p>
      <button class="btn primary" id="pgPhoto">Choose a photo</button><input type="file" id="pgPhotoFile" accept="image/*" hidden>
      <div id="pgMatched"></div>
      <p class="pgh">Other takes</p>
      <p class="pgp">An extension, another angle, a retake: matched to this take's colour, then given this grade.</p>
      <button class="btn" id="pgOthers">Grade other takes to match</button>`;
    q("#pgPhoto", pane).onclick = () => q("#pgPhotoFile", pane).click();
    q("#pgPhotoFile", pane).onchange = e => { const f = e.target.files[0]; e.target.value = ""; if (f) matchPhoto(f).catch(err => say(err.message, true)); };
    q("#pgOthers", pane).onclick = () => { close(); GG().reopen(); GG().matchOpen(); };
    showMatched();
  }
  function showMatched() {
    const box = q("#pgMatched", pane);
    if (!box) return;
    if (!matched) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="pgmatched"><img src="${matched.thumb}" alt=""><span>Matched to the photo: ${matched.before.toFixed(1)} → ${matched.after.toFixed(1)} apart in colour (ΔE; under 2 is hard to see).${matched.hadCdl ? " It replaced the CDL the grade had." : ""}</span></div>
      <p class="pgp">The stock and grade go on top of the match.</p>
      <div class="row"><button class="btn small" id="pgPlain">Just the photo's colour</button><button class="btn small" id="pgUnmatch">Undo the match</button></div>`;
    q("#pgPlain", box).onclick = async () => { await GG().setPreset("clean", "natural"); await rebase(); touched(); say("Clean stock, natural grade: the photo's colour as it is."); };
    q("#pgUnmatch", box).onclick = () => { if (matched.was) L().cdl = matched.was; else delete L().cdl; matched = null; touched(); showMatched(); };
  }
  // three frames of the take on screen, from the clip that's playing: a phone
  // won't load a second copy of a clip that isn't
  async function framesHere() {
    if (!vid) return [grab(src, 320)];
    const was = vid.currentTime, playing = !vid.paused, d = vid.duration || 0, out = [];
    vid.pause();
    for (const t of [0.05, d / 2, Math.max(0, d - 0.1)]) {
      await new Promise(ok => { const done = () => { clearTimeout(late); vid.removeEventListener("seeked", done); ok(); }, late = setTimeout(done, 2500); vid.addEventListener("seeked", done); vid.currentTime = t; });
      out.push(grab(vid, 320));
    }
    vid.currentTime = was;
    if (playing) vid.play().catch(() => {});
    playIcon();
    return out;
  }
  async function matchPhoto(file) {
    const g = await window.goboReady, box = q("#pgMatched", pane);
    if (!g || !src) return;
    if (box) box.innerHTML = `<p class="pgp">Reading the photo…</p>`;
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => no(new Error("That photo didn't open. Try a JPEG or a PNG.")); i.src = url; });
      const k = Math.min(1, 480 / img.naturalWidth), c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(img.naturalHeight * k));
      const x = c.getContext("2d", { willReadFrequently: true });
      x.drawImage(img, 0, 0, c.width, c.height);
      const photo = x.getImageData(0, 0, c.width, c.height);
      const m = g.matchShot(await framesHere(), [photo]);
      if (m.error) throw new Error(m.error);
      const was = L().cdl ? JSON.parse(JSON.stringify(L().cdl)) : null;
      L().cdl = m.cdl;
      matched = { thumb: c.toDataURL("image/jpeg", 0.7), before: m.before, after: m.after, was, hadCdl: !!was };
      touched(); showMatched();
    } catch (e) {
      if (box) box.innerHTML = "";
      throw e;
    } finally { URL.revokeObjectURL(url); }
  }

  // ── the menu ────────────────────────────────────────────────────────────
  q("#pgMoreBtn", el).onclick = () => q("#pgMenu", el).classList.toggle("hidden");
  q("#pgScopeBtn", el).onclick = () => {
    scopeOn = !scopeOn;
    try { localStorage.setItem("gobo.phone.scope", scopeOn ? "1" : "0"); } catch (e) { /* this visit only */ }
    scopeButton(); kick();
  };
  function scopeButton() {
    q("#pgScopeBtn", el).classList.toggle("on", scopeOn); q("#pgScopeBtn", el).setAttribute("aria-pressed", String(scopeOn));
    q("#pgScope", el).classList.toggle("hidden", !scopeOn);
  }
  q("#pgMenu", el).onclick = async e => {
    const b = e.target.closest("[data-m]");
    if (!b) return;
    q("#pgMenu", el).classList.add("hidden");
    const m = b.dataset.m;
    if (m === "still") return saveStill();
    if (m === "cube") return GG().saveCube();
    if (m === "reset") {
      await GG().reset(); delete L().wheels; delete L().curves; delete L().cdl; delete L().lut3d;
      matched = null; await rebase(); showTab(tab); touched(); return;
    }
    if (m === "desk") { close(); return desk.open(); }
    if (m === "layout") { choose("desktop"); say("The desktop layout. “Phone layout” in the header brings this back."); }
  };
  // a graded still: to Photos from the share sheet where there is one, else a download
  async function saveStill() {
    const g = await window.goboReady, t = GG().source();
    if (!g || !src) return;
    const w0 = src.naturalWidth || src.videoWidth, h0 = src.naturalHeight || src.videoHeight, k = Math.min(1, 4096 / w0);
    const c = document.createElement("canvas"); c.width = Math.round(w0 * k); c.height = Math.round(h0 * k);
    const x = c.getContext("2d", { willReadFrequently: true }); x.drawImage(src, 0, 0, c.width, c.height);
    const img = x.getImageData(0, 0, c.width, c.height); g.grade(img, L(), 7); x.putImageData(img, 0, 0);
    const blob = await new Promise(ok => c.toBlob(ok, "image/jpeg", 0.92));
    const name = `${(t && `${t.shot || ""}${t.letter || ""}`) || "take"}-graded.jpg`, file = new File([blob], name, { type: "image/jpeg" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file] }); return; } catch (e) { if (e.name === "AbortError") return; }
    }
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
  q("#pgBake", el).onclick = () => GG().bake(q("#pgBake", el));

  // ── open and close ──────────────────────────────────────────────────────
  // the phone's Back gesture closes the grader rather than leaving the studio
  async function open() {
    if (!el.classList.contains("hidden")) return;
    await GG().take();
    await rebase();
    matched = null;
    el.classList.remove("hidden"); document.body.classList.add("pgopen");
    try { history.pushState({ goboPhone: 1 }, ""); } catch (e) { /* no history: ✕ still closes */ }
    scopeButton(); showTab(tab);
    await loadSource();
    if (tab === "looks") looksTool();                     // the thumbnails want the picture
    if (vid && !hinted) { hinted = true; tag("Hold for the original · drag across to adjust", 3000); }
  }
  let hinted = false;
  function shut() {
    if (el.classList.contains("hidden")) return;
    stop(); orig = false; press = null; tag("");
    el.classList.add("hidden"); document.body.classList.remove("pgopen"); q("#pgMenu", el).classList.add("hidden");
    GG().changed();
  }
  function close() {
    if (el.classList.contains("hidden")) return;
    shut();
    if (history.state && history.state.goboPhone) history.back();
  }
  window.addEventListener("popstate", shut);
  q("#pgX", el).onclick = close;
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !el.classList.contains("hidden")) { e.stopPropagation(); close(); } }, true);
  // the Lab's Advanced (and anything else that opens Colour) gets the grader on a phone
  const desk = { open: GC().open, close: GC().close };
  GC().open = () => (isPhone() ? open() : desk.open());
  GC().close = simple => { close(); return desk.close(simple); };

  // ── the studio on a phone ───────────────────────────────────────────────
  const sw = document.createElement("button");
  sw.className = "chipbtn"; sw.id = "layoutBtn"; sw.type = "button";
  const head = q("header");
  if (head) head.appendChild(sw);
  function choose(v) { setPref(v === (touchSmall.matches ? "phone" : "desktop") ? "" : v); layout(); }
  sw.onclick = () => choose(isPhone() ? "desktop" : "phone");
  const actions = q("#actions");
  // a Grade button on every finished take
  function gradeButton() {
    if (!actions) return;
    const t = ST() && ST().current(), b = q("[data-pg]", actions);
    const want = document.body.classList.contains("phone") && t && t.status === "done" && (t.kind === "video" || t.kind === "image");
    if (want && !b) actions.insertAdjacentHTML("afterbegin", `<button class="btn primary" data-pg title="Colour, laid out for the phone">Grade</button>`);
    if (!want && b) b.remove();
  }
  if (actions) {
    new MutationObserver(gradeButton).observe(actions, { childList: true });
    actions.addEventListener("click", e => { if (e.target.closest("[data-pg]")) open(); });
  }
  function layout() {
    const on = isPhone();
    document.body.classList.toggle("phone", on);
    sw.textContent = on ? "Desktop layout" : "Phone layout";
    sw.classList.toggle("hidden", !on && !narrow.matches);
    if (!on) close();
    gradeButton();
  }
  touchSmall.addEventListener("change", layout);
  narrow.addEventListener("change", layout);
  layout();
  window.goboPhone = { open, close, isPhone, choose };
})();
