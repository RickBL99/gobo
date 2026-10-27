// Gobo's nodes on the ComfyUI page.
//
// * A workflow from Gobo: "Open in ComfyUI" in Gobo downloads it as a file
//   (ComfyUI turns away pages other sites send it to), and dropped here or
//   opened with File › Open it's laid out left to right, the way the picture
//   flows: ComfyUI on its own puts every node of such a file in one spot.
// * The Look node behaves as Gobo's Lab does: picking a film stock or a grade
//   fills the sliders with its numbers (from /gobo/look, the same Rust).
// * The Director shows the prompt it writes as its choices change, coloured
//   the way Gobo's prompt panel colours it (/gobo/compile, the shot compiler);
//   after a run, the prompt it sent, with what came from the still.
// * Draw the Move is a canvas, as in Gobo's Lab: the camera pen and the
//   subject pen, read by Gobo's Rust as you draw (/gobo/draw), and the
//   Director it's wired to shows the drawn move in its prompt at once.
import { app } from "../../scripts/app.js";

// Gobo's prompt colours: the choices, the scene, the cinematic words, references
const SEG = { direction: "#f2c14e", scene: "#ecebe8", enhance: "#9ad1ff", reference: "#c6a6ff" };
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const linkOf = (node, id) => { const ls = node.graph && node.graph.links; return ls ? (ls instanceof Map ? ls.get(id) : ls[id]) : null; };
// the node wired into one of a node's inputs
function fromInput(node, name) {
  const i = (node.inputs || []).find(x => x.name === name && x.link != null);
  const l = i && linkOf(node, i.link);
  return l && node.graph.getNodeById(l.origin_id);
}
// the nodes one of a node's outputs is wired into
function intoOutput(node, slot) {
  const o = (node.outputs || [])[slot];
  return ((o && o.links) || []).map(id => linkOf(node, id)).filter(Boolean).map(l => node.graph.getNodeById(l.target_id)).filter(Boolean);
}
const drawingOf = n => { const s = n && (n.widgets || []).find(x => x.name === "strokes"); try { return s && s.value ? JSON.parse(s.value) : null; } catch (e) { return null; } };

function drawMove(node) {
  const store = (node.widgets || []).find(x => x.name === "strokes");
  if (!store || typeof node.addDOMWidget !== "function") return;
  // the drawing lives in the strokes widget, which the canvas fills in: hide it
  store.hidden = true; store.computeSize = () => [0, -4]; store.options = Object.assign({}, store.options, { hidden: true });
  const wrap = document.createElement("div");
  Object.assign(wrap.style, { display: "flex", flexDirection: "column", gap: "6px", font: "12px/1.4 Inter, system-ui, sans-serif", color: "#ecebe8" });
  wrap.innerHTML = `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
      <button data-pen="camera" style="${BTN}">🎥 Camera</button><button data-pen="subject" style="${BTN}">🏃 Subject</button>
      <input data-who placeholder="who (the subject)" style="flex:1;min-width:80px;${FIELD}">
      <button data-undo style="${BTN}">Undo</button><button data-clear style="${BTN}">Clear</button></div>
    <canvas width="640" height="360" style="width:100%;border-radius:6px;background:#101014;touch-action:none;cursor:crosshair"></canvas>
    <div data-read style="color:#cfc9ba;white-space:pre-wrap;min-height:34px"></div>`;
  const cv = wrap.querySelector("canvas"), ctx = cv.getContext("2d"), read = wrap.querySelector("[data-read]");
  let pen = "camera", strokes = [], live = null, bg = null, t0 = 0;
  const saved = drawingOf(node);
  if (saved && Array.isArray(saved.strokes)) strokes = saved.strokes;
  const pens = () => wrap.querySelectorAll("[data-pen]").forEach(b => { b.style.background = b.dataset.pen === pen ? "#f2c14e" : "#1d1a12"; b.style.color = b.dataset.pen === pen ? "#1b1505" : "#ecebe8"; });
  function arrow(pts, colour) {
    if (pts.length < 2) return;
    ctx.strokeStyle = colour; ctx.fillStyle = colour; ctx.lineWidth = 5; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); pts.forEach(p => ctx.lineTo(p[0], p[1])); ctx.stroke();
    const a = pts[pts.length - 1], b = pts[Math.max(0, pts.length - 6)], ang = Math.atan2(a[1] - b[1], a[0] - b[0]);
    ctx.beginPath(); ctx.moveTo(a[0], a[1]);
    ctx.lineTo(a[0] - 20 * Math.cos(ang - 0.45), a[1] - 20 * Math.sin(ang - 0.45));
    ctx.lineTo(a[0] - 20 * Math.cos(ang + 0.45), a[1] - 20 * Math.sin(ang + 0.45)); ctx.closePath(); ctx.fill();
  }
  function redraw() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (bg) { ctx.globalAlpha = 0.55; ctx.drawImage(bg, 0, 0, cv.width, cv.height); ctx.globalAlpha = 1; }
    else { ctx.fillStyle = "#101014"; ctx.fillRect(0, 0, cv.width, cv.height); }
    ctx.strokeStyle = "#ffffff22"; ctx.lineWidth = 1;
    for (const f of [1 / 3, 2 / 3]) {
      ctx.beginPath(); ctx.moveTo(cv.width * f, 0); ctx.lineTo(cv.width * f, cv.height); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, cv.height * f); ctx.lineTo(cv.width, cv.height * f); ctx.stroke();
    }
    [...strokes, ...(live ? [live] : [])].forEach(s => arrow(s.points, s.pen === "camera" ? "#f2c14e" : "#7ee0d0"));
  }
  async function save() {
    store.value = strokes.length ? JSON.stringify({ w: cv.width, h: cv.height, strokes }) : "";
    if (store.callback) store.callback(store.value);
    try {
      const res = await fetch("/gobo/draw", { method: "POST", headers: { "Content-Type": "application/json" }, body: store.value || "{}" });
      const v = res.ok ? await res.json() : null;
      read.textContent = v ? v.said : "";
    } catch (e) { /* the server is restarting */ }
    intoOutput(node, 0).forEach(n => n._goboRefresh && n._goboRefresh());
    node.setDirtyCanvas(true, true);
  }
  // the frame to draw on: a Load Image wired into `image`
  function background() {
    const src = fromInput(node, "image"), name = src && (src.widgets || []).find(x => x.name === "image");
    if (!name || !name.value) { bg = null; cv.height = 360; return redraw(); }
    const im = new Image();
    im.onload = () => { bg = im; cv.height = Math.round(cv.width * im.naturalHeight / Math.max(1, im.naturalWidth)); redraw(); };
    im.src = `/view?filename=${encodeURIComponent(name.value)}&type=input`;
  }
  const at = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height]; };
  cv.addEventListener("pointerdown", e => {
    e.stopPropagation(); e.preventDefault();
    cv.setPointerCapture(e.pointerId); t0 = performance.now();
    live = { pen, label: pen === "subject" ? (wrap.querySelector("[data-who]").value.trim()) : "", points: [[...at(e), 0]] }; redraw();
  });
  cv.addEventListener("pointermove", e => { if (!live) return; e.stopPropagation(); live.points.push([...at(e), Math.round(performance.now() - t0)]); redraw(); });
  cv.addEventListener("pointerup", e => { if (!live) return; e.stopPropagation(); strokes.push(live); live = null; redraw(); save(); });
  wrap.querySelectorAll("[data-pen]").forEach(b => b.onclick = () => { pen = b.dataset.pen; pens(); });
  wrap.querySelector("[data-undo]").onclick = () => { strokes.pop(); redraw(); save(); };
  wrap.querySelector("[data-clear]").onclick = () => { strokes = []; redraw(); save(); };
  ["pointerdown", "wheel", "keydown"].forEach(ev => wrap.addEventListener(ev, e => e.stopPropagation()));
  node.addDOMWidget("gobo_draw", "gobo-draw", wrap, { serialize: false, getValue: () => "", setValue: () => {}, getMinHeight: () => 330 });
  const conn = node.onConnectionsChange;
  node.onConnectionsChange = function () { const r = conn ? conn.apply(this, arguments) : undefined; background(); return r; };
  const conf = node.onConfigure;
  node.onConfigure = function () {
    const r = conf ? conf.apply(this, arguments) : undefined;
    const d = drawingOf(node); strokes = d && Array.isArray(d.strokes) ? d.strokes : []; background(); save();
    return r;
  };
  node.size = [Math.max(node.size[0], 420), Math.max(node.size[1], 420)];
  pens(); background(); save();
}
const BTN = "border:1px solid #4a3a10;border-radius:6px;padding:3px 9px;font:inherit;cursor:pointer;background:#1d1a12;color:#ecebe8";
const FIELD = "border:1px solid #3a3a44;border-radius:6px;padding:3px 7px;font:inherit;background:#15151a;color:#ecebe8";

function directorPreview(node) {
  const w = n => (node.widgets || []).find(x => x.name === n);
  const box = document.createElement("div");
  Object.assign(box.style, { font: "12px/1.45 Inter, system-ui, sans-serif", color: "#ecebe8", background: "#15120a",
    border: "1px solid #4a3a10", borderRadius: "6px", padding: "6px 8px", overflowY: "auto", height: "160px", boxSizing: "border-box" });
  const show = (segs, note) => {
    box.innerHTML = `<div style="font-size:10px;letter-spacing:.08em;color:#f2c14e;font-weight:700;margin-bottom:3px">PROMPT · RUST · LIVE</div>`
      + (segs && segs.length ? segs.map(s => `<span style="color:${SEG[s.source] || SEG.direction}">${esc(s.text)}</span>`).join(" ")
        : `<span style="opacity:.6">Write a scene to see the prompt.</span>`)
      + (note ? `<div style="opacity:.6;margin-top:5px">${esc(note)}</div>` : "");
  };
  let t = null;
  const refresh = () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      const picks = {};
      (node.widgets || []).forEach(x => { if (x.type === "combo" && x.name !== "shoot") picks[x.name] = x.value; });
      const linked = (node.inputs || []).some(i => i.name === "direction" && i.link != null);
      const drew = fromInput(node, "drawn"), drawing = drew && drew.type === "GoboDrawMove" ? drawingOf(drew) : null;
      try {
        const res = await fetch("/gobo/compile", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scene: (w("scene") || {}).value || "", shoot: (w("shoot") || {}).value, cinematic: (w("cinematic") || {}).value !== false, picks,
                                 ...(drawing ? { drawing } : {}) }) });
        if (!res.ok) return;
        const v = await res.json();
        show(v.segments, linked ? "Steal the Look is connected: the still's choices replace these when it runs." : "");
      } catch (e) { /* the server is restarting: the next change tries again */ }
    }, 120);
  };
  if (typeof node.addDOMWidget !== "function") return;           // an older ComfyUI: no preview, the node still works
  node._goboRefresh = refresh;                                     // Draw the Move redraws: the prompt follows
  node.addDOMWidget("gobo_prompt", "gobo-prompt", box, { serialize: false, getValue: () => "", setValue: () => {}, getMinHeight: () => 160 });
  (node.widgets || []).forEach(x => {
    if (x.name === "gobo_prompt") return;
    const was = x.callback;
    x.callback = function () { const r = was ? was.apply(this, arguments) : undefined; refresh(); return r; };
  });
  const sc = w("scene"), el = sc && (sc.inputEl || sc.element);
  if (el && el.addEventListener) el.addEventListener("input", refresh);
  const conn = node.onConnectionsChange;
  node.onConnectionsChange = function () { const r = conn ? conn.apply(this, arguments) : undefined; refresh(); return r; };
  const conf = node.onConfigure;
  node.onConfigure = function () { const r = conf ? conf.apply(this, arguments) : undefined; refresh(); return r; };
  const ran = node.onExecuted;
  node.onExecuted = function (msg) {
    const r = ran ? ran.apply(this, arguments) : undefined;
    const g = msg && msg.gobo_prompt && msg.gobo_prompt[0];
    if (g) show(g.segments, g.from_still && g.from_still.length ? `From the still: ${g.from_still.join(", ")}.` : "As sent.");
    return r;
  };
  refresh();
}

const SLIDERS = ["exposure", "contrast", "pivot", "saturation", "warmth", "tint", "fade", "white_point",
  "grain", "grain_size", "halation", "vignette"];
const PRESET_OWNED = ["shadows", "highlights", "bleach", "mono"];

// a workflow in API form (what Gobo downloads) with Gobo's nodes in it
const fromGobo = p => !!p && typeof p === "object" && !Array.isArray(p)
  && Object.values(p).some(n => n && String(n.class_type || "").startsWith("Gobo"));

app.registerExtension({
  name: "gobo.nodes",
  async setup() {
    // a dropped or opened file comes through app.loadApiJson; Gobo's are laid out after
    const load = typeof app.loadApiJson === "function" ? app.loadApiJson.bind(app) : null;
    if (!load) return;
    app.loadApiJson = async (prompt, ...rest) => {
      const out = await load(prompt, ...rest);
      if (fromGobo(prompt)) {
        try { if (app.graph && app.graph.arrange) app.graph.arrange(80); } catch (e) { /* leave it as loaded */ }
        app.canvas && app.canvas.setDirty(true, true);
      }
      return out;
    };
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.category !== "Gobo") return;
    const made = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const out = made ? made.apply(this, arguments) : undefined;
      this.color = "#4a3a10"; this.bgcolor = "#2b2310";         // Gobo amber
      if (nodeData.name === "GoboDirector") { directorPreview(this); return out; }
      if (nodeData.name === "GoboDrawMove") { drawMove(this); return out; }
      if (nodeData.name !== "GoboLook") return out;
      const w = n => (this.widgets || []).find(x => x.name === n);
      const refill = async () => {
        const q = new URLSearchParams({ film: w("film").value, grade: w("grade").value });
        const res = await fetch(`/gobo/look?${q}`);
        if (!res.ok) return;
        const v = await res.json();
        SLIDERS.forEach(k => { const x = w(k); if (x && v[k] != null) x.value = v[k]; });
        const box = w("colour");
        if (box) {
          let cur = {};
          try { cur = JSON.parse(box.value || "{}"); } catch (e) { cur = {}; }
          PRESET_OWNED.forEach(k => { delete cur[k]; });
          box.value = JSON.stringify(Object.assign(cur, v.colour || {}));
        }
        this.setDirtyCanvas(true, true);
      };
      ["film", "grade"].forEach(n => {
        const x = w(n);
        if (!x) return;
        const was = x.callback;
        x.callback = function () { const r = was ? was.apply(this, arguments) : undefined; refill(); return r; };
      });
      return out;
    };
  },
});
