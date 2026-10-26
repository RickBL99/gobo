// Gobo's Colour page: the Lab's Grade, advanced. DaVinci Resolve's Color page
// in miniature, on the same look the Lab grades with (window.goboGrade, in
// gobo_lab.js) and the same gobo.wasm (gobo.js):
//
//   the viewer    the take, graded live (grain moving on video), with a wipe
//                 to the original and a scrubber
//   scopes        waveform, RGB parade, vectorscope and histogram, drawn by
//                 Rust from the graded picture
//   primaries     lift, gamma, gain and offset wheels, each with a master
//   curves        custom Y, R, G and B curves (monotone: they never overshoot)
//   LUT and CDL   a .cube from any editor, first or after the look, and an
//                 ASC CDL from a colourist
//   look, film    the stock and grade, exposure to tint, grain to vignette
//
// Every tool changes colour one value at a time, so all of them fold into
// the look's one 3D LUT (crates/scrim): the preview, the exported .cube and
// Grade the take on the server all match.
(function () {
  "use strict";
  const ST = () => window.STUDIO, GG = () => window.goboGrade;
  const q = (sel, el) => (el || document).querySelector(sel);
  const qa = (sel, el) => Array.from((el || document).querySelectorAll(sel));
  const h = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const say = (m, bad) => (ST() && ST().toast ? ST().toast(m, bad) : console.log(m));
  const fix = (v, n) => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(n == null ? 3 : n);
  if (typeof CAT === "undefined" || !window.goboReady) return;

  // [key, label, chroma at the rim of the wheel, the master's range]
  const WHEELS = [["lift", "Lift", 0.12, 0.4], ["gamma", "Gamma", 0.3, 0.8], ["gain", "Gain", 0.3, 0.8], ["offset", "Offset", 0.12, 0.4]];
  const BASICS = [["exposure", "Exposure", -2, 2, 0.01], ["contrast", "Contrast", 0.5, 2, 0.01], ["pivot", "Pivot", 0.3, 0.8, 0.01],
    ["sat", "Saturation", 0, 2, 0.01], ["temp", "Warmth", -1, 1, 0.01], ["tint", "Tint", -1, 1, 0.01]];
  const FILM = [["grain", "Grain", 0, 1, 0.01], ["grain_size", "Grain size", 0.5, 4, 0.05], ["halation", "Halation", 0, 1, 0.01],
    ["vignette", "Vignette", 0, 0.8, 0.01], ["fade", "Fade", 0, 0.25, 0.005], ["white", "White point", 0.7, 1, 0.005], ["amount", "Amount", 0, 1, 0.01]];
  const SCOPES = [["waveform", "Waveform"], ["parade", "Parade"], ["vector", "Vectorscope"], ["histogram", "Histogram"]];
  const CURVES = [["y", "Y", "#ecebe8"], ["r", "R", "#ff6b5e"], ["g", "G", "#6fe08a"], ["b", "B", "#6aa8ff"]];
  // BT.709: a chroma direction (Cb, Cr) is an RGB change with no change in luma
  const toRgb = (cb, cr) => [1.5748 * cr, -0.1873 * cb - 0.4681 * cr, 1.8556 * cb];
  const toCbCr = c => [-0.1146 * c[0] - 0.3854 * c[1] + 0.5 * c[2], 0.5 * c[0] - 0.4542 * c[1] - 0.0458 * c[2]];

  const style = document.createElement("style");
  style.textContent = `
body.colouropen { overflow: hidden; }
#colour { position: fixed; inset: 0; z-index: 70; background: #0b0b0d; color: var(--text); display: flex; flex-direction: column; }
#colour.hidden { display: none; }
#colour .chead { display: flex; align-items: center; gap: 10px; padding: 10px 16px; border-bottom: 1px solid var(--line); }
#colour .chead b { font-size: 1.02rem; }
#colour .chead .what { color: var(--muted); font-size: .84rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#colour .cbody { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 380px; }
#colour .cview { display: flex; flex-direction: column; min-width: 0; min-height: 0; padding: 12px 16px; gap: 10px; }
#colour .stage { flex: 1; min-height: 160px; display: flex; align-items: center; justify-content: center; background: #000; border-radius: 12px; overflow: hidden; position: relative; }
#colour .stage canvas { width: 100%; height: 100%; object-fit: contain; display: block; }
#colour .stage .msg { color: var(--muted); padding: 30px; text-align: center; }
#colour .vbar { display: flex; align-items: center; gap: 10px; font-size: .76rem; color: var(--muted); }
#colour .vbar input[type=range] { flex: 1; }
#colour .scopes { display: flex; flex-direction: column; gap: 6px; }
#colour .scopes .seg { align-self: flex-start; }
#colour .scopes .seg button { font-size: .72rem; padding: 4px 10px; }
#colour .scopebox { flex: 1; display: flex; justify-content: center; background: #050506; border: 1px solid var(--line); border-radius: 10px; padding: 6px; min-width: 0; }
#colour .scopebox canvas { height: 190px; max-width: 100%; image-rendering: auto; }
#colour .cside { border-left: 1px solid var(--line); overflow: auto; padding: 4px 16px 20px; background: var(--panel); }
#colour .cside h3 { font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 16px 0 8px; display: flex; align-items: center; gap: 8px; }
#colour .cside h3 .linkbtn { font-size: .7rem; text-transform: none; letter-spacing: 0; margin-left: auto; }
#colour .wheels { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 12px; }
#colour .wheel { text-align: center; font-size: .74rem; }
#colour .wheel canvas { width: 132px; height: 132px; touch-action: none; cursor: crosshair; display: block; margin: 2px auto 4px; }
#colour .wheel .nums { color: var(--dim); font-variant-numeric: tabular-nums; font-size: .66rem; min-height: 1em; }
#colour .wheel input { width: 100%; }
#colour .sl { display: grid; grid-template-columns: 84px 1fr 44px; gap: 6px; align-items: center; font-size: .76rem; margin: 3px 0; }
#colour .sl output { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
#colour .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 6px 0; }
#colour .curvebox canvas { width: 100%; aspect-ratio: 1; display: block; background: #0d0d10; border-radius: 10px; border: 1px solid var(--line); touch-action: none; cursor: crosshair; }
#colour .cfoot { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; padding: 10px 16px; border-top: 1px solid var(--line); }
#colour .hint { font-size: .72rem; }
@media (max-width: 900px) {
  #colour .cbody { grid-template-columns: 1fr; overflow: auto; }
  #colour .cview { min-height: 70vh; }
  #colour .cside { border-left: 0; border-top: 1px solid var(--line); overflow: visible; }
  #colour #cPerf { display: none; }
}`;
  document.head.appendChild(style);

  const el = document.createElement("section");
  el.id = "colour"; el.className = "hidden"; el.setAttribute("role", "dialog"); el.setAttribute("aria-label", "Colour");
  el.innerHTML = `
  <header class="chead"><b>Colour</b><span class="rustpill" title="Graded in your browser by Rust compiled to WebAssembly; the server grades the take with the same module">Rust · WebAssembly</span>
    <span class="what" id="cWhat"></span><span style="flex:1"></span>
    <button class="btn small" id="cSimple" title="Back to the Lab's simple Grade">Simple</button>
    <button class="btn small" id="cReset" title="Back to the shot's stock and grade, no wheels, curves, LUT or CDL">Reset</button>
    <button class="iconbtn" id="cClose" title="Close (Esc). The grade stays on the Lab">✕</button></header>
  <div class="cbody">
    <div class="cview">
      <div class="stage" id="cStage"><canvas id="cCv"></canvas></div>
      <div class="vbar"><button class="btn small" id="cPlay" title="Play or pause (space)">❚❚</button>
        <input type="range" id="cSeek" min="0" max="1000" value="0" title="Scrub">
        <span>Wipe</span><input type="range" id="cWipe" min="0" max="100" value="0" title="Left of the line is the original" style="flex:0 0 120px">
        <span id="cPerf"></span></div>
      <div class="scopes"><div class="seg" id="cScopes">${SCOPES.map(([k, l], i) => `<button data-scope="${k}" class="${i ? "" : "on"}">${l}</button>`).join("")}</div>
        <div class="scopebox"><canvas id="cScope" width="560" height="200"></canvas></div></div>
    </div>
    <div class="cside">
      <h3>Look</h3>
      <div class="row"><select class="field" id="cFilm" title="Film stock" style="flex:1"></select><select class="field" id="cGrade" title="Grade" style="flex:1"></select></div>
      <h3>Primaries <button class="linkbtn" id="cWheelsOff">Reset wheels</button></h3>
      <div class="wheels" id="cWheels"></div>
      <p class="hint">Drag a wheel toward a colour to push it into the shadows (lift), the mid-tones (gamma), the highlights (gain) or everything (offset); the slider under it makes that range darker or brighter. Double-click a wheel to centre it.</p>
      <h3>Curves <button class="linkbtn" id="cCurveOff">Reset curve</button></h3>
      <div class="row"><div class="seg" id="cCurveCh">${CURVES.map(([k, l], i) => `<button data-ch="${k}" class="${i ? "" : "on"}">${l}</button>`).join("")}</div></div>
      <div class="curvebox"><canvas id="cCurve" width="340" height="340"></canvas></div>
      <p class="hint">Click to add a point, drag to bend, double-click a point to take it out. Y moves all three channels; R, G and B one each.</p>
      <h3>LUT and CDL</h3>
      <div class="row"><select class="field" id="cLut" style="flex:1;min-width:120px"><option value="">No LUT</option></select>
        <input type="file" id="cLutFile" accept=".cube,.cdl,.cc,.ccc,.xml" hidden><button class="btn small" id="cLutPick">Import LUT or CDL</button></div>
      <div id="cLutOpts" class="hidden">
        <label class="sl"><span>LUT amount</span><input type="range" id="cLutMix" min="0" max="1" step="0.01"><output></output></label>
        <div class="row"><span class="hint">Apply it</span><div class="seg" id="cLutWhere"><button data-first="0">After the look</button><button data-first="1" title="For a camera's log-to-video LUT">First</button></div></div>
      </div>
      <div class="hint" id="cLutNote"></div>
      <div class="hint" id="cCdl"></div>
      <h3>Basics</h3><div id="cBasics"></div>
      <h3>Film</h3><div id="cFilmFx"></div>
    </div>
  </div>
  <footer class="cfoot"><button class="btn primary small" id="cBake" title="Every frame through this grade, in Rust on the server, as a new take beside the original">Grade the take</button>
    <button class="btn small" id="cCube" title="Everything here but grain, halation and the vignette, as one LUT for Resolve, Premiere or Final Cut">Download .cube</button>
    <button class="btn small" id="cStill">Download graded still</button><span style="flex:1"></span>
    <span class="hint" id="cNote"></span><button class="btn small" id="cDone">Done</button></footer>`;
  document.body.appendChild(el);

  const L = () => GG().look;
  const cv = q("#cCv", el);
  let src = null, vid = null, raf = 0, busy = false, again = false, frameNo = 0, perfMs = 0, scopeKind = "waveform";
  const work = document.createElement("canvas"), wctx = work.getContext("2d", { willReadFrequently: true });
  function grab(s, maxW) {
    const w0 = s.naturalWidth || s.videoWidth || s.width, h0 = s.naturalHeight || s.videoHeight || s.height;
    const k = Math.min(1, maxW / w0), w = Math.max(1, Math.round(w0 * k)), hh = Math.max(1, Math.round(h0 * k));
    if (work.width !== w || work.height !== hh) { work.width = w; work.height = hh; }
    wctx.drawImage(s, 0, 0, w, hh);
    return wctx.getImageData(0, 0, w, hh);
  }
  // a drag keeps going off the control (a pen or a synthetic pointer may have nothing to capture)
  const capture = (c, e) => { try { c.setPointerCapture(e.pointerId); } catch (err) { /* nothing to capture */ } };
  // an edit: the Lab keeps it, and the picture follows
  function touched() { GG().edited(); kick(); }
  function kick() { if (!vid || vid.paused) render(); }

  // ── the viewer ──────────────────────────────────────────────────────────
  async function render() {
    const g = await window.goboReady;
    if (!g || !src || !L()) return;
    if (busy) { again = true; return; }
    if (vid && vid.readyState < 2) return;
    busy = true;
    try {
      if (L().lut3d && !(await GG().ensureLut(L()))) syncLut();
      const orig = grab(src, vid ? 960 : 1280);
      const out = new ImageData(new Uint8ClampedArray(orig.data), orig.width, orig.height);
      const t0 = performance.now();
      const r = g.grade(out, L(), vid ? ++frameNo : 7);
      const ms = performance.now() - t0;
      if (r && r.error) { q("#cPerf", el).textContent = r.error; return; }
      if (!vid || frameNo % 2 === 0) drawScope(g, out);
      const wipe = Number(q("#cWipe", el).value) / 100;
      if (wipe > 0) {
        const cut = Math.round(out.width * wipe);
        for (let y = 0; y < out.height; y++) { const row = y * out.width * 4; out.data.set(orig.data.subarray(row, row + cut * 4), row); }
      }
      if (cv.width !== out.width || cv.height !== out.height) { cv.width = out.width; cv.height = out.height; }
      cv.getContext("2d").putImageData(out, 0, 0);
      perfMs = perfMs ? perfMs * 0.9 + ms * 0.1 : ms;
      if (!vid || frameNo % 12 === 0) q("#cPerf", el).textContent = `${perfMs.toFixed(1)} ms a frame, in Rust`;
      if (vid && !vid.paused && vid.duration) q("#cSeek", el).value = Math.round(vid.currentTime / vid.duration * 1000);
    } finally {
      busy = false;
      if (again) { again = false; kick(); }
    }
  }
  function stop() { cancelAnimationFrame(raf); raf = 0; if (vid) { vid.pause(); vid.removeAttribute("src"); vid.load(); vid = null; } src = null; }
  async function loadSource() {
    stop();
    const t = ST().current(), stage = q("#cStage", el);
    q("#cWhat", el).textContent = t ? `Shot ${t.shot}, take ${t.letter || ""} · ${t.label || ""}` : "";
    qa(".msg", stage).forEach(m => m.remove());
    if (!t || t.status !== "done" || !t.url) {
      cv.width = cv.height = 0;
      stage.insertAdjacentHTML("beforeend", `<div class="msg">Pick a finished take in the studio, then open Colour from the Lab's Grade.</div>`);
      return;
    }
    q("#cPlay", el).disabled = q("#cSeek", el).disabled = t.kind !== "video";
    if (t.kind === "video") {
      vid = document.createElement("video");
      Object.assign(vid, { muted: true, loop: true, playsInline: true, src: t.url });
      await vid.play().catch(() => {});
      src = vid;
      q("#cPlay", el).textContent = vid.paused ? "▶" : "❚❚";
      const tick = () => { if (vid && !vid.paused) render(); raf = requestAnimationFrame(tick); };
      raf = requestAnimationFrame(tick);
      vid.onseeked = () => render();
    } else {
      src = await new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => no(new Error("That picture didn't load.")); i.src = t.url; }).catch(e => { say(e.message, true); return null; });
      render();
    }
  }
  q("#cPlay", el).onclick = () => { if (!vid) return; if (vid.paused) vid.play(); else vid.pause(); q("#cPlay", el).textContent = vid.paused ? "▶" : "❚❚"; render(); };
  q("#cSeek", el).oninput = e => { if (vid && vid.duration) { vid.pause(); q("#cPlay", el).textContent = "▶"; vid.currentTime = Number(e.target.value) / 1000 * vid.duration; } };
  q("#cWipe", el).oninput = kick;

  // ── scopes ──────────────────────────────────────────────────────────────
  function drawScope(g, img) {
    const sc = q("#cScope", el), vec = scopeKind === "vector";
    const W = vec ? 220 : 560, H = vec ? 220 : 200;
    if (sc.width !== W || sc.height !== H) { sc.width = W; sc.height = H; }
    const x = sc.getContext("2d");
    x.putImageData(g.scope(img, scopeKind, W, H), 0, 0);
    x.save(); x.lineWidth = 1; x.font = "10px Inter, system-ui, sans-serif";
    x.strokeStyle = "rgba(255,255,255,.14)"; x.fillStyle = "rgba(255,255,255,.4)";
    if (vec) {
      const c = (W - 1) / 2, rim = Math.min(W, H) * 0.46;
      for (const r of [rim, rim * 0.75]) { x.beginPath(); x.arc(c, c, r, 0, Math.PI * 2); x.stroke(); }
      x.beginPath(); x.moveTo(c - rim, c); x.lineTo(c + rim, c); x.moveTo(c, c - rim); x.lineTo(c, c + rim); x.stroke();
      // the skin-tone line, and where 75% red, yellow, green, cyan, blue and magenta land
      x.strokeStyle = "rgba(242,193,78,.35)"; x.beginPath(); x.moveTo(c, c);
      const skin = toCbCr([0.85, 0.62, 0.48].map(v => v - (0.2126 * 0.85 + 0.7152 * 0.62 + 0.0722 * 0.48)));
      const n = Math.hypot(skin[0], skin[1]);
      x.lineTo(c + skin[0] / n * rim, c - skin[1] / n * rim); x.stroke();
      [["R", [1, 0, 0]], ["Yl", [1, 1, 0]], ["G", [0, 1, 0]], ["Cy", [0, 1, 1]], ["B", [0, 0, 1]], ["Mg", [1, 0, 1]]].forEach(([l, rgb]) => {
        const [cb, cr] = toCbCr(rgb.map(v => v * 0.75)), px = c + cb / 0.5 * rim, py = c - cr / 0.5 * rim;
        x.strokeStyle = "rgba(255,255,255,.3)"; x.strokeRect(px - 4, py - 4, 8, 8); x.fillText(l, px + 6, py + 3);
      });
    } else if (scopeKind === "histogram") {
      for (let i = 1; i < 4; i++) { const px = Math.round(i / 4 * (W - 1)) + 0.5; x.beginPath(); x.moveTo(px, 0); x.lineTo(px, H); x.stroke(); }
    } else {
      [0, 25, 50, 75, 100].forEach(v => { const y = Math.round((1 - v / 100) * (H - 1)) + 0.5; x.beginPath(); x.moveTo(0, y); x.lineTo(W, y); x.stroke(); x.fillText(String(v), 2, Math.min(H - 2, Math.max(9, y - 2))); });
      if (scopeKind === "parade") [1, 2].forEach(i => { const px = Math.round(i * W / 3) + 0.5; x.beginPath(); x.moveTo(px, 0); x.lineTo(px, H); x.stroke(); });
    }
    x.restore();
  }
  qa("#cScopes button", el).forEach(b => b.onclick = () => {
    scopeKind = b.dataset.scope;
    qa("#cScopes button", el).forEach(x => x.classList.toggle("on", x === b));
    try { localStorage.setItem("colourScope", scopeKind); } catch (e) { /* private mode */ }
    kick();
  });

  // ── the wheels ──────────────────────────────────────────────────────────
  const wheelsOf = () => (L().wheels = L().wheels || { lift: [0, 0, 0, 0], gamma: [0, 0, 0, 0], gain: [0, 0, 0, 0], offset: [0, 0, 0, 0] });
  const tidyWheels = () => { const w = L().wheels; if (w && Object.values(w).every(a => a.every(v => Math.abs(v) < 1e-6))) delete L().wheels; };
  let ring = null;
  function ringImage(size) {
    // the colour each point of a wheel pushes toward, drawn once
    if (ring && ring.width === size) return ring;
    const c = document.createElement("canvas"); c.width = c.height = size;
    const x = c.getContext("2d"), img = x.createImageData(size, size), r = size / 2 - 2;
    for (let y = 0; y < size; y++) for (let xx = 0; xx < size; xx++) {
      const u = (xx - size / 2) / r, v = (size / 2 - y) / r, d = Math.hypot(u, v), i = (y * size + xx) * 4;
      if (d > 1) continue;
      const rgb = toRgb(u * 0.5, v * 0.5).map(k => 0.32 + k * 0.62);
      img.data.set([...rgb.map(k => Math.max(0, Math.min(255, k * 255))), d > 0.97 ? 255 : 210], i);
    }
    x.putImageData(img, 0, 0);
    return (ring = c);
  }
  function drawWheel(key) {
    const [, , rim] = WHEELS.find(w => w[0] === key), c = q(`#cWheels [data-wheel=${key}] canvas`, el), x = c.getContext("2d"), s = c.width;
    const w = (L().wheels || {})[key] || [0, 0, 0, 0];
    x.clearRect(0, 0, s, s); x.drawImage(ringImage(s), 0, 0);
    x.strokeStyle = "rgba(0,0,0,.35)"; x.beginPath(); x.moveTo(s / 2, 8); x.lineTo(s / 2, s - 8); x.moveTo(8, s / 2); x.lineTo(s - 8, s / 2); x.stroke();
    const [cb, cr] = toCbCr(w), r = s / 2 - 2;
    const px = s / 2 + Math.max(-1, Math.min(1, cb / rim)) * r, py = s / 2 - Math.max(-1, Math.min(1, cr / rim)) * r;
    x.lineWidth = 2; x.strokeStyle = "#fff"; x.fillStyle = "rgba(0,0,0,.5)";
    x.beginPath(); x.arc(px, py, 7, 0, Math.PI * 2); x.fill(); x.stroke();
    const nums = q(`#cWheels [data-wheel=${key}] .nums`, el);
    nums.textContent = w.some(v => Math.abs(v) > 5e-4) ? `R ${fix(w[0])} G ${fix(w[1])} B ${fix(w[2])} · ${fix(w[3])}` : "";
    const m = q(`#cWheels [data-wheel=${key}] input`, el); m.value = w[3];
  }
  q("#cWheels", el).innerHTML = WHEELS.map(([k, label, , range]) =>
    `<div class="wheel" data-wheel="${k}"><b>${label}</b><canvas width="264" height="264"></canvas>
      <input type="range" min="${-range}" max="${range}" step="0.005" value="0" title="${label}: darker or brighter (double-click to centre)"><div class="nums"></div></div>`).join("");
  WHEELS.forEach(([k, , rim]) => {
    const box = q(`#cWheels [data-wheel=${k}]`, el), c = q("canvas", box), m = q("input", box);
    const at = e => {
      const b = c.getBoundingClientRect(), r = b.width / 2 - 2;
      let u = (e.clientX - b.left - b.width / 2) / r, v = (b.top + b.height / 2 - e.clientY) / r;
      const d = Math.hypot(u, v); if (d > 1) { u /= d; v /= d; }
      const rgb = toRgb(u * rim, v * rim), w = wheelsOf();
      w[k] = [rgb[0], rgb[1], rgb[2], w[k][3]];
      drawWheel(k); touched();
    };
    c.onpointerdown = e => { capture(c, e); at(e); c.onpointermove = at; };
    c.onpointerup = c.onpointercancel = () => { c.onpointermove = null; tidyWheels(); };
    c.ondblclick = () => { const w = wheelsOf(); w[k] = [0, 0, 0, w[k][3]]; tidyWheels(); drawWheel(k); touched(); };
    m.oninput = () => { const w = wheelsOf(); w[k][3] = Number(m.value); drawWheel(k); touched(); };
    m.ondblclick = () => { const w = wheelsOf(); w[k][3] = 0; tidyWheels(); drawWheel(k); touched(); };
  });
  q("#cWheelsOff", el).onclick = () => { delete L().wheels; WHEELS.forEach(([k]) => drawWheel(k)); touched(); };

  // ── curves ──────────────────────────────────────────────────────────────
  // the same monotone cubic as scrim's (crates/scrim/src/colour.rs), to draw it
  function monotone(pts) {
    const p = pts.slice().sort((a, b) => a[0] - b[0]), n = p.length;
    const x = p.map(v => v[0]), y = p.map(v => v[1]);
    const d = []; for (let i = 0; i < n - 1; i++) d.push((y[i + 1] - y[i]) / Math.max(1e-6, x[i + 1] - x[i]));
    const m = new Array(n).fill(0); m[0] = d[0]; m[n - 1] = d[n - 2];
    for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
    for (let i = 0; i < n - 1; i++) {
      if (d[i] === 0) { m[i] = m[i + 1] = 0; continue; }
      const a = m[i] / d[i], b = m[i + 1] / d[i], s = a * a + b * b;
      if (s > 9) { const t = 3 / Math.sqrt(s); m[i] = t * a * d[i]; m[i + 1] = t * b * d[i]; }
    }
    return v => {
      let i = x.findIndex(xx => xx > v); i = i <= 0 ? (i === 0 ? 0 : n - 2) : i - 1;
      const hh = x[i + 1] - x[i], t = Math.min(1, Math.max(0, (v - x[i]) / hh)), t2 = t * t, t3 = t2 * t;
      return Math.min(1, Math.max(0, (2 * t3 - 3 * t2 + 1) * y[i] + (t3 - 2 * t2 + t) * hh * m[i] + (-2 * t3 + 3 * t2) * y[i + 1] + (t3 - t2) * hh * m[i + 1]));
    };
  }
  let curveCh = "y", dragPt = -1;
  const curveOf = ch => ((L().curves || {})[ch] || [[0, 0], [1, 1]]).map(p => p.slice());
  function setCurve(ch, pts) {
    const c = L().curves = L().curves || {};
    const flat = pts.length === 2 && pts.every(p => Math.abs(p[0] - p[1]) < 1e-4) && pts[0][0] === 0 && pts[1][0] === 1;
    if (flat) delete c[ch]; else c[ch] = pts.map(p => [Math.round(p[0] * 1e4) / 1e4, Math.round(p[1] * 1e4) / 1e4]);
    if (!Object.keys(c).length) delete L().curves;
  }
  const PAD = 14;
  function drawCurve() {
    const c = q("#cCurve", el), x = c.getContext("2d"), s = c.width, span = s - 2 * PAD;
    const X = v => PAD + v * span, Y = v => PAD + (1 - v) * span;
    x.clearRect(0, 0, s, s);
    x.strokeStyle = "rgba(255,255,255,.07)"; x.lineWidth = 1;
    for (let i = 0; i <= 4; i++) { const g = PAD + i / 4 * span; x.beginPath(); x.moveTo(g, PAD); x.lineTo(g, s - PAD); x.moveTo(PAD, g); x.lineTo(s - PAD, g); x.stroke(); }
    x.beginPath(); x.moveTo(X(0), Y(0)); x.lineTo(X(1), Y(1)); x.stroke();
    CURVES.forEach(([k, , colour]) => {
      const pts = curveOf(k), on = k === curveCh;
      if (!on && !(L().curves || {})[k]) return;
      const f = monotone(pts);
      x.strokeStyle = colour; x.globalAlpha = on ? 1 : 0.35; x.lineWidth = on ? 2 : 1.5;
      x.beginPath(); for (let i = 0; i <= 200; i++) { const v = i / 200; i ? x.lineTo(X(v), Y(f(v))) : x.moveTo(X(v), Y(f(v))); } x.stroke();
      if (on) pts.forEach(p => { x.beginPath(); x.arc(X(p[0]), Y(p[1]), 5, 0, Math.PI * 2); x.fillStyle = "#0d0d10"; x.fill(); x.stroke(); });
      x.globalAlpha = 1;
    });
  }
  (() => {
    const c = q("#cCurve", el);
    const at = e => { const b = c.getBoundingClientRect(), k = c.width / b.width, span = c.width - 2 * PAD;
      return [Math.min(1, Math.max(0, ((e.clientX - b.left) * k - PAD) / span)), Math.min(1, Math.max(0, 1 - ((e.clientY - b.top) * k - PAD) / span)), k]; };
    const near = (pts, [x, y, k]) => pts.findIndex(p => Math.hypot((p[0] - x) * (c.width - 2 * PAD), (p[1] - y) * (c.width - 2 * PAD)) < 12 * Math.max(1, k));
    c.onpointerdown = e => {
      capture(c, e);
      const pts = curveOf(curveCh), p = at(e);
      dragPt = near(pts, p);
      if (dragPt < 0) {
        if (pts.length >= 16) return say("A curve takes 16 points at most.", true);
        pts.push([p[0], p[1]]); pts.sort((a, b) => a[0] - b[0]);
        dragPt = pts.findIndex(q2 => q2[0] === p[0] && q2[1] === p[1]);
        setCurve(curveCh, pts); drawCurve(); touched();
      }
    };
    c.onpointermove = e => {
      if (dragPt < 0) return;
      const pts = curveOf(curveCh), [x, y] = at(e), last = pts.length - 1;
      const lo = dragPt === 0 ? 0 : pts[dragPt - 1][0] + 0.01, hi = dragPt === last ? 1 : pts[dragPt + 1][0] - 0.01;
      pts[dragPt] = [dragPt === 0 ? 0 : dragPt === last ? 1 : Math.min(hi, Math.max(lo, x)), y];
      setCurve(curveCh, pts); drawCurve(); touched();
    };
    c.onpointerup = c.onpointercancel = () => { dragPt = -1; };
    c.ondblclick = e => {
      const pts = curveOf(curveCh), i = near(pts, at(e));
      if (i > 0 && i < pts.length - 1) { pts.splice(i, 1); setCurve(curveCh, pts); drawCurve(); touched(); }
    };
  })();
  qa("#cCurveCh button", el).forEach(b => b.onclick = () => { curveCh = b.dataset.ch; qa("#cCurveCh button", el).forEach(x => x.classList.toggle("on", x === b)); drawCurve(); });
  q("#cCurveOff", el).onclick = () => { setCurve(curveCh, [[0, 0], [1, 1]]); drawCurve(); touched(); };

  // ── LUT and CDL ─────────────────────────────────────────────────────────
  async function syncLut() {
    const look = L(), sel = q("#cLut", el), cur = look.lut3d;
    const list = await GG().listLuts().catch(() => []);
    sel.innerHTML = `<option value="">No LUT</option>` + list.map(m => `<option value="${h(m.id)}">${h(m.title || m.name)}${m.kind === "1D" ? " (1D)" : ""}</option>`).join("");
    sel.value = cur ? cur.of || cur.id : "";
    // a LUT read into the sliders is them plus its rest: Amount is all of it
    q("#cLutOpts", el).classList.toggle("hidden", !cur || !!cur.of);
    q("#cLutNote", el).innerHTML = GG().lutNote();
    if (cur) {
      q("#cLutMix", el).value = cur.mix == null ? 1 : cur.mix;
      q("#cLutMix", el).nextElementSibling.textContent = Number(q("#cLutMix", el).value).toFixed(2);
      qa("#cLutWhere button", el).forEach(b => b.classList.toggle("on", (b.dataset.first === "1") === !!cur.first));
    }
    const c = look.cdl;
    q("#cCdl", el).innerHTML = c ? `CDL: slope ${c.slope.map(v => v.toFixed(3)).join(" ")} · offset ${c.offset.map(v => v.toFixed(3)).join(" ")} · power ${c.power.map(v => v.toFixed(3)).join(" ")} · saturation ${Number(c.sat).toFixed(2)}. It goes before everything else. <button class="linkbtn" id="cCdlOff">Remove</button>` : "";
    const off = q("#cCdlOff", el); if (off) off.onclick = () => { delete L().cdl; syncLut(); touched(); };
  }
  q("#cLutPick", el).onclick = () => q("#cLutFile", el).click();
  q("#cLutFile", el).onchange = async e => {
    const f = e.target.files[0]; e.target.value = "";
    if (!f) return;
    try { lutDone(await GG().importGrade(f)); } catch (err) { say(err.message, true); }
  };
  // a LUT or CDL moves the sliders, the wheels and the stock: show them all
  function lutDone(msg) { syncAll(); touched(); if (msg) say(msg); }
  q("#cLut", el).onchange = async e => {
    try { lutDone(await GG().useLut(e.target.value)); } catch (err) { say(err.message, true); syncLut(); }
  };
  q("#cLutNote", el).onclick = async e => {
    const b = e.target.closest("[data-lut]");
    if (!b) return;
    try { lutDone(await GG().lutAction(b.dataset.lut)); } catch (err) { say(err.message, true); }
  };
  q("#cLutMix", el).oninput = e => { if (L().lut3d) { L().lut3d.mix = Number(e.target.value); e.target.nextElementSibling.textContent = Number(e.target.value).toFixed(2); touched(); } };
  qa("#cLutWhere button", el).forEach(b => b.onclick = () => { if (!L().lut3d) return; L().lut3d.first = b.dataset.first === "1"; syncLut(); touched(); });

  // ── look, basics and film ───────────────────────────────────────────────
  const fill = (sel, g) => { sel.innerHTML = ((CAT.groups[g] || {}).options || []).map(o => `<option value="${h(o.id)}">${h(o.label)}</option>`).join(""); };
  fill(q("#cFilm", el), "film"); fill(q("#cGrade", el), "grade");
  const slider = ([k, label, mn, mx, st]) => `<label class="sl"><span>${label}</span><input type="range" data-k="${k}" min="${mn}" max="${mx}" step="${st}"><output></output></label>`;
  q("#cBasics", el).innerHTML = BASICS.map(slider).join("");
  q("#cFilmFx", el).innerHTML = FILM.map(slider).join("");
  qa("#cBasics input, #cFilmFx input", el).forEach(i => i.oninput = () => {
    L()[i.dataset.k] = Number(i.value); i.nextElementSibling.textContent = Number(i.value).toFixed(2); touched();
  });
  function syncSliders() {
    qa("#cBasics input, #cFilmFx input", el).forEach(i => { const v = L()[i.dataset.k]; i.value = v; i.nextElementSibling.textContent = Number(v).toFixed(2); });
  }
  q("#cFilm", el).onchange = q("#cGrade", el).onchange = async () => {
    await GG().setPreset(q("#cFilm", el).value, q("#cGrade", el).value);
    syncSliders(); touched();
  };
  function syncAll() {
    const p = GG().preset();
    q("#cFilm", el).value = p.film; q("#cGrade", el).value = p.grade;
    syncSliders(); WHEELS.forEach(([k]) => drawWheel(k)); drawCurve(); syncLut();
  }

  // ── out ─────────────────────────────────────────────────────────────────
  q("#cBake", el).onclick = () => GG().bake(q("#cBake", el));
  q("#cCube", el).onclick = () => GG().saveCube();
  q("#cStill", el).onclick = async () => {
    const g = await window.goboReady, t = ST().current();
    if (!g || !src) return;
    const s = src, w0 = s.naturalWidth || s.videoWidth, h0 = s.naturalHeight || s.videoHeight, k = Math.min(1, 4096 / w0);
    const c = document.createElement("canvas"); c.width = Math.round(w0 * k); c.height = Math.round(h0 * k);
    const x = c.getContext("2d", { willReadFrequently: true }); x.drawImage(s, 0, 0, c.width, c.height);
    const img = x.getImageData(0, 0, c.width, c.height); g.grade(img, L(), 7); x.putImageData(img, 0, 0);
    c.toBlob(b => { const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = `${(t && (t.shot + (t.letter || ""))) || "take"}-graded.png`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); }, "image/png");
  };
  q("#cReset", el).onclick = async () => { await GG().reset(); delete L().wheels; delete L().curves; delete L().cdl; delete L().lut3d; syncAll(); touched(); };

  // ── open and close ──────────────────────────────────────────────────────
  async function open() {
    await GG().ready();
    try { const s = localStorage.getItem("colourScope"); if (SCOPES.some(x => x[0] === s)) scopeKind = s; } catch (e) { /* private mode */ }
    qa("#cScopes button", el).forEach(b => b.classList.toggle("on", b.dataset.scope === scopeKind));
    el.classList.remove("hidden"); document.body.classList.add("colouropen");
    q("#cNote", el).textContent = "The Lab keeps this grade when you close.";
    syncAll();
    await loadSource();
  }
  function close(simple) {
    if (el.classList.contains("hidden")) return;
    stop(); el.classList.add("hidden"); document.body.classList.remove("colouropen");
    if (simple) GG().reopen();
    GG().changed();
  }
  q("#cClose", el).onclick = () => close();
  q("#cDone", el).onclick = () => close();
  q("#cSimple", el).onclick = () => close(true);
  document.addEventListener("keydown", e => {
    if (el.classList.contains("hidden")) return;
    if (e.key === "Escape") { e.stopPropagation(); close(); }
    if (e.key === " " && !/^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(document.activeElement.tagName)) { e.preventDefault(); q("#cPlay", el).click(); }
  }, true);
  window.goboColour = { open, close, render };
})();
