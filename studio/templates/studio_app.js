// Gobo — page logic. Loaded after the inline block in studio.html,
// which defines CAT, MODELS, S, $, $$, esc, LS, thumb(), camGlyph(), optOf().
"use strict";

// ── small helpers ──────────────────────────────────────────────────────
async function api(path, opts) {
  const o = Object.assign({ headers: {} }, opts || {});
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; delete o.json; }
  const r = await fetch(API + path, o);
  let data = null;
  try { data = await r.json(); } catch (e) { data = { error: `The server answered ${r.status} without JSON.` }; }
  if (!r.ok && !data.error) data.error = `Request failed (${r.status}).`;
  return data;
}
async function copyLink(url) {
  try { await navigator.clipboard.writeText(url); toast(`Link copied: ${url}`); }
  catch (e) { prompt("The public link:", url); }
}
let toastT = null;
function toast(msg, bad) {
  const t = $("#toast"); t.textContent = msg; t.className = bad ? "bad" : ""; t.classList.remove("hidden");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), bad ? 6000 : 3200);
}
const fmtUSD = v => v == null ? "" : (v < 0.1 ? `$${v.toFixed(3)}` : `$${v.toFixed(2)}`);
const fmtTime = s => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const ratioOf = a => { const [w, h] = String(a || "16:9").split(":").map(Number); return w && h ? w / h : 16 / 9; };
const LOGO_COLORS = ["#f2c14e", "#9ad1ff", "#c6a6ff", "#6fd08c", "#ff9d7a", "#e6d8ff", "#ffd27a", "#7ee0d0"];
function logo(m) {
  const p = (m.provider || m.label || "?");
  const c = LOGO_COLORS[[...p].reduce((a, ch) => a + ch.charCodeAt(0), 0) % LOGO_COLORS.length];
  return `<span class="mlogo" style="background:${c}">${esc(p.replace(/[^A-Za-z0-9]/g, "").slice(0, 2) || "?")}</span>`;
}

// nearest supported aspect, by log-ratio (Comfy's rule)
function snapAspect(want, list) {
  if (!list || !list.length) return want;
  if (list.includes(want)) return want;
  const lw = Math.log(ratioOf(want));
  return list.slice().sort((a, b) => Math.abs(Math.log(ratioOf(a)) - lw) - Math.abs(Math.log(ratioOf(b)) - lw))[0];
}
function nearest(n, list) { return list.slice().sort((a, b) => Math.abs(a - n) - Math.abs(b - n))[0]; }

// ── capability of the current shot ─────────────────────────────────────
// What the chosen model can do with what is attached decides which controls
// show and what is sent; the server checks again, this just keeps the page
// from offering what will be refused.
function caps() {
  const m = model();
  if (!m) return {};
  const usingFrame = S.mode === "video" && !!S.firstFrame;
  const v = usingFrame && m.i2v ? Object.assign({}, m, m.i2v) : m;
  return {
    m, aspects: (S.refs.length && m.ref_aspects) ? m.ref_aspects : (v.aspects || []),
    resolutions: v.resolutions || [], durations: v.durations || [], audio: !!v.audio,
    refs: m.refs || 0, firstFrame: !!(m.i2v || m.first_frame), lastFrame: !!(v.last_frame),
    sourceVideo: !!m.source_video, negative: !!m.negative, seed: !!m.seed,
    takes: S.mode === "image" && m.kind === "image",
  };
}
function blockReason() {
  const c = caps();
  if (!c.m) return "No model available for this mode.";
  if (!S.scene.trim() && !(S.override && S.override.trim())) return "";   // the button says it
  if (S.mode === "video" && c.m.requires_frame && !S.firstFrame) return `${c.m.label} starts from an image — add a starting frame, or pick another model.`;
  if (S.mode === "video" && c.m.requires_source && !S.sourceVideo) return "Add the video to edit first — drop a clip from the sequence onto the slot.";
  if (S.mode === "image" && c.m.requires_ref && !S.refs.length) return `${c.m.label} edits an image — add a reference, or drag a take onto the slot.`;
  if (S.mode === "video" && S.firstFrame && !c.firstFrame) return `${c.m.label} can't start from an image. Remove the frame or pick another model.`;
  if (S.mode === "video" && S.lastFrame && !S.firstFrame) return "Add the opening frame too: the clip runs from it to the ending frame.";
  if (S.refs.length > c.refs) return c.refs ? `${c.m.label} takes up to ${c.refs} reference image${c.refs > 1 ? "s" : ""}.` : `${c.m.label} doesn't take reference images. Remove them or pick another model.`;
  return "";
}

// ── persistence of the working shot (per browser, a convenience only) ──
const KEEP = ["mode", "model", "shootout", "scene", "pmode", "direction", "colors", "mainColor", "aspect", "resolution", "ntakes", "duration", "audio", "negative", "seed"];
function saveLocal() { const o = {}; KEEP.forEach(k => o[k] = S[k]); LS.set("shot", o); if (S.reel) LS.set("reel", S.reel.id); }
// where Reset goes: Comfy's look in the studio; nothing at all in the Content Generator
const baseDirection = () => STILLS ? Object.fromEntries(Object.keys(CAT.defaults).map(k => [k, "auto"])) : Object.assign({}, CAT.defaults);
function loadLocal() {
  const o = LS.get("shot", null);
  if (o) KEEP.forEach(k => { if (o[k] !== undefined) S[k] = o[k]; });
  S.direction = Object.assign(baseDirection(), S.direction || {});
  ["image", "video"].forEach(k => { if (!modelById(S.model[k])) S.model[k] = (byKind(k)[0] || {}).id; });
  S.shootout = { image: (S.shootout && S.shootout.image || []).filter(modelById), video: (S.shootout && S.shootout.video || []).filter(modelById) };
}

// ── model button + sheet ───────────────────────────────────────────────
function modelMeta(m) {
  const bits = [];
  if (m.kind === "video") {
    const r = (m.resolutions || []).slice(-1)[0]; if (r) bits.push(r);
    const d = m.durations || []; if (d.length) bits.push(d.length > 1 ? `${d[0]}–${d[d.length - 1]}s` : `${d[0]}s`);
    if (m.audio) bits.push("Audio");
    if (m.i2v || m.first_frame) bits.push("Start frame");
  } else {
    if (m.refs) bits.push(`${m.refs} ref${m.refs > 1 ? "s" : ""}`);
    if (m.max_px) bits.push(`${Math.round(m.max_px / 1024)}K`);
  }
  if (m.price != null) bits.push(`≈${fmtUSD(m.price)}${m.kind === "video" && m.price_per === "second" ? "/s" : ""}`);
  return bits.join(" · ");
}
function renderModelBtn() {
  const m = model();
  $("#modelBtn").innerHTML = m ? `${logo(m)}<span><span class="mname">${esc(m.label)}</span><br><span class="mmeta">${esc(m.provider || "")}${modelMeta(m) ? " · " + esc(modelMeta(m)) : ""}</span></span><span class="chev">▾</span>` : "No models";
  const extra = S.shootout[S.mode] || [];
  $("#shootChips").classList.toggle("hidden", !extra.length);
  $("#shootChips").innerHTML = extra.length ? `<span class="hint">Also on:</span>` + extra.map(id => `<span class="chip">${esc((modelById(id) || {}).label || id)}</span>`).join("") : "";
  renderBoardModels();
}
let sheetMulti = false;
const MFILTERS = {
  image: [["refs", "References"], ["text", "Text & type"], ["fast", "Fast"], ["cheap", "Under $0.05"]],
  video: [["i2v", "Start frame"], ["last", "End frame"], ["audio", "Audio"], ["long", "10s+"], ["edit", "Change video"], ["extend", "Extend video"]],
};
let mFilter = new Set(), mQuery = "";
// the guide's anchor for a model's directory entry (same rule as studio_models.anchor)
const modelAnchor = id => "m-" + id.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
function openModelSheet(multi) {
  sheetMulti = !!multi;
  const el = $("#modelSheet");
  const list = byKind(S.mode);
  const pass = m => {
    if (mQuery && !(`${m.label} ${m.provider} ${m.group} ${(m.tags || []).join(" ")}`.toLowerCase().includes(mQuery))) return false;
    for (const f of mFilter) {
      if (f === "refs" && !m.refs) return false;
      if (f === "text" && !(m.tags || []).includes("text")) return false;
      if (f === "fast" && !(m.tags || []).includes("fast")) return false;
      if (f === "cheap" && !(m.price != null && m.price < 0.05)) return false;
      if (f === "i2v" && !(m.i2v || m.first_frame)) return false;
      if (f === "last" && !((m.i2v && m.i2v.last_frame) || m.last_frame)) return false;
      if (f === "audio" && !m.audio) return false;
      if (f === "long" && !((m.durations || []).some(d => d >= 10))) return false;
      if (f === "edit" && !(m.source_video && m.category !== "video-extend")) return false;
      if (f === "extend" && m.category !== "video-extend") return false;
    }
    return true;
  };
  const groups = {};
  list.filter(pass).forEach(m => (groups[m.group || "Other"] = groups[m.group || "Other"] || []).push(m));
  const cur = S.model[S.mode], extra = new Set(S.shootout[S.mode]);
  const caps1 = m => {
    const out = [];
    if (m.badge) out.push(`<span class="new">${esc(m.badge)}</span>`);
    if (m.kind === "image") {
      if (m.refs) out.push(`<span>${m.refs} ref${m.refs > 1 ? "s" : ""}</span>`);
      (m.tags || []).filter(t => t !== "fast").slice(0, 2).forEach(t => out.push(`<span>${esc(t)}</span>`));
      if ((m.tags || []).includes("fast")) out.push(`<span>fast</span>`);
    } else {
      const d = m.durations || []; if (d.length) out.push(`<span>${d.length > 1 ? `${d[0]}–${d[d.length - 1]}s` : d[0] + "s"}</span>`);
      const r = (m.resolutions || []).slice(-1)[0]; if (r) out.push(`<span>${esc(r)}</span>`);
      if (m.audio) out.push(`<span>audio</span>`);
      if (m.i2v || m.first_frame) out.push(`<span>start frame</span>`);
      if ((m.i2v && m.i2v.last_frame) || m.last_frame) out.push(`<span>end frame</span>`);
      if (m.source_video) out.push(`<span>edits video</span>`);
    }
    if (m.price != null) out.push(`<span class="price">≈${fmtUSD(m.price)}${m.price_per === "second" ? "/s" : ""}</span>`);
    return out.join("");
  };
  const kinds = STILLS || boardOn() ? "" : `<span class="seg" id="mKind">${["image", "video"].map(k => `<button data-k="${k}" class="${S.mode === k ? "on" : ""}">${k === "image" ? "Image" : "Video"} · ${byKind(k).length}</button>`).join("")}</span>`;
  el.innerHTML = `<h3>${sheetMulti ? "Shootout — run one shot on several models" : "Choose a model"} ${kinds} <span class="r"><a class="btn small" href="${API}/guide#directory" target="studioguide" title="What every model is good at, in plain English">Model directory</a> <button class="btn small" data-close>Done</button></span></h3>
    <div class="mfilters"><input class="field" id="mq" placeholder="Search ${list.length} models" value="${esc(mQuery)}">
      ${(MFILTERS[S.mode] || []).map(([k, l]) => `<button class="chipbtn ${mFilter.has(k) ? "on" : ""}" data-f="${k}">${l}</button>`).join("")}</div>
    ${Object.keys(groups).length ? Object.entries(groups).map(([g, ms]) => `<div class="mgroup"><h4>${esc(g)}</h4>${ms.map(m => `
      <button class="mitem ${(sheetMulti ? (m.id === cur || extra.has(m.id)) : m.id === cur) ? "on" : ""}" data-m="${esc(m.id)}" title="${esc(m.about || m.notes || "")}">
        ${sheetMulti ? `<input type="checkbox" ${m.id === cur || extra.has(m.id) ? "checked" : ""} ${m.id === cur ? "disabled" : ""} tabindex="-1">` : ""}
        ${logo(m)}<span><span class="mn">${esc(m.label)}</span><br><span class="mm">${esc(m.provider || "")}${m.about || m.notes ? " · " + esc(m.about || m.notes) : ""}</span></span>
        <span class="caps">${caps1(m)}</span><span class="about" data-about="${esc(m.id)}" title="What it's good at and where it falls down">?</span></button>`).join("")}</div>`).join("") : `<p class="hint">No model matches.</p>`}
    ${sheetMulti ? `<div class="foot"><span class="hint">${extra.size + 1} of 4 — the primary model plus up to three more. One take each.</span><span style="flex:1"></span><button class="btn small" data-clear>Clear</button><button class="btn small primary" data-close>Done</button></div>` : ""}`;
  showPop(el);
  const q = $("#mq", el); q.focus(); q.setSelectionRange(q.value.length, q.value.length);
  q.oninput = () => { mQuery = q.value.trim().toLowerCase(); openModelSheet(sheetMulti); };
  $$("[data-f]", el).forEach(b => b.onclick = () => { const f = b.dataset.f; mFilter.has(f) ? mFilter.delete(f) : mFilter.add(f); openModelSheet(sheetMulti); });
  // the sheet's own Image / Video tabs: looking for a video model IS asking for video
  $$("#mKind button", el).forEach(b => b.onclick = () => { if (b.dataset.k === S.mode) return; setMode(b.dataset.k); mFilter.clear(); openModelSheet(sheetMulti); });
  $$("[data-m]", el).forEach(b => b.onclick = e => {
    const id = b.dataset.m;
    if (e.target.closest("[data-about]")) return window.open(`${API}/guide#${modelAnchor(id)}`, "studioguide");
    if (sheetMulti) {
      if (id === cur) return;
      const a = S.shootout[S.mode];
      const i = a.indexOf(id);
      if (i >= 0) a.splice(i, 1); else if (a.length < 3) a.push(id); else return toast("Four models at most in one shootout.");
      openModelSheet(true);
    } else {
      S.model[S.mode] = id;
      S.shootout[S.mode] = S.shootout[S.mode].filter(x => x !== id);
      closePop(); onShotChange();
    }
  });
  $$("[data-close]", el).forEach(b => b.onclick = () => { closePop(); onShotChange(); });
  const clr = $("[data-clear]", el); if (clr) clr.onclick = () => { S.shootout[S.mode] = []; openModelSheet(true); };
}

// ── popovers ───────────────────────────────────────────────────────────
let popOwner = null;
function showPop(el, anchor) {
  $$(".pop").forEach(p => p !== el && p.classList.add("hidden"));
  $("#scrim").classList.remove("hidden");
  el.classList.remove("hidden");
  if (anchor && el.id === "pop") {
    const r = anchor.getBoundingClientRect();
    const w = Math.min(el.dataset.wide ? 720 : 540, window.innerWidth - 24);
    el.style.width = w + "px";
    el.style.left = Math.min(r.right + 10, window.innerWidth - w - 12) + "px";
    el.style.top = Math.max(64, Math.min(r.top - 20, window.innerHeight - el.offsetHeight - 12)) + "px";
  }
}
function closePop() {
  $$(".pop video").forEach(v => v.pause());      // a hidden clip would keep playing its sound
  if (boardOn() && S.mode !== "video") { S.mode = "video"; onShotChange(); }
  if ($("#libView")) libClose();
  $$(".pop").forEach(p => p.classList.add("hidden"));
  $("#scrim").classList.add("hidden");
  $$(".row.open").forEach(r => r.classList.remove("open"));
  if (popOwner) { try { popOwner.focus(); } catch (e) {} popOwner = null; }
}

// ── shot list ──────────────────────────────────────────────────────────
function rowGroups() {
  // on the board, framing and movement are set per card; the panel is the spot's look
  return CAT.order.direction.filter(g => (!CAT.groups[g].video || S.mode === "video") && !(boardOn() && (g === "shot" || g === "move")));
}
function renderShotList() {
  const d = S.direction;
  const camChips = CAT.order.camera.map(p => d[p] && d[p] !== "auto" ? optOf(p, d[p]).label : null).filter(Boolean);
  const body = optOf("body", d.body);
  let h = `<button class="row" data-g="camera"><span class="th" style="background:#1a1a1f">${camGlyph("body", d.body || "auto")}</span>
      <span><span class="rt">Camera</span><br><span class="rv ${body.id === "auto" ? "auto" : ""}">${esc(body.label)}</span></span>
      <span class="chips">${camChips.slice(1, 4).map(c => `<span>${esc(c)}</span>`).join("")}</span></button>`;
  for (const g of rowGroups()) {
    const o = optOf(g, d[g]);
    const custom = g === "grade" && S.colors.length;
    h += `<button class="row" data-g="${g}"><span class="th">${custom ? thumb("grade", "custom") : thumb(g, o.id)}</span>
      <span><span class="rt">${esc(CAT.groups[g].title)}</span><br><span class="rv ${o.id === "auto" && !custom ? "auto" : ""}">${custom ? "Your palette" : esc(o.label)}</span></span></button>`;
  }
  $("#shotlist").innerHTML = h;
  $$("#shotlist .row").forEach(b => b.onclick = () => { popOwner = b; b.classList.add("open"); b.dataset.g === "camera" ? openCamera(b) : openGroup(b.dataset.g, b); });
}
function openCamera(anchor) {
  const el = $("#pop"); el.dataset.wide = "1";
  el.innerHTML = `<h3>Camera <span class="r"><button class="btn small" data-close>Done</button></span></h3>
    <div class="camcols">${CAT.order.camera.map(p => `<div><h4>${esc(CAT.groups[p].title)}</h4>${CAT.groups[p].options.map(o => `
      <button class="camopt ${S.direction[p] === o.id ? "on" : ""}" data-p="${p}" data-o="${esc(o.id)}" title="${esc(o.phrase || "adds nothing")}">${camGlyph(p, o.id)}${esc(o.label)}</button>`).join("")}</div>`).join("")}</div>
    <p class="hint" style="margin:10px 0 0">The camera choices add words to the prompt; no model physically simulates a lens. They are the words a cinematographer would use.</p>`;
  showPop(el, anchor);
  $$("[data-p]", el).forEach(b => b.onclick = () => { S.direction[b.dataset.p] = b.dataset.o; $$(`[data-p="${b.dataset.p}"]`, el).forEach(x => x.classList.toggle("on", x === b)); onShotChange(); });
  $("[data-close]", el).onclick = closePop;
}
function openGroup(g, anchor) {
  const el = $("#pop"); delete el.dataset.wide;
  const grp = CAT.groups[g];
  let extra = "";
  if (g === "grade") {
    extra = `<div style="margin-top:12px;border-top:1px solid var(--line);padding-top:10px">
      <p class="sectitle">Your palette <span class="r"><button class="btn small" id="palFromImg">From an image…</button></span></p>
      <div class="palette" id="palEd"></div>
      <p class="hint" style="margin:6px 0 0">Up to 8 colours. Click a swatch to make it the main colour; a palette replaces the preset grade.</p></div>`;
  }
  el.innerHTML = `<h3>${esc(grp.title)} <span class="r"><button class="btn small" data-close>Done</button></span></h3>
    <div class="tiles ${g === "move" || g === "angle" || g === "compose" ? "three" : ""}">${grp.options.map(o => `
      <button class="tile ${S.direction[g] === o.id && !(g === "grade" && S.colors.length) ? "on" : ""}" data-o="${esc(o.id)}">
        <div class="tv">${thumb(g, o.id)}</div><div class="tl"><b>${esc(o.label)}</b><small>${esc(o.phrase || "Adds nothing — the model decides")}</small></div></button>`).join("")}</div>${extra}`;
  showPop(el, anchor);
  $$(".tile", el).forEach(b => b.onclick = () => {
    S.direction[g] = b.dataset.o;
    if (g === "grade") { S.colors = []; S.mainColor = null; }
    $$(".tile", el).forEach(x => x.classList.toggle("on", x === b));
    onShotChange(); if (g === "grade") renderPalette();
  });
  $("[data-close]", el).onclick = closePop;
  if (g === "grade") {
    renderPalette();
    $("#palFromImg").onclick = () => pickFile("image/png,image/jpeg,image/webp", async f => {
      try { S.colors = await paletteFromImage(f); S.mainColor = null; S.direction.grade = "auto"; renderPalette(); onShotChange(); $$(".tile", el).forEach(x => x.classList.remove("on")); }
      catch (e) { toast("Couldn't read colors from that image.", true); }
    });
  }
}
function renderPalette() {
  const ed = $("#palEd"); if (!ed) return;
  ed.innerHTML = S.colors.map((c, i) => `<span class="sw ${S.mainColor === i ? "main on" : ""}" style="background:${c}" data-i="${i}" title="${c} — click: main colour · right-click: remove"></span>`).join("") +
    (S.colors.length < 8 ? `<input type="color" id="palAdd" title="Add a colour" value="#808080">` : "") +
    (S.colors.length ? `<button class="btn small" id="palClear">Clear</button>` : "");
  $$(".sw", ed).forEach(s => {
    s.onclick = () => { const i = +s.dataset.i; S.mainColor = S.mainColor === i ? null : i; renderPalette(); onShotChange(); };
    s.oncontextmenu = e => { e.preventDefault(); const i = +s.dataset.i; S.colors.splice(i, 1); if (S.mainColor === i) S.mainColor = null; else if (S.mainColor > i) S.mainColor--; renderPalette(); onShotChange(); };
  });
  const add = $("#palAdd"); if (add) add.onchange = () => { S.colors.push(add.value); S.direction.grade = "auto"; renderPalette(); onShotChange(); };
  const clr = $("#palClear"); if (clr) clr.onclick = () => { S.colors = []; S.mainColor = null; renderPalette(); onShotChange(); };
}
// Comfy's extraction: 80×80, channels quantised to 32, top five
function paletteFromImage(file) {
  return new Promise((res, rej) => {
    const img = new Image(); const url = URL.createObjectURL(file);
    img.onload = () => {
      try {
        const c = document.createElement("canvas"); c.width = c.height = 80;
        const x = c.getContext("2d"); x.imageSmoothingEnabled = false; x.drawImage(img, 0, 0, 80, 80);
        const d = x.getImageData(0, 0, 80, 80).data, hist = {};
        for (let i = 0; i < d.length; i += 4) {
          if (d[i + 3] < 128) continue;
          const q = v => Math.min(255, Math.round(v / 32) * 32);
          const hex = "#" + [d[i], d[i + 1], d[i + 2]].map(v => q(v).toString(16).padStart(2, "0")).join("");
          hist[hex] = (hist[hex] || 0) + 1;
        }
        const top = Object.entries(hist).sort((a, b) => b[1] - a[1]).slice(0, 5).map(e => e[0]);
        URL.revokeObjectURL(url); top.length ? res(top) : rej(new Error("empty"));
      } catch (e) { rej(e); }
    };
    img.onerror = rej; img.src = url;
  });
}

// ── format ─────────────────────────────────────────────────────────────
function renderFormat() {
  const c = caps();
  S.aspect = snapAspect(S.aspect, c.aspects.length ? c.aspects : CAT.aspects.map(a => a.id));
  if (c.resolutions.length && !c.resolutions.includes(S.resolution)) S.resolution = (c.m && c.m.default_resolution && c.resolutions.includes(c.m.default_resolution)) ? c.m.default_resolution : c.resolutions[c.resolutions.length > 1 ? 1 : 0];
  if (!c.resolutions.length) S.resolution = "";
  if (S.mode === "video" && c.durations.length && !c.durations.includes(S.duration)) S.duration = nearest(5, c.durations);
  const aname = id => (CAT.aspects.find(a => a.id === id) || {}).name || "";
  let h = "";
  if (c.aspects.length || S.mode === "image") {
    const list = c.aspects.length ? c.aspects : CAT.aspects.map(a => a.id);
    h += `<div><label for="fAspect">${S.mode === "video" ? "Frame" : "Aspect"}</label><select class="field" id="fAspect">${list.map(a => `<option value="${a}" ${a === S.aspect ? "selected" : ""}>${a}${aname(a) ? " · " + aname(a) : ""}</option>`).join("")}</select></div>`;
  }
  if (c.resolutions.length) h += `<div><label for="fRes">Resolution</label><select class="field" id="fRes">${c.resolutions.map(r => `<option ${r === S.resolution ? "selected" : ""}>${esc(r)}</option>`).join("")}</select></div>`;
  if (S.mode === "image") {
    h += `<div><label>${STILLS ? "Images" : "Takes"}</label><div class="stepper"><button id="tMinus" aria-label="Fewer takes">−</button><span id="tN">${S.ntakes}</span><button id="tPlus" aria-label="More takes">+</button></div></div>`;
  } else {
    if (c.durations.length && !boardOn()) h += `<div><label for="fDur">Duration</label><select class="field" id="fDur">${c.durations.map(d => `<option value="${d}" ${d === S.duration ? "selected" : ""}>${d}s</option>`).join("")}</select></div>`;
    if (c.audio) h += `<div><label>Audio</label><label class="switch" style="margin-top:6px"><input type="checkbox" id="fAudio" ${S.audio ? "checked" : ""}><i></i> ${S.audio ? "On" : "Off"}</label></div>`;
    if (!c.durations.length && !c.aspects.length) h += `<div style="grid-column:1/-1" class="hint">This model sets the length and resolution itself.</div>`;
  }
  $("#fmt").innerHTML = h;
  const on = (id, ev, fn) => { const e = $("#" + id); if (e) e[ev] = fn; };
  on("fAspect", "onchange", e => { S.aspect = e.target.value; onShotChange(); });
  on("fRes", "onchange", e => { S.resolution = e.target.value; onShotChange(); });
  on("fDur", "onchange", e => { S.duration = +e.target.value; onShotChange(); });
  on("fAudio", "onchange", e => { S.audio = e.target.checked; onShotChange(); });
  on("tMinus", "onclick", () => { S.ntakes = Math.max(1, S.ntakes - 1); onShotChange(); });
  on("tPlus", "onclick", () => { S.ntakes = Math.min(STILLS ? 20 : 4, S.ntakes + 1); onShotChange(); });
  $("#negWrap").classList.toggle("hidden", !c.negative);
  $("#seedWrap").classList.toggle("hidden", !c.seed);
}

// ── references ─────────────────────────────────────────────────────────
function slotHtml(key, label, f) {
  if (!f) return `<div class="slot" data-slot="${key}" title="${esc(label)} — click to upload, or drag a take here">+<br>${esc(label)}</div>`;
  const media = f.kind === "video" ? `<video src="${esc(f.url)}" muted></video>` : `<img src="${esc(f.thumb || f.url)}" alt="">`;
  return `<div class="slot filled" data-slot="${key}" data-name="${esc(f.name)}" title="${esc(label)}">${media}<span class="tag">${esc(label)}</span><button class="x" data-rm="${key}" data-name="${esc(f.name)}" aria-label="Remove reference: ${esc(label)}">✕</button></div>`;
}
function renderRefs() {
  const c = caps(); let h = "";
  if (S.mode === "image") {
    S.refs.forEach((r, i) => h += slotHtml("ref", S.refs.length > 1 ? `Ref ${i + 1}` : "Character", r));
    if (S.refs.length < Math.max(c.refs, 1) && (c.refs || !S.refs.length)) h += slotHtml("ref", c.refs ? (S.refs.length ? "Another ref" : "Character / product") : "Reference", null);
  } else {
    if (c.sourceVideo) h += slotHtml("source", "Video to edit", S.sourceVideo);
    if (c.firstFrame || S.firstFrame) h += slotHtml("first", "Starting frame", S.firstFrame);
    if ((c.lastFrame && S.firstFrame) || S.lastFrame) h += slotHtml("last", "Ending frame", S.lastFrame);
    if (c.refs) S.refs.forEach((r, i) => h += slotHtml("ref", `Ref ${i + 1}`, r));
    if (c.refs && S.refs.length < c.refs) h += slotHtml("ref", "Character ref", null);
  }
  $("#refrow").innerHTML = h || `<span class="hint">This model takes no reference files.</span>`;
  $$("#refrow .slot").forEach(s => {
    s.onclick = e => {
      if (e.target.closest("[data-rm]")) return;
      if (s.classList.contains("filled")) return;
      const k = s.dataset.slot;
      pickFile(k === "source" ? "video/mp4,video/quicktime,video/webm" : "image/png,image/jpeg,image/webp", f => uploadRef(f, k));
    };
    s.ondragover = e => { e.preventDefault(); s.classList.add("drop"); };
    s.ondragleave = () => s.classList.remove("drop");
    s.ondrop = e => {
      e.preventDefault(); s.classList.remove("drop");
      const tid = e.dataTransfer.getData("text/take");
      if (tid) { const t = S.takes.find(x => x.id === tid); if (t) useTakeAs(t, s.dataset.slot); return; }
      const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) uploadRef(f, s.dataset.slot);
    };
  });
  $$("#refrow [data-rm]").forEach(b => b.onclick = e => {
    e.stopPropagation(); const k = b.dataset.rm;
    if (k === "ref") S.refs = S.refs.filter(r => r.name !== b.dataset.name);
    if (k === "first") { S.firstFrame = null; S.lastFrame = null; }
    if (k === "last") S.lastFrame = null;
    if (k === "source") S.sourceVideo = null;
    onShotChange();
  });
}
function pickFile(accept, cb) { const i = $("#filePick"); i.accept = accept; i.value = ""; i.onchange = () => i.files[0] && cb(i.files[0]); i.click(); }
async function uploadRef(file, slot) {
  if (!S.reel) await ensureReel();
  const fd = new FormData(); fd.append("file", file);
  toast("Uploading…");
  const r = await api(`/reels/${S.reel.id}/refs`, { method: "POST", body: fd });
  if (r.error) return toast(r.error, true);
  placeRef({ name: r.name, url: r.url, kind: r.kind }, slot);
  toast("Added.");
}
function placeRef(f, slot) {
  if (slot === "first") S.firstFrame = f;
  else if (slot === "last") S.lastFrame = f;
  else if (slot === "source") S.sourceVideo = f;
  else if (!S.refs.find(r => r.name === f.name)) S.refs.push(f);
  onShotChange();
}
function takeFile(t) { return { name: t.file, url: t.url, thumb: t.thumb_url, kind: t.kind }; }
function useTakeAs(t, slot) {
  if (t.status !== "done") return toast("That take hasn't finished.");
  if (slot === "source") { if (t.kind !== "video") return toast("Drop a clip here."); }
  else if (t.kind !== "image") return toast("Drop a still here — or grab a frame from the clip first.");
  placeRef(takeFile(t), slot);
}

// ── prompt inspector ───────────────────────────────────────────────────
let compileT = null, lastCompiled = "";
function shotBody() {
  // a model that changes or extends a clip gets the instruction as written, not the panel's look
  const raw = S.mode === "video" && !!(model() || {}).requires_source;
  return { mode: S.mode, scene: S.scene, direction: raw ? {} : S.direction, enhance: raw ? false : S.pmode !== "plain",
    refs: S.refs.map(r => r.name), colors: S.colors, main_color: S.mainColor, model: (model() || {}).id };
}
function scheduleCompile() { clearTimeout(compileT); compileT = setTimeout(compileNow, 180); }
async function compileNow() {
  const r = await api("/compile", { method: "POST", json: shotBody() });
  if (r.error) return;
  lastCompiled = r.prompt;
  if (S.override == null) {
    $("#segs").innerHTML = r.segments.length ? r.segments.map(s => `<span class="s-${s.source}">${esc(s.text)}</span>`).join(" ") : `<span class="hint">Write a scene to see the prompt.</span>`;
  }
  if (S.pmode === "director" && S.directed && S.override != null) $("#directNotes").textContent = "Director's version below — edit it, or turn Edit off to go back to the compiled brief.";
}
function setOverride(text) {
  S.override = text;
  $("#ovToggle").checked = text != null;
  $("#override").classList.toggle("hidden", text == null);
  $("#segs").classList.toggle("hidden", text != null);
  if (text != null) $("#override").value = text;
  if (text == null) { S.directed = null; $("#directNotes").textContent = ""; scheduleCompile(); }
  updateGen();
}
async function directIt() {
  if (!S.scene.trim()) return toast("Write a scene first.");
  const b = $("#directBtn"); b.disabled = true; b.textContent = "Directing…";
  $("#main").classList.remove("noinspector");
  const r = await api("/direct", { method: "POST", json: shotBody() });
  b.disabled = false; b.textContent = "Direct it";
  if (r.error) return toast(r.error, true);
  S.directed = r.prompt; setOverride(r.prompt);
  $("#directNotes").textContent = r.notes ? "Director: " + r.notes : "Director's version — edit freely before you generate.";
  if (r.negative_prompt && !S.negative && caps().negative) { S.negative = r.negative_prompt; $("#neg").value = S.negative; }
}

// ── generate ───────────────────────────────────────────────────────────
function estimate() {
  const c = caps(); if (!c.m) return "";
  const ids = [c.m.id, ...(S.shootout[S.mode] || [])];
  let total = 0, known = true;
  ids.forEach(id => {
    const m = modelById(id); if (!m || m.price == null) { known = false; return; }
    const n = ids.length > 1 ? 1 : (S.mode === "image" ? S.ntakes : 1);
    total += (m.price_per === "second" ? m.price * (S.duration || 5) : m.price) * n;
  });
  const unit = STILLS ? "image" : "take";
  const n = ids.length > 1 ? `${ids.length} models` : (S.mode === "image" && S.ntakes > 1 ? `${S.ntakes} ${unit}s` : `1 ${unit}`);
  return known ? `${n} · ≈ ${fmtUSD(total)}` : `${n} · cost varies`;
}
function updateGen() {
  const why = blockReason();
  const noScene = !S.scene.trim() && !(S.override && S.override.trim());
  $("#blocknote").textContent = why; $("#blocknote").classList.toggle("hidden", !why);
  const g = $("#genBtn");
  g.disabled = !!why || noScene;
  const extra = (S.shootout[S.mode] || []).length;
  g.innerHTML = (extra ? `Run shootout · ${extra + 1} models` : (S.mode === "video" ? "Generate clip" : "Generate image")) + ` <kbd>⌘↵</kbd>`;
  $("#costline").textContent = noScene ? "Write a scene, set the direction, then Generate." : estimate();
}
async function ensureReel() {
  if (S.reel) return S.reel;
  const r = await api("/reels", { method: "POST", json: { name: $("#reelName").value, app: STILLS ? "cg" : "studio" } });
  if (r.error) { toast(r.error, true); throw new Error(r.error); }
  S.reel = r; saveLocal(); return r;
}
async function generate(extra) {
  if ($("#genBtn").disabled && !extra) return;
  await ensureReel();
  const c = caps();
  const body = Object.assign(shotBody(), {
    model: c.m.id, models: [c.m.id, ...(S.shootout[S.mode] || [])],
    aspect: S.aspect, resolution: S.resolution, takes: S.ntakes, duration: S.duration, audio: S.audio,
    negative_prompt: c.negative ? S.negative : "", seed: c.seed ? S.seed : "",
    first_frame: S.firstFrame && S.firstFrame.name, last_frame: S.lastFrame && S.lastFrame.name,
    source_video: S.sourceVideo && S.sourceVideo.name,
    refs: S.refs.map(r => r.name), prompt_override: S.override, colors: S.colors, main_color: S.mainColor,
  }, extra || {});
  if (!c.refs) body.refs = [];
  $("#genBtn").disabled = true;
  const r = await api(`/reels/${S.reel.id}/run`, { method: "POST", json: body });
  updateGen();
  if (r.error) return refused(r.error, body.model);
  mergeTakes(r.takes); select(r.takes[0].id); poll();
}

// ── reel + polling ─────────────────────────────────────────────────────
let pollT = null;
function mergeTakes(list) {
  const byId = new Map(S.takes.map(t => [t.id, t]));
  list.forEach(t => byId.set(t.id, t));
  S.takes = Array.from(byId.values()).sort((a, b) => a.id < b.id ? -1 : 1);
  renderStrip(); renderStage(); refreshBoard(); renderFails();
  if (list.some(t => t.status === "queued" || t.status === "running")) creditsSoon();
}
async function loadReel(id) {
  if (!STILLS) await flushBoard();
  const r = await api(`/reels/${id}`);
  if (r.error) { LS.set("reel", null); return false; }
  S.reel = r.reel; S.takes = r.takes; $("#reelName").value = r.reel.name;
  if (!STILLS) { boardFrom(r.board, id); renderKit(); if (boardOn()) onShotChange(); }
  S.picking = false; S.picked.clear(); updateStitch();
  S.current = (S.takes.slice().reverse().find(t => t.status === "done") || S.takes[S.takes.length - 1] || {}).id || null;
  refusals.length = 0;
  renderStrip(); renderStage(); renderFails(); saveLocal(); poll();
  return true;
}
function poll() {
  clearTimeout(pollT);
  if (!S.reel || !S.takes.some(t => t.status === "queued" || t.status === "running")) return;
  pollT = setTimeout(async () => {
    const r = await api(`/reels/${S.reel.id}`);
    if (!r.error) {
      const before = new Map(S.takes.map(t => [t.id, `${t.status}|${t.ahead ?? ""}|${t.wait_reason || ""}`]));
      S.takes = r.takes; renderStrip(); refreshBoard();
      const cur = S.takes.find(t => t.id === S.current);
      if (!cur || before.get(cur.id) !== `${cur.status}|${cur.ahead ?? ""}|${cur.wait_reason || ""}`) renderStage(); else tickStage();
      renderFails();
      if (S.takes.some(t => before.has(t.id) && before.get(t.id).split("|")[0] !== t.status)) creditsSoon();
    }
    poll();
  }, 2000);
}

// ── the line: a provider runs only so many at once ─────────────────────
// Animate a whole board and the clips queue on the server behind their
// provider's limit (Luma: four at a time); a waiting take says where it stands.
function lineNote(t) {
  if (!t || t.status !== "queued") return "";
  const ahead = t.ahead ? ` · ${t.ahead} ahead of it` : t.ahead === 0 ? " · it's next" : "";
  if (t.wait_reason === "busy") return `${t.lane || "The provider"} was full, so it went back in line${ahead}`;
  if (t.wait_reason === "rateLimit") return "The provider asked us to slow down; it goes again in a moment";
  return t.lane_cap ? `In line: ${t.lane} makes ${t.lane_cap} at a time${ahead}` : "";
}
const lineShort = t => `In line${t.ahead ? ` · ${t.ahead} ahead` : " · next"}`;

// ── failures: they stay on screen until you've read them ───────────────
// A video can fail minutes after the click, while you're looking elsewhere,
// and a six-second toast was all it got. Every failure in the reel from the
// last day now waits in a panel (and on its board card) until dismissed, in
// plain English with what to try; so does a run the server refused outright.
const FAIL_WINDOW = 24 * 3600;
const failSeen = new Set(LS.get("failSeen", []));
const refusals = [];                     // runs refused before anything ran: {id, title, text}
const failAsked = new Set();             // older failures you clicked on to read again
const isFailOpen = t => t.status === "failed" && !failSeen.has(t.id)
  && (failAsked.has(t.id) || Date.now() / 1000 - (t.finished || t.created || 0) < FAIL_WINDOW);
const actionName = t => ({ change: "Change", extend: "Extend", keyframe: "Keyframe", upscale: "Upscale" }[t.action] || (t.kind === "video" ? "Clip" : "Picture"));
const firstSentence = s => (String(s).match(/^.*?[.!?](?=\s|$)/) || [String(s)])[0];
function seeFail(id) {
  const n = refusals.findIndex(x => x.id === id);
  if (n >= 0) refusals.splice(n, 1);
  else { failSeen.add(id); failAsked.delete(id); LS.set("failSeen", [...failSeen].slice(-300)); }
  renderFails(); if (!STILLS) refreshBoard();
}
function unseeFail(id) { failSeen.delete(id); failAsked.add(id); LS.set("failSeen", [...failSeen]); renderFails(); if (!STILLS) refreshBoard(); }
let refusalN = 0;
function refused(text, model) {
  const m = model && modelById(model), title = m ? `${m.label}: not run` : "Not run";
  const same = refusals.find(r => r.title === title && r.text === text);     // clicked again: point at it, don't stack it
  if (!same) refusals.push({ id: `r${++refusalN}`, title, text });
  if (/Comfy has \$/.test(text)) loadCredits(true);
  renderFails();
  if (same) { const c = $(`#fails [data-fail="${same.id}"]`); if (c) { c.classList.remove("flash"); void c.offsetWidth; c.classList.add("flash"); } }
}
function showFail(t) {
  if (!STILLS && t.board && B.shots.some(s => s.id === t.board)) {
    if (!boardOn()) setView("board");
    const c = $(`.bcard[data-s="${t.board}"]`);
    if (c) { c.scrollIntoView({ behavior: "smooth", block: "center" }); c.classList.add("flash"); setTimeout(() => c.classList.remove("flash"), 1800); }
  } else {
    if (!STILLS && boardOn()) setView("shot");
    select(t.id);
  }
}
let failsKey = "";
function renderFails() {
  const box = $("#fails"); if (!box) return;
  const open = S.takes.filter(isFailOpen);
  // polling calls this every 2 s: redraw only when the list changes, or an open "What the model said" snaps shut
  const key = [...refusals.map(r => r.id), ...open.map(t => t.id + (t.hint || t.error || ""))].join("|");
  if (key === failsKey) return;
  failsKey = key;
  box.innerHTML = refusals.map(r => `<div class="fail" data-fail="${r.id}" role="alert">
      <button class="x" data-fx="${r.id}" aria-label="Dismiss">✕</button><b>${esc(r.title)}</b><p>${esc(r.text)}</p></div>`).join("")
    + open.map(t => `<div class="fail" data-fail="${t.id}" role="alert">
      <button class="x" data-fx="${t.id}" aria-label="Dismiss">✕</button>
      <b>${esc(actionName(t))} failed · ${esc(t.label)}</b><span class="where">Shot ${esc(t.shot)} · take ${esc(t.letter)}</span>
      <p>${esc(t.hint || t.error || "It failed.")}</p>
      ${t.hint && t.error ? `<details><summary>What the model said</summary><p>${esc(t.error)}</p></details>` : ""}
      <div class="acts"><button class="btn small" data-fshow="${t.id}">Show me</button><button class="btn small" data-fx="${t.id}">Got it</button></div></div>`).join("");
  box.classList.toggle("hidden", !box.children.length);
  $$("[data-fx]", box).forEach(b => b.onclick = () => seeFail(b.dataset.fx));
  $$("[data-fshow]", box).forEach(b => b.onclick = () => { const t = S.takes.find(x => x.id === b.dataset.fshow); if (t) showFail(t); });
}

// ── stage ──────────────────────────────────────────────────────────────
function cur() { return S.takes.find(t => t.id === S.current); }
// the models ComfyUI has a partner node for (studio/comfy_export.py SUPPORTED)
const comfyNode = m => /^(ltx\/ltx-2-5-(fast|pro)|bfl\/flux-3-(image|video)|byteplus\/dreamina-seedance|wan\/wan3\.0-video|veo\/veo-3\.1|minimax\/minimax-h3$|kling\/kling-(v3-omni|video-o1)$|vertexai\/gemini-nano-banana-2\.1$)/.test(m || "");
function select(id) { S.current = id; S.compareWith = null; S.zoom = false; renderStage(); renderStrip(); }
function frameFor(t) {
  const f = $("#frame"), st = $("#stage");
  const ar = t && t.w && t.h ? t.w / t.h : ratioOf(t ? t.aspect : S.aspect);
  f.style.aspectRatio = ar;
  // a tall frame is limited by height, a wide one by width
  const box = st.getBoundingClientRect();
  f.classList.toggle("letter", box.width / Math.max(1, box.height) > ar);
}
let elapsedT = null;
function tickStage() {
  const t = cur(); const el = $("#elapsed");
  if (t && el) {
    const s = (Date.now() / 1000) - (t.started || t.created);
    el.textContent = fmtTime(s);
    const slow = $("#slownote"); if (slow) slow.classList.toggle("hidden", s < 30);
    const gp = $("#gradeprog"); if (gp) gp.textContent = t.progress || "";
  }
}
function renderStage() {
  const t = cur(), f = $("#frame"), bar = $("#takebar");
  clearInterval(elapsedT);
  frameFor(t);
  if (!t) {
    f.innerHTML = `<div class="empty"><div class="ar">${esc(S.aspect)}</div><b>Your first shot appears here</b>Write a scene, set the direction, then Generate.</div>`;
    bar.classList.add("hidden"); $("#actions").innerHTML = ""; renderTakeInfo(); return;
  }
  f.classList.toggle("zoom", S.zoom);
  if (lineNote(t)) {
    f.innerHTML = `<div class="render"><div class="spinner"></div><b>Shot ${t.shot}, take ${t.letter} is waiting its turn</b>
      <div class="hint" style="margin-top:8px;max-width:380px">${esc(lineNote(t))}. It starts by itself; you can keep working.</div>
      <div style="margin-top:12px"><button class="btn small" id="cancelTake">Don't run it</button></div></div>`;
    $("#cancelTake").onclick = () => takeAction("cancel");
  } else if ((t.status === "queued" || t.status === "running") && t.action === "grade") {
    f.innerHTML = `<div class="render"><div class="spinner"></div>Grading shot ${t.shot}, take ${t.letter} · <span id="gradeprog">${esc(t.progress || "")}</span> · <span id="elapsed">0:00</span>
      <div class="hint" style="margin-top:8px;max-width:380px">Every frame goes through the look in Rust, here on the server. You can keep working; the graded take lands beside the original.</div>
      <div style="margin-top:12px"><button class="btn small" id="cancelTake">Stop grading</button></div></div>`;
    $("#cancelTake").onclick = () => takeAction("cancel");
    tickStage(); elapsedT = setInterval(tickStage, 1000);
  } else if (t.status === "queued" || t.status === "running") {
    f.innerHTML = `<div class="render"><div class="spinner"></div>Rendering shot ${t.shot}, take ${t.letter} on ${esc(t.label)} · <span id="elapsed">0:00</span>
      <div class="hint hidden" id="slownote" style="margin-top:8px;max-width:360px">${t.kind === "video" ? "Clips usually take one to five minutes. You can keep directing; it lands in the sequence when it's done." : "Still going. Some models take about a minute."}</div>
      <div style="margin-top:12px"><button class="btn small" id="cancelTake">Stop waiting</button></div></div>`;
    $("#cancelTake").onclick = () => takeAction("cancel");
    tickStage(); elapsedT = setInterval(tickStage, 1000);
  } else if (t.status === "done") {
    const other = S.compareWith && S.takes.find(x => x.id === S.compareWith);
    if (other) {
      const media = (x, cls) => x.kind === "video" ? `<video class="${cls}" src="${esc(x.url)}" muted loop autoplay playsinline></video>` : `<img class="${cls}" src="${esc(x.url)}" alt="">`;
      f.innerHTML = `<div class="cmp" id="cmp">${media(t, "a")}${media(other, "b")}<div class="handle"></div><span class="lab a">${esc(t.shot + t.letter)} · ${esc(t.label)}</span><span class="lab b2">${esc(other.shot + other.letter)} · ${esc(other.label)}</span></div>`;
      const cmp = $("#cmp");
      const move = e => { const r = cmp.getBoundingClientRect(); const x = ((e.touches ? e.touches[0].clientX : e.clientX) - r.left) / r.width; cmp.style.setProperty("--cut", Math.max(0, Math.min(1, x)) * 100 + "%"); };
      cmp.onmousemove = move; cmp.ontouchmove = move;
    } else if (t.kind === "video") {
      f.innerHTML = `<video class="main" id="vid" src="${esc(t.url)}" ${t.poster ? `poster="${API}/reels/${t.reel}/file/${esc(t.poster)}"` : ""} controls loop playsinline autoplay muted></video>`;
    } else {
      f.innerHTML = `<img class="main" id="mainImg" src="${esc(t.url)}" alt="${esc(t.prompt)}">`;
      const img = $("#mainImg");
      img.onclick = e => {
        if (!S.zoom) { const r = img.getBoundingClientRect(); f.style.setProperty("--zx", ((e.clientX - r.left) / r.width * 100) + "%"); f.style.setProperty("--zy", ((e.clientY - r.top) / r.height * 100) + "%"); }
        S.zoom = !S.zoom; f.classList.toggle("zoom", S.zoom); renderActions();
      };
      img.onmousemove = e => { if (!S.zoom) return; const r = f.getBoundingClientRect(); f.style.setProperty("--zx", ((e.clientX - r.left) / r.width * 100) + "%"); f.style.setProperty("--zy", ((e.clientY - r.top) / r.height * 100) + "%"); };
    }
  } else {
    const head = { cancelled: "You stopped this take", failed: t.reason === "policy" ? "Blocked by the model's content policy" : t.reason === "credits" ? "Not enough credits for this shot" : "This take failed" }[t.status] || "This take failed";
    const alt = alternativeModel(t);
    f.innerHTML = `<div class="notice"><h3>${esc(head)}</h3><p>${esc(t.hint || t.error || "")}</p>
      ${t.hint && t.error ? `<p class="raw">What the model said: ${esc(t.error)}</p>` : ""}
      <div style="display:flex;gap:6px;justify-content:center;flex-wrap:wrap"><button class="btn small" id="nTry">Try again</button>
      ${alt ? `<button class="btn small" id="nAlt">Try on ${esc(alt.label)}</button>` : ""}<button class="btn small" id="nEdit">Edit scene</button></div>
      ${t.request_id ? `<div class="rid">Request ID: ${esc(t.request_id)}</div>` : ""}</div>`;
    $("#nTry").onclick = () => takeAction("retry");
    if (alt) $("#nAlt").onclick = () => takeAction("retry", { model: alt.id });
    $("#nEdit").onclick = () => { reuseSettings(t); $("#scene").focus(); };
  }
  // take bar
  const shotTakes = S.takes.filter(x => x.shot === t.shot);
  bar.classList.remove("hidden");
  bar.innerHTML = `<span class="shotno">Shot ${t.shot}</span><span class="letters">${shotTakes.map(x => `<button class="${x.id === t.id ? "on" : ""} ${x.status === "failed" ? "bad" : ""}" data-t="${x.id}" title="${esc(x.label)}">${esc(x.letter)}</button>`).join("")}</span>
    <span class="meta">${esc(t.label)} · ${esc(t.aspect || "")}${t.elapsed ? ` · ${t.elapsed}s` : ""}${t.action && t.action !== "generate" ? ` · ${esc(t.action)}` : ""}</span><span class="sp"></span>
    ${t.status === "done" ? `<button class="iconbtn ${t.starred ? "on" : ""}" id="starBtn" title="Star (S)">★</button><a class="iconbtn" href="${esc(t.url)}?download=1">Download</a>` : ""}`;
  $$("#takebar .letters button").forEach(b => b.onclick = () => select(b.dataset.t));
  const sb = $("#starBtn"); if (sb) sb.onclick = () => toggleStar(t);
  renderActions(); renderTakeInfo();
}
function alternativeModel(t) {
  const list = byKind(t.kind).filter(m => m.id !== t.model && !m.requires_frame && !m.requires_ref && !m.requires_source);
  return list[0];
}
function renderActions() {
  const t = cur(), a = $("#actions");
  if (!t || t.status !== "done") {
    // a stopped take may still have finished at the provider (and been billed): offer to collect it
    const recheck = t && t.request_id && (t.status === "cancelled" || !["validation", "policy", "credits"].includes(t.reason));
    a.innerHTML = t && (t.status === "failed" || t.status === "cancelled") ? `${recheck ? `<button class="btn primary" data-a="recheck" title="Ask the provider again: if it finished, collect the result">Check again</button>` : ""}<button class="btn" data-a="reuse">Reuse settings</button><button class="btn" data-a="delete">Delete take</button>` : "";
    wireActions(); return;
  }
  const img = t.kind === "image", vid = t.kind === "video";
  const anyI2V = byKind("video").some(m => m.i2v || m.first_frame);
  const others = S.takes.filter(x => x.id !== t.id && x.status === "done" && x.kind === t.kind);
  a.innerHTML = [
    `<button class="btn" data-a="again" title="Same settings, new take">Generate again</button>`,
    `<button class="btn" data-a="reuse" title="Load this take's settings into the panel">Reuse settings</button>`,
    img ? `<button class="btn" data-a="asref" title="Use it as a character / product reference">Use as reference</button>` : "",
    img && anyI2V ? `<button class="btn" data-a="animate" title="Make it the starting frame of a clip">Animate</button>` : "",
    img && anyI2V ? `<button class="btn" data-a="asend" title="Make it the ending frame of a clip">Use as end frame</button>` : "",
    vid ? `<button class="btn" data-a="continue" title="Start the next clip from this clip's last frame">Continue shot</button>` : "",
    vid ? `<button class="btn" data-a="grab" title="Pull the paused frame out as a still">Grab frame</button>` : "",
    vid ? `<button class="btn" data-a="change" title="Restyle it, change the season or the light, swap the product or wardrobe from a reference — motion kept">Change clip</button>` : "",
    vid && extendTools().length ? `<button class="btn" data-a="extend" title="Carry the clip on past its end, camera move and all">Extend clip</button>` : "",
    vid ? `<button class="btn" data-a="leadin" title="Make a shot that ends exactly on this clip's first frame">Lead into it</button>` : "",
    `<span class="sep"></span>`,
    t.share ? `<button class="btn" data-a="share" title="Copy the public link again">Copy share link</button><button class="btn" data-a="unshare" title="Take the public page down: the link stops working">Unshare</button>`
      : `<button class="btn" data-a="share" title="A public page for this take that anyone with the link can watch">Share</button>`,
    comfyNode(t.model) ? `<a class="btn" href="${API}/reels/${t.reel}/comfyui?tid=${t.id}" title="This take as a ComfyUI workflow: drop workflow_api.json on the ComfyUI canvas (keyframes included)">Open in ComfyUI</a>` : "",
    `<button class="btn ${S.upAsk === t.id ? "primary" : ""}" data-a="upscale" title="${img ? "A bigger still: ×2 or ×4" : "A bigger clip: 1080p, 1440p or 4K"}">Upscale…</button>`,
    img ? `<button class="btn" data-a="cutout" title="Cut the subject out onto transparency">Remove background</button>` : "",
    others.length ? `<select class="field" data-a="compare" style="width:auto"><option value="">Compare with…</option>${others.slice(-24).reverse().map(x => `<option value="${x.id}" ${S.compareWith === x.id ? "selected" : ""}>${x.shot}${x.letter} · ${esc(x.label)}</option>`).join("")}</select>` : "",
    img ? `<button class="btn ${S.zoom ? "primary" : ""}" data-a="zoom" title="Click the picture to zoom where you click">${S.zoom ? "Zoom out" : "Zoom"}</button>` : "",
    `<button class="btn" data-a="copy" title="Copy the exact prompt">Copy prompt</button>`,
    `<button class="btn" data-a="delete">Delete</button>`,
  ].join("") + (S.upAsk === t.id ? upAskHtml(t) : "");
  wireActions();
}
// Upscale asks where to first: a clip to 1080p, 1440p or 4K (Gobo keeps clips
// at UHD at most: past it, players stutter), a still ×2 or ×4, and which
// upscaler, with what it costs and how far it goes. The server works out the
// scale that lands there.
const UP_TO = [["1080p", 1080, "1080p"], ["1440p", 1440, "1440p"], ["4k", 2160, "4K"]];
const upModels = t => byKind(t.kind === "video" ? "vupscale" : "upscale").filter(m => m.reach && (m.reach.scale || (m.reach.tiers || []).length));
function upPick(t) {
  const list = upModels(t), want = $("#upModel") ? $("#upModel").value : LS.get(`up.model.${t.kind}`, "");
  return list.find(m => m.id === want) || list.find(m => m.default) || list[0];
}
function upTargets(t) {
  const w = +t.w || 0, h = +t.h || 0;
  if (t.kind === "video") {
    const short = Math.min(w, h);
    return UP_TO.filter(([, px]) => !short || px > short).map(([id, px, label]) => {
      const k = short ? px / short : 0;
      return { id, px, label, size: k ? `${Math.round(w * k / 2) * 2}×${Math.round(h * k / 2) * 2}` : "" };
    });
  }
  return [2, 4].map(s => ({ id: String(s), px: Math.max(w, h) * s, label: `×${s}`, size: w ? `${w * s}×${h * s}` : "" }));
}
function upNote(t, m, to) {
  if (!m || !to) return "";
  const r = m.reach || {}, now = t.kind === "video" ? Math.min(+t.w || 0, +t.h || 0) : Math.max(+t.w || 0, +t.h || 0);
  if (r.scale && r.scale.max && now && to.px / now > r.scale.max + 1e-6)
    return `${m.label} goes up to ×${r.scale.max}, so this lands smaller than ${to.label}.`;
  if ((r.tiers_px || []).length && !r.tiers_px.some(px => px >= to.px))
    return `${m.label} goes up to ${r.tiers[r.tiers.length - 1]}, so this lands smaller than ${to.label}.`;
  return "";
}
function upAskHtml(t) {
  const targets = upTargets(t), list = upModels(t), m = upPick(t);
  if (!targets.length || !m) return `<div class="upask"><span>${!m ? "No upscaler is available right now." : `It's ${t.w}×${t.h} already: 4K is as far as Gobo takes a clip.`}</span><button class="btn small" data-up="x">OK</button></div>`;
  const want = LS.get(`up.to.${t.kind}`, t.kind === "video" ? "4k" : "2"), sel = targets.find(x => x.id === (S.upTo || want)) || targets[targets.length - 1];
  return `<div class="upask"><b>Upscale to</b>
      <span class="seg" id="upTo">${targets.map(x => `<button data-to="${x.id}" class="${x.id === sel.id ? "on" : ""}">${x.label}${x.size ? ` <small>${x.size}</small>` : ""}</button>`).join("")}</span>
      <select class="field" id="upModel" aria-label="Upscaler">${list.map(x => `<option value="${x.id}" ${x.id === m.id ? "selected" : ""}>${esc(x.label)}${x.price != null ? ` · ≈${fmtUSD(x.price)}${x.price_per === "second" ? "/s" : ""}` : ""}</option>`).join("")}</select>
      <button class="btn small primary" data-up="go">Upscale to ${sel.label}</button><button class="btn small" data-up="x">Cancel</button>
      <span class="hint">${t.w && t.h ? `Now ${t.w}×${t.h}.` : ""} ${esc(upNote(t, m, sel))}</span></div>`;
}
async function upGo(t) {
  const sel = upTargets(t).find(x => x.id === ($("#upTo .on") || {}).dataset?.to), m = upPick(t);
  if (!sel || !m) return;
  LS.set(`up.to.${t.kind}`, sel.id); LS.set(`up.model.${t.kind}`, m.id);
  S.upAsk = null; S.upTo = null;
  const r = await api(`/reels/${S.reel.id}/takes/${t.id}/upscale`, { method: "POST", json: { model: m.id, ...(t.kind === "video" ? { to: sel.id } : { scale: +sel.id }) } });
  if (r.error) { renderActions(); return refused(r.error, m.id); }
  mergeTakes([r.take]); select(r.take.id); poll();
  toast(`Upscaling to ${sel.label}${sel.size ? ` (${sel.size})` : ""} on ${m.label}.${r.note ? " " + r.note : ""}`);
}
function wireActions() {
  $$("#actions [data-a]").forEach(b => {
    const a = b.dataset.a;
    if (b.tagName === "SELECT") b.onchange = () => { S.compareWith = b.value || null; renderStage(); };
    else b.onclick = () => takeAction(a);
  });
  const t = cur();
  $$("#upTo button").forEach(b => b.onclick = () => { S.upTo = b.dataset.to; renderActions(); });
  const um = $("#upModel"); if (um) um.onchange = () => { LS.set(`up.model.${t.kind}`, um.value); renderActions(); };
  $$("[data-up]").forEach(b => b.onclick = () => b.dataset.up === "go" ? upGo(t) : (S.upAsk = null, S.upTo = null, renderActions()));
}
async function takeAction(a, opts) {
  const t = cur(); if (!t) return;
  const R = `/reels/${S.reel.id}/takes/${t.id}`;
  if (a === "again") { reuseSettings(t); return generate({ parent: t.id, action: "again" }); }
  if (a === "reuse") return reuseSettings(t);
  if (a === "share") {
    const r = t.share ? { url: `/f/${t.share}` } : await api(`${R}/share`, { method: "POST", json: { title: S.reel.name } });
    if (r.error) return toast(r.error, true);
    if (r.take) mergeTakes([r.take]);
    return copyLink(location.origin + r.url);
  }
  if (a === "unshare") {
    const r = await api(`${R}/share`, { method: "DELETE" });
    if (r.error) return toast(r.error, true);
    mergeTakes([r.take]);
    return toast("Unshared: that link no longer works.");
  }
  if (a === "asref") { setMode("image"); useTakeAs(t, "ref"); return toast("Added as a reference."); }
  if (a === "animate") { animateFrom(t, "first"); return; }
  if (a === "asend") { animateFrom(t, "last"); return; }
  if (a === "continue" || a === "grab") {
    const v = $("#vid"); const at = a === "continue" ? "last" : (v ? v.currentTime : 0);
    toast(a === "continue" ? "Reading the last frame…" : "Grabbing the frame…");
    const r = await api(`${R}/frame`, { method: "POST", json: { at } });
    if (r.error) return toast(r.error, true);
    mergeTakes([r.take]);
    if (a === "continue") {
      reuseSettings(t);
      S.firstFrame = takeFile(r.take); S.lastFrame = null; setMode("video");
      toast("The next clip starts where this one ended. Describe what happens next.");
      $("#scene").focus(); $("#scene").select();
    } else select(r.take.id);
    return;
  }
  if (a === "change" || a === "extend") {
    setMode("video");
    const tools = a === "extend" ? extendTools() : changeTools(), m = model();
    const pick = tools.find(x => x.id === (m && m.id)) || (a === "extend" ? defaultExtend() : defaultChange());
    if (!pick) return toast(`No ${a === "extend" ? "extend" : "video-change"} model is connected yet.`, true);
    S.model.video = pick.id; S.sourceVideo = takeFile(t); S.firstFrame = S.lastFrame = null; S.override = null; setOverride(null);
    S.pmode = "plain"; S.scene = ""; $("#scene").value = "";
    onShotChange(); $("#scene").focus();
    $("#scene").placeholder = a === "extend" ? "What happens next: she keeps walking and turns the corner…" : "The change: make it a snowy winter afternoon; put the Icebox bottle in her hand…";
    return toast(a === "extend" ? `${pick.label} carries the clip on. Say what happens next; the model picker has the other extend models.`
      : `${pick.label} changes the clip and keeps its motion. Describe the change; for a product or wardrobe swap pick a model that takes references (Kling O1 Reference, Pruna Replace).`);
  }
  if (a === "leadin") {
    toast("Reading the first frame…");
    const r = await api(`${R}/frame`, { method: "POST", json: { at: 0 } });
    if (r.error) return toast(r.error, true);
    mergeTakes([r.take]); setMode("video");
    const m = model();
    if (!(m && framed(m).last_frame)) { const pick = byKind("video").find(x => framed(x).last_frame && !x.requires_source); if (pick) S.model.video = pick.id; }
    S.sourceVideo = null; S.firstFrame = null; S.lastFrame = takeFile(r.take);
    onShotChange(); $("#scene").focus();
    return toast("The new shot ends on this clip's first frame. Add its opening frame (upload one, or drag a keyframe onto Starting frame), then describe the move into it.");
  }
  if (a === "upscale") { S.upAsk = S.upAsk === t.id ? null : t.id; S.upTo = null; return renderActions(); }
  if (a === "cutout") {
    const r = await api(`${R}/upscale`, { method: "POST", json: { tool: "cutout" } });
    if (r.error) return refused(r.error);
    mergeTakes([r.take]); select(r.take.id); poll(); return;
  }
  if (a === "zoom") { S.zoom = !S.zoom; $("#frame").classList.toggle("zoom", S.zoom); return renderActions(); }
  if (a === "copy") { try { await navigator.clipboard.writeText(t.prompt || ""); toast("Prompt copied."); } catch (e) { toast("Couldn't copy.", true); } return; }
  if (a === "delete") {
    if (!confirm("Delete this take?")) return;
    const r = await api(R, { method: "DELETE" }); if (r.error) return toast(r.error, true);
    S.takes = S.takes.filter(x => x.id !== t.id); S.current = (S.takes[S.takes.length - 1] || {}).id; renderStrip(); renderStage(); return;
  }
  if (a === "recheck") {
    const r = await api(`${R}/recheck`, { method: "POST" });
    if (r.error) return toast(r.error, true);
    mergeTakes([r.take]); select(r.take.id); poll();
    return toast(r.take.status === "done" ? "It had finished. Collected." : "Still rendering. Waiting for it again.");
  }
  if (a === "retry") {
    const r = await api(`${R}/retry`, { method: "POST", json: opts || {} });
    if (r.error) return refused(r.error, (opts && opts.model) || t.model);
    mergeTakes([r.take]); select(r.take.id); poll(); return;
  }
  if (a === "cancel") {
    const r = await api(`${R}/cancel`, { method: "POST" });
    if (!r.error) mergeTakes([r.take]);
  }
}
function animateFrom(t, slot) {
  setMode("video");
  const m = model();
  if (!(m && (m.i2v || m.first_frame))) {
    const pick = byKind("video").find(x => x.i2v || x.first_frame);
    if (pick) { S.model.video = pick.id; toast(`${pick.label} can start from a still — switched to it.`); }
  }
  if (slot === "last" && !S.firstFrame) { toast("Add a starting frame first; the ending frame needs one."); }
  if (slot === "first") { S.firstFrame = takeFile(t); }
  else if (S.firstFrame) { S.lastFrame = takeFile(t); }
  if (!S.scene.trim()) S.scene = t.settings && t.settings.scene || "";
  $("#scene").value = S.scene;
  onShotChange(); $("#scene").focus();
}
function reuseSettings(t) {
  const s = t.settings || {};
  if (s.mode) setMode(s.mode, true);
  if (s.scene != null) { S.scene = s.scene; $("#scene").value = s.scene; }
  if (s.direction) S.direction = Object.assign(baseDirection(), s.direction);
  S.pmode = s.enhance === false ? "plain" : (s.prompt_override ? "director" : "cinematic");
  ["aspect", "resolution", "duration", "audio"].forEach(k => { if (s[k] != null && s[k] !== "") S[k] = s[k]; });
  if (s.takes) S.ntakes = Math.max(1, Math.min(4, +s.takes || 1));
  S.colors = s.colors || []; S.mainColor = s.main_color != null ? s.main_color : null;
  S.negative = s.negative_prompt || ""; $("#neg").value = S.negative;
  S.seed = s.seed || ""; $("#seed").value = S.seed;
  if (modelById(t.model)) S.model[t.kind] = t.model;
  const fileRef = n => n ? { name: n, url: `${API}/reels/${S.reel.id}/file/${n}`, kind: /\.(mp4|mov|webm)$/i.test(n) ? "video" : "image" } : null;
  S.refs = (s.refs || []).map(fileRef);
  S.firstFrame = fileRef(s.first_frame); S.lastFrame = fileRef(s.last_frame); S.sourceVideo = fileRef(s.source_video);
  setOverride(s.prompt_override || null);
  onShotChange();
}
async function toggleStar(t) {
  const r = await api(`/reels/${S.reel.id}/takes/${t.id}/star`, { method: "POST", json: { starred: !t.starred } });
  if (!r.error) mergeTakes([r.take]);
}
function renderTakeInfo() {
  const t = cur(), el = $("#takeMeta");
  if (!t) { el.innerHTML = "Nothing selected yet."; return; }
  const rows = [["Shot", `${t.shot}${t.letter}`], ["Model", t.label], ["Status", t.status], ["Aspect", t.aspect],
    ["Size", t.w ? `${t.w}×${t.h}` : ""], ["Length", t.seconds ? `${(+t.seconds).toFixed(1)}s` : ""], ["Time", t.elapsed ? `${t.elapsed}s` : ""],
    ["Seed", t.request && t.request.seed], ["From", t.action && t.action !== "generate" ? t.action : ""], ["Request", t.request_id]];
  el.innerHTML = `<dl class="kv">${rows.filter(r => r[1] !== undefined && r[1] !== null && r[1] !== "").map(r => `<dt>${esc(r[0])}</dt><dd>${esc(r[1])}</dd>`).join("")}</dl>
    ${t.prompt ? `<p class="sectitle" style="margin-top:10px">Prompt sent</p><div class="segs" style="font-size:.78rem">${esc(t.prompt)}</div>` : ""}
    ${t.payload_preview ? `<p class="sectitle" style="margin-top:10px">Payload · ${esc(t.endpoint || "")}</p><pre class="payload">${esc(JSON.stringify(t.payload_preview, null, 2))}</pre>` : ""}`;
}

// ── sequence strip ─────────────────────────────────────────────────────
function renderStrip() {
  const shots = {};
  S.takes.forEach(t => (shots[t.shot] = shots[t.shot] || []).push(t));
  const n = S.takes.length;
  $("#seqCount").textContent = S.picking ? `${S.picked.size} selected`
    : n ? `${n} take${n > 1 ? "s" : ""} · ${Object.keys(shots).length} shot${Object.keys(shots).length > 1 ? "s" : ""}` : "";
  const st = t => ({ queued: lineNote(t) ? "In line" : "Queued", running: "Rendering", cancelled: "Stopped", failed: t.reason === "policy" ? "Blocked" : "Failed" }[t.status]);
  const order = filmOrder();
  // a take that didn't make it is a sliver beside the ones that did: its
  // letter and a mark, why on hover, the whole story when clicked
  const dud = t => t.status === "failed" || t.status === "cancelled";
  const why = t => `${st(t)} · ${t.label}${t.hint || t.error ? `: ${firstSentence(t.hint || t.error)}` : ""} · click for what to try`;
  $("#strip").innerHTML = n ? Object.keys(shots).sort((a, b) => a - b).map(k => `<div class="shotgrp"><span class="gl">Shot ${k}</span><div class="items">${shots[k].map(t => `
    <div class="tk ${t.id === S.current ? "on" : ""} ${canSelect(t) ? "can" : ""} ${S.picked.has(t.id) ? "picked" : ""} ${dud(t) ? "dud" : ""}" data-t="${t.id}" data-kind="${t.kind}" draggable="${t.status === "done"}" title="${esc(dud(t) ? why(t) : `${t.label} · ${t.action || ""}`)}">
      ${t.thumb_url ? `<img src="${esc(t.thumb_url)}" alt="" loading="lazy">` : ""}
      ${dud(t) ? `<div class="st ${t.status === "failed" ? "bad" : ""}" aria-label="${esc(st(t))}">${t.status === "failed" ? "✕" : "■"}</div>`
        : t.status !== "done" ? `<div class="st">${st(t)}<br>${esc(t.label)}</div>` : ""}
      <span class="lt">${esc(t.letter)}</span>${t.kind === "video" ? `<span class="kind">▶${t.seconds ? " " + Math.round(t.seconds) + "s" : ""}</span>` : ""}
      ${t.starred ? `<span class="star">★</span>` : ""}
      ${canSelect(t) ? `<input type="checkbox" class="pick" data-pick="${t.id}" ${S.picked.has(t.id) ? "checked" : ""}>` : ""}
      ${S.picking && S.picked.has(t.id) ? `<span class="ord">${order.indexOf(t.id) + 1}</span>` : ""}
      ${t.status === "running" || t.status === "queued" ? `<span class="mini"><i></i></span>` : ""}</div>`).join("")}</div></div>`).join("")
    : `<span class="hint">Takes land here, grouped by shot. Drag one onto a reference slot to use it.</span>`;
  $$("#strip .tk").forEach(el => {
    el.onclick = e => {
      if (e.target.classList.contains("pick")) return;
      // selecting: a click toggles; takes still rendering sit it out. ⌘/Shift-click starts selecting.
      if (!S.picking && (e.metaKey || e.ctrlKey || e.shiftKey) && el.classList.contains("can")) { S.picking = true; S.picked.clear(); }
      if (S.picking) { if (el.classList.contains("can")) togglePick(el.dataset.t); return; }
      select(el.dataset.t);
    };
    el.ondragstart = e => { e.dataTransfer.setData("text/take", el.dataset.t); e.dataTransfer.effectAllowed = "copy"; };
  });
  $$("#strip .pick").forEach(p => p.onchange = () => togglePick(p.dataset.pick, p.checked));
  const duds = S.takes.filter(dud), cf = $("#clearFails");
  if (cf) {
    cf.classList.toggle("hidden", !duds.length || S.picking);
    cf.textContent = `Clear ${duds.length} failed`;
    cf.title = `Take the ${duds.length} failed and stopped take${duds.length === 1 ? "" : "s"} out of this ${REELWORD}`;
  }
  if (S.reel) { $("#dlAll").href = `${API}/reels/${S.reel.id}/download?size=${dlSize()}`; $("#dlStar").href = `${API}/reels/${S.reel.id}/download?starred=1&size=${dlSize()}`; }
  const film = S.takes.filter(t => t.action === "stitch" && t.status === "done").sort((a, b) => a.id < b.id ? 1 : -1)[0];
  $("#dlFilm").classList.toggle("hidden", !film || S.picking);
  if (film) $("#dlFilm").href = film.url + "?download=1";
}
// The bin's selection: pick takes to stitch, download or delete. A take still
// rendering can't be picked (stop it first); only finished ones stitch or download.
// Content Generator: stills delivered at 2K/4K/8K, 300 dpi (0 = as made)
const dlSize = () => ($("#dlSize") && +$("#dlSize").value) || 0;
const canSelect = t => t.status !== "running" && t.status !== "queued";
const stitchable = t => t.status === "done" && (t.kind === "video" || t.kind === "image");
const takeById = id => S.takes.find(t => t.id === id);
// the order a stitch plays in: the strip's, shot by shot, left to right
function filmOrder() {
  const shot = {};
  S.takes.forEach((t, i) => shot[t.id] = [+t.shot, i]);
  return [...S.picked].filter(id => shot[id]).sort((a, b) => shot[a][0] - shot[b][0] || shot[a][1] - shot[b][1]);
}
function togglePick(id, on) {
  if (on === undefined) on = !S.picked.has(id);
  on ? S.picked.add(id) : S.picked.delete(id);
  updateStitch(); renderStrip();
}
// A first cut to start from: one take per shot — the starred one, else a
// clip over a still, else the newest — and no earlier stitches. Everything
// finished if that leaves fewer than two.
function firstCut() {
  const ok = S.takes.filter(t => stitchable(t) && t.action !== "stitch"), by = {};
  const rank = t => [t.starred ? 1 : 0, t.kind === "video" ? 1 : 0, t.id];
  const better = (a, b) => { const x = rank(a), y = rank(b); return x[0] - y[0] || x[1] - y[1] || (x[2] > y[2] ? 1 : -1); };
  ok.forEach(t => { if (!by[t.shot] || better(t, by[t.shot]) > 0) by[t.shot] = t; });
  const cut = Object.values(by);
  return (cut.length >= 2 ? cut : ok).map(t => t.id);
}
function startSelect() { S.picking = true; S.picked.clear(); updateStitch(); renderStrip(); }
function startFilm() {
  const n = S.takes.filter(t => stitchable(t) && t.action !== "stitch").length;
  if (n < 2) return toast(n ? "Stitching joins two or more takes. Make another one first." : "Stitching joins takes. Make two first.");
  S.picked = new Set(firstCut()); S.picking = true; updateStitch(); renderStrip();
}
function stopFilm() { S.picking = false; S.picked.clear(); updateStitch(); renderStrip(); }
// everything that can be picked, alternates included; earlier stitches stay out
function pickAll() { S.takes.filter(t => canSelect(t) && t.action !== "stitch").forEach(t => S.picked.add(t.id)); updateStitch(); renderStrip(); }
function updateStitch() {
  const ids = [...S.picked], sel = ids.map(takeById).filter(Boolean);
  const nStitch = sel.filter(stitchable).length, nDone = sel.filter(t => t.status === "done").length;
  $("#seq").classList.toggle("picking", S.picking);
  ["#stitchGo", "#stitchCancel", "#stitchHint", "#stitchAll", "#selDl", "#selDel"].forEach(s => $(s).classList.toggle("hidden", !S.picking));
  ["#selBtn", "#stitchBtn", "#dlAll", "#dlStar"].forEach(s => $(s).classList.toggle("hidden", S.picking || (STILLS && s === "#stitchBtn")));
  $("#holdWrap").classList.toggle("hidden", !S.picking || !sel.some(t => stitchable(t) && t.kind === "image"));
  $("#stitchGo").textContent = nStitch < 2 ? "Stitch (pick 2+)" : `Stitch ${nStitch}`;
  $("#stitchGo").disabled = nStitch < 2;
  $("#selDl").textContent = `Download ${nDone || ""}`.trim();
  $("#selDl").classList.toggle("off", !nDone);
  if (S.reel) $("#selDl").href = nDone ? `${API}/reels/${S.reel.id}/download?size=${dlSize()}&takes=${sel.filter(t => t.status === "done").map(t => t.id).join(",")}` : "#";
  $("#selDel").textContent = `Delete ${ids.length || ""}`.trim();
  $("#selDel").disabled = !ids.length;
  if (S.picking) $("#seqCount").textContent = `${ids.length} selected`;
}
async function stitchGo() {
  const ids = filmOrder().filter(id => stitchable(takeById(id) || {}));
  $("#stitchGo").disabled = true; $("#stitchGo").textContent = "Stitching…";
  const r = await api(`/reels/${S.reel.id}/stitch`, { method: "POST", json: { takes: ids, still_seconds: +$("#holdSel").value } });
  if (r.error) { updateStitch(); return toast(r.error, true); }
  S.picking = false; S.picked.clear(); updateStitch();
  mergeTakes([r.take]); select(r.take.id);
  toast(`${r.take.label}${r.take.seconds ? ` · ${(+r.take.seconds).toFixed(1)}s` : ""}. Download stitched is in the Sequence bar.`);
}
// the shot on the panel in the colourist's own ComfyUI: the Gobo Director with
// the scene and every choice, wired to the model's own node (studio/comfy_shot.py)
function shotToComfy(ask) {
  if (!window.goboComfy) return toast("The Lab hasn't loaded yet: try again in a moment.", true);
  const m = model(), name = f => f ? f.name : "";
  const body = { reel: S.reel ? S.reel.id : "", scene: S.scene, direction: S.direction, mode: S.mode, pmode: S.pmode, model: m ? m.id : "",
    aspect: S.aspect, resolution: S.resolution, duration: S.duration, audio: S.audio, seed: S.seed, negative_prompt: S.negative,
    first_frame: name(S.firstFrame), last_frame: name(S.lastFrame), refs: (S.refs || []).filter(Boolean).map(name) };
  window.goboComfy.open(() => api("/comfyui/shot", { method: "POST", json: body }), ask,
    `Gobo opens your ComfyUI with this shot as Gobo's own nodes: the Gobo Director holding your scene and every choice, writing the prompt${m ? ` for ${m.label}'s own node` : ""}, then a save. Change anything and queue it.`,
    (err, r) => err ? toast(err, true) : toast(`ComfyUI is opening with this shot (${r.title}). Empty? Shift-click Open in ComfyUI to add Gobo's nodes.`));
}
// the takes that didn't make it, out of the reel in one go
async function clearFails() {
  const ids = S.takes.filter(t => t.status === "failed" || t.status === "cancelled").map(t => t.id);
  if (!ids.length || !confirm(`Clear the ${ids.length} failed and stopped take${ids.length > 1 ? "s" : ""} from this ${REELWORD}? This can't be undone.`)) return;
  const r = await api(`/reels/${S.reel.id}/takes/delete`, { method: "POST", json: { takes: ids } });
  if (r.error) return toast(r.error, true);
  const gone = new Set(r.deleted);
  S.takes = S.takes.filter(t => !gone.has(t.id));
  if (gone.has(S.current)) S.current = (S.takes.slice().reverse().find(t => t.status === "done") || S.takes[S.takes.length - 1] || {}).id || null;
  renderStrip(); renderStage(); renderFails(); if (!STILLS) refreshBoard();
  toast(`Cleared ${gone.size} take${gone.size === 1 ? "" : "s"} that didn't make it.`);
}
async function deleteSelected() {
  const ids = [...S.picked];
  if (!ids.length || !confirm(`Delete ${ids.length} take${ids.length > 1 ? "s" : ""}? This can't be undone.`)) return;
  const r = await api(`/reels/${S.reel.id}/takes/delete`, { method: "POST", json: { takes: ids } });
  if (r.error) return toast(r.error, true);
  const gone = new Set(r.deleted);
  S.takes = S.takes.filter(t => !gone.has(t.id));
  if (gone.has(S.current)) S.current = (S.takes[S.takes.length - 1] || {}).id || null;
  S.picking = false; S.picked.clear(); updateStitch(); renderStrip(); renderStage();
  toast(`Deleted ${gone.size} take${gone.size === 1 ? "" : "s"}.${r.kept.length ? ` ${r.kept.length} still rendering — stop ${r.kept.length > 1 ? "them" : "it"} first.` : ""}`);
}

// ── reels sheet ────────────────────────────────────────────────────────
async function openReels() {
  const el = $("#reelSheet");
  const word = STILLS ? "Sessions" : "Reels";
  el.innerHTML = `<h3>${word} <span class="r"><button class="btn small" data-close>Close</button></span></h3><p class="hint">Loading…</p>`;
  showPop(el); $("[data-close]", el).onclick = closePop;
  const r = await api(`/reels?app=${STILLS ? "cg" : "studio"}`);
  const list = r.reels || [];
  el.innerHTML = `<h3>${word} <span class="r"><button class="btn small primary" id="rsNew">${STILLS ? "New session" : "New reel"}</button> <button class="btn small" data-close>Close</button></span></h3>
    ${list.length ? `<div class="reels">${list.map(x => `<button class="reelcard ${S.reel && S.reel.id === x.id ? "on" : ""}" data-r="${x.id}">
      <div class="rc" style="${x.cover ? `background-image:url('${API}/reels/${x.id}/thumb/${encodeURIComponent(x.cover)}?w=360')` : ""}"></div>
      <div class="rl"><b>${esc(x.name)}</b><small>${x.takes} take${x.takes === 1 ? "" : "s"} · ${new Date((x.touched || x.created) * 1000).toLocaleDateString()}</small></div></button>`).join("")}</div>`
      : `<p class="hint">No ${word.toLowerCase()} yet. Your first Generate makes one.</p>`}
    <p class="hint" style="margin-top:10px">Takes stay until you delete them — the Library has all of yours, from every ${STILLS ? "session" : "reel"}. Empty ${word.toLowerCase()} are cleared after 60 days.</p>`;
  $("[data-close]", el).onclick = closePop;
  $("#rsNew").onclick = () => { closePop(); newReel(); };
  $$("[data-r]", el).forEach(b => b.onclick = async () => { closePop(); await loadReel(b.dataset.r); });
}
async function newReel() {
  if (!STILLS) { await flushBoard(); B = newBoard(); renderKit(); renderBoard(); }
  S.reel = null; S.takes = []; S.current = null; $("#reelName").value = STILLS ? "Untitled session" : "Untitled reel";
  S.refs = []; S.firstFrame = S.lastFrame = S.sourceVideo = null;
  refusals.length = 0;
  LS.set("reel", null); renderStrip(); renderStage(); renderFails(); onShotChange();
}

// ── library: everything you've made, across reels ──────────────────────
// A take stays until its maker deletes it. Mine is what you made, by the name
// you signed in with; Everyone is the whole team's, plus takes from before
// names were kept, which anyone may tidy away.
const LIB = { who: LS.get("lib.who", "mine"), kind: LS.get("lib.kind", ""), starred: false, q: "", size: 0,
              items: [], total: 0, counts: {}, me: "", picked: new Set(), view: -1, seq: 0 };
const libKey = t => `${t.reel}:${t.id}`;
const REELWORD = STILLS ? "session" : "reel";
function libDay(ts) {
  const d = new Date(ts * 1000), today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.round((today - new Date(ts * 1000).setHours(0, 0, 0, 0)) / 864e5);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: days < 7 ? "long" : undefined, month: "short", day: "numeric",
                                           year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}
async function openLibrary() {
  const el = $("#libSheet");
  LIB.picked.clear(); LIB.view = -1;
  el.innerHTML = `<div class="libhead">
      <h3>Library <small id="libTotal"></small><span class="r"><button class="btn small" data-close>Close</button></span></h3>
      <div class="libbar">
        <span class="seg" id="libWho"><button data-w="mine">Mine<i></i></button><button data-w="all">Everyone<i></i></button></span>
        ${STILLS ? "" : `<span class="seg" id="libKind"><button data-k="">All<i></i></button><button data-k="video">Clips<i></i></button><button data-k="image">Stills<i></i></button></span>`}
        <button class="chipbtn" id="libStar">★ Starred<i></i></button>
        <input class="field" id="libQ" placeholder="Search prompts, models, ${REELWORD}s" value="${esc(LIB.q)}">
      </div></div>
    <div class="libbody" id="libBody"><p class="hint" style="padding-top:14px">Loading…</p></div>
    <div class="libfoot" id="libFoot"></div>`;
  showPop(el);
  $("[data-close]", el).onclick = closePop;
  $$("#libWho button", el).forEach(b => b.onclick = () => { LIB.who = b.dataset.w; LS.set("lib.who", LIB.who); libFetch(); });
  $$("#libKind button", el).forEach(b => b.onclick = () => { LIB.kind = b.dataset.k; LS.set("lib.kind", LIB.kind); libFetch(); });
  $("#libStar", el).onclick = () => { LIB.starred = !LIB.starred; libFetch(); };
  let qT = null;
  $("#libQ", el).oninput = e => { clearTimeout(qT); qT = setTimeout(() => { LIB.q = e.target.value.trim(); libFetch(); }, 250); };
  await libFetch();
}
async function libFetch(more) {
  const seq = ++LIB.seq;            // a later search wins over an earlier, slower one
  const qs = new URLSearchParams({ app: STILLS ? "cg" : "studio", who: LIB.who, kind: STILLS ? "" : LIB.kind,
                                   starred: LIB.starred ? 1 : 0, q: LIB.q, offset: more ? LIB.items.length : 0, limit: 60 });
  const r = await api(`/library?${qs}`);
  if (seq !== LIB.seq) return;
  if (r.error) return toast(r.error, true);
  LIB.items = more ? LIB.items.concat(r.items) : r.items;
  LIB.total = r.total; LIB.counts = r.counts; LIB.me = r.me;
  if (!more) { LIB.picked.clear(); $("#libBody").scrollTop = 0; }
  renderLib();
}
function libCounts() {
  const c = LIB.counts, n = v => v ? ` ${v}` : "";
  $$("#libWho button").forEach(b => { b.classList.toggle("on", b.dataset.w === LIB.who); $("i", b).textContent = n(b.dataset.w === "all" ? c.all : c.mine); });
  $$("#libKind button").forEach(b => { b.classList.toggle("on", b.dataset.k === LIB.kind);
    $("i", b).textContent = n(b.dataset.k === "video" ? c.video : b.dataset.k === "image" ? c.image : (c.video || 0) + (c.image || 0)); });
  $("#libStar").classList.toggle("on", LIB.starred);
  $("i", $("#libStar")).textContent = n(c.starred);
  $("#libTotal").textContent = LIB.total ? `${LIB.total} take${LIB.total === 1 ? "" : "s"}` : "";
}
function libEmpty() {
  const filtered = LIB.q || LIB.starred || LIB.kind;
  const all = `<button class="btn" data-who="all">Show everyone's</button>`;
  if (LIB.who === "mine" && !LIB.me) return `<div class="libempty"><b>This page doesn't know who you are yet.</b>
    Sign in from the <a href="/">home page</a> — everything you make from then on is kept under your name.<br><br>${all}</div>`;
  if (filtered) return `<div class="libempty"><b>Nothing matches.</b><button class="btn" data-clear>Clear filters</button></div>`;
  if (LIB.who === "mine" && LIB.counts.all) return `<div class="libempty"><b>Nothing of yours yet.</b>
    Takes made before 23 Oct carry no name, so they're under Everyone — select yours there and Mark as mine.<br><br>${all}</div>`;
  return `<div class="libempty"><b>Nothing here yet.</b>Every finished take lands here, from every ${REELWORD}, and stays until you delete it.</div>`;
}
function libCard(t, i) {
  const k = libKey(t), vid = t.kind === "video", on = LIB.picked.has(k);
  const media = t.thumb_url ? `<img src="${esc(t.thumb_url)}" alt="" loading="lazy" decoding="async">`
    : vid ? `<video src="${esc(t.url)}#t=0.1" preload="metadata" muted playsinline></video>` : `<div class="ph">🖼</div>`;
  const size = vid ? "" : t.w && t.h ? `${t.w}×${t.h} · ` : "";
  const when = new Date(t.created * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `<div class="lc ${on ? "picked" : ""}" data-i="${i}" tabindex="0" title="${esc((t.prompt || "").slice(0, 300))}">
    <div class="lm">${media}${vid ? `<span class="badge">▶${t.seconds ? " " + Math.round(t.seconds) + "s" : ""}</span>` : ""}
      ${t.starred ? `<span class="star">★</span>` : ""}<input type="checkbox" class="pick" ${on ? "checked" : ""} aria-label="Select">
      ${STILLS ? "" : `<span class="lo">${t.board ? `<button data-o="board" title="Its board, its card showing this ${vid ? "clip" : "keyframe"}">On its board</button>` : ""}<button data-o="clip" title="In ${esc(t.reel_name || `its ${REELWORD}`)}, on the Shot view's stage">${vid ? "As a clip" : "As a still"}</button></span>`}</div>
    <div class="ll"><b>${esc(t.label)}</b><small>${size}${when} · ${esc(t.reel_name)}</small>
      ${LIB.who === "all" ? `<small>${esc(t.owner || "no name")}</small>` : ""}</div></div>`;
}
function renderLib() {
  libCounts();
  const body = $("#libBody");
  if (!LIB.items.length) {
    body.innerHTML = libEmpty();
    $$("[data-who]", body).forEach(b => b.onclick = () => { LIB.who = "all"; LS.set("lib.who", "all"); libFetch(); });
    $$("[data-clear]", body).forEach(b => b.onclick = () => { LIB.q = ""; LIB.starred = false; LIB.kind = ""; $("#libQ").value = ""; libFetch(); });
    return libFoot();
  }
  let html = "", day = null;
  LIB.items.forEach((t, i) => {
    const d = libDay(t.created);
    if (d !== day) { html += `${day ? "</div>" : ""}<p class="libday">${esc(d)}</p><div class="libgrid">`; day = d; }
    html += libCard(t, i);
  });
  html += "</div>";
  if (LIB.items.length < LIB.total) html += `<div class="libmore"><button class="btn" id="libMore">Show more · ${LIB.total - LIB.items.length} more</button></div>`;
  body.innerHTML = html;
  $$(".lc", body).forEach(el => {
    const t = LIB.items[+el.dataset.i];
    el.onclick = e => {
      if (e.target.classList.contains("pick")) return;
      const o = e.target.closest("[data-o]"); if (o) return libOpen(t, o.dataset.o === "board");
      if (LIB.picked.size || e.metaKey || e.ctrlKey || e.shiftKey) return libPick(el, t);
      libView(+el.dataset.i);
    };
    el.onkeydown = e => { if (e.key === "Enter") libView(+el.dataset.i); };
    $(".pick", el).onchange = () => libPick(el, t);
    // a clip plays under the pointer, muted
    el.onmouseenter = () => {
      if (t.kind !== "video" || $("video.hov", el)) return;
      const v = document.createElement("video");
      Object.assign(v, { className: "hov", src: t.url, muted: true, loop: true, playsInline: true });
      $(".lm", el).insertBefore(v, $(".badge", el)); v.play().catch(() => {});
    };
    el.onmouseleave = () => { const v = $("video.hov", el); if (v) { v.pause(); v.remove(); } };
  });
  const more = $("#libMore"); if (more) more.onclick = () => { more.disabled = true; more.textContent = "Loading…"; libFetch(true); };
  libFoot();
}
function libPick(el, t) {
  const k = libKey(t), on = !LIB.picked.has(k);
  on ? LIB.picked.add(k) : LIB.picked.delete(k);
  el.classList.toggle("picked", on); $(".pick", el).checked = on;
  libFoot();
}
const libSelected = () => LIB.items.filter(t => LIB.picked.has(libKey(t)));
function libFoot() {
  const sel = libSelected(), mine = sel.filter(t => t.yours), f = $("#libFoot");
  const unnamed = LIB.me ? sel.filter(t => !t.owner) : [];
  $("#libSheet").classList.toggle("picking", sel.length > 0);
  const sizes = STILLS ? `<select class="field" id="libSize" title="Stills at this long side, sharpened, 300 dpi">${[[0, "Original size"], [2000, "2K · 300 dpi"], [4000, "4K · 300 dpi"], [8000, "8K · 300 dpi"]]
    .map(([v, l]) => `<option value="${v}" ${LIB.size === v ? "selected" : ""}>${l}</option>`).join("")}</select>` : "";
  f.innerHTML = sel.length ? `<b>${sel.length} selected</b>
      <button class="btn small" id="libAll">Select all ${LIB.items.length < LIB.total ? `${LIB.items.length} shown` : LIB.items.length}</button>
      <button class="btn small" id="libNone">Clear</button><span class="grow"></span>${sizes}
      ${unnamed.length ? `<button class="btn small" id="libClaim" title="Put your name on takes made before names were kept">Mark ${unnamed.length} as mine</button>` : ""}
      ${STILLS ? "" : `<button class="btn small" id="libBoard" title="A card each on a board, in film order, to cut or work on">Put on a board</button>`}
      <a class="btn small primary" id="libDl" href="${API}/library/download?items=${sel.map(libKey).join(",")}&size=${LIB.size}">Download ${sel.length} (.zip)</a>
      <button class="btn small danger" id="libDel" ${mine.length ? "" : "disabled"} title="${mine.length < sel.length ? "Only your own takes, and ones with no name on them, can be deleted here" : ""}">Delete ${mine.length}</button>`
    : `<span class="hint">Click a take to view it · tick or ⌘-click to select several · takes stay here until you delete them</span><span class="grow"></span>${sizes}`;
  const sz = $("#libSize", f); if (sz) sz.onchange = () => { LIB.size = +sz.value; libFoot(); };
  if (!sel.length) return;
  $("#libAll", f).onclick = () => { LIB.items.forEach(t => LIB.picked.add(libKey(t))); renderLib(); };
  $("#libNone", f).onclick = () => { LIB.picked.clear(); renderLib(); };
  $("#libDel", f).onclick = () => libDelete(sel);
  const cl = $("#libClaim", f); if (cl) cl.onclick = () => libClaim(unnamed);
  const bd = $("#libBoard", f); if (bd) bd.onclick = () => libBoardAsk(sel);
}
// takes from anywhere onto a board: a new one, or the one on screen
function libBoardAsk(sel) {
  const f = $("#libFoot"), here = S.reel && S.reel.id;
  const from = [...new Set(sel.map(t => t.reel_name).filter(Boolean))];
  f.innerHTML = `<b>Put ${sel.length} on</b>
    <button class="btn small primary" id="lbNew">A new board</button>
    ${here ? `<button class="btn small" id="lbHere" title="Added after its cards">${esc(S.reel.name || "this reel")}'s board</button>` : ""}
    <span class="grow"></span><button class="btn small" id="lbBack">Back</button>`;
  $("#lbBack", f).onclick = libFoot;
  $("#lbNew", f).onclick = () => {
    const name = prompt("Name the new board:", from.length === 1 ? from[0] : "From the library");
    if (name !== null) libToBoard(sel, "", name);
  };
  const hb = $("#lbHere", f); if (hb) hb.onclick = () => libToBoard(sel, here, "");
}
async function libToBoard(sel, reel, name) {
  const r = await api("/library/board", { method: "POST", json: { items: sel.map(libKey), reel, name } });
  if (r.error) return toast(r.error, true);
  LIB.picked.clear(); closePop();
  if (!await loadReel(r.reel.id)) return toast(`That ${REELWORD} is gone.`, true);
  setView("board");
  toast(`${r.added} card${r.added === 1 ? "" : "s"} on ${r.reel.name}'s board${r.left.length ? `; ${r.left.length} left out (still rendering, or already on it)` : ""}. Cut it when it's in order.`);
}
// takes from before names were kept: whoever made them puts their name on them
async function libClaim(list) {
  const r = await api("/library/claim", { method: "POST", json: { items: list.map(libKey) } });
  if (r.error) { toast(r.error, true); return false; }
  const got = new Set(r.claimed);
  LIB.items.forEach(t => { if (got.has(libKey(t))) { t.owner = r.me; t.yours = true; } });
  LIB.counts.mine = (LIB.counts.mine || 0) + got.size;
  LIB.picked.clear(); renderLib();
  toast(`${got.size} take${got.size === 1 ? "" : "s"} now under Mine.`);
  return true;
}
async function libDelete(list) {
  const mine = list.filter(t => t.yours), others = list.length - mine.length;
  if (!mine.length || !confirm(`Delete ${mine.length} take${mine.length > 1 ? "s" : ""}? This can't be undone.${others ? ` The ${others} made by someone else stay.` : ""}`)) return false;
  const r = await api("/library/delete", { method: "POST", json: { items: mine.map(libKey) } });
  if (r.error) { toast(r.error, true); return false; }
  const gone = new Set(r.deleted);
  LIB.items = LIB.items.filter(t => !gone.has(libKey(t)));
  LIB.total -= gone.size; LIB.picked.clear();
  // the reel on the stage loses them too
  if (S.reel) {
    const ids = new Set(r.deleted.filter(k => k.startsWith(S.reel.id + ":")).map(k => k.split(":")[1]));
    if (ids.size) {
      S.takes = S.takes.filter(t => !ids.has(t.id));
      if (ids.has(S.current)) S.current = (S.takes[S.takes.length - 1] || {}).id || null;
      renderStrip(); renderStage();
    }
  }
  renderLib();
  api(`/library?${new URLSearchParams({ app: STILLS ? "cg" : "studio", who: LIB.who, kind: STILLS ? "" : LIB.kind, starred: LIB.starred ? 1 : 0, q: LIB.q, limit: 1 })}`)
    .then(c => { if (!c.error) { LIB.counts = c.counts; libCounts(); } });
  toast(`Deleted ${gone.size} take${gone.size === 1 ? "" : "s"}.${r.kept.length ? ` ${r.kept.length} left alone — still rendering, or someone else's.` : ""}`);
  return true;
}
function libView(i) {
  const t = LIB.items[i];
  if (!t) return libClose();
  LIB.view = i;
  if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) document.activeElement.blur();   // so ← → move
  let v = $("#libView");
  if (!v) { v = document.createElement("div"); v.id = "libView"; v.className = "libview"; $("#libSheet").appendChild(v); }
  const vid = t.kind === "video";
  const made = new Date(t.created * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  const dims = [t.w && t.h ? `${t.w}×${t.h}` : "", vid && t.seconds ? `${(+t.seconds).toFixed(1)}s` : "", t.aspect || ""].filter(Boolean).join(" · ");
  v.innerHTML = `<div class="vm">${vid ? `<video src="${esc(t.url)}" poster="${esc(t.thumb_url || "")}" controls autoplay loop playsinline></video>` : `<img src="${esc(t.url)}" alt="">`}
      <button class="nav prev" data-nav="-1" ${i ? "" : "disabled"} aria-label="Previous">‹</button>
      <button class="nav next" data-nav="1" ${i < LIB.total - 1 ? "" : "disabled"} aria-label="Next">›</button></div>
    <div class="vi">
      <h4>${esc(t.label)}<span class="r"><button class="btn small" id="lvBack">Back</button></span></h4>
      <dl class="kv">
        <dt>Made</dt><dd>${esc(made)}</dd>
        <dt>By</dt><dd>${esc(t.owner || "No name — made before names were kept")}</dd>
        <dt>${STILLS ? "Session" : "Reel"}</dt><dd>${esc(t.reel_name)}</dd>
        <dt>Shot</dt><dd>${esc(t.shot)} · take ${esc(t.letter)}</dd>
        ${dims ? `<dt>Format</dt><dd>${esc(dims)}</dd>` : ""}
        ${t.cost != null ? `<dt>Cost</dt><dd>${fmtUSD(+t.cost)}</dd>` : ""}
      </dl>
      ${t.prompt ? `<div><p class="sectitle">Prompt</p><div class="prompt">${esc(t.prompt)}</div></div>` : ""}
      <div class="vacts">
        ${t.board && !STILLS ? `<button class="btn small primary" id="lvBoard" title="The board this was made for, its card showing this ${vid ? "clip" : "keyframe"}">Open on its board</button>` : ""}
        <button class="btn small ${t.board && !STILLS ? "" : "primary"}" id="lvOpen" title="${STILLS ? "" : `In ${esc(t.reel_name || `its ${REELWORD}`)}, on the Shot view's stage: play it, grade it, upscale it, grab a frame`}">${STILLS ? `Open in its ${REELWORD}` : `Open as a ${vid ? "clip" : "still"}`}</button>
        <a class="btn small" href="${esc(t.url)}?download=1">Download${STILLS ? " original" : ""}</a>
        <button class="btn small" id="lvStar">${t.starred ? "★ Starred" : "☆ Star"}</button>
        ${t.prompt ? `<button class="btn small" id="lvCopy">Copy prompt</button>` : ""}
        ${t.kind === "image" ? `<button class="btn small" id="lvUse">${boardOn() ? "Add to kit" : "Use as reference"}</button>` : ""}
        ${!t.owner && LIB.me ? `<button class="btn small" id="lvClaim">Mark as mine</button>` : ""}
        ${t.yours ? `<button class="btn small danger" id="lvDel">Delete</button>` : ""}
      </div>
      <p class="hint">← → move through the library · S stars · Esc goes back</p>
    </div>`;
  $$("[data-nav]", v).forEach(b => b.onclick = () => libStep(+b.dataset.nav));
  $("#lvBack", v).onclick = libClose;
  $("#lvOpen", v).onclick = () => libOpen(t, false);
  const ob = $("#lvBoard", v); if (ob) ob.onclick = () => libOpen(t, true);
  $("#lvStar", v).onclick = () => libStar(t);
  const cm = $("#lvClaim", v); if (cm) cm.onclick = async () => { if (await libClaim([t])) libView(i); };
  const use = $("#lvUse", v); if (use) use.onclick = () => libUse(t);
  const cp = $("#lvCopy", v); if (cp) cp.onclick = () => navigator.clipboard.writeText(t.prompt).then(() => toast("Prompt copied."), () => toast("Couldn't reach the clipboard.", true));
  const del = $("#lvDel", v); if (del) del.onclick = async () => {
    if (!await libDelete([t])) return;
    LIB.items.length ? libView(Math.min(i, LIB.items.length - 1)) : libClose();
  };
}
// a take from the library, where you can see it: on the stage of its reel
// (the Shot view, whichever view was up), or on its card on the board, the
// card switched to this take so it's the one you see and the one the cut uses
async function libOpen(t, onBoard) {
  closePop();
  if (!S.reel || S.reel.id !== t.reel) { if (!await loadReel(t.reel)) return toast(`That ${REELWORD} is gone.`, true); }
  if (!S.takes.some(x => x.id === t.id)) return toast("That take isn't in its reel any more.", true);
  const s = onBoard && B && B.shots.find(x => x.id === t.board);
  if (!s) {
    setView("shot"); select(t.id);
    if (onBoard) toast("Its card isn't on the board any more, so here's the take.");
    return;
  }
  const was = pickOf(s, t.role);
  setView("board");
  pickTake(s, t.id);
  const c = $(`.bcard[data-s="${s.id}"]`);
  if (c) { c.scrollIntoView({ behavior: "smooth", block: "center" }); c.classList.add("found"); setTimeout(() => c.classList.remove("found"), 2300); }
  const what = t.role === "key" ? "keyframe" : "clip", n = B.shots.indexOf(s) + 1;
  if (was && was.id !== t.id) toast(`Card ${n} now uses ${what} ${t.letter}; it had ${was.letter}. Click ${was.letter} on the card to go back.`);
}
async function libStep(d) {
  const i = LIB.view + d;
  if (i < 0 || i >= LIB.total) return;
  if (i >= LIB.items.length) await libFetch(true);   // the next page, when the viewer walks off this one
  libView(i);
}
async function libStar(t) {
  const r = await api(`/reels/${t.reel}/takes/${t.id}/star`, { method: "POST", json: { starred: !t.starred } });
  if (r.error) return toast(r.error, true);
  t.starred = r.take.starred;
  const mine = S.takes.find(x => x.id === t.id); if (mine) { mine.starred = t.starred; renderStrip(); }
  renderLib();                       // the grid behind; the viewer keeps playing
  const b = $("#lvStar"); if (b) b.textContent = t.starred ? "★ Starred" : "☆ Star";
}
// a still from anywhere in the library, into this reel: the kit on the board, a reference otherwise
async function libUse(t) {
  if (boardOn()) { if (await kitFromTake(libKey(t), "")) { closePop(); toast("In the kit. Name it, then tick the shots it's in."); } return; }
  await ensureReel();
  const r = await api(`/reels/${S.reel.id}/refs/copy`, { method: "POST", json: { item: libKey(t) } });
  if (r.error) return toast(r.error, true);
  closePop();
  const slot = S.mode === "video" && !caps().refs && caps().firstFrame ? "first" : "ref";
  placeRef({ name: r.name, url: r.url, kind: "image" }, slot);
  toast(slot === "first" ? "Added as the starting frame." : "Added as a reference.");
}
function libClose() {
  const v = $("#libView");
  if (v) { $$("video", v).forEach(x => x.pause()); v.remove(); }
  LIB.view = -1;
}
function libKeys(e) {
  if (e.key === "Escape") { e.preventDefault(); return LIB.view >= 0 ? libClose() : closePop(); }
  if (LIB.view < 0 || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
  if (e.key === "ArrowRight") { e.preventDefault(); libStep(1); }
  if (e.key === "ArrowLeft") { e.preventDefault(); libStep(-1); }
  if (e.key.toLowerCase() === "s") libStar(LIB.items[LIB.view]);
}

// ── mode + global wiring ───────────────────────────────────────────────
function setMode(m, quiet) {
  if (S.mode === m) return;
  S.mode = m;
  $("#modeImage").classList.toggle("on", m === "image");
  $("#modeVideo").classList.toggle("on", m === "video");
  $("#scene").placeholder = m === "video" ? "Describe the action and how the camera moves. Who, where, what happens." : "Describe the moment. Who, where, what is happening.";
  if (!quiet) onShotChange();
}
function renderPmode() {
  $$("#pmode button").forEach(b => b.classList.toggle("on", b.dataset.p === S.pmode));
  $("#directBtn").classList.toggle("hidden", S.pmode !== "director");
  $("#pmodeHint").textContent = { plain: "Just your words and the direction.", cinematic: S.mode === "video" ? "Adds cinematic motion." : "Adds “cinematic film still”.", director: "The Director rewrites the brief for this model." }[S.pmode];
}
function onShotChange() {
  if (boardOn() && S.mode !== "video" && $("#modelSheet").classList.contains("hidden")) S.mode = "video";
  if (boardOn() && B) { saveBoard(); renderBoard(); }
  renderModelBtn(); renderRefs(); renderShotList(); renderFormat(); renderPmode(); updateGen(); scheduleCompile(); saveLocal();
  if (!cur()) renderStage();
  $("#sceneHint").textContent = S.mode === "video" ? "Action + camera movement" : "";
}
function shuffle() {
  const pick = g => { const o = CAT.groups[g].options; return o[1 + Math.floor(Math.random() * (o.length - 1))].id; };
  [...CAT.order.camera, ...rowGroups()].forEach(g => { if (g !== "compose" || Math.random() < .4) S.direction[g] = pick(g); });
  if (Math.random() < .5) S.direction.angle = "auto";
  S.colors = []; onShotChange(); toast("Shuffled. Keep what you like.");
}
function openStarters() {
  const el = $("#pop"); delete el.dataset.wide;
  el.innerHTML = `<h3>Starter shots <span class="r"><button class="btn small" data-close>Close</button></span></h3>
    <div class="tiles">${CAT.starters.map(s => `<button class="tile" data-s="${s.id}"><div class="tv">${thumb(s.video ? "move" : "light", s.video ? (s.direction.move || "push") : (s.direction.light || "auto"), true)}</div>
      <div class="tl"><b>${esc(s.label)}</b><small>${esc(s.aspect)}${s.video ? " · clip" : ""}</small></div></button>`).join("")}</div>`;
  showPop(el, $("#starterBtn"));
  $("[data-close]", el).onclick = closePop;
  $$("[data-s]", el).forEach(b => b.onclick = () => {
    const s = CAT.starters.find(x => x.id === b.dataset.s);
    setMode(s.video ? "video" : "image", true);
    S.scene = s.scene; $("#scene").value = s.scene;
    S.direction = Object.assign({}, CAT.defaults, s.direction); S.aspect = s.aspect; S.colors = [];
    closePop(); onShotChange(); $("#scene").focus();
  });
}

function wire() {
  $("#modelBtn").onclick = () => openModelSheet(false);
  $("#shootBtn").onclick = () => openModelSheet(true);
  $("#scrim").onclick = closePop;
  $("#modeImage").onclick = () => setMode("image");
  $("#modeVideo").onclick = () => setMode("video");
  $("#scene").oninput = e => { S.scene = e.target.value; updateGen(); scheduleCompile(); saveLocal(); };
  $$("#pmode button").forEach(b => b.onclick = () => {
    S.pmode = b.dataset.p; renderPmode(); scheduleCompile(); saveLocal();
    if (S.pmode !== "director" && S.directed) setOverride(null);
  });
  $("#directBtn").onclick = directIt;
  $("#ovToggle").onchange = e => setOverride(e.target.checked ? (S.override != null ? S.override : lastCompiled) : null);
  $("#override").oninput = e => { S.override = e.target.value; updateGen(); };
  $("#neg").oninput = e => { S.negative = e.target.value; saveLocal(); };
  $("#seed").oninput = e => { S.seed = e.target.value.replace(/\D/g, "").slice(0, 12); e.target.value = S.seed; saveLocal(); };
  $("#seedDice").onclick = () => { S.seed = String(Math.floor(Math.random() * 2147483647)); $("#seed").value = S.seed; saveLocal(); };
  $("#seedClear").onclick = () => { S.seed = ""; $("#seed").value = ""; saveLocal(); };
  $("#resetDir").onclick = () => { S.direction = baseDirection(); S.colors = []; S.mainColor = null; onShotChange(); };
  $("#autoBtn").onclick = () => { Object.keys(S.direction).forEach(k => S.direction[k] = "auto"); S.colors = []; onShotChange(); };
  $("#surpriseBtn").onclick = shuffle;
  $("#starterBtn").onclick = openStarters;
  $("#genBtn").onclick = () => generate();
  $("#reelsBtn").onclick = openReels;
  $("#libBtn").onclick = openLibrary;
  if (!STILLS) {
    $$("#viewSeg button").forEach(b => b.onclick = () => setView(b.dataset.v)); wireKit();
    $("#footBtn").onclick = () => pickFootage(async f => {
      const r = await importFootage(f, null);
      if (r) { select(r.take.id); toast(`In the reel as shot ${r.take.shot}. Change it, extend it or lead into it with the buttons under the picture.`); }
    });
  }
  $("#newReelBtn").onclick = newReel;
  $("#inspBtn").onclick = () => $("#main").classList.toggle("noinspector");
  let nameT = null;
  $("#reelName").oninput = () => { clearTimeout(nameT); nameT = setTimeout(async () => { if (S.reel) { const r = await api(`/reels/${S.reel.id}`, { method: "PATCH", json: { name: $("#reelName").value } }); if (!r.error) S.reel = r; } }, 600); };
  $("#stitchBtn").onclick = startFilm;
  $("#selBtn").onclick = startSelect;
  $("#clearFails").onclick = clearFails;
  $("#shotComfy").onclick = e => shotToComfy(e.shiftKey || e.altKey);
  $("#selDel").onclick = deleteSelected;
  $("#selDl").onclick = e => { if ($("#selDl").classList.contains("off")) { e.preventDefault(); toast("Select a finished take to download."); } };
  $("#stitchCancel").onclick = stopFilm;
  $("#stitchAll").onclick = pickAll;
  $("#stitchGo").onclick = stitchGo;
  $("#dlAll").onclick = e => { if (!S.reel) { e.preventDefault(); toast("Nothing to download yet."); } };
  $("#dlStar").onclick = e => { if (!S.reel) { e.preventDefault(); toast("Nothing to download yet."); } };
  document.addEventListener("keydown", e => {
    if (!$("#libSheet").classList.contains("hidden")) return libKeys(e);
    if (e.key === "Escape") return S.picking && !$(".pop:not(.hidden)") ? stopFilm() : closePop();
    if (boardOn()) return;
    if (S.picking && (e.key === "Delete" || e.key === "Backspace") && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); return deleteSelected(); }
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); return generate(); }
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
    const i = S.takes.findIndex(t => t.id === S.current);
    if (e.key === "ArrowRight" && i < S.takes.length - 1) select(S.takes[i + 1].id);
    if (e.key === "ArrowLeft" && i > 0) select(S.takes[i - 1].id);
    if (e.key.toLowerCase() === "s" && cur() && cur().status === "done") toggleStar(cur());
  });
  window.addEventListener("resize", () => frameFor(cur()));
  window.addEventListener("beforeunload", () => saveLocal());
}

// The balance used to be read once, when the page opened, so it went stale as
// takes were paid for. It's read live now: when a take lands, every minute,
// when you come back to the tab, on a click; with what's already committed
// (rendering or in line) beside it, amber under the low mark.
let creditsT = null, creditsAt = 0;
async function loadCredits(fresh) {
  const el = $("#credits"); if (!el) return;
  const r = await api(`/credits${fresh ? "?fresh=1" : ""}`);
  if (!r || r.error || r.credits == null) { el.title = "The balance didn't come back. Click to ask again."; return; }
  creditsAt = Date.now();
  const busy = (r.rendering || 0) + (r.waiting || 0), usd = v => `$${(+v).toFixed(2)}`;
  const topup = "https://docs.comfy.org/interface/credits";
  el.innerHTML = `Comfy ${usd(r.credits)}${busy ? ` <span class="pend">· ≈${usd(busy)} in progress</span>` : ""}`
    + (r.low ? ` <a href="${topup}" target="_blank" rel="noopener">top up ↗</a>` : "");
  el.classList.toggle("low", !!r.low);
  el.title = `Live from Comfy at ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`
    + (busy ? ` About ${usd(busy)} is rendering or waiting in line, so about ${usd(r.left)} is left after that.` : "")
    + (r.low ? ` That's under ${usd(r.low_at)}: top up in ComfyUI (Plan & Credits).` : "") + " Click to check again.";
}
const creditsSoon = () => { clearTimeout(creditsT); creditsT = setTimeout(() => loadCredits(true), 2500); };

// ── Content Generator: prompts from a reference image, or a folder ─────
// GPT-4o reads the reference and writes N prompts at the chosen variation;
// they run as one shot, one take each. A folder runs that per image, with at
// most THROTTLE takes rendering at once so a big folder doesn't flood the provider.
const REF = { file: null, files: [], style: "photography", n: 4, prompts: [], busy: false, stop: false };
const THROTTLE = 8;
const IMG_RE = /\.(jpe?g|png|webp)$/i;
const sleep = ms => new Promise(r => setTimeout(r, ms));
// a camera original can be 20 MB; the model only needs to see it
function shrink(file, max = 1536) {
  return new Promise(res => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(img.src);
      c.toBlob(b => res(b || file), "image/jpeg", 0.9);
    };
    img.onerror = () => res(file);
    img.src = URL.createObjectURL(file);
  });
}
async function writePrompts(file) {
  const blob = await shrink(file);
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt) await sleep(attempt * 4000);
    const fd = new FormData();
    fd.append("file", blob, "reference.jpg");
    fd.append("image_style", REF.style);
    fd.append("num_variants", REF.n);
    fd.append("variation_level", $("#refVar").value);
    const notes = $("#refNotes").value.trim();
    if (notes) fd.append("user_notes", notes);
    try {
      const r = await (await fetch(`${API}/prompt-variants`, { method: "POST", body: fd })).json();
      const ps = (r.prompts || []).filter(Boolean);
      if (!r.error && ps.length) return ps.map((p, i) => ({ scene: p, negative_prompt: (r.negative_prompts || [])[i] || "" }));
    } catch (e) { /* retried below */ }
  }
  return null;
}
function refPrice(n) {
  const m = model();
  return m && m.price != null ? ` · ≈ ${fmtUSD(m.price * n)}` : "";
}
function renderRef() {
  const many = REF.files.length;
  $("#refWrite").disabled = REF.busy || (!REF.file && !many);
  $("#refWrite").textContent = many ? `Run ${many} image${many > 1 ? "s" : ""} × ${REF.n}` : `Write ${REF.n} prompt${REF.n > 1 ? "s" : ""}`;
  $("#refStop").classList.toggle("hidden", !REF.busy);
  $("#rnN").textContent = REF.n;
  $("#refVarV").textContent = $("#refVar").value;
  $$("#refStyle button").forEach(b => b.classList.toggle("on", b.dataset.s === REF.style));
  if (!REF.busy && many) $("#refStatus").textContent = `${many * REF.n} images${refPrice(many * REF.n)}`;
}
function setRefFiles(list, label) {
  const files = Array.from(list || []).filter(f => IMG_RE.test(f.name)).sort((a, b) => a.name.localeCompare(b.name));
  if (!files.length) return toast("No JPEG, PNG or WebP images there.", true);
  REF.prompts = []; renderRefList();
  if (files.length === 1 && !label) { REF.file = files[0]; REF.files = []; }
  else { REF.file = null; REF.files = files; }
  $("#refThumb").style.backgroundImage = `url('${URL.createObjectURL(files[0])}')`;
  $("#refThumb").classList.add("has");
  $("#refName").textContent = label ? `${label} · ${files.length} image${files.length > 1 ? "s" : ""}` : files[0].name;
  $("#refSub").textContent = files.length > 1 ? `${REF.n} prompt${REF.n > 1 ? "s" : ""} each, one shot per image` : "GPT-4o writes the prompts from this";
  $("#refStatus").textContent = "";
  renderRef();
}
function renderRefList() {
  const el = $("#refList");
  if (!REF.prompts.length) { el.innerHTML = ""; return; }
  el.innerHTML = REF.prompts.map((p, i) => `<div class="rp"><span class="n">${i + 1}</span><textarea class="field" data-i="${i}">${esc(p.scene)}</textarea>
      <div style="display:flex;flex-direction:column;gap:4px"><button class="btn small x" data-x="${i}" title="Drop this prompt">✕</button><button class="btn small" data-use="${i}" title="Put this prompt in the Scene box">Use</button></div></div>`).join("")
    + `<div class="rpfoot"><button class="btn primary" id="refGen">Generate ${REF.prompts.length}</button><span class="hint">${esc((model() || {}).label || "")}${refPrice(REF.prompts.length)}</span><span style="flex:1"></span><button class="btn small" id="refClear">Clear</button></div>`;
  $$("textarea[data-i]", el).forEach(t => t.oninput = () => { REF.prompts[+t.dataset.i].scene = t.value; });
  $$("[data-x]", el).forEach(b => b.onclick = () => { REF.prompts.splice(+b.dataset.x, 1); renderRefList(); });
  $$("[data-use]", el).forEach(b => b.onclick = () => {
    const p = REF.prompts[+b.dataset.use];
    S.scene = p.scene; $("#scene").value = p.scene;
    if (p.negative_prompt && caps().negative) { S.negative = p.negative_prompt; $("#neg").value = S.negative; }
    onShotChange(); toast("In the Scene box. Direct it, then Generate.");
  });
  $("#refClear").onclick = () => { REF.prompts = []; renderRefList(); };
  $("#refGen").onclick = () => runPrompts(REF.prompts.filter(p => p.scene.trim()));
}
async function runPrompts(prompts) {
  if (!prompts.length) return false;
  const why = blockReason();
  if (why) { toast(why, true); return false; }
  await generate({ prompts, models: [model().id], prompt_override: null, takes: prompts.length });
  return true;
}
async function refreshTakes() {
  const r = await api(`/reels/${S.reel.id}`);
  if (!r.error) { S.takes = r.takes; renderStrip(); }
}
async function runFolder() {
  const files = REF.files.slice(), n = files.length;
  let sent = 0, ok = 0;
  REF.busy = true; REF.stop = false; renderRef();
  await ensureReel();
  for (let i = 0; i < n && !REF.stop; i++) {
    const f = files[i];
    $("#refThumb").style.backgroundImage = `url('${URL.createObjectURL(f)}')`;
    $("#refStatus").textContent = `${i + 1}/${n} · ${f.name} · writing prompts…`;
    const prompts = await writePrompts(f);
    if (REF.stop) break;
    if (!prompts) { toast(`${f.name}: no prompts after 3 tries — skipped.`, true); continue; }
    // keep at most THROTTLE takes rendering before sending more
    for (;;) {
      const busy = S.takes.filter(t => t.status === "running" || t.status === "queued").length;
      if (busy + prompts.length <= Math.max(THROTTLE, prompts.length) || REF.stop) break;
      $("#refStatus").textContent = `${i + 1}/${n} · ${f.name} · waiting for ${busy} take${busy > 1 ? "s" : ""} to finish…`;
      await sleep(3000); await refreshTakes();
    }
    if (REF.stop) break;
    if (await runPrompts(prompts)) { ok++; sent += prompts.length; }
  }
  REF.busy = false; renderRef();
  $("#refStatus").textContent = `${REF.stop ? "Stopped" : "Done"} · ${sent} prompt${sent === 1 ? "" : "s"} sent from ${ok} of ${n} image${n > 1 ? "s" : ""}.`;
}
function wireReference() {
  const drop = $("#refDrop");
  $("#refPickImg").onclick = () => $("#refImg").click();
  $("#refPickFolder").onclick = () => $("#refFolder").click();
  $("#refImg").onchange = e => setRefFiles(e.target.files);
  $("#refFolder").onchange = e => {
    const fs = e.target.files;
    setRefFiles(fs, fs.length && fs[0].webkitRelativePath ? fs[0].webkitRelativePath.split("/")[0] : "Folder");
  };
  drop.ondragover = e => { if (!e.dataTransfer.types.includes("text/take")) { e.preventDefault(); drop.classList.add("over"); } };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { drop.classList.remove("over"); if (e.dataTransfer.files.length) { e.preventDefault(); setRefFiles(e.dataTransfer.files, e.dataTransfer.files.length > 1 ? "Dropped" : ""); } };
  $$("#refStyle button").forEach(b => b.onclick = () => { REF.style = b.dataset.s; renderRef(); });
  $("#rnMinus").onclick = () => { REF.n = Math.max(1, REF.n - 1); renderRef(); };
  $("#rnPlus").onclick = () => { REF.n = Math.min(20, REF.n + 1); renderRef(); };
  $("#refVar").oninput = renderRef;
  $("#refStop").onclick = () => { REF.stop = true; $("#refStatus").textContent = "Stopping after this image…"; };
  $("#refWrite").onclick = async () => {
    if (REF.files.length) return runFolder();
    REF.busy = true; renderRef(); $("#refStatus").textContent = `Writing ${REF.n} prompt${REF.n > 1 ? "s" : ""}…`;
    const ps = await writePrompts(REF.file);
    REF.busy = false; renderRef();
    if (!ps) { $("#refStatus").textContent = ""; return toast("Couldn't write prompts from that image — try again.", true); }
    REF.prompts = ps; $("#refStatus").textContent = `${ps.length} prompt${ps.length > 1 ? "s" : ""} — edit, drop or use any, then Generate.`;
    renderRefList();
  };
  if ($("#dlSize")) {
    $("#dlSize").value = String(LS.get("dlSize", 0));
    $("#dlSize").onchange = () => { LS.set("dlSize", dlSize()); renderStrip(); updateStitch(); };
  }
  renderRef();
}

// ── board: a spot, shot by shot ────────────────────────────────────────
// A TV spot is a fixed pipeline, not a graph: brief → shot list → keyframes →
// clips → the cut. The board keeps what must stay the same across shots — the
// kit (named references), the spot seed, the look set in the panel — and one
// card per shot. Keyframes and clips are ordinary takes tagged with their card,
// so everything the Shot view can do (upscale, grab a frame, edit) still works.
let B = null;                     // this reel's board; null while unread, so nothing saves over it
let boardT = null, boardDirty = false;
const boardOn = () => !STILLS && $("#main").classList.contains("boardview");
const rid = () => Math.random().toString(36).slice(2, 10);
const fmtSec = s => { s = Math.round(Math.max(0, +s || 0) * 10) / 10; const m = Math.floor(s / 60), r = Math.round((s - m * 60) * 10) / 10;
  return `${m}:${r < 10 ? "0" : ""}${Number.isInteger(r) ? r : r.toFixed(1)}`; };
const keyModel = () => modelById(S.model.image) || byKind("image")[0];
const clipModel = () => modelById(S.model.video) || byKind("video")[0];
const framed = m => m && m.i2v ? Object.assign({}, m, m.i2v) : m;      // the clip model as it runs from a keyframe
const clipDurations = () => { const m = clipModel(); return m ? (framed(m).durations || m.durations || []) : []; };
const elById = id => B.elements.find(e => e.id === id);
const elThumb = e => S.reel ? `${API}/reels/${S.reel.id}/thumb/${encodeURIComponent(e.file)}?w=160` : "";
const cardTakes = (s, role) => S.takes.filter(t => t.board === s.id && t.role === role);
const busyOf = (s, role) => cardTakes(s, role).some(t => t.status === "queued" || t.status === "running");
// the chosen keyframe or clip: the one picked, else the newest finished
function pickOf(s, role) {
  const done = cardTakes(s, role).filter(t => t.status === "done");
  return done.find(t => t.id === s[role]) || done[done.length - 1] || null;
}
function lenOf(s) {
  const c = pickOf(s, "clip");
  if (c && c.seconds) return s.trim && s.trim_for === c.id ? Math.max(0, s.trim[1] - s.trim[0]) : +c.seconds;
  return +s.duration || 0;
}
// a retake of a shot repeats its seed; different shots differ
const shotSeed = s => B.seed ? String((+B.seed + (s.n || 0) * 1009) % 2147483647) : "";
const cardDirection = s => Object.assign({}, S.direction, { shot: s.shot || "auto", move: s.move || "auto" });
function nextN() { return Math.max(0, ...S.takes.map(t => +t.shot || 0), ...((B && B.shots) || []).map(s => +s.n || 0)) + 1; }
function newBoard() { return { brief: "", target: 30, seed: 1 + Math.floor(Math.random() * 999999999), variants: 1, look: {}, elements: [], shots: [], title: "", notes: "" }; }
function newShot() {
  return { id: rid(), n: nextN(), type: "shot", scene: "", shot: "auto", move: "auto", duration: nearest(5, clipDurations()) || 5, elements: [],
           start: "cut", land: false, vo: "", super: "", key: "", clip: "", trim: null, trim_for: "" };
}

// ── footage: a clip someone shot, changed or carried on ────────────────
// Google's video models refuse real people, so their tools sort last and say so.
const isGoogle = m => /google/i.test(m.provider || "");
const toolOrder = (a, b) => isGoogle(a) - isGoogle(b) || (a.price ?? 99) - (b.price ?? 99);
const changeTools = () => byKind("video").filter(m => m.requires_source && m.category === "video-edit" && !/^sam/i.test(m.label)).sort(toolOrder);
const extendTools = () => byKind("video").filter(m => m.requires_source && m.category === "video-extend").sort(toolOrder);
const defaultChange = () => changeTools().find(m => m.id === "bfl/video-edit-v1") || changeTools()[0];
const defaultExtend = () => extendTools()[0];               // none on Router today: the Board then offers only changes
const toolOf = s => modelById(s.tool) || defaultChange();
const FOOTAGE_MAX = 100 * 1024 * 1024;
function pickFootage(cb) { pickFile("video/mp4,video/quicktime,video/webm,.mov,.mp4,.m4v,.webm", cb); }
// fetch can't report upload progress, and a 100 MB clip takes a while
function upload(path, fd, onp) {
  return new Promise(res => {
    const x = new XMLHttpRequest();
    x.open("POST", API + path);
    x.upload.onprogress = e => { if (e.lengthComputable) onp(e.loaded / e.total); };
    x.upload.onload = () => onp(1);
    x.onload = () => { let d; try { d = JSON.parse(x.responseText); } catch (e) { d = { error: `The server answered ${x.status}.` }; } if (x.status >= 400 && !d.error) d.error = `Upload failed (${x.status}).`; res(d); };
    x.onerror = () => res({ error: "The upload didn't go through. Check the connection and try again." });
    x.send(fd);
  });
}
async function importFootage(file, card) {
  if (file.size > FOOTAGE_MAX) { toast("Footage is limited to 100 MB for now. Export a 1080p H.264 clip: 30 seconds is about 40–60 MB.", true); return null; }
  await ensureReel();
  if (card) { if (!card.n) card.n = nextN(); await saveBoardNow(); }
  const fd = new FormData(); fd.append("file", file);
  if (card) { fd.append("shot", card.n); fd.append("board", card.id); }
  const r = await upload(`/reels/${S.reel.id}/footage`, fd, p => toast(p < 1 ? `Uploading ${file.name} · ${Math.round(p * 100)}%` : "Making a playable copy…"));
  if (r.error) { toast(r.error, true); return null; }
  mergeTakes([r.take, r.key].filter(Boolean));
  return r;
}

// ── board: persistence ─────────────────────────────────────────────────
function saveBoard(now) {
  if (!B) return;
  boardDirty = true; clearTimeout(boardT);
  boardT = setTimeout(saveBoardNow, now ? 0 : 500);
}
async function saveBoardNow() {
  clearTimeout(boardT);
  if (!B) return;
  if (!S.reel && !(B.brief.trim() || B.shots.length || B.elements.length)) return;   // a glance at an empty board makes no reel
  if (boardOn()) B.look = { direction: S.direction, aspect: S.aspect, resolution: S.resolution, pmode: S.pmode, audio: S.audio, colors: S.colors, main_color: S.mainColor };
  boardDirty = false;
  await ensureReel();
  const r = await api(`/reels/${S.reel.id}/board`, { method: "PUT", json: B });
  if (r.error) { boardDirty = true; toast(r.error, true); }
}
async function flushBoard() { if (boardDirty) await saveBoardNow(); }
function applyLook(L) {
  if (!L || !L.direction) return;
  S.direction = Object.assign(baseDirection(), L.direction);
  if (L.aspect) S.aspect = L.aspect;
  if (L.resolution) S.resolution = L.resolution;
  if (L.pmode) S.pmode = L.pmode;
  S.audio = !!L.audio; S.colors = L.colors || []; S.mainColor = L.main_color != null ? L.main_color : null;
}
function boardFrom(board, reelId) {
  if (board === null) { B = null; retryBoard(reelId, 0); return; }       // there is one; it couldn't be read just now
  B = Object.assign(newBoard(), board || {});
  if (boardOn() && B.shots.length) applyLook(B.look);
}
async function retryBoard(id, n) {
  await sleep(900);
  if (!S.reel || S.reel.id !== id) return;
  const r = await api(`/reels/${id}/board`);
  if (r.board) { boardFrom(r.board, id); onShotChange(); renderKit(); renderBoard(); }
  else if (n < 4) retryBoard(id, n + 1);
  else toast("This reel's board couldn't be read. Reload the page before editing it.", true);
}

// ── board: the kit ─────────────────────────────────────────────────────
function renderKit() {
  if (STILLS) return;
  const els = B ? B.elements : [];
  $("#kitList").innerHTML = els.length ? els.map(e => `<div class="kit" data-e="${e.id}"><span class="kt" style="background-image:url('${esc(elThumb(e))}')"></span>
      <input class="field" value="${esc(e.name)}" placeholder="Name — e.g. Maya" aria-label="Name">
      <select class="field" aria-label="Kind">${[["cast", "Cast"], ["product", "Product"], ["place", "Place"]].map(([v, l]) => `<option value="${v}" ${e.kind === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <button class="btn small ghost" data-rm title="Take out of the kit">✕</button></div>`).join("")
    : `<span class="hint">Cast, products and places, each from one good picture. Upload them, bring them from the Library, or drag a keyframe here. Name them, then tick who's in each shot.</span>`;
  $("#kitSeed").value = B && B.seed ? B.seed : "";
  $$("#kitList .kit").forEach(row => {
    const e = elById(row.dataset.e);
    $("input", row).oninput = ev => { e.name = ev.target.value; saveBoard(); $$(`[data-el="${e.id}"]`).forEach(b => b.textContent = e.name || "Untitled"); };
    $("select", row).onchange = ev => { e.kind = ev.target.value; saveBoard(); };
    $("[data-rm]", row).onclick = () => {
      B.elements = B.elements.filter(x => x !== e);
      B.shots.forEach(s => s.elements = s.elements.filter(id => id !== e.id));
      saveBoard(); renderKit(); renderBoard();
    };
  });
}
function addElement(file, name) {
  B.elements.push({ id: rid(), name: (name || "").trim() || `Element ${B.elements.length + 1}`, kind: "cast", file });
  saveBoard(); renderKit(); renderBoard();
  const ins = $$("#kitList .kit input"), last = ins[ins.length - 1];
  if (last) { last.focus(); last.select(); }
}
async function kitUpload(file) {
  await ensureReel();
  const fd = new FormData(); fd.append("file", file);
  toast("Uploading…");
  const r = await api(`/reels/${S.reel.id}/refs`, { method: "POST", body: fd });
  if (r.error) return toast(r.error, true);
  if (r.kind !== "image") return toast("The kit takes stills.", true);
  addElement(r.name, file.name.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " "));
}
// a take — from this reel or any other — copied in, so deleting the take never breaks the kit
async function kitFromTake(key, name) {
  await ensureReel();
  const r = await api(`/reels/${S.reel.id}/refs/copy`, { method: "POST", json: { item: key } });
  if (r.error) { toast(r.error, true); return false; }
  addElement(r.name, name || "");
  return true;
}
function wireKit() {
  $("#kitUpload").onclick = () => pickFile("image/png,image/jpeg,image/webp", kitUpload);
  $("#kitLib").onclick = () => { LIB.kind = "image"; openLibrary(); };
  $("#kitSeed").oninput = e => { const v = e.target.value.replace(/\D/g, "").slice(0, 9); e.target.value = v; if (B) { B.seed = v ? +v : null; saveBoard(); } };
  $("#kitDice").onclick = () => { if (!B) return; B.seed = 1 + Math.floor(Math.random() * 999999999); $("#kitSeed").value = B.seed; saveBoard(); };
  const list = $("#kitList");
  list.ondragover = e => { e.preventDefault(); list.classList.add("drop"); };
  list.ondragleave = () => list.classList.remove("drop");
  list.ondrop = async e => {
    e.preventDefault(); list.classList.remove("drop");
    if (!B) return;
    const tid = e.dataTransfer.getData("text/take");
    if (tid) {
      const t = S.takes.find(x => x.id === tid);
      if (!t || t.status !== "done" || t.kind !== "image") return toast("Drop a finished still — a keyframe, or a frame grabbed from a clip.");
      return kitFromTake(`${S.reel.id}:${t.id}`);
    }
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) kitUpload(f);
  };
}

// ── board: rendering ───────────────────────────────────────────────────
function renderBoardModels() {
  if (STILLS) return;
  const row = (kind, m, label) => `<button class="mrow" data-k="${kind}" type="button">${m ? logo(m) : ""}<span><span class="rt">${label}</span><br>
    <span class="mname">${esc(m ? m.label : "None")}</span><br><span class="mmeta">${m ? esc(modelMeta(m)) : ""}</span></span><span class="chev">▾</span></button>`;
  $("#bModels").innerHTML = row("image", keyModel(), "Keyframes") + row("video", clipModel(), "Clips");
  $$("#bModels .mrow").forEach(b => b.onclick = () => { S.mode = b.dataset.k; mFilter.clear(); openModelSheet(false); });
}
function renderBoard() {
  if (STILLS) return;
  const el = $("#board");
  if (!B) { el.innerHTML = `<div class="bempty"><b>Reading the board…</b></div>`; return; }
  const durs = clipDurations();
  B.shots.forEach(s => { if (s.type !== "footage" && durs.length && !durs.includes(+s.duration)) s.duration = nearest(+s.duration || 5, durs); });
  el.innerHTML = `<div class="bhead">
      <textarea class="field" id="bBrief" placeholder="The brief: the product, who it's for, the idea, the one thing to remember. Gobo turns it into a shot list that runs to the length you pick.">${esc(B.brief)}</textarea>
      <div class="bline"><span class="hint">Length</span><span class="seg" id="bTarget">${[15, 30, 60].map(n => `<button data-n="${n}" class="${+B.target === n ? "on" : ""}">:${n}</button>`).join("")}</span>
        <button class="btn small primary" id="bPlan">${B.shots.length ? "Rewrite the shot list" : "Write the shot list"}</button><span class="grow"></span>
        <span class="hint">Keyframes per shot</span><span class="stepper" style="width:84px"><button id="bVm" aria-label="Fewer">−</button><span id="bV">${B.variants}</span><button id="bVp" aria-label="More">+</button></span></div>
      <div id="bNotes">${B.title ? `<b>${esc(B.title)}</b>${B.notes ? " — " : ""}` : ""}${esc(B.notes || "")}</div>
      <div id="bTime"></div>
      <div class="bline"><button class="btn small" id="bKeyAll"></button><button class="btn small" id="bAnimAll"></button><span class="grow"></span>
        <button class="btn small" id="bAnimatic" title="The keyframes held for their planned lengths: the spot's timing before any video is paid for">Animatic</button>
        <button class="btn small primary" id="bCut" title="The chosen clips, trimmed, in board order. A keyframe stands in where a clip isn't made yet.">Cut the spot</button>
        <button class="btn small hidden" id="bWatch">▶ Last cut</button></div>
    </div>
    <div class="bgrid">${B.shots.length ? "" : `<div class="bempty" style="grid-column:1/-1"><b>No shots yet.</b>Write the brief and let Gobo draft the shot list, or add cards yourself.</div>`}
      ${B.shots.map((s, i) => cardHtml(s, i)).join("")}<div class="badds"><button class="badd" id="bAdd">＋ AI shot</button><button class="badd real" id="bFoot" title="A clip you shot: change it, extend it, lead into it">＋ Footage</button></div></div>`;
  wireBoard();
  refreshBoard();
}
function cardHtml(s, i) {
  if (s.type === "footage") return footageCardHtml(s, i);
  const durs = clipDurations();
  const opts = (g, v) => CAT.groups[g].options.map(o => `<option value="${o.id}" ${o.id === v ? "selected" : ""}>${esc(o.id === "auto" ? (g === "shot" ? "Framing · auto" : "Move · auto") : o.label)}</option>`).join("");
  return `<div class="bcard ${s.start === "continue" ? "cont" : ""}" data-s="${s.id}">
    <div class="bct" title="Drag to move this shot"><span class="grip" aria-hidden="true">⠿</span><span class="no">${i + 1}</span><span class="dur" data-dur></span>
      ${i ? `<span class="seg" data-start><button data-v="cut" class="${s.start !== "continue" ? "on" : ""}" title="A new setup, from its own keyframe">Cut</button><button data-v="continue" class="${s.start === "continue" ? "on" : ""}" title="Starts on the last frame of the shot before">Continue</button></span>` : ""}
      <span class="grow"></span>
      <button class="ib" data-mv="-1" title="Earlier" ${i ? "" : "disabled"}>←</button><button class="ib" data-mv="1" title="Later" ${i < B.shots.length - 1 ? "" : "disabled"}>→</button>
      <button class="ib" data-dup title="Duplicate">⧉</button><button class="ib" data-del title="Take the card off the board (its takes stay in the reel)">✕</button></div>
    <div class="bframe" data-frame></div>
    <div class="bvars" data-vars></div>
    <div class="bbody">
      <textarea class="field" data-f="scene" placeholder="What the camera sees: who, where, what happens, how the shot ends.">${esc(s.scene)}</textarea>
      <div class="bsel"><select class="field" data-f="shot" aria-label="Framing">${opts("shot", s.shot)}</select><select class="field" data-f="move" aria-label="Camera move">${opts("move", s.move)}</select>
        <select class="field" data-f="duration" aria-label="Length">${(durs.length ? durs : [s.duration]).map(d => `<option value="${d}" ${+d === +s.duration ? "selected" : ""}>${d}s</option>`).join("")}</select></div>
      <div class="bels">${B.elements.map(e => `<button data-el="${e.id}" class="${s.elements.includes(e.id) ? "on" : ""}" title="In this shot">${esc(e.name || "Untitled")}</button>`).join("") || `<span class="hint">Put cast and products in the kit to tick them here.</span>`}</div>
      <div class="bwords"><input class="field" data-f="vo" placeholder="Voice-over" value="${esc(s.vo)}"><input class="field" data-f="super" placeholder="On-screen text" value="${esc(s.super)}"></div>
    </div>
    <div class="bacts" data-acts></div></div>`;
}
function footageCardHtml(s, i) {
  const m = toolOf(s), ext = m && m.category === "video-extend", durs = ext ? (m.durations || []) : [];
  const opt = x => `<option value="${x.id}" ${m && x.id === m.id ? "selected" : ""}>${esc(x.label)}${x.price != null ? ` · ≈${fmtUSD(x.price)}` : ""}${x.refs ? ` · ${x.refs} refs` : ""}${isGoogle(x) ? " · no real people" : ""}</option>`;
  return `<div class="bcard real" data-s="${s.id}">
    <div class="bct" title="Drag to move this shot"><span class="grip" aria-hidden="true">⠿</span><span class="no">${i + 1}</span><span class="tag">Footage</span><span class="dur" data-dur></span><span class="grow"></span>
      <button class="ib" data-mv="-1" title="Earlier" ${i ? "" : "disabled"}>←</button><button class="ib" data-mv="1" title="Later" ${i < B.shots.length - 1 ? "" : "disabled"}>→</button>
      <button class="ib" data-del title="Take the card off the board (the footage stays in the reel)">✕</button></div>
    <div class="bframe" data-frame></div>
    <div class="bvars" data-vars></div>
    <div class="bbody">
      <textarea class="field" data-f="scene" placeholder="What's in this footage. Gobo plans the AI shots around it.">${esc(s.scene)}</textarea>
      <div class="btool ${durs.length ? "" : "nodur"}"><select class="field" data-f="tool" aria-label="What to do with the footage">
          <optgroup label="Change it">${changeTools().map(opt).join("")}</optgroup>${extendTools().length ? `<optgroup label="Extend it">${extendTools().map(opt).join("")}</optgroup>` : ""}</select>
        ${durs.length ? `<select class="field" data-f="tooldur" aria-label="How much longer">${durs.map(d => `<option value="${d}" ${+d === nearest(+s.tooldur || 5, durs) ? "selected" : ""}>+${d}s</option>`).join("")}</select>` : ""}</div>
      ${m && m.takes_prompt ? `<input class="field" data-f="change" value="${esc(s.change)}" placeholder="${ext ? "What happens next: she keeps walking and turns the corner" : "The change: snowy winter afternoon; the Icebox bottle in her hand"}">`
        : `<span class="hint">${esc(m ? m.label : "This tool")} takes no instructions${m && m.refs ? ": tick what to swap in from the kit" : ""}.</span>`}
      <div class="bels">${B.elements.map(e => `<button data-el="${e.id}" class="${s.elements.includes(e.id) ? "on" : ""}" title="Swap this in (models that take references)">${esc(e.name || "Untitled")}</button>`).join("") || `<span class="hint">To swap in a product or wardrobe, put it in the kit.</span>`}</div>
      <div class="bwords"><input class="field" data-f="vo" placeholder="Voice-over" value="${esc(s.vo)}"><input class="field" data-f="super" placeholder="On-screen text" value="${esc(s.super)}"></div>
    </div>
    <div class="bacts" data-acts></div></div>`;
}
function frameHtml(s) {
  if (s._importing) return `<div class="empty"></div><div class="st"><div><div class="spinner"></div>Importing the footage…</div></div>`;
  const clip = pickOf(s, "clip"), key = pickOf(s, "key");
  const show = s._view === "key" ? (key || clip) : (clip || key);
  const waiting = [...cardTakes(s, "clip"), ...cardTakes(s, "key")].find(t => lineNote(t));
  const busy = waiting ? lineShort(waiting) : busyOf(s, "clip") ? (s.type === "footage" ? "Changing" : (cardTakes(s, "clip").find(t => t.status === "running" || t.status === "queued") || {}).action === "extend" ? "Extending" : "Animating") : busyOf(s, "key") ? (s.start === "continue" ? "Reading the frame" : "Keyframing") : "";
  const all = S.takes.filter(t => t.board === s.id), last = all[all.length - 1];
  let h = "";
  if (show) {
    h += show.kind === "video"
      ? `<video src="${esc(show.url)}" ${show.thumb_url ? `poster="${esc(show.thumb_url)}"` : ""} muted loop playsinline preload="none"></video>`
      : `<img src="${esc(show.thumb_url || show.url)}" alt="" draggable="true" data-take="${show.id}" title="Drag onto the kit to make this a reference">`;
    const what = show.action === "footage" ? "Footage" : show.action === "change" ? "Changed" : show.action === "extend" ? "Extension" : show.kind === "video" ? "Clip" : "Keyframe";
    h += `<span class="what">${what} ${esc(show.letter)}</span><button class="iconbtn open" data-open="${show.id}" title="Open in the Shot view: upscale, grab a frame, edit">Open ↗</button>`;
  } else {
    h += `<div class="empty">${s.start === "continue" ? "Starts on the last frame of the shot before" : "No keyframe yet"}</div>`;
  }
  if (busy) h += `<div class="st" ${waiting ? `title="${esc(lineNote(waiting))}"` : ""}><div><div class="spinner"></div>${busy}${waiting ? "" : "…"}</div></div>`;
  // the newest run on the card failed: said over whatever the card shows, until dismissed
  else if (last && isFailOpen(last)) h += `<div class="st bad fail"><div><b>${esc(actionName(last))} ${esc(last.letter)} failed</b>
      <p>${esc(firstSentence(last.hint || last.error || ""))}</p>
      <button class="btn small" data-fwhy="${last.id}">Why, and what to try</button> <button class="btn small" data-fseen="${last.id}">Dismiss</button></div></div>`;
  else if (!show && last && (last.status === "failed" || last.status === "cancelled")) h += `<div class="st bad">${esc(last.label)}: ${esc((last.error || "stopped").slice(0, 140))}</div>`;
  return h;
}
function varsHtml(s) {
  const ks = cardTakes(s, "key"), cs = cardTakes(s, "clip"), pk = pickOf(s, "key"), pc = pickOf(s, "clip");
  const b = (t, on) => `<button data-pick="${t.id}" class="${on ? "on" : ""} ${t.status === "failed" || t.status === "cancelled" ? "bad" : ""} ${t.status === "running" || t.status === "queued" ? "run" : ""}" title="${esc(t.label)} · ${esc(t.status)}">${esc(t.letter)}</button>`;
  return (ks.length ? `<span class="lab">Key</span>${ks.map(t => b(t, pk && pk.id === t.id)).join("")}` : "")
    + (cs.length ? `<span class="sep"></span><span class="lab">Clip</span>${cs.map(t => b(t, pc && pc.id === t.id)).join("")}` : "");
}
function trimHtml(s, clip) {
  if (!clip || !clip.seconds) return "";
  const t = s.trim && s.trim_for === clip.id ? s.trim : [0, +clip.seconds];
  return `<span class="btrim" title="Use only this stretch of the clip">In <input class="field" type="number" step="0.1" min="0" max="${clip.seconds}" data-trim="0" value="${(+t[0]).toFixed(1)}">
      Out <input class="field" type="number" step="0.1" min="0" max="${clip.seconds}" data-trim="1" value="${(+t[1]).toFixed(1)}"></span>`;
}
function actsHtml(s, i) {
  const key = pickOf(s, "key"), clip = pickOf(s, "clip"), cm = clipModel(), prev = i ? B.shots[i - 1] : null;
  if (s.type === "footage") {
    const m = toolOf(s), ext = m && m.category === "video-extend";
    return `<button class="btn small primary" data-act="tool" ${clip && m ? "" : "disabled"} title="${ext ? "Runs on the trimmed stretch; the extension becomes the next card" : "Runs on the trimmed stretch; the result is a new version of this card"}">${ext ? "Extend" : "Change"}${m && m.price != null ? ` · ≈ ${fmtUSD(m.price)}` : ""}</button><span class="grow"></span>` + trimHtml(s, clip);
  }
  if (clip && clip.action === "extend") return `<span class="hint">Extension of shot ${i}</span><span class="grow"></span>` + trimHtml(s, clip);
  const keyBtn = s.start === "continue"
    ? `<button class="btn small" data-act="key" ${prev && pickOf(prev, "clip") ? "" : "disabled"} title="This shot starts on the last frame of shot ${i}'s clip">${key ? "Re-read last frame" : `Last frame of ${i}`}</button>`
    : `<button class="btn small ${key ? "" : "primary"}" data-act="key">${key ? "New keyframe" : "Keyframe"}${B.variants > 1 ? ` ×${B.variants}` : ""}</button>`;
  const anim = `<button class="btn small ${key && !clip ? "primary" : ""}" data-act="clip" ${key || (cm && !cm.requires_frame) ? "" : "disabled"}>${clip ? "Re-animate" : "Animate"}</button>`;
  const land = cm && framed(cm).last_frame && B.shots[i + 1]
    ? `<label class="bland" title="The clip ends on shot ${i + 2}'s keyframe: a seamless hand-off"><input type="checkbox" data-land ${s.land ? "checked" : ""}>ends on ${i + 2}</label>` : "";
  return keyBtn + anim + land + `<span class="grow"></span>` + trimHtml(s, clip);
}
function keyCost() { const m = keyModel(); return m && m.price != null ? m.price * B.variants : null; }
function clipCost(s) { const m = clipModel(); return m && m.price != null ? (m.price_per === "second" ? m.price * (+s.duration || 5) : m.price) : null; }
const costOf = xs => xs.some(x => x == null) ? "cost varies" : `≈ ${fmtUSD(xs.reduce((a, b) => a + b, 0))}`;
function renderTimeline() {
  const el = $("#bTime"); if (!el) return;
  const lens = B.shots.map(lenOf), total = lens.reduce((a, b) => a + b, 0), span = Math.max(+B.target, total) || 1;
  const state = s => s.type === "footage" ? "real" : pickOf(s, "clip") ? "clip" : pickOf(s, "key") ? "key" : "";
  const off = total - B.target;
  el.innerHTML = `<div class="tl">${B.shots.map((s, i) => `<button class="seg2 ${state(s)} ${busyOf(s, "key") || busyOf(s, "clip") ? "busy" : ""}" style="flex:${Math.max(lens[i], .2)} 0 0" data-go="${s.id}" title="Shot ${i + 1} · ${fmtSec(lens[i])}">${i + 1}</button>`).join("")}${total < span ? `<span style="flex:${span - total} 0 0"></span>` : ""}<span class="mark" style="left:calc(${B.target / span * 100}% - 1px)" title="${fmtSec(B.target)}"></span></div>
    <div class="tlmeta"><span>Running time <b>${fmtSec(total)}</b> of ${fmtSec(B.target)} ${Math.abs(off) <= .5 ? `<span class="on">· on time</span>` : off > 0 ? `<span class="over">· ${fmtSec(off)} over</span>` : `· ${fmtSec(-off)} to fill`}</span>
      <span>${B.shots.length} shot${B.shots.length === 1 ? "" : "s"} · ${B.shots.filter(s => pickOf(s, "clip")).length} animated</span></div>`;
  $$("#bTime [data-go]").forEach(b => b.onclick = () => { const c = $(`.bcard[data-s="${b.dataset.go}"]`); if (c) c.scrollIntoView({ behavior: "smooth", block: "center" }); });
}
// what changes while takes render, without touching anything being typed in
function refreshBoard() {
  if (!B || !boardOn() || !$("#board .bhead")) return;
  renderTimeline();
  const needKey = B.shots.filter(s => s.type !== "footage" && s.start !== "continue" && s.scene.trim() && !pickOf(s, "key") && !busyOf(s, "key"));
  const needClip = B.shots.filter(s => s.type !== "footage" && pickOf(s, "key") && !pickOf(s, "clip") && !busyOf(s, "clip"));
  // an extension that came back with the original attached plays only its new seconds
  B.shots.forEach(s => { const c = pickOf(s, "clip"); if (c && +c.new_from > 0 && s.trim_for !== c.id) { s.trim = [+c.new_from, +c.seconds]; s.trim_for = c.id; saveBoard(); } });
  const k = $("#bKeyAll"), a = $("#bAnimAll");
  k.textContent = needKey.length ? `Keyframe ${needKey.length} shot${needKey.length > 1 ? "s" : ""} · ${costOf(needKey.map(keyCost))}` : "Keyframes made";
  k.disabled = !needKey.length;
  a.textContent = needClip.length ? `Animate ${needClip.length} shot${needClip.length > 1 ? "s" : ""} · ${costOf(needClip.map(clipCost))}` : "Animate";
  a.disabled = !needClip.length;
  const cut = S.takes.filter(t => t.action === "stitch" && t.status === "done" && /^(Spot cut|Animatic)/.test(t.label || "")).pop();
  $("#bWatch").classList.toggle("hidden", !cut);
  if (cut) { $("#bWatch").textContent = `▶ ${cut.label}${cut.seconds ? ` · ${fmtSec(cut.seconds)}` : ""}`; $("#bWatch").onclick = () => { setView("shot"); select(cut.id); }; }
  $$("#board .bcard").forEach(refreshCard);
}
function refreshCard(card) {
  const i = B.shots.findIndex(x => x.id === card.dataset.s), s = B.shots[i];
  if (!s) return;
  const typing = card.contains(document.activeElement) && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
  $("[data-frame]", card).innerHTML = frameHtml(s);
  $("[data-vars]", card).innerHTML = varsHtml(s);
  if (!typing || !$("[data-acts]", card).contains(document.activeElement)) $("[data-acts]", card).innerHTML = actsHtml(s, i);
  const c = pickOf(s, "clip");
  $("[data-dur]", card).textContent = `· ${fmtSec(lenOf(s))}${c && s.trim && s.trim_for === c.id ? " trimmed" : ""}`;
}

// ── board: editing ─────────────────────────────────────────────────────
function wireBoard() {
  $("#bBrief").oninput = e => { B.brief = e.target.value; saveBoard(); };
  $$("#bTarget button").forEach(b => b.onclick = () => { B.target = +b.dataset.n; $$("#bTarget button").forEach(x => x.classList.toggle("on", x === b)); saveBoard(); renderTimeline(); });
  $("#bPlan").onclick = planSpot;
  const variants = d => { B.variants = Math.max(1, Math.min(4, B.variants + d)); $("#bV").textContent = B.variants; saveBoard(); refreshBoard(); };
  $("#bVm").onclick = () => variants(-1); $("#bVp").onclick = () => variants(1);
  $("#bKeyAll").onclick = keyAll; $("#bAnimAll").onclick = animAll;
  $("#bAnimatic").onclick = () => cutSpot(true); $("#bCut").onclick = () => cutSpot(false);
  $("#bFoot").onclick = () => pickFootage(addFootageCard);
  $("#bAdd").onclick = () => {
    B.shots.push(newShot()); saveBoard(); renderBoard();
    const cards = $$("#board .bcard"), c = cards[cards.length - 1];
    if (c) { c.scrollIntoView({ behavior: "smooth", block: "center" }); $("textarea", c).focus(); }
  };
  $$("#board .bcard").forEach(wireCard);
}
// a card dragged by its top bar to another place in the film: pointer events,
// so a finger works as well as a mouse (the ← → buttons still move it one step)
function dragCard(card, e) {
  const s = B.shots.find(x => x.id === card.dataset.s), bar = e.currentTarget, grid = card.parentElement;
  if (!s || e.button !== 0 || e.target.closest("button, select, input, textarea")) return;
  e.preventDefault();
  const x0 = e.clientX, y0 = e.clientY;
  let moving = false, ghost = null, at = null, py = y0, scrollT = null;
  const scroller = (() => {
    for (let p = grid; p && p !== document.body; p = p.parentElement) {
      const o = getComputedStyle(p).overflowY;
      if ((o === "auto" || o === "scroll") && p.scrollHeight > p.clientHeight) return p;
    }
    return document.scrollingElement;
  })();
  const clear = () => $$(".bcard.dropb, .bcard.dropa", grid).forEach(c => c.classList.remove("dropb", "dropa"));
  // the card under the pointer, and which side of it: left or right, or above or below in one column
  const target = (x, y) => {
    const one = card.offsetWidth > grid.clientWidth * 0.66;
    grid.classList.toggle("onecol", one);
    for (const c of $$(".bcard", grid)) {
      if (c === card) continue;
      const r = c.getBoundingClientRect();
      if (x >= r.left - 8 && x <= r.right + 8 && y >= r.top - 8 && y <= r.bottom + 8)
        return { c, after: one ? y > r.top + r.height / 2 : x > r.left + r.width / 2 };
    }
    return null;
  };
  const start = () => {
    moving = true;
    card.classList.add("dragging"); document.body.classList.add("cardmoving");
    ghost = document.createElement("div"); ghost.className = "bghost"; ghost.textContent = `Shot ${B.shots.indexOf(s) + 1}`;
    document.body.appendChild(ghost);
    // held near the top or bottom of the view, the board scrolls
    scrollT = setInterval(() => {
      const r = scroller === document.scrollingElement ? { top: 0, bottom: innerHeight } : scroller.getBoundingClientRect();
      if (py < r.top + 60) scroller.scrollBy(0, -14); else if (py > r.bottom - 60) scroller.scrollBy(0, 14);
    }, 16);
  };
  const move = ev => {
    if (!moving) { if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 6) return; start(); }
    py = ev.clientY;
    ghost.style.transform = `translate(${ev.clientX + 14}px, ${ev.clientY + 10}px)`;
    at = target(ev.clientX, ev.clientY);
    clear(); if (at) at.c.classList.add(at.after ? "dropa" : "dropb");
  };
  const end = drop => {
    bar.removeEventListener("pointermove", move); bar.removeEventListener("pointerup", up);
    bar.removeEventListener("pointercancel", cancel); document.removeEventListener("keydown", esc);
    clearInterval(scrollT); clear(); grid.classList.remove("onecol");
    card.classList.remove("dragging"); document.body.classList.remove("cardmoving"); if (ghost) ghost.remove();
    if (!moving || !drop || !at) return;
    const from = B.shots.indexOf(s);
    B.shots.splice(from, 1);
    B.shots.splice(B.shots.findIndex(x => x.id === at.c.dataset.s) + (at.after ? 1 : 0), 0, s);
    if (B.shots.indexOf(s) === from) return;
    if (B.shots[0].start === "continue") B.shots[0].start = "cut";        // nothing before the first shot to continue
    saveBoard(); renderBoard();
  };
  const up = () => end(true), cancel = () => end(false), esc = ev => { if (ev.key === "Escape") end(false); };
  try { bar.setPointerCapture(e.pointerId); } catch (err) { /* a pointer the page didn't start */ }
  bar.addEventListener("pointermove", move); bar.addEventListener("pointerup", up);
  bar.addEventListener("pointercancel", cancel); document.addEventListener("keydown", esc);
}
function wireCard(card) {
  const s = B.shots.find(x => x.id === card.dataset.s);
  if (!s) return;
  $$("[data-f]", card).forEach(f => {
    const k = f.dataset.f;
    f[f.tagName === "SELECT" ? "onchange" : "oninput"] = () => {
      s[k] = k === "duration" || k === "tooldur" ? +f.value : f.value; saveBoard();
      if (k === "tool") return renderBoard();
      if (k === "duration") { refreshCard(card); renderTimeline(); }
      if (k === "scene") refreshBoard();
    };
  });
  $$("[data-el]", card).forEach(b => b.onclick = () => {
    const id = b.dataset.el;
    s.elements = s.elements.includes(id) ? s.elements.filter(x => x !== id) : [...s.elements, id];
    b.classList.toggle("on", s.elements.includes(id)); saveBoard();
  });
  $$("[data-start] button", card).forEach(b => b.onclick = () => { s.start = b.dataset.v; saveBoard(); renderBoard(); });
  $$("[data-mv]", card).forEach(b => b.onclick = () => {
    const i = B.shots.indexOf(s), j = i + +b.dataset.mv;
    if (j < 0 || j >= B.shots.length) return;
    [B.shots[i], B.shots[j]] = [B.shots[j], B.shots[i]];
    if (B.shots[0].start === "continue") B.shots[0].start = "cut";        // nothing before the first shot to continue
    saveBoard(); renderBoard();
  });
  $(".bct", card).onpointerdown = e => dragCard(card, e);
  const dup = $("[data-dup]", card);
  if (dup) dup.onclick = () => {
    B.shots.splice(B.shots.indexOf(s) + 1, 0, Object.assign(newShot(), { scene: s.scene, shot: s.shot, move: s.move, duration: s.duration,
      elements: [...s.elements], vo: s.vo, super: s.super }));
    saveBoard(); renderBoard();
  };
  $("[data-del]", card).onclick = () => {
    if ((s.scene.trim() || S.takes.some(t => t.board === s.id)) && !confirm(`Take shot ${B.shots.indexOf(s) + 1} off the board? Its takes stay in the reel.`)) return;
    B.shots = B.shots.filter(x => x !== s);
    if (B.shots[0] && B.shots[0].start === "continue") B.shots[0].start = "cut";
    saveBoard(); renderBoard();
  };
  // the parts that change as takes land are redrawn, so their clicks are delegated here
  card.addEventListener("click", e => {
    const p = e.target.closest("[data-pick]"); if (p) return pickTake(s, p.dataset.pick);
    const fs = e.target.closest("[data-fseen]"); if (fs) return seeFail(fs.dataset.fseen);
    const fw = e.target.closest("[data-fwhy]"); if (fw) { unseeFail(fw.dataset.fwhy); const c = $(`#fails [data-fail="${fw.dataset.fwhy}"]`); if (c) { c.classList.add("flash"); setTimeout(() => c.classList.remove("flash"), 1800); } return; }
    const a = e.target.closest("[data-act]"); if (a) return a.dataset.act === "key" ? keyframe(s) : a.dataset.act === "tool" ? runTool(s) : animate(s);
    const o = e.target.closest("[data-open]"); if (o) { setView("shot"); select(o.dataset.open); }
  });
  card.addEventListener("change", e => {
    if (e.target.matches("[data-land]")) { s.land = e.target.checked; saveBoard(); }
    if (e.target.matches("[data-trim]")) trimShot(s, card, e.target);
  });
  card.addEventListener("dragstart", e => { const img = e.target.closest("[data-take]"); if (img) { e.dataTransfer.setData("text/take", img.dataset.take); e.dataTransfer.effectAllowed = "copy"; } });
  const fr = $("[data-frame]", card);
  fr.onmouseenter = () => { const v = $("video", fr); if (v) v.play().catch(() => {}); };
  fr.onmouseleave = () => { const v = $("video", fr); if (v) v.pause(); };
}
function pickTake(s, id) {
  const t = S.takes.find(x => x.id === id);
  if (!t) return;
  if (t.status === "failed") return unseeFail(t.id);           // bring its explanation back
  if (t.status === "cancelled") return toast(`${t.label} · ${t.letter}: ${t.error || "stopped"}`, true);
  if (t.status !== "done") return toast("Still rendering.");
  s[t.role] = t.id; s._view = t.role; saveBoard();
  const card = $(`.bcard[data-s="${s.id}"]`); if (card) refreshCard(card);
  renderTimeline();
}
function trimShot(s, card, input) {
  const c = pickOf(s, "clip"); if (!c) return;
  const t = s.trim && s.trim_for === c.id ? [...s.trim] : [0, +c.seconds];
  t[+input.dataset.trim] = Math.max(0, Math.min(+c.seconds, +input.value || 0));
  if (t[1] - t[0] < 0.3) { input.value = (s.trim && s.trim_for === c.id ? s.trim : [0, +c.seconds])[+input.dataset.trim].toFixed(1); return toast("Keep at least a third of a second."); }
  s.trim = t; s.trim_for = c.id; saveBoard();
  $("[data-dur]", card).textContent = `· ${fmtSec(lenOf(s))} trimmed`;
  renderTimeline();
}

// ── board: shooting ────────────────────────────────────────────────────
async function runCard(s, body) {
  await ensureReel();
  if (!s.n) s.n = nextN();
  await saveBoardNow();                 // the server numbers new shots around the board's
  const r = await api(`/reels/${S.reel.id}/run`, { method: "POST", json: Object.assign(body, { shot: s.n, board: s.id }) });
  if (r.error) { refused(r.error, body.model); return null; }
  s._view = body.role; s[body.role] = "";
  mergeTakes(r.takes); poll(); saveBoard();
  return r.takes;
}
async function keyframe(s) {
  if (s.start === "continue") return continueFrom(s);
  if (!s.scene.trim()) return toast("Write what the camera sees first.");
  const m = keyModel(); if (!m) return toast("No still model is available.", true);
  const els = s.elements.map(elById).filter(Boolean), cap = m.refs || 0;
  if (els.length && !cap) return toast(`${m.label} takes no reference images, so the kit can't reach this keyframe. Pick a keyframe model that does — Nano Banana Pro, GPT Image, Seedream.`, true);
  if (!els.length && m.requires_ref) return toast(`${m.label} edits a picture. Tick a kit element on this shot, or pick another keyframe model.`, true);
  if (els.length > cap) toast(`${m.label} takes ${cap} reference${cap > 1 ? "s" : ""}; using the first ${cap}.`);
  const use = els.slice(0, cap);
  const aspects = use.length && m.ref_aspects ? m.ref_aspects : (m.aspects && m.aspects.length ? m.aspects : CAT.aspects.map(a => a.id));
  const res = (m.resolutions || []).length ? (m.default_resolution && m.resolutions.includes(m.default_resolution) ? m.default_resolution : m.resolutions[m.resolutions.length > 1 ? 1 : 0]) : "";
  return runCard(s, { mode: "image", model: m.id, models: [m.id], scene: s.scene, direction: cardDirection(s), enhance: S.pmode !== "plain",
    refs: use.map(e => e.file), ref_names: use.map(e => e.name), colors: S.colors, main_color: S.mainColor,
    aspect: snapAspect(S.aspect, aspects), resolution: res, takes: B.variants, seed: m.seed ? shotSeed(s) : "",
    negative_prompt: m.negative ? S.negative : "", action: "keyframe", role: "key" });
}
async function continueFrom(s) {
  const i = B.shots.indexOf(s), prev = B.shots[i - 1], clip = prev && pickOf(prev, "clip");
  if (!clip) return toast(`Shot ${i + 1} picks up where shot ${i} ends. Animate shot ${i} first.`);
  await saveBoardNow();
  const r = await api(`/reels/${S.reel.id}/takes/${clip.id}/frame`, { method: "POST", json: { at: "last", shot: s.n, board: s.id } });
  if (r.error) return toast(r.error, true);
  s.key = r.take.id; s._view = "key"; saveBoard();
  mergeTakes([r.take]);
  return [r.take];
}
async function animate(s) {
  const m = clipModel(); if (!m) return toast("No video model is available.", true);
  if (!s.scene.trim()) return toast("Write what the camera sees first.");
  const key = pickOf(s, "key");
  if (key && !(m.i2v || m.first_frame)) return toast(`${m.label} can't start from a keyframe. Pick a clip model that can — Seedance, Veo, Kling.`, true);
  if (!key && m.requires_frame) return toast(`${m.label} starts from a keyframe. Make one first.`, true);
  const v = key ? framed(m) : m, i = B.shots.indexOf(s), next = B.shots[i + 1];
  const nextKey = next && s.land ? (next.type === "footage" ? await landFrame(next) : pickOf(next, "key")) : null;
  if (s.land && !nextKey) toast(`Shot ${i + 2} has no keyframe yet, so this clip won't end on it.`);
  // the keyframe carries the kit into the clip; without one, a model that takes references gets them
  const els = key ? [] : s.elements.map(elById).filter(Boolean).slice(0, m.refs || 0);
  const durs = v.durations || [];
  return runCard(s, { mode: "video", model: m.id, models: [m.id], scene: s.scene, direction: cardDirection(s), enhance: S.pmode !== "plain",
    refs: els.map(e => e.file), ref_names: els.map(e => e.name), first_frame: key ? key.file : null,
    last_frame: s.land && nextKey && v.last_frame ? nextKey.file : null, aspect: S.aspect, resolution: S.resolution,
    duration: durs.length ? nearest(+s.duration || 5, durs) : null, audio: S.audio, seed: m.seed ? shotSeed(s) : "",
    negative_prompt: m.negative ? S.negative : "", takes: 1, action: "animate", role: "clip" });
}
async function addFootageCard(file) {
  const s = Object.assign(newShot(), { type: "footage", tool: (defaultChange() || {}).id || "", _importing: true });
  B.shots.push(s); renderBoard();
  const r = await importFootage(file, s);
  delete s._importing;
  if (!r) { B.shots = B.shots.filter(x => x !== s); saveBoard(); renderBoard(); return; }
  s.clip = r.take.id; if (r.key) s.key = r.key.id;
  saveBoard(); renderBoard();
  const c = $(`.bcard[data-s="${s.id}"] textarea`); if (c) { c.scrollIntoView({ behavior: "smooth", block: "center" }); c.focus(); }
  toast("Footage in. Say what's in it, then change it or extend it. To lead into it, tick “ends on” on the shot before.");
}
// a change is a new version of the footage card; an extension is the next card
async function runTool(s) {
  const m = toolOf(s), src = pickOf(s, "clip");
  if (!m || !src) return;
  const ext = m.category === "video-extend", text = (s.change || "").trim();
  if (m.takes_prompt && !text) { const f = $(`.bcard[data-s="${s.id}"] [data-f=change]`); if (f) f.focus(); return toast(ext ? "Say what happens next." : "Describe the change."); }
  // a kit item the instruction names goes with it, ticked or not ("…with the olive blouse")
  const said = text.toLowerCase();
  const mentioned = B.elements.filter(e => e.name && !s.elements.includes(e.id) && said.includes(e.name.toLowerCase().replace(/^the\s+/, "")));
  if (mentioned.length) {
    s.elements.push(...mentioned.map(e => e.id)); saveBoard();
    const card = $(`.bcard[data-s="${s.id}"]`); if (card) mentioned.forEach(e => { const b = $(`[data-el="${e.id}"]`, card); if (b) b.classList.add("on"); });
  }
  const els = s.elements.map(elById).filter(Boolean).slice(0, m.refs || 0);
  if (m.requires_ref && !els.length) return toast(`${m.label} swaps in what's in the kit. Tick an element on this card first.`, true);
  if (m.refs && !els.length && B.elements.length && !confirm(`Nothing from the kit is ticked on this card, so ${m.label} gets only your words. Run it anyway?`)) return;
  if (!m.refs && s.elements.length) toast(`${m.label} takes no reference pictures, so it goes by your words alone. For the kit's picture, pick a tool showing “refs”.`);
  // the stretch the model gets, and what it accepts: a model with a length setting is told the stretch's length
  const trim = s.trim && s.trim_for === src.id ? s.trim : null, len = trim ? trim[1] - trim[0] : +src.seconds || 0, durs = m.durations || [];
  // what the tool takes of footage (the server checks again, whatever page sends it)
  if (m.source_max && len > m.source_max + 0.05)
    return refused(`${m.label} works on up to ${m.source_max} s of footage, and this stretch is ${len.toFixed(1)} s. Set In and Out ${m.source_max} s or less apart and run it again.`, m.id);
  if (m.source_min && len && len < m.source_min - 0.05)
    return refused(`${m.label} needs at least ${m.source_min} s of footage, and this stretch is ${len.toFixed(1)} s. Widen In and Out.`, m.id);
  let duration = null;
  if (ext) duration = durs.length ? nearest(+s.tooldur || 5, durs) : null;
  else if (durs.length) {
    const max = Math.max(...durs), min = Math.min(...durs);
    if (len > max + 0.05) return refused(`${m.label} makes ${min}–${max} s, and this stretch is ${len.toFixed(1)} s. Set In and Out to fit.`, m.id);
    if (len < min - 0.05) return refused(`${m.label} makes ${min}–${max} s, and this stretch is ${len.toFixed(1)} s. Set In and Out to fit.`, m.id);
    duration = nearest(Math.round(len), durs);
  } else if (!m.source_max && len > 10.05 && !confirm(`This stretch is ${len.toFixed(1)} s, and most change models take up to 10 s. Run it anyway? (Set In and Out to work on a shorter piece.)`)) return;
  const named = els.length ? ` Reference images: ${els.map((e, i) => `image ${i + 1} is ${e.name}`).join(", ")}.` : "";
  const body = { mode: "video", model: m.id, models: [m.id], scene: text || m.label, prompt_override: m.takes_prompt ? text + named : null,
    enhance: false, direction: {}, refs: els.map(e => e.file), ref_names: els.map(e => e.name), source_video: src.file,
    source_trim: trim, aspect: src.aspect || S.aspect, resolution: "", duration, audio: false, takes: 1, seed: "", negative_prompt: "" };
  if (!ext) return runCard(s, Object.assign(body, { role: "clip", action: "change" }));
  const nc = Object.assign(newShot(), { scene: text, start: "continue", duration: body.duration || 5 });
  B.shots.splice(B.shots.indexOf(s) + 1, 0, nc); saveBoard(); renderBoard();
  const r = await runCard(nc, Object.assign(body, { role: "clip", action: "extend" }));
  if (!r) { B.shots = B.shots.filter(x => x !== nc); saveBoard(); renderBoard(); }
  return r;
}
// what a shot lands on when the next card is footage: the first frame of the version that card uses
async function landFrame(next) {
  const c = pickOf(next, "clip");
  if (!c) return null;
  const have = S.takes.find(t => t.board === next.id && t.role === "key" && t.parent === c.id && t.status === "done");
  if (have) return have;
  const r = await api(`/reels/${S.reel.id}/takes/${c.id}/frame`, { method: "POST", json: { at: 0, shot: next.n, board: next.id } });
  if (r.error) { toast(r.error, true); return null; }
  mergeTakes([r.take]);
  return r.take;
}
async function keyAll() {
  const list = B.shots.filter(s => s.type !== "footage" && s.start !== "continue" && s.scene.trim() && !pickOf(s, "key") && !busyOf(s, "key"));
  for (const s of list) if (!await keyframe(s)) break;
}
async function animAll() {
  const list = B.shots.filter(s => s.type !== "footage" && pickOf(s, "key") && !pickOf(s, "clip") && !busyOf(s, "clip")), m = clipModel();
  if (!list.length || !confirm(`Animate ${list.length} shot${list.length > 1 ? "s" : ""} on ${m.label}? ${costOf(list.map(clipCost))}.`)) return;
  for (const s of list) if (!await animate(s)) break;
  const waiting = B.shots.map((s, i) => s.start === "continue" && !pickOf(s, "key") ? i + 1 : 0).filter(Boolean);
  if (waiting.length) toast(`Shot${waiting.length > 1 ? "s" : ""} ${waiting.join(", ")} continue${waiting.length > 1 ? "" : "s"} the shot before: read ${waiting.length > 1 ? "their" : "its"} last frame once that clip lands.`);
}
async function cutSpot(animatic) {   // → the cut take, or nothing
  const parts = [], trims = {}, gaps = [];
  B.shots.forEach((s, i) => {
    const c = (!animatic || s.type === "footage") && pickOf(s, "clip"), k = pickOf(s, "key");
    if (c) { parts.push(c.id); if (s.trim && s.trim_for === c.id) trims[c.id] = s.trim; }
    else if (k) { parts.push(k.id); trims[k.id] = [0, +s.duration || 3]; }
    else gaps.push(i + 1);
  });
  if (parts.length < 2) return toast(animatic ? "Keyframe at least two shots first." : "Make at least two shots first.");
  const b = animatic ? $("#bAnimatic") : $("#bCut"), was = b.textContent;
  b.disabled = true; b.textContent = "Cutting…";
  await flushBoard();
  const r = await api(`/reels/${S.reel.id}/stitch`, { method: "POST", json: { takes: parts, trims, label: animatic ? "Animatic" : "Spot cut" } });
  b.disabled = false; b.textContent = was;
  if (r.error) return toast(r.error, true);
  mergeTakes([r.take]);
  toast(`${r.take.label} · ${fmtSec(r.take.seconds)}${gaps.length ? `. Left out shot${gaps.length > 1 ? "s" : ""} ${gaps.join(", ")}: nothing made yet` : ""}.`);
  return r.take;
}
async function planSpot(auto) {
  if (!B.brief.trim()) { if (!auto) $("#bBrief").focus(); return toast("Write the brief first."); }
  const ai = B.shots.filter(s => s.type !== "footage"), real = B.shots.filter(s => s.type === "footage");
  if (ai.length && !auto && !confirm(`Replace the ${ai.length} AI card${ai.length > 1 ? "s" : ""} on the board with a new shot list?${real.length ? " Your footage stays; Gobo plans around it." : ""} Takes already made stay in the reel.`)) return;
  await ensureReel();
  const b = $("#bPlan"); b.disabled = true; b.textContent = "Writing the shot list…";
  const r = await api(`/reels/${S.reel.id}/board/plan`, { method: "POST", json: { brief: B.brief, target: B.target, model: (clipModel() || {}).id,
    elements: B.elements.map(e => ({ name: e.name, kind: e.kind })), direction: S.direction,
    footage: real.map(s => ({ id: s.id, seconds: lenOf(s), notes: s.scene })) } });
  b.disabled = false;
  if (r.error) { b.textContent = B.shots.length ? "Rewrite the shot list" : "Write the shot list"; toast(r.error, true); return auto ? r : undefined; }
  const byName = new Map(B.elements.map(e => [e.name.toLowerCase(), e.id]));
  let n = nextN();
  B.shots = (r.shots || []).map(x => real.find(f => f.id === x.footage) || Object.assign(newShot(), { n: n++, scene: x.scene || "", shot: x.shot || "auto", move: x.move || "auto",
    duration: +x.duration || 5, elements: (x.elements || []).map(nm => byName.get(String(nm).toLowerCase())).filter(Boolean),
    start: x.start === "continue" ? "continue" : "cut", vo: x.vo || "", super: x.super || "" }));
  B.title = r.title || ""; B.notes = r.notes || "";
  saveBoard(true); renderBoard();
  toast(`${B.shots.length} shots · ${fmtSec(r.total)} of ${fmtSec(r.target)}. Check the cards, then Keyframe.`);
  return r;
}
function setView(v) {
  if (STILLS) return;
  const on = v === "board";
  $("#main").classList.toggle("boardview", on);
  document.body.classList.toggle("boardview", on);
  $$("#viewSeg button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  LS.set("view", v);
  if (on) {
    if (S.mode !== "video") setMode("video", true);
    if (B && B.shots.length) applyLook(B.look);
  }
  $("#viewHint").textContent = on ? "Brief → shot list → keyframes → clips → the cut. The panel sets the look of every shot." : "";
  onShotChange(); renderKit(); renderBoard();
  if (!on) { renderStrip(); renderStage(); }
}

(async function init() {
  loadLocal();
  // Video is the default since VICTOR8. A mode saved before then was the old
  // default, not a choice, so everyone lands on video once; after that the
  // mode they pick is remembered.
  if (!STILLS && LS.get("videoDefault", false) !== true) { S.mode = "video"; LS.set("videoDefault", true); }
  if (STILLS || !byKind("video").length) S.mode = "image";
  const m = S.mode; S.mode = null; setMode(m, true);
  $("#scene").value = S.scene; $("#neg").value = S.negative; $("#seed").value = S.seed;
  if (!byKind("video").length) $("#modeVideo").disabled = true;
  wire(); if (STILLS) wireReference();
  if (!STILLS) { B = newBoard(); setView(LS.get("view", "shot")); }
  onShotChange(); renderStrip(); renderStage();
  const saved = LS.get("reel", null);
  if (saved) await loadReel(saved);
  loadCredits();
  setInterval(() => { if (!document.hidden) loadCredits(); }, 60000);
  window.addEventListener("focus", () => { if (Date.now() - creditsAt > 15000) loadCredits(true); });
  $("#credits").addEventListener("click", e => { if (!e.target.closest("a")) loadCredits(true); });
})();

// ── the Lab's way in (static/gobo_lab.js): read the shot, change it, refresh ──
window.STUDIO = {
  direction: () => S.direction, aspect: () => S.aspect, mode: () => S.mode,
  current: () => cur(), takes: () => S.takes.slice(), toast, api, apiBase: API,
  clipModel: () => (modelById(S.model.video) || {}).label || "",
  reel: () => S.reel && S.reel.id,
  addTakes(ts) { mergeTakes(ts); poll(); },
  show(id) { setView("shot"); select(id); },
  // Draw the move's "Shoot it": the take on screen again with the drawn move
  // and action (a still is animated from), on the clip model picked on the
  // left, and nothing else changed
  async redo(t, move, action) {
    const picked = S.model.video;
    if (t && t.status === "done") { if (t.kind === "image") animateFrom(t, "first"); else reuseSettings(t); }
    if (modelById(picked)) S.model.video = picked;     // not the model the take was made with
    setOverride(null);                                   // a hand-edited prompt would ignore the new move
    if (S.pmode === "director") S.pmode = "cinematic";
    if (move) S.direction.move = move;
    if (action) { S.scene = (S.scene.trim() ? S.scene.trim().replace(/[.!?]?$/, ". ") : "") + action; $("#scene").value = S.scene; }
    onShotChange(); setView("shot");
    return generate(t && t.status === "done" ? { parent: t.id, action: "move" } : undefined);
  },
  setDirection(patch) { Object.assign(S.direction, patch); onShotChange(); },
  setAspect(a) { S.aspect = a; onShotChange(); },
  addToScene(text) {
    S.scene = (S.scene.trim() ? S.scene.trim().replace(/[.!?]?$/, ". ") : "") + text;
    $("#scene").value = S.scene; onShotChange();
  },
};
