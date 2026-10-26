// Gobo Lab — the studio's Rust tools in a drawer (studio/static/gobo.wasm via gobo.js).
//
//   Steal the look   drop any frame: Rust reads its tone, colour, palette, grain,
//                    letterbox, focus and light, names the nearest stock and grade,
//                    applies them to the shot, and exports the look as a .cube.
//   Draw the move    scribble on the frame with a camera pen and a subject pen;
//                    Rust reads the strokes as the move and the action.
//   Grade            the take through a film stock and grade, live, grain dancing
//                    on video; save it as a graded take (every frame, in Rust on
//                    the server: studio/rust.py runs this same gobo.wasm), or
//                    download the graded still or the .cube. A .cube or a CDL
//                    from any editor goes on the grade too, and Advanced opens
//                    the full-screen Colour page (gobo_colour.js) on it.
//   Continuity       the script supervisor: each cut's last and first frames
//                    compared, what jumped and how to fix it, the odd shot out.
//
// It changes the shot only through window.STUDIO (studio_app.js).
(function () {
  "use strict";
  const ST = () => window.STUDIO;
  const q = (sel, el) => (el || document).querySelector(sel);
  const qa = (sel, el) => Array.from((el || document).querySelectorAll(sel));
  const h = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const say = (m, bad) => (ST() && ST().toast ? ST().toast(m, bad) : console.log(m));
  const optLabel = (g, id) => { const o = ((CAT.groups[g] || {}).options || []).find(o => o.id === id); return o ? o.label : id; };
  const hasOpt = (g, id) => ((CAT.groups[g] || {}).options || []).some(o => o.id === id);

  // ── frames ──────────────────────────────────────────────────────────────
  function pixels(src, maxW) {
    const w0 = src.naturalWidth || src.videoWidth || src.width, h0 = src.naturalHeight || src.videoHeight || src.height;
    const k = Math.min(1, (maxW || 960) / w0);
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(w0 * k)); c.height = Math.max(1, Math.round(h0 * k));
    const g = c.getContext("2d", { willReadFrequently: true });
    g.drawImage(src, 0, 0, c.width, c.height);
    return g.getImageData(0, 0, c.width, c.height);
  }
  const image = url => new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => no(new Error("That picture didn't load.")); i.src = url; });
  async function clipFrame(url, at, maxW) {
    const v = document.createElement("video");
    Object.assign(v, { muted: true, preload: "auto", playsInline: true, src: url });
    await new Promise((ok, no) => { v.onloadeddata = ok; v.onerror = () => no(new Error("That clip didn't load.")); });
    const t = at === "first" ? 0.04 : at === "last" ? Math.max(0, v.duration - 0.1) : at;
    await new Promise(ok => { v.onseeked = ok; v.currentTime = t; });
    return pixels(v, maxW);
  }
  const takeFrame = (t, at, maxW) => t.kind === "video" ? clipFrame(t.url, at, maxW) : image(t.url).then(i => pixels(i, maxW));
  function paint(canvas, img) {
    if (canvas.width !== img.width || canvas.height !== img.height) { canvas.width = img.width; canvas.height = img.height; }
    canvas.getContext("2d").putImageData(img, 0, 0);
  }
  // one scratch canvas for the live grade: no allocation per frame
  const work = document.createElement("canvas"), wctx = work.getContext("2d", { willReadFrequently: true });
  function grab(src, maxW) {
    const w0 = src.naturalWidth || src.videoWidth || src.width, h0 = src.naturalHeight || src.videoHeight || src.height;
    const k = Math.min(1, maxW / w0), w = Math.max(1, Math.round(w0 * k)), hh = Math.max(1, Math.round(h0 * k));
    if (work.width !== w || work.height !== hh) { work.width = w; work.height = hh; }
    wctx.drawImage(src, 0, 0, w, hh);
    return wctx.getImageData(0, 0, w, hh);
  }
  function save(blob, name) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
  const done = () => (ST() ? ST().takes() : []).filter(t => t.status === "done");

  // ── the drawer ──────────────────────────────────────────────────────────
  const style = document.createElement("style");
  style.textContent = `
/* the studio's failed-take cards sit in the same corner: beside the Lab while it's open, not on top of it */
body.labopen #fails { right: calc(min(440px, 100vw) + 16px); }
@media (max-width: 700px) { body.labopen #fails { display: none; } }
#lab { position: fixed; top: 58px; right: 0; bottom: 0; width: min(440px, 100vw); z-index: 40; background: var(--panel);
  border-left: 1px solid var(--line); display: flex; flex-direction: column; box-shadow: -12px 0 40px #0008; }
#lab .labhead { display: flex; align-items: center; gap: 8px; padding: 12px 14px 8px; }
#lab .labhead b { font-size: 1rem; letter-spacing: .02em; }
.rustpill { font-size: .62rem; font-weight: 700; letter-spacing: .06em; padding: 2px 8px; border-radius: 99px;
  color: #f2c14e; border: 1px solid #4d4116; background: #2a2410; }
#lab .labtabs { margin: 0 14px 8px; display: flex; }
#lab .labtabs button { flex: 1; font-size: .74rem; }
#lab .labbody { overflow: auto; padding: 4px 14px 24px; flex: 1; }
#lab .drop { border: 1.5px dashed var(--line2); border-radius: var(--radius); padding: 18px; text-align: center; cursor: pointer; }
#lab .drop.over { border-color: var(--accent); background: #2a241033; }
#lab canvas.pic { width: 100%; height: auto; border-radius: 10px; display: block; background: #000; }
#lab .phrase { font-size: .95rem; line-height: 1.4; margin: 10px 0; }
#lab .pal { display: flex; height: 26px; border-radius: 8px; overflow: hidden; margin: 8px 0 12px; }
#lab .pal i { display: block; height: 100%; }
#lab .sug { display: grid; grid-template-columns: 92px 1fr; gap: 6px 10px; font-size: .8rem; margin-bottom: 12px; }
#lab .sug dt { color: var(--muted); }
#lab .sug dd { margin: 0; } #lab .sug dd small { color: var(--dim); display: block; }
#lab .near { font-size: .76rem; color: var(--muted); margin: 6px 0 12px; }
#lab .near span { display: inline-block; margin-right: 10px; }
#lab .row { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0; align-items: center; }
#lab .meas { font-size: .72rem; color: var(--dim); margin-top: 10px; }
#lab .meas summary { cursor: pointer; color: var(--muted); }
#lab .meas table { width: 100%; border-collapse: collapse; margin-top: 6px; }
#lab .meas td { padding: 2px 0; } #lab .meas td:last-child { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
#lab .pens button.on.cam { background: #f2c14e; color: #1b1505; }
#lab .pens button.on.sub { background: #7ee0d0; color: #0b1a17; }
#lab .drawwrap { position: relative; touch-action: none; }
#lab .drawwrap canvas { width: 100%; display: block; border-radius: 10px; cursor: crosshair; }
#lab .read { font-size: .84rem; margin: 10px 0; min-height: 2.4em; }
#lab .read b { color: var(--accent); }
#lab .read .act { color: #7ee0d0; display: block; margin-top: 4px; }
#lab .sl { display: grid; grid-template-columns: 82px 1fr 38px; gap: 6px; align-items: center; font-size: .76rem; margin: 3px 0; }
#lab .sl output { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
#lab .gradewrap { position: relative; }
#lab .wipe { width: 100%; margin: 6px 0 2px; }
#lab .perf { font-size: .7rem; color: var(--dim); }
#lab .cut { display: grid; grid-template-columns: 64px 64px 1fr; gap: 8px; align-items: start; padding: 10px 0; border-top: 1px solid var(--line); }
#lab .cut canvas { width: 64px; border-radius: 6px; }
#lab .verdict { font-size: .66rem; font-weight: 700; padding: 1px 7px; border-radius: 99px; text-transform: uppercase; letter-spacing: .05em; }
#lab .verdict.match { background: #1d3524; color: var(--ok); } #lab .verdict.drift { background: #3a3014; color: var(--warn); }
#lab .verdict.jump { background: #3d1a16; color: var(--bad); }
#lab .cut ul { margin: 4px 0 0; padding-left: 16px; font-size: .76rem; color: var(--muted); }
#lab .cut .fix { color: var(--text); }
#lab .odd { margin: 10px 0; padding: 10px; border-radius: 10px; background: #3a301455; font-size: .8rem; }
#lab .linkbtn, #colour .linkbtn { background: none; border: 0; padding: 0; color: var(--accent); cursor: pointer; font: inherit; text-decoration: underline; }
`;
  document.head.appendChild(style);

  const lab = document.createElement("aside");
  lab.id = "lab"; lab.className = "hidden"; lab.setAttribute("aria-label", "Lab");
  lab.innerHTML = `
  <div class="labhead"><b>Lab</b><span class="rustpill" id="labRust" title="These tools run in your browser, in Rust compiled to WebAssembly: nothing is uploaded">Rust · WebAssembly</span>
    <span style="flex:1"></span><button class="iconbtn" id="labClose" title="Close (Esc)">✕</button></div>
  <div class="seg labtabs" role="tablist">
    <button data-tab="look" class="on">Steal the look</button><button data-tab="draw">Draw the move</button>
    <button data-tab="grade">Grade</button><button data-tab="cont">Continuity</button></div>
  <div class="labbody">
    <section data-pane="look">
      <div class="drop" id="lookDrop"><input type="file" id="lookFile" accept="image/*" hidden>
        <b>Drop a frame from any film or photo</b><div class="hint">or paste it, pick a file, or read the take on screen</div>
        <div class="row" style="justify-content:center"><button class="btn small" id="lookPick">Pick a picture</button><button class="btn small" id="lookTake">This take</button></div></div>
      <div id="lookOut"></div>
    </section>
    <section data-pane="draw" class="hidden">
      <p class="hint">Draw how the camera moves (gold) and where things go (teal). A line into the middle pushes in, a sweep tracks, an arc arcs, a loop orbits, a spiral dives into the eyes, a scribble is handheld. Draw fast for a whip or a crash zoom.</p>
      <div class="row pens"><div class="seg"><button class="on cam" data-pen="camera">🎥 Camera</button><button class="sub" data-pen="subject">🏃 Subject</button></div>
        <input class="field" id="drawWho" placeholder="who (the rider)" style="flex:1;min-width:90px">
        <button class="btn small" id="drawUndo">Undo</button><button class="btn small" id="drawClear">Clear</button></div>
      <div class="drawwrap"><canvas id="drawCv"></canvas></div>
      <div class="read" id="drawRead"><span class="hint">Nothing drawn yet.</span></div>
      <div class="row"><button class="btn primary small" id="drawShoot" disabled title="Shoot the take on screen again with this move (a still is animated from)">Shoot it</button>
        <button class="btn small" id="drawApply" disabled title="Put the move and the action on the shot, and shoot it yourself later">Just set it</button><span class="hint" id="drawWhy"></span></div>
    </section>
    <section data-pane="grade" class="hidden">
      <div class="row"><select class="field" id="gFilm" title="Film stock"></select><select class="field" id="gGrade" title="Grade"></select>
        <button class="btn small" id="gShot" title="The stock and grade the shot is set to">From shot</button></div>
      <div class="gradewrap"><canvas class="pic" id="gCv"></canvas></div>
      <input type="range" class="wipe" id="gWipe" min="0" max="100" value="0" title="Drag to compare with the original">
      <div class="perf" id="gPerf"></div>
      <div id="gSliders"></div>
      <div class="row"><select class="field" id="gLut" title="A LUT from Resolve, Premiere, a LUT pack or a camera maker" style="flex:1;min-width:120px"><option value="">No LUT</option></select>
        <input type="file" id="gLutFile" accept=".cube,.cdl,.cc,.ccc,.xml" hidden>
        <button class="btn small" id="gLutPick" title="A .cube LUT, or a colourist's CDL (.cdl, .cc)">Import LUT or CDL</button></div>
      <label class="sl hidden" id="gLutMixRow"><span>LUT amount</span><input type="range" id="gLutMix" min="0" max="1" step="0.01" value="1"><output></output></label>
      <div class="hint hidden" id="gLutNote"></div>
      <div class="hint hidden" id="gCdlNote"></div>
      <div class="row"><button class="btn small" id="gAdv" title="Lift, gamma, gain and offset wheels, curves and scopes, full screen">Advanced: wheels, curves, scopes →</button></div>
      <div class="row"><button class="btn small primary" id="gBake" title="Every frame through this look, in Rust, as a new take beside the original">Grade the take</button>
        <button class="btn small" id="gStill">Download graded still</button><button class="btn small" id="gCube">Download .cube</button>
        <button class="btn small" id="gReset">Reset</button></div>
      <p class="hint">The .cube is the same table the preview uses: load it in Resolve, Premiere or Final Cut and the colour matches. Grain, halation and the vignette need the whole picture, so they stay out of the LUT.</p>
    </section>
    <section data-pane="cont" class="hidden">
      <p class="hint">The script supervisor reads where each take ends and the next begins. A cut to a new setup may change the light; it must keep the grade (black level, contrast, colour, grain, the shape of the frame). A shot that continues the last one must carry everything, the picture included.</p>
      <label class="hint row"><input type="checkbox" id="cCont"> These takes continue one another (the picture must carry on, not just the look)</label>
      <div class="row"><button class="btn primary small" id="cRun">Check the sequence</button><span class="hint" id="cStatus"></span></div>
      <div id="cOut"></div>
    </section>
  </div>`;
  document.body.appendChild(lab);

  const chip = document.createElement("button");
  chip.className = "chipbtn"; chip.id = "labBtn"; chip.title = "Steal a look, draw a move, grade a take, check continuity: Rust in your browser";
  chip.innerHTML = `Lab <span class="rustpill" style="margin-left:4px">Rust</span>`;
  const credits = q("#credits");
  if (credits && credits.parentNode) credits.parentNode.insertBefore(chip, credits);

  let tab = "look";
  function open(which) {
    lab.classList.remove("hidden");
    document.body.classList.add("labopen");
    if (which) setTab(which); else setTab(tab);
  }
  function close() { lab.classList.add("hidden"); document.body.classList.remove("labopen"); stopVideo(); }
  function setTab(t) {
    tab = t;
    qa(".labtabs button", lab).forEach(b => b.classList.toggle("on", b.dataset.tab === t));
    qa("[data-pane]", lab).forEach(p => p.classList.toggle("hidden", p.dataset.pane !== t));
    if (t !== "grade") stopVideo();
    if (t === "draw") drawSetup();
    if (t === "grade") gradeSetup();
  }
  chip.onclick = () => lab.classList.contains("hidden") ? open() : close();
  q("#labClose", lab).onclick = close;
  qa(".labtabs button", lab).forEach(b => b.onclick = () => setTab(b.dataset.tab));
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !lab.classList.contains("hidden")) close(); });
  window.goboReady.then(g => {
    if (!g) { chip.disabled = true; chip.title = "The Lab needs WebAssembly"; return; }
    const want = new URLSearchParams(location.search).get("lab");
    if (want) setTimeout(() => open(["look", "draw", "grade", "cont"].includes(want) ? want : "look"), 900);
    const a = g.about();
    q("#labRust", lab).title += ` · crates: ${a.crates.join(", ")}`;
  });

  // ── Steal the look ──────────────────────────────────────────────────────
  let stolen = null;
  async function steal(img, label) {
    const g = await window.goboReady;
    if (!g) return say("The Lab needs WebAssembly.", true);
    const t0 = performance.now();
    const r = g.read(img);
    const ms = performance.now() - t0;
    if (r.error) return say(r.error, true);
    stolen = Object.assign(r, { label, ms, img });
    renderLook();
  }
  function renderLook() {
    const { reading: R, suggest: S, ms, img, label } = stolen;
    const out = q("#lookOut", lab);
    const rows = [];
    const add = (g, text) => { if (S.direction[g]) rows.push(`<dt>${h((CAT.groups[g] || {}).title || g)}</dt><dd>${h(optLabel(g, S.direction[g]))}${text ? `<small>${h(text)}</small>` : ""}</dd>`); };
    ["film", "grade", "light", "aperture", "compose", "lens"].forEach(g => add(g, S.why[g]));
    if (S.aspect) rows.push(`<dt>Shape</dt><dd>${h(S.aspect)}<small>${h(R.frame.letterbox ? `letterboxed ${R.frame.aspect_name || ""}` : "the frame's own shape")}</small></dd>`);
    out.innerHTML = `
      <canvas class="pic" id="lookCv" style="margin-top:12px"></canvas>
      <div class="phrase">${h(S.phrase)}</div>
      <div class="pal">${R.palette.map(p => `<i style="background:${p.hex};flex:${Math.max(0.04, p.share)}" title="${p.hex} · ${p.name} · ${Math.round(p.share * 100)}%"></i>`).join("")}</div>
      <dl class="sug">${rows.join("")}</dl>
      <div class="near">Nearest: ${S.nearest.map(n => `<span>${h(optLabel("film", n.film))} + ${h(optLabel("grade", n.grade))} <b>${Math.round(n.fit * 100)}%</b></span>`).join("")}</div>
      <div class="row"><button class="btn small primary" id="lookApply">Apply to the shot</button>
        <button class="btn small" id="lookGrade">Grade the take with it</button><button class="btn small" id="lookCube">Download .cube</button></div>
      <details class="meas"><summary>What Rust measured · ${img.width}×${img.height} in ${ms.toFixed(1)} ms</summary><table>
        ${[["Key", R.tone.key], ["Black · median · white", `${R.tone.black} · ${R.tone.median} · ${R.tone.white}`], ["Contrast (P10–P90)", R.tone.spread],
           ["Crushed / clipped", `${Math.round(R.tone.crushed * 100)}% / ${Math.round(R.tone.clipped * 100)}%`], ["Colour (chroma)", R.colour.chroma],
           ["Cast · shadows · highlights", `${R.colour.cast} · ${R.colour.shadows} · ${R.colour.highlights}`], ["Grain", R.grain], ["Vignette (corners ÷ centre)", R.vignette],
           ["Focus centre ÷ edges", (R.focus.centre / Math.max(1e-4, R.focus.edges)).toFixed(2)], ["Light right − left", R.light.right_minus_left],
           ["Subject at", `${Math.round(R.subject[0] * 100)}% · ${Math.round(R.subject[1] * 100)}%`], ["Frame", `${R.frame.aspect}${R.frame.letterbox ? " (letterbox)" : ""}`],
           ["Fingerprint", R.fingerprint]].map(([k, v]) => `<tr><td>${h(k)}</td><td>${h(v)}</td></tr>`).join("")}</table></details>`;
    paint(q("#lookCv", out), img);
    q("#lookApply", out).onclick = () => {
      const patch = {};
      Object.entries(S.direction).forEach(([g, id]) => { if (hasOpt(g, id)) patch[g] = id; });
      ST().setDirection(patch);
      if (S.aspect) ST().setAspect(S.aspect);
      say(`Look applied: ${Object.entries(patch).map(([g, id]) => optLabel(g, id)).join(", ")}.`);
    };
    q("#lookCube", out).onclick = async () => {
      const g = await window.goboReady;
      save(new Blob([g.cube(Object.assign({}, S.look, { title: `Stolen from ${label}` }), 33)], { type: "text/plain" }), "gobo-stolen-look.cube");
    };
    q("#lookGrade", out).onclick = () => { gradeLook = Object.assign({}, S.look, { grain: S.look.grain, halation: 0.15 }); gradeFrom = "stolen"; open("grade"); };
  }
  const drop = q("#lookDrop", lab);
  const readFile = async f => { if (!f || !/^image\//.test(f.type)) return say("That isn't a picture.", true); const url = URL.createObjectURL(f); try { await steal(pixels(await image(url), 1600), f.name || "a picture"); } finally { URL.revokeObjectURL(url); } };
  q("#lookPick", lab).onclick = e => { e.stopPropagation(); q("#lookFile", lab).click(); };
  q("#lookFile", lab).onchange = e => readFile(e.target.files[0]);
  drop.onclick = () => q("#lookFile", lab).click();
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); readFile(e.dataTransfer.files[0]); };
  document.addEventListener("paste", e => {
    if (lab.classList.contains("hidden") || tab !== "look") return;
    const f = Array.from(e.clipboardData.files || [])[0];
    if (f) { e.preventDefault(); readFile(f); }
  });
  q("#lookTake", lab).onclick = async e => {
    e.stopPropagation();
    const t = ST().current();
    if (!t || t.status !== "done") return say("Pick a finished take first.", true);
    try { await steal(await takeFrame(t, t.kind === "video" ? 0.5 : "first", 1600), `take ${t.shot || ""}${t.letter || ""}`); }
    catch (err) { say(err.message, true); }
  };

  // ── Draw the move ───────────────────────────────────────────────────────
  const cv = q("#drawCv", lab), ctx = cv.getContext("2d");
  let pen = "camera", strokes = [], live = null, bg = null, direction = null;
  qa("[data-pen]", lab).forEach(b => b.onclick = () => { pen = b.dataset.pen; qa("[data-pen]", lab).forEach(x => x.classList.toggle("on", x === b)); });
  async function drawSetup() {
    const t = ST().current();
    const ratio = (() => { const [w, hh] = String(ST().aspect() || "16:9").split(":").map(Number); return w && hh ? w / hh : 16 / 9; })();
    bg = null;
    if (t && t.status === "done") {
      try { bg = await takeFrame(t, "first", 900); } catch (e) { bg = null; }
    }
    cv.width = 880; cv.height = Math.round(880 / (bg ? bg.width / bg.height : ratio));
    redraw();
  }
  function arrow(pts, colour) {
    if (pts.length < 2) return;
    ctx.strokeStyle = colour; ctx.fillStyle = colour; ctx.lineWidth = 5; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); pts.forEach(p => ctx.lineTo(p[0], p[1])); ctx.stroke();
    const a = pts[pts.length - 1], b = pts[Math.max(0, pts.length - 6)];
    const ang = Math.atan2(a[1] - b[1], a[0] - b[0]);
    ctx.beginPath(); ctx.moveTo(a[0], a[1]);
    ctx.lineTo(a[0] - 22 * Math.cos(ang - 0.45), a[1] - 22 * Math.sin(ang - 0.45));
    ctx.lineTo(a[0] - 22 * Math.cos(ang + 0.45), a[1] - 22 * Math.sin(ang + 0.45)); ctx.closePath(); ctx.fill();
  }
  function redraw() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (bg) {
      const tmp = document.createElement("canvas"); paint(tmp, bg);
      ctx.globalAlpha = 0.55; ctx.drawImage(tmp, 0, 0, cv.width, cv.height); ctx.globalAlpha = 1;
    } else { ctx.fillStyle = "#101014"; ctx.fillRect(0, 0, cv.width, cv.height); }
    ctx.strokeStyle = "#ffffff22"; ctx.lineWidth = 1;
    for (const f of [1 / 3, 2 / 3]) {
      ctx.beginPath(); ctx.moveTo(cv.width * f, 0); ctx.lineTo(cv.width * f, cv.height); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, cv.height * f); ctx.lineTo(cv.width, cv.height * f); ctx.stroke();
    }
    [...strokes, ...(live ? [live] : [])].forEach(s => arrow(s.points, s.pen === "camera" ? "#f2c14e" : "#7ee0d0"));
  }
  const at = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height]; };
  let t0 = 0;
  cv.onpointerdown = e => { cv.setPointerCapture(e.pointerId); t0 = performance.now(); live = { pen, label: pen === "subject" ? q("#drawWho", lab).value : "", points: [[...at(e), 0]] }; redraw(); };
  cv.onpointermove = e => { if (!live) return; live.points.push([...at(e), Math.round(performance.now() - t0)]); redraw(); };
  cv.onpointerup = () => { if (!live) return; strokes.push(live); live = null; redraw(); readStrokes(); };
  q("#drawUndo", lab).onclick = () => { strokes.pop(); redraw(); readStrokes(); };
  q("#drawClear", lab).onclick = () => { strokes = []; redraw(); readStrokes(); };
  async function readStrokes() {
    const g = await window.goboReady;
    const out = q("#drawRead", lab);
    if (!strokes.length) { direction = null; out.innerHTML = `<span class="hint">Nothing drawn yet.</span>`; q("#drawApply", lab).disabled = q("#drawShoot", lab).disabled = true; q("#drawWhy", lab).textContent = ""; return; }
    direction = g.direct({ w: cv.width, h: cv.height, strokes });
    const c = direction.camera;
    const d = drawnMove(c);
    const named = c && (hasOpt("move", c.move) ? optLabel("move", c.move)
      : `${String(c.phrase || c.move).split(":")[0]} (${d.move ? `shot as ${optLabel("move", d.move).toLowerCase()}, with the move in words` : "in words"})`);
    out.innerHTML = (c ? `Camera: <b>${h(named)}</b> — ${h(c.phrase)}` : `<span class="hint">No camera stroke: the camera stays as set.</span>`)
      + (direction.action ? `<span class="act">${h(direction.action)}</span>` : "");
    q("#drawWhy", lab).textContent = c ? String(c.why || "").replace(/\ba ([aeiou])/gi, "an $1") : "";
    q("#drawApply", lab).disabled = q("#drawShoot", lab).disabled = false;
  }
  // The drawing reader knows more moves than a studio may offer (the in-house
  // one has 15): a move that isn't in this studio's list takes the nearest
  // one, and the drawing's own words go into the scene, so the model still
  // gets the move that was drawn (2026-10-26: bullet time was dropped silently)
  const NEAREST = { bullet: "orbit", orbit360: "orbit", arc: "orbit", tiltdown: "tilt", cranedown: "crane", fpv: "drone",
                    eyesin: "push", earth: "pull", snorri: "handheld", headtrack: "track", dutchroll: "handheld",
                    rack: "static", robo: "dolly", hyperlapse: "track" };
  function drawnMove(c) {
    if (!c) return { move: "", words: "" };
    if (hasOpt("move", c.move)) return { move: c.move, words: "" };
    const near = NEAREST[c.move] && hasOpt("move", NEAREST[c.move]) ? NEAREST[c.move] : "";
    const p = String(c.phrase || "").trim().replace(/[.]+$/, "");
    return { move: near, words: p ? p[0].toUpperCase() + p.slice(1) + "." : "" };
  }
  const drawnText = d => [d.words, direction.action].filter(Boolean).join(" ");
  q("#drawApply", lab).onclick = () => {
    if (!direction) return;
    const d = drawnMove(direction.camera), text = drawnText(d);
    if (d.move) ST().setDirection({ move: d.move });
    if (text) ST().addToScene(text);
    say(!d.move && !text ? "Nothing in this drawing changes the shot." : `${d.move ? `Camera: ${optLabel("move", d.move)}` : "Camera as set"}${text ? "; the drawing's words went into the scene" : ""}. Generate when you're ready.`);
  };
  q("#drawShoot", lab).onclick = async () => {
    if (!direction) return;
    const d = drawnMove(direction.camera), text = drawnText(d), t = ST().current();
    if (!d.move && !text) return say("Nothing in this drawing changes the shot, so nothing was shot. Draw a camera or a subject stroke.", true);
    close();
    await ST().redo(t, d.move, text);
    ST().toast(`Shooting it again${d.move ? ` with a ${optLabel("move", d.move).toLowerCase()}` : ""}${d.words ? ", and the drawn move in the scene" : ""}. The new take lands beside the old one.`);
  };

  // ── Grade ───────────────────────────────────────────────────────────────
  const gcv = q("#gCv", lab), gctx = gcv.getContext("2d", { willReadFrequently: true });
  let gradeLook = null, gradeFrom = "shot", gsrc = null, gorig = null, vid = null, raf = 0, frameNo = 0, perfMs = 0;
  const SLIDERS = [["amount", "Amount", 0, 1, 0.01], ["grain", "Grain", 0, 1, 0.01], ["halation", "Halation", 0, 1, 0.01], ["vignette", "Vignette", 0, 0.8, 0.01],
    ["fade", "Fade", 0, 0.25, 0.005], ["contrast", "Contrast", 0.6, 1.6, 0.01], ["sat", "Colour", 0, 1.6, 0.01], ["temp", "Warmth", -1, 1, 0.02]];
  function fillSelect(sel, g) { sel.innerHTML = (CAT.groups[g].options || []).map(o => `<option value="${h(o.id)}">${h(o.label)}</option>`).join(""); }
  fillSelect(q("#gFilm", lab), "film"); fillSelect(q("#gGrade", lab), "grade");
  q("#gSliders", lab).innerHTML = SLIDERS.map(([k, label, mn, mx, st]) =>
    `<label class="sl"><span>${label}</span><input type="range" data-k="${k}" min="${mn}" max="${mx}" step="${st}"><output></output></label>`).join("");
  async function baseLook(film, grade) {
    const g = await window.goboReady;
    return g.look({ film: film === "auto" ? "clean" : film, grade: grade === "auto" ? "natural" : grade });
  }
  function syncSliders() {
    qa("#gSliders input", lab).forEach(i => { i.value = gradeLook[i.dataset.k]; i.nextElementSibling.textContent = Number(gradeLook[i.dataset.k]).toFixed(2); });
    syncLut();
  }

  // ── LUTs and CDLs: from any editor, kept with the reel ─────────────────
  // A .cube is uploaded to the reel (so Grade the take can use it on the
  // server) and loaded into this page's gobo.wasm; the look names it by id.
  //
  // A LUT is read into the sliders: they move to the nearest they come to it,
  // and what they can't show is kept as its rest, a small LUT that goes first,
  // so the picture is still the LUT's (scrim::unpack). One no slider comes
  // near (a negative, a swap of colours) stays whole, after the look. A CDL's
  // slope, offset and power are lift, gain and gamma, so it goes on the wheels.
  const loadedLuts = new Set();
  let reelLuts = [], lutsFor = null, lastRead = null;
  async function listLuts(force) {
    const reel = ST() && ST().reel();
    if (!reel) return [];
    if (force || lutsFor !== reel) { const r = await ST().api(`/reels/${reel}/luts`); reelLuts = r.luts || []; lutsFor = reel; }
    return reelLuts.filter(m => !m.of);               // a LUT's rest isn't one to pick
  }
  async function ensureLut(look) {
    const id = look && look.lut3d && look.lut3d.id;
    if (!id || loadedLuts.has(id)) return true;
    const g = await window.goboReady, reel = ST().reel();
    const res = await fetch(`${ST().apiBase}/reels/${reel}/luts/${id}`);
    if (!res.ok) { delete look.lut3d; say("That LUT isn't in this reel any more, so it's off.", true); return false; }
    const r = g.loadLut(id, await res.text());
    if (r.error) { delete look.lut3d; say(r.error, true); return false; }
    loadedLuts.add(id);
    return true;
  }
  const signed = v => (v > 0 ? "+" : "") + v.toFixed(2);
  const MOVED = [["exposure", "Exposure", 0, 0.02, signed], ["contrast", "Contrast", 1, 0.02], ["fade", "Fade", 0, 0.005], ["white", "White point", 1, 0.005],
    ["sat", "Saturation", 1, 0.02], ["temp", "Warmth", 0, 0.02, signed], ["tint", "Tint", 0, 0.02, signed]];
  const andList = a => a.length < 2 ? a.join("") : `${a.slice(0, -1).join(", ")} and ${a[a.length - 1]}`;
  function moved(look) {
    const out = MOVED.filter(([k, , at, min]) => Math.abs(look[k] - at) >= min).map(([k, label, , , f]) => `${label} ${(f || (v => v.toFixed(2)))(look[k])}`);
    const w = look.wheels || {}, wheels = ["lift", "gamma", "gain"].filter(k => (w[k] || []).some(v => Math.abs(v) >= 0.005));
    if (wheels.length) out.push(`the ${andList(wheels.map(k => k[0].toUpperCase() + k.slice(1)))} wheel${wheels.length > 1 ? "s" : ""}`);
    return out;
  }
  async function readLut(meta) {
    const g = await window.goboReady, reel = ST().reel(), name = meta.title || meta.name || "The LUT";
    if (!(await ensureLut({ lut3d: { id: meta.id } }))) throw new Error("That LUT isn't in this reel any more.");
    const u = g.unpackLut(meta.id);
    if (u.error) throw new Error(u.error);
    if (u.whole) {
      gradeLook.lut3d = { id: meta.id, mix: 1, first: false };
      lastRead = null;
      return u.whole === "nothing" ? `${name} barely changes the picture, so it's on as a LUT and the sliders stay where they were.`
        : `${name} does what no slider does (it turns one colour into another, inverts or posterises), so it stays a LUT, after the look, and the sliders stay where they were.`;
    }
    const fd = new FormData();
    fd.append("file", new Blob([u.rest], { type: "text/plain" }), `${name} (the rest).cube`);
    fd.append("of", meta.id);
    const r = await ST().api(`/reels/${reel}/luts`, { method: "POST", body: fd });
    if (r.error) throw new Error(r.error);
    const loaded = g.loadLut(r.lut.id, u.rest);
    if (loaded.error) throw new Error(loaded.error);
    loadedLuts.add(r.lut.id);
    lastRead = { of: meta.id, name, before: JSON.parse(JSON.stringify(gradeLook)) };
    gradeLook = Object.assign(u.look, { lut3d: { id: r.lut.id, mix: 1, first: true, of: meta.id } });
    q("#gFilm", lab).value = "clean"; q("#gGrade", lab).value = "natural";
    const m = moved(gradeLook);
    return `${name} is in the sliders: ${m.length ? andList(m) : "they barely move"}. What they can't show is kept as a small LUT before them, so the picture is exactly the LUT's. Amount sets how much of it.`;
  }
  function keepWhole() {
    const cur = gradeLook && gradeLook.lut3d;
    if (!cur || !cur.of) return;
    if (lastRead && lastRead.of === cur.of) gradeLook = lastRead.before;
    gradeLook.lut3d = { id: cur.of, mix: 1, first: false };
    lastRead = null;
  }
  function lutNote() {
    const cur = gradeLook && gradeLook.lut3d;
    if (!cur) return "";
    const m = reelLuts.find(x => x.id === (cur.of || cur.id)), name = h((m && (m.title || m.name)) || "This LUT");
    return cur.of ? `${name} is read into the sliders, with what they can't show kept as a small LUT before them: the picture is exactly the LUT's. <button class="linkbtn" data-lut="whole">Keep it as one LUT instead</button>`
      : `${name} is one LUT on the look; the sliders don't show it. <button class="linkbtn" data-lut="read">Read it into the sliders</button>`;
  }
  async function lutAction(what) {
    const cur = gradeLook && gradeLook.lut3d;
    if (!cur) return "";
    if (what === "whole") { keepWhole(); return "It's one LUT again, after the look, and the sliders are back where they were."; }
    const m = reelLuts.find(x => x.id === cur.id) || { id: cur.id };
    return readLut(m);
  }
  async function useLut(id) {
    if (!id) { delete gradeLook.lut3d; lastRead = null; return ""; }
    const m = (await listLuts()).find(x => x.id === id) || { id };
    return readLut(m);
  }
  function parseCdl(text) {
    // ASC CDL XML (.cdl, .cc, .ccc): the first correction's SOP and saturation
    const doc = new DOMParser().parseFromString(text, "application/xml");
    const nums = tag => { const el = doc.getElementsByTagName(tag)[0]; return el ? el.textContent.trim().split(/\s+/).map(Number) : null; };
    const slope = nums("Slope"), offset = nums("Offset"), power = nums("Power"), sat = nums("Saturation");
    if (!slope && !offset && !power && !sat) throw new Error("That file has no CDL in it (no Slope, Offset, Power or Saturation).");
    const three = (v, d) => v && v.length === 3 && v.every(Number.isFinite) ? v : [d, d, d];
    return { slope: three(slope, 1), offset: three(offset, 0), power: three(power, 1), sat: sat && Number.isFinite(sat[0]) ? sat[0] : 1 };
  }
  function cdlWheels(c) {
    // (x·slope + offset)^power is the wheels' (gain·(x + lift·(1 − x)))^(2^−gamma)
    // exactly, with gain = slope + offset, lift = offset / gain, gamma = −log2 power.
    // The colour goes on each wheel and the brightness on its slider.
    const w = { lift: [0, 0, 0, 0], gamma: [0, 0, 0, 0], gain: [0, 0, 0, 0], offset: [0, 0, 0, 0] };
    for (let k = 0; k < 3; k++) {
      const gain = c.slope[k] + c.offset[k];
      if (!(gain > 0.01) || !(c.power[k] > 0)) return null;
      w.lift[k] = c.offset[k] / gain; w.gain[k] = gain - 1; w.gamma[k] = -Math.log2(c.power[k]);
    }
    for (const k of ["lift", "gamma", "gain"]) {
      const v = w[k], y = 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
      w[k] = [v[0] - y, v[1] - y, v[2] - y, y];
    }
    const fits = [...w.lift, ...w.gamma].every(v => Math.abs(v) <= 1) && w.gain.every(v => Math.abs(v) <= 2);
    return fits ? w : null;
  }
  async function importGrade(file) {
    const text = await file.text(), g = await window.goboReady;
    if (/\.(cdl|cc|ccc|xml)$/i.test(file.name) || /<(ColorCorrection|ColorDecision|SOPNode)/.test(text.slice(0, 4000))) {
      const c = parseCdl(text), w = cdlWheels(c);
      if (!w) {
        gradeLook.cdl = c;
        return `The CDL from ${file.name} is beyond what the wheels reach, so it's on the look as it is: slope, offset, power and saturation, before everything else.`;
      }
      delete gradeLook.cdl;
      gradeLook.wheels = w;
      gradeLook.sat = Math.max(0, Math.min(2, (gradeLook.sat == null ? 1 : gradeLook.sat) * c.sat));
      return `The CDL from ${file.name} is on the wheels: its slope, offset and power are the Lift, Gamma and Gain wheels exactly (Advanced shows them)${c.sat !== 1 ? `, and its saturation (×${c.sat.toFixed(2)}) is on Saturation` : ""}.`;
    }
    const reel = ST().reel();
    if (!reel) throw new Error("Open a reel first: a LUT is kept with the reel.");
    const fd = new FormData(); fd.append("file", file, file.name);
    const r = await ST().api(`/reels/${reel}/luts`, { method: "POST", body: fd });
    if (r.error) throw new Error(r.error);
    const loaded = g.loadLut(r.lut.id, text);
    if (loaded.error) throw new Error(loaded.error);
    loadedLuts.add(r.lut.id);
    await listLuts(true);
    return readLut(r.lut);
  }
  async function syncLut() {
    if (!gradeLook) return;
    const sel = q("#gLut", lab), cur = gradeLook.lut3d;
    const list = await listLuts().catch(() => []);
    sel.innerHTML = `<option value="">No LUT</option>` + list.map(m => `<option value="${h(m.id)}">${h(m.title || m.name)}</option>`).join("");
    sel.value = cur ? cur.of || cur.id : "";
    // a LUT read into the sliders has no amount of its own: Amount is all of it
    q("#gLutMixRow", lab).classList.toggle("hidden", !cur || !!cur.of);
    if (cur) { q("#gLutMix", lab).value = cur.mix == null ? 1 : cur.mix; q("#gLutMix", lab).nextElementSibling.textContent = Number(q("#gLutMix", lab).value).toFixed(2); }
    const ln = q("#gLutNote", lab), html = lutNote();
    ln.classList.toggle("hidden", !html); ln.innerHTML = html;
    const c = gradeLook.cdl, note = q("#gCdlNote", lab);
    note.classList.toggle("hidden", !c);
    if (c) note.innerHTML = `CDL: slope ${c.slope.map(v => v.toFixed(3)).join(" ")} · offset ${c.offset.map(v => v.toFixed(3)).join(" ")} · power ${c.power.map(v => v.toFixed(3)).join(" ")} · sat ${Number(c.sat).toFixed(2)} <button class="linkbtn" id="gCdlOff">Remove</button>`;
    const off = q("#gCdlOff", lab); if (off) off.onclick = () => { delete gradeLook.cdl; syncLut(); render(); };
  }
  // after a LUT or CDL changes the look: every slider, the note, the picture
  async function lutDone(msg) { gradeFrom = "lab"; syncSliders(); render(); if (msg) say(msg); }
  q("#gLutNote", lab).onclick = async e => {
    const b = e.target.closest("[data-lut]");
    if (!b) return;
    try { await lutDone(await lutAction(b.dataset.lut)); } catch (err) { say(err.message, true); }
  };
  q("#gLutPick", lab).onclick = () => q("#gLutFile", lab).click();
  q("#gLutFile", lab).onchange = async e => {
    const f = e.target.files[0]; e.target.value = "";
    if (!f || !gradeLook) return;
    try { await lutDone(await importGrade(f)); } catch (err) { say(err.message, true); }
  };
  q("#gLut", lab).onchange = async e => {
    try { await lutDone(await useLut(e.target.value)); } catch (err) { say(err.message, true); syncLut(); }
  };
  q("#gLutMix", lab).oninput = e => { if (gradeLook.lut3d) { gradeLook.lut3d.mix = Number(e.target.value); e.target.nextElementSibling.textContent = Number(e.target.value).toFixed(2); render(); } };
  async function fromShot() {
    const d = ST().direction();
    q("#gFilm", lab).value = hasOpt("film", d.film) ? d.film : "clean";
    q("#gGrade", lab).value = hasOpt("grade", d.grade) ? d.grade : "natural";
    gradeLook = await baseLook(q("#gFilm", lab).value, q("#gGrade", lab).value);
    gradeFrom = "shot"; syncSliders(); render();
  }
  q("#gShot", lab).onclick = fromShot;
  q("#gReset", lab).onclick = fromShot;
  // a new stock or grade replaces the look's numbers, not the colourist's tools on it
  const TOOLS = ["cdl", "wheels", "curves", "lut3d"];
  const keepTools = (base, from) => { TOOLS.forEach(k => { if (from && from[k]) base[k] = from[k]; }); return base; };
  q("#gFilm", lab).onchange = q("#gGrade", lab).onchange = async () => { gradeLook = keepTools(await baseLook(q("#gFilm", lab).value, q("#gGrade", lab).value), gradeLook); gradeFrom = "lab"; syncSliders(); render(); };
  qa("#gSliders input", lab).forEach(i => i.oninput = () => { gradeLook[i.dataset.k] = Number(i.value); gradeFrom = "lab"; i.nextElementSibling.textContent = Number(i.value).toFixed(2); render(); });
  q("#gWipe", lab).oninput = () => render();
  function stopVideo() { cancelAnimationFrame(raf); raf = 0; if (vid) { vid.pause(); vid.removeAttribute("src"); vid.load(); vid = null; } }
  async function gradeSetup() {
    stopVideo();
    const t = ST().current();
    if (!gradeLook || gradeFrom === "shot") await fromShot(); else syncSliders();
    if (!t || t.status !== "done") { gsrc = null; gctx.clearRect(0, 0, gcv.width, gcv.height); q("#gPerf", lab).textContent = "Pick a finished take to grade it."; return; }
    if (t.kind === "video") {
      vid = document.createElement("video");
      Object.assign(vid, { muted: true, loop: true, playsInline: true, src: t.url });
      await vid.play().catch(() => {});
      gsrc = vid;
      const tick = () => { render(); raf = requestAnimationFrame(tick); };
      raf = requestAnimationFrame(tick);
    } else {
      gsrc = await image(t.url);
      render();
    }
  }
  let gbusy = false;
  async function render() {
    const g = await window.goboReady;
    if (!g || !gsrc || !gradeLook || gbusy) return;
    if (gradeLook.lut3d && !loadedLuts.has(gradeLook.lut3d.id) && !(await ensureLut(gradeLook))) syncLut();
    if (gsrc === vid && (vid.readyState < 2)) return;
    gbusy = true;
    try {
      gorig = grab(gsrc, gsrc === vid ? 960 : 1280);
      const graded = new ImageData(new Uint8ClampedArray(gorig.data), gorig.width, gorig.height);
      const t0 = performance.now();
      g.grade(graded, gradeLook, gsrc === vid ? ++frameNo : 7);
      const ms = performance.now() - t0;
      const wipe = Number(q("#gWipe", lab).value) / 100;
      if (wipe > 0) {
        const cut = Math.round(graded.width * wipe);
        for (let y = 0; y < graded.height; y++) {
          const row = y * graded.width * 4;
          graded.data.set(gorig.data.subarray(row, row + cut * 4), row);
        }
      }
      paint(gcv, graded);
      perfMs = perfMs ? perfMs * 0.9 + ms * 0.1 : ms;
      if (gsrc !== vid || frameNo % 12 === 0) {
        q("#gPerf", lab).textContent = `Graded in Rust: ${perfMs.toFixed(1)} ms for ${graded.width}×${graded.height}${gsrc === vid ? ", every frame, grain moving" : ""}${wipe > 0 ? " · left of the line is the original" : ""}`;
      }
    } finally { gbusy = false; }
  }
  q("#gStill", lab).onclick = async () => {
    const g = await window.goboReady, t = ST().current();
    if (!g || !gsrc) return;
    const full = gsrc === vid ? pixels(vid, 4096) : pixels(gsrc, 4096);
    g.grade(full, gradeLook, 7);
    const c = document.createElement("canvas"); paint(c, full);
    c.toBlob(b => save(b, `${(t && (t.shot + (t.letter || ""))) || "take"}-graded.png`), "image/png");
  };
  function gradeName() {
    const parts = [`${optLabel("film", q("#gFilm", lab).value)} + ${optLabel("grade", q("#gGrade", lab).value)}`];
    const lut = gradeLook && gradeLook.lut3d && reelLuts.find(m => m.id === gradeLook.lut3d.id);
    if (lut) parts.push(lut.title || lut.name);
    if (gradeLook && (gradeLook.wheels || gradeLook.curves || gradeLook.cdl)) parts.push("colour");
    return parts.join(" · ");
  }
  async function bake(button) {
    const t = ST().current(), b = button || q("#gBake", lab);
    if (!t || t.status !== "done" || !gradeLook) return ST().toast("Pick a finished take to grade it.", true);
    const label = b.textContent;
    b.disabled = true; b.textContent = t.kind === "video" ? "Grading every frame…" : "Grading…";
    try {
      const r = await ST().api(`/reels/${ST().reel()}/takes/${t.id}/grade`, { method: "POST", json: { look: gradeLook, label: `Graded · ${gradeName()}` } });
      if (r.error) return ST().toast(r.error, true);
      ST().addTakes([r.take]);
      // show the result: the Lab (and the Colour page) cover the stage, so they close on the graded take
      if (window.goboColour) window.goboColour.close();
      close();
      if (ST().show) ST().show(r.take.id);
      ST().toast(r.take.status === "running" ? "Grading every frame in the background, frame by frame on screen. Keep working if you like; the graded take lands beside the original."
        : "Graded. You're looking at the graded take; the original is beside it.");
    } finally { b.disabled = false; b.textContent = label; }
  }
  q("#gBake", lab).onclick = () => bake();
  async function saveCube() {
    const g = await window.goboReady;
    if (!(await ensureLut(gradeLook))) return;
    save(new Blob([g.cube(Object.assign({}, gradeLook, { title: `Gobo ${gradeName()}` }), 33)], { type: "text/plain" }), "gobo-look.cube");
  }
  q("#gCube", lab).onclick = saveCube;
  async function saveStill() { q("#gStill", lab).onclick(); }

  // the Colour page (gobo_colour.js) works on this same look
  q("#gAdv", lab).onclick = async () => {
    if (!gradeLook) await fromShot();
    stopVideo();
    if (window.goboColour) window.goboColour.open();
  };
  window.goboGrade = {
    get look() { return gradeLook; },
    async ready() { if (!gradeLook) await fromShot(); return gradeLook; },
    edited() { gradeFrom = "lab"; },
    changed() { gradeFrom = "lab"; syncSliders(); if (!lab.classList.contains("hidden") && tab === "grade") gradeSetup(); },
    async setPreset(film, grade) {
      q("#gFilm", lab).value = film; q("#gGrade", lab).value = grade;
      gradeLook = keepTools(await baseLook(film, grade), gradeLook); gradeFrom = "lab"; return gradeLook;
    },
    preset: () => ({ film: q("#gFilm", lab).value, grade: q("#gGrade", lab).value }),
    reset: fromShot, ensureLut, listLuts, importGrade, useLut, lutNote, lutAction, bake, saveCube, saveStill, name: gradeName,
    reopen() { open("grade"); },
  };

  // ── Continuity ──────────────────────────────────────────────────────────
  q("#cRun", lab).onclick = async () => {
    const g = await window.goboReady;
    const takes = done();
    const out = q("#cOut", lab), status = q("#cStatus", lab);
    if (takes.length < 2) { out.innerHTML = ""; status.textContent = "Two finished takes, at least."; return; }
    status.textContent = "Reading frames…";
    const firsts = [], lasts = [];
    try {
      for (const t of takes) {
        firsts.push(await takeFrame(t, "first", 480));
        lasts.push(t.kind === "video" ? await takeFrame(t, "last", 480) : firsts[firsts.length - 1]);
      }
    } catch (e) { status.textContent = e.message; return; }
    const cont = q("#cCont", lab).checked;
    const t0 = performance.now();
    const continues = t => t.action === "extend" || (typeof B !== "undefined" && B && (B.shots.find(s => s.id === t.board) || {}).start === "continue");
    const cuts = takes.slice(1).map((t, i) => Object.assign(g.compare(lasts[i], firsts[i + 1], cont || continues(t)), { a: i, b: i + 1 }));
    // the odd one out: the take whose look sits furthest from all the others
    let odd = null;
    if (takes.length >= 3) {
      // as crates/flag's outlier(): clearly further from the rest than they are from each other
      const far = firsts.map((f, i) => firsts.reduce((s, o, j) => j === i ? s : s + (1 - g.compare(o, f, false).score), 0) / (firsts.length - 1));
      const i = far.indexOf(Math.max(...far)), rest = far.filter((_, j) => j !== i).sort((a, b) => a - b);
      if (far[i] > 0.1 && far[i] > 2.5 * Math.max(0.01, rest[Math.floor(rest.length / 2)])) odd = i;
    }
    const ms = performance.now() - t0;
    const name = t => `${t.shot || ""}${t.letter || ""}`.trim() || t.id.slice(-4);
    out.innerHTML = (odd != null ? `<div class="odd">Take <b>${h(name(takes[odd]))}</b> is the odd one out: its look sits furthest from the rest. Regrade it toward the others first.</div>` : "")
      + cuts.map((c, k) => `<div class="cut"><canvas data-i="l${c.a}"></canvas><canvas data-i="f${c.b}"></canvas><div>
          <b>${h(name(takes[c.a]))} → ${h(name(takes[c.b]))}</b> <span class="verdict ${c.verdict}">${c.verdict}</span>
          ${c.notes.length ? `<ul>${c.notes.map(n => `<li>${h(n)}</li>`).join("")}${c.fixes.map(f => `<li class="fix">${h(f)}</li>`).join("")}</ul>` : `<div class="hint">Light, colour and grain carry across.</div>`}</div></div>`).join("");
    qa("canvas[data-i]", out).forEach(c => { const i = Number(c.dataset.i.slice(1)); paint(c, c.dataset.i[0] === "l" ? lasts[i] : firsts[i]); });
    const bad = cuts.filter(c => c.verdict !== "match").length;
    status.textContent = `${cuts.length} cut${cuts.length === 1 ? "" : "s"}, ${bad ? `${bad} to look at` : "all match"} · Rust compared them in ${ms.toFixed(0)} ms`;
  };
})();
