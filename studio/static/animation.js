// Gobo — Animation: the studio's mode for animators. Loaded after app.js; it
// wraps the Board's own functions (setView, renderBoard, renderKit, frameHtml,
// keyframe, animate, boardFrom) so a drawn film runs down the same pipeline:
// kit → shot list → keyframes → clips → the cut. It lays the work out as the
// stages an animation studio works in:
//
//   1. Style      a named style (cel, painterly, 3D CG…) or a style picture,
//                 written into every prompt.
//   2. Cast       each character's own drawings (as many as you have) → one
//                 consolidated design → a model sheet (turnaround and
//                 expressions). The sheet is what every shot is drawn from.
//                 The seed can't do this; the sheet does.
//   3. World      each place painted once as background art.
//   4. Storyboard upload the panels; the studio reads them into shot cards
//                 (what the camera sees, framing, camera move, who's in it,
//                 the dialogue written on them). The panel rides on the
//                 keyframe as its composition reference. No panels? Write the
//                 story and let the director draft the shot list.
//   5. Shots      keyframes and clips as on the Board, with the sheets, the
//                 plate, the panel and the style picture handed to the model
//                 in that order of need, within the few pictures it takes;
//                 when a scene has more characters than fit, one labelled
//                 line-up of the whole cast goes instead. Clips from the
//                 keyframe (the Board's way) or straight from the references,
//                 for the models that hold a cast from the sheets alone. Then
//                 the cut, as on the Board.
//
// Every take it makes is tagged `anim` with what it is (design, sheet, plate,
// keyframe, animate), so the Library's Animation blade finds them.
(function () {
  "use strict";
  if (typeof setView !== "function" || typeof renderBoard !== "function") return;
  const AN = { cat: null, building: new Map(), lineupBusy: null };
  const WHO = /gobo/i.test(document.title) ? "Gobo" : "The studio";   // the same file runs in both studios
  const animOn = () => !!(B && B.anim && B.anim.on);
  const defaults = () => ({ on: true, style: "cel", style_notes: "", clips_from: "keyframe", lineup: "", lineup_key: "" });
  const reelId = () => S.reel && S.reel.id;
  const isCast = e => e && (e.kind === "cast" || e.kind === "product");
  const castOf = s => (s.elements || []).map(elById).filter(isCast);
  const thumbOf = f => reelId() && f ? `${API}/reels/${reelId()}/thumb/${encodeURIComponent(f)}?w=160` : "";
  const boardBits = () => ({ anim: B.anim, elements: B.elements });

  async function catalogue() {
    if (!AN.cat) AN.cat = await api("/anim/catalogue");
    return AN.cat;
  }

  // ── the mode: a view of its own, and a chip in the header ──────────────
  const seg = $("#viewSeg");
  if (seg && !$('[data-v="anim"]', seg)) {
    const b = document.createElement("button"); b.dataset.v = "anim"; b.setAttribute("role", "tab"); b.textContent = "Animation";
    b.title = "For animators: style, character sheets, background art, storyboard panels, then keyframes and clips";
    seg.appendChild(b); b.onclick = () => setView("anim");
  }
  const lib = $("#libBtn");
  if (lib && !$("#animBtn")) {
    const c = document.createElement("button"); c.className = "chipbtn"; c.id = "animBtn"; c.textContent = "🎞 Animation";
    c.title = "Animation: a drawn film from your characters, backgrounds and storyboard panels";
    lib.parentNode.insertBefore(c, lib); c.onclick = () => setView("anim");
  }
  const _setView = setView;
  setView = function (v) {
    const on = v === "anim";
    if (B) {
      if (on) B.anim = Object.assign(defaults(), B.anim || {}, { on: true });
      else if (B.anim && B.anim.on) { B.anim.on = false; saveBoard(); }
    }
    _setView(on ? "board" : v);
    document.body.classList.toggle("animview", on);
    $$("#viewSeg button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
    const chip = $("#animBtn"); if (chip) chip.classList.toggle("on", on);
    LS.set("view", v);
    if (on) $("#viewHint").textContent = "Style → cast → world → storyboard → shots → the cut. The sheets hold the characters; the seed can't.";
  };
  // The model sheet, in Animation: the sheets reach a model only through its
  // reference pictures, so every row says how many it takes, the ones that take
  // none are dimmed, and the References filter is on until it's switched off.
  if (typeof MFILTERS === "object" && MFILTERS.video && !MFILTERS.video.some(f => f[0] === "refs")) MFILTERS.video.unshift(["refs", "References"]);
  let refsFilterSet = false;
  const _openModelSheet = openModelSheet;
  openModelSheet = function (multi) {
    if (animOn() && !refsFilterSet) { mFilter.add("refs"); refsFilterSet = true; }
    _openModelSheet(multi);
    if (!animOn()) return;
    const el = $("#modelSheet"); if (!el) return;
    const h3 = $("h3", el);
    if (h3 && !$(".anote", el)) h3.insertAdjacentHTML("afterend", `<p class="hint anote">Animation draws every shot from the model sheets, and a sheet reaches a model only as a <b>reference picture</b>. A model that takes none can't hold your characters: it's dimmed here. The number is how many pictures it takes at once (sheets, the panel, the background, the style picture).</p>`);
    $$(".mitem[data-m]", el).forEach(b => {
      const m = modelById(b.dataset.m); if (!m || $(".aref", b)) return;
      const n = m.refs || 0;
      b.classList.toggle("norefs", !n);
      $(".caps", b).insertAdjacentHTML("afterbegin", n ? `<span class="aref ok" title="Takes ${n} reference picture${n > 1 ? "s" : ""} at once">${n} picture${n > 1 ? "s" : ""}</span>` : `<span class="aref bad" title="Takes no reference pictures: the sheets can't reach it">no references</span>`);
    });
  };
  const _boardFrom = boardFrom;
  boardFrom = function (board, reelId) {
    _boardFrom(board, reelId);
    const on = animOn(), here = document.body.classList.contains("animview");
    if (on && !here) setView("anim");
    else if (!on && here && B) B.anim = Object.assign(defaults(), B.anim || {});   // a plain board opened in Animation joins it
  };
  if (new URLSearchParams(location.search).get("open") === "animation") setView("anim");

  // ── the layout ─────────────────────────────────────────────────────────
  const _renderBoard = renderBoard;
  renderBoard = function () {
    _renderBoard();
    if (!animOn()) return;
    const el = $("#board"), bhead = $(".bhead", el), bgrid = $(".bgrid", el);
    if (!el || !bhead || !bgrid) return;
    const mode = document.createElement("div"); mode.className = "amode"; mode.innerHTML = modeHtml();
    el.insertBefore(mode, el.firstChild);
    // the Board's own head (brief, shot list, keyframe/animate/cut buttons, timeline)
    // and its cards become stage 5; the brief waits behind "Write it instead"
    const shots = $("#aShots", mode);
    bhead.classList.toggle("showbrief", !!B.shots.length === false && !!B.brief);
    shots.appendChild(bhead); shots.appendChild(bgrid);
    wireMode(mode);
    catalogue().then(() => { if ($(".amode", el) === mode) { $("#aStyle", mode).innerHTML = styleOptions(); $("#aModels", mode).innerHTML = modelsHtml(); wireModels(mode); } });
  };
  function stages() {
    const cast = B.elements.filter(isCast), places = B.elements.filter(e => e.kind === "place"), style = B.elements.find(e => e.kind === "style");
    const cards = B.shots.filter(s => s.type !== "footage"), keyed = cards.filter(s => pickOf(s, "key")), clips = cards.filter(s => pickOf(s, "clip"));
    const panels = B.shots.filter(s => s.panel).length;
    const cut = S.takes.filter(t => t.action === "stitch" && t.status === "done" && /^(Spot cut|Animatic)/.test(t.label || "")).pop();
    return [
      { id: "aStyleSec", n: 1, name: "Style", state: !(keyModel() || {}).refs ? "Keyframe model takes no pictures" : (B.anim.style && B.anim.style !== "custom") || style ? "The look is set" : "Pick a style", done: !!(keyModel() || {}).refs && !!((B.anim.style && B.anim.style !== "custom") || style) },
      { id: "aCastSec", n: 2, name: "Cast", state: cast.length ? `${cast.filter(e => e.sheet).length} of ${cast.length} on model sheets` : "Add the characters", done: cast.length > 0 && cast.every(e => e.sheet) },
      { id: "aWorldSec", n: 3, name: "World", state: places.length ? `${places.filter(e => e.file).length} of ${places.length} painted` : "Add the places", done: places.length > 0 && places.every(e => e.file) },
      { id: "aBoardSec", n: 4, name: "Storyboard", state: cards.length ? `${cards.length} shot${cards.length > 1 ? "s" : ""}${panels ? ` · ${panels} from panels` : ""}` : "Upload the panels", done: cards.length > 0 },
      { id: "aShots", n: 5, name: "Shots", state: cards.length ? `${keyed.length} keyframed · ${clips.length} animated${cut ? " · cut" : ""}` : "Nothing to draw yet", done: cards.length > 0 && clips.length === cards.length },
    ];
  }
  function stripHtml() {
    return stages().map(s => `<button class="astage ${s.done ? "done" : ""}" data-go="${s.id}"><span class="n">${s.n}</span><b>${esc(s.name)}</b><small>${esc(s.state)}</small></button>`).join("");
  }
  function styleOptions() {
    const list = (AN.cat && AN.cat.styles) || [{ id: "cel", label: "2D cel anime" }];
    return list.map(s => `<option value="${s.id}" ${B.anim.style === s.id ? "selected" : ""}>${esc(s.label)}</option>`).join("");
  }
  function modelsHtml() {
    if (!AN.cat) return "";
    const km = keyModel() || {}, cm = clipModel() || {};
    const rk = AN.cat.keyframe_models || [], rc = AN.cat.clip_models || [];
    const kOk = rk.some(m => m.id === km.id), cOk = rc.some(m => m.id === cm.id);
    const cur = rc.find(m => m.id === cm.id);
    const note = cur ? (cur.refs_with_frame ? `${esc(cur.label)} takes the sheets and the keyframe together.` : B.anim.clips_from === "refs" ? `${esc(cur.label)} draws each clip from the sheets, the panel and the plate.` : `${esc(cur.label)} takes the keyframe (it carries the cast) and no other picture.`) : "";
    const bad = [];
    if (!km.refs) bad.push(`<b>${esc(km.label || "the keyframe model")}</b> takes no reference pictures, so the sheets can't reach the keyframes`);
    if (!cm.refs && (B.anim.clips_from === "refs" || !(cm.i2v || cm.first_frame))) bad.push(`<b>${esc(cm.label || "the clip model")}</b> takes no reference pictures${(cm.i2v || cm.first_frame) ? "" : " and can't start from a keyframe"}, so it can't draw the cast`);
    return `${bad.length ? `<p class="awarn">${bad.join("; ")}. <button class="btn small" data-pick="${!km.refs ? "image" : "video"}">Pick one that does</button></p>` : ""}
      <span class="hint">Keyframes on <b>${esc(km.label || "—")}</b>${km.refs ? ` (${km.refs} pictures)` : " — takes no reference pictures"}${kOk ? "" : " · not a model this mode knows"}.
      Clips on <b>${esc(cm.label || "—")}</b>${cm.refs ? ` (${cm.refs} pictures)` : " — takes no reference pictures"}${cOk ? "" : " · not a model this mode knows"}. ${note}</span>
      ${kOk && cOk ? "" : `<button class="btn small" id="aRec" title="${esc((rk[0] || {}).label || "")} for keyframes, ${esc((rc[0] || {}).label || "")} for clips">Use the recommended models</button>`}
      <button class="btn small ghost" id="aWhich" title="Every model that takes reference pictures, and how many">Which models take references?</button>`;
  }
  function elRow(e) {
    const files = (e.files || []).map(f => `<span class="ath" data-file="${esc(f)}" data-what="${esc(e.name)} — your drawing" style="background-image:url('${esc(thumbOf(f))}')" title="${esc(e.name)} — your drawing (click to inspect)"><button data-rmf="${esc(f)}" title="Take this drawing out">✕</button></span>`).join("");
    const busy = AN.building.get(e.id);
    const made = e.kind === "place"
      ? (e.file ? `<span class="ath made" data-file="${esc(e.file)}" data-what="${esc(e.name)} — background art" style="background-image:url('${esc(thumbOf(e.file))}')" title="Background art (click to inspect)"></span>` : "")
      : (e.hero ? `<span class="ath made" data-file="${esc(e.hero)}" data-what="${esc(e.name)} — the design" style="background-image:url('${esc(thumbOf(e.hero))}')" title="The design (click to inspect)"></span>` : "")
        + (e.sheet ? `<span class="ath made sheet" data-file="${esc(e.sheet)}" data-what="${esc(e.name)} — the model sheet" style="background-image:url('${esc(thumbOf(e.sheet))}')" title="The model sheet (click to inspect)"></span>` : "");
    const act = e.kind === "place"
      ? `<button class="btn small ${e.file ? "" : "primary"}" data-plate="${e.id}" ${busy ? "disabled" : ""}>${busy ? busy : e.file ? "Paint again" : "Paint the plate"}</button>`
      : `<button class="btn small ${e.sheet ? "" : "primary"}" data-sheet="${e.id}" ${busy ? "disabled" : ""}>${busy ? busy : e.sheet ? "Build the sheet again" : "Build the sheet"}</button>`;
    return `<div class="arow" data-e="${e.id}">
      <div class="aname"><input class="field" value="${esc(e.name)}" placeholder="Name" aria-label="Name"><small class="hint">${e.kind === "place" ? "place" : e.kind === "product" ? "prop" : "character"}</small></div>
      <textarea class="field alook" placeholder="${e.kind === "place" ? "The place: layout, materials, time of day, light." : "Who they are: age, build, face, hair, exactly what they wear."}">${esc(e.look || "")}</textarea>
      <div class="athumbs">${files}<button class="ath add" data-addf="${e.id}" title="Add your drawings of ${esc(e.name)}: sketches, turnarounds, colour keys — as many as you have">＋</button>${made}</div>
      <div class="aacts">${act}<button class="btn small ghost" data-rme="${e.id}" title="Take out of the kit">✕</button></div></div>`;
  }
  function modeHtml() {
    const cast = B.elements.filter(isCast), places = B.elements.filter(e => e.kind === "place");
    const style = B.elements.find(e => e.kind === "style");
    const panels = B.shots.filter(s => s.panel).length;
    return `<div class="atitle"><div><h2>Animation</h2><p class="hint">A drawn film from your characters, backgrounds and storyboard panels. Nothing here is photographed, so nothing is left to a seed: every shot is drawn from the model sheets.</p></div>
      <div class="astrip">${stripHtml()}</div></div>
    <section class="asec" id="aStyleSec">
      <p class="sectitle"><span class="num">1</span>Style <span class="r hint">written into every prompt</span></p>
      <div class="aline"><select class="field" id="aStyle">${styleOptions()}</select>
        <input class="field grow" id="aNotes" placeholder="And in a few words: thick lines, pastel palette, grainy…" value="${esc(B.anim.style_notes || "")}">
        ${style && style.file ? `<span class="ath made" data-file="${esc(style.file)}" data-what="The style picture" style="background-image:url('${esc(thumbOf(style.file))}')" title="The style picture (click to inspect)"></span><button class="btn small ghost" data-rme="${style.id}" title="Take the style picture out">✕</button>` : `<button class="btn small" id="aStyleUp" title="A frame in the look you want: ${WHO} matches its linework, shading and colour">Style picture</button>`}
      </div>
      <div id="aModels" class="aline">${modelsHtml()}</div>
    </section>
    <section class="asec" id="aCastSec">
      <p class="sectitle"><span class="num">2</span>Cast <span class="r hint">${cast.length ? `${cast.filter(e => e.sheet).length} of ${cast.length} on model sheets` : "the characters and props that recur"}</span></p>
      <div class="arows">${cast.map(elRow).join("") || `<p class="hint">Add each character, say who they are, and add your own drawings of them: sketches, turnarounds, colour keys, as many as you have.</p>`}</div>
      <div class="aline"><button class="btn small" id="aAddCast">＋ Character</button><button class="btn small" id="aAddProp">＋ Prop</button>
        ${cast.some(e => !e.sheet) ? `<button class="btn small primary" id="aSheets" title="Each character: one design from their drawings, then a turnaround sheet with expressions — two pictures each">Build all sheets · ${costOf(cast.filter(e => !e.sheet).map(() => keyCost() == null ? null : 2 * keyCost() / Math.max(1, B.variants)))}</button>` : ""}
        <span class="hint">${WHO} draws one design from a character's drawings in the film's style, then the model sheet every shot is drawn from: a turnaround, four expressions and the colour swatches.</span></div>
    </section>
    <section class="asec" id="aWorldSec">
      <p class="sectitle"><span class="num">3</span>World <span class="r hint">each place painted once as background art</span></p>
      <div class="arows">${places.map(elRow).join("") || `<p class="hint">Add the places the story happens in. Each is painted once; every shot set there is drawn over it.</p>`}</div>
      <div class="aline"><button class="btn small" id="aAddPlace">＋ Place</button></div>
    </section>
    <section class="asec" id="aBoardSec">
      <p class="sectitle"><span class="num">4</span>Storyboard <span class="r hint">${panels ? `${panels} panel${panels > 1 ? "s" : ""} on cards` : "panels become shot cards"}</span></p>
      <div class="aline"><button class="btn small primary" id="aPanels">Upload panels</button>
        <span class="hint">PNG or JPEG, in story order (by file name). ${WHO} reads each panel into a card: what the camera sees, the framing, the move, who's in it and the dialogue written on it. The panel itself guides the keyframe's composition.</span></div>
      <div class="aline"><button class="btn small ghost" id="aWrite">No panels? Write the story instead</button><span class="hint">The director drafts the shot list from a brief, as on the Board.</span></div>
    </section>
    <section class="asec" id="aShots">
      <p class="sectitle"><span class="num">5</span>Shots and the cut <span class="r hint">keyframe each card, animate it, cut the film</span></p>
      <div class="aline"><span class="hint">Clips from</span><span class="seg" id="aFrom"><button data-f="keyframe" class="${B.anim.clips_from !== "refs" ? "on" : ""}" title="Each clip starts on its keyframe (the Board's way)">the keyframe</button><button data-f="refs" class="${B.anim.clips_from === "refs" ? "on" : ""}" title="Each clip is drawn straight from the sheets, the panel and the plate, no keyframe needed — for models that hold a cast from references (Seedance, Kling Omni, Grok)">the references</button></span>
        <span class="hint" id="aLineup">${B.anim.lineup ? "Cast line-up ready" : cast.filter(e => e.sheet).length > 1 ? "A cast line-up is drawn when a shot needs it" : ""}</span></div>
    </section>`;
  }
  function wireModels(mode) {
    const rec = $("#aRec", mode); if (rec) rec.onclick = () => {
      const rk = AN.cat.keyframe_models[0], rc = AN.cat.clip_models[0];
      if (rk) S.model.image = rk.id;
      if (rc) S.model.video = rc.id;
      renderBoardModels(); renderBoard(); toast(`Keyframes on ${rk ? rk.label : "—"}, clips on ${rc ? rc.label : "—"}.`);
    };
    const w = $("#aWhich", mode); if (w) w.onclick = whichModels;
    $$("[data-pick]", mode).forEach(b => b.onclick = () => { S.mode = b.dataset.pick; mFilter.clear(); mFilter.add("refs"); refsFilterSet = true; openModelSheet(false); });
  }
  function whichModels() {
    const c = AN.cat || {};
    const why = Object.fromEntries([...(c.keyframe_models || []), ...(c.clip_models || [])].map(m => [m.id, m]));
    const withFrame = new Set(c.refs_with_frame || []);
    const km = keyModel() || {}, cm = clipModel() || {};
    const row = (m, kind) => {
      const w = why[m.id] || {};
      const on = (kind === "image" ? km.id : cm.id) === m.id;
      return `<li class="${on ? "on" : ""}"><button class="btn small ${on ? "primary" : ""}" data-use="${esc(m.id)}" data-kind="${kind}">${on ? "In use" : "Use"}</button> <b>${esc(m.label)}</b> · ${m.refs} picture${m.refs === 1 ? "" : "s"}${withFrame.has(m.id) ? " · with the keyframe too" : kind === "video" && (m.i2v || m.first_frame) ? " · or a keyframe (not both)" : ""}${w.why ? ` — ${esc(w.why)}` : ""}</li>`;
    };
    const stills = byKind("image").filter(m => m.refs).sort((a, b) => (why[b.id] ? 1 : 0) - (why[a.id] ? 1 : 0) || b.refs - a.refs);
    const clips = byKind("video").filter(m => m.refs).sort((a, b) => (why[b.id] ? 1 : 0) - (why[a.id] ? 1 : 0) || b.refs - a.refs);
    const pop = document.createElement("div"); pop.className = "apop"; pop.innerHTML = `<div class="apopin">
      <p class="sectitle">Which models take reference pictures</p>
      <p class="hint">Every model here takes pictures with the prompt, so the sheets can reach it. The number is how many at once: the sheets go in first, then the storyboard panel, the background art and the style picture, and when a scene has more characters than fit, one labelled line-up of the whole cast goes instead. A model not listed takes none and can't hold your characters. The recommended ones come first.</p>
      <p class="hint"><b>Keyframes</b> — ${stills.length} still models:</p><ul class="amodels">${stills.map(m => row(m, "image")).join("")}</ul>
      <p class="hint"><b>Clips</b> — ${clips.length} video models. "With the keyframe too" takes the sheets and a starting frame together; "or a keyframe (not both)" means clips from the keyframe carry the cast in the frame alone, and clips from the references get the sheets:</p><ul class="amodels">${clips.map(m => row(m, "video")).join("")}</ul>
      <p class="hint">Why not the seed: it fixes the noise a model starts from, and a different prompt gives a different face. The sheet is what keeps a character the same.</p>
      <div class="aline"><span class="grow"></span><button class="btn small" id="apopClose">Close</button></div></div>`;
    document.body.appendChild(pop);
    $("#apopClose", pop).onclick = () => pop.remove(); pop.onclick = e => { if (e.target === pop) pop.remove(); };
    $$("[data-use]", pop).forEach(b => b.onclick = () => {
      const m = modelById(b.dataset.use); if (!m) return;
      S.model[b.dataset.kind] = m.id; renderBoardModels(); renderBoard(); pop.remove();
      toast(`${b.dataset.kind === "image" ? "Keyframes" : "Clips"} on ${m.label} · ${m.refs} picture${m.refs > 1 ? "s" : ""}.`);
    });
  }
  // any picture in the rows, full size: a sheet has to be read before it's trusted
  function inspect(file, what) {
    const url = `${API}/reels/${reelId()}/file/${encodeURIComponent(file)}`;
    const pop = document.createElement("div"); pop.className = "apop aview"; pop.innerHTML = `<div class="aviewin">
      <img src="${esc(url)}" alt="${esc(what)}">
      <div class="aline"><b>${esc(what)}</b><span class="grow"></span><a class="btn small" href="${esc(url)}" download="${esc(file)}">Download</a><button class="btn small" data-close>Close</button></div></div>`;
    document.body.appendChild(pop);
    const close = () => { pop.remove(); document.removeEventListener("keydown", esc_); };
    const esc_ = e => { if (e.key === "Escape") close(); };
    $("[data-close]", pop).onclick = close; pop.onclick = e => { if (e.target === pop || e.target.tagName === "IMG") close(); };
    document.addEventListener("keydown", esc_);
  }
  // Every text box in the mode opens as a pop-up: a card's box is a few lines
  // wide, and a scene, a look or a line of dialogue wants room. What's typed
  // in the pop-up lands in the box as it's typed, so the usual wiring saves it.
  let editing = null;
  function editText(field, title, hint, find) {
    if (editing === field) return;
    editing = field;
    const target = () => (find && find()) || field;
    const long = field.tagName === "TEXTAREA";
    const pop = document.createElement("div"); pop.className = "apop aedit"; pop.innerHTML = `<div class="apopin aeditin">
      <p class="sectitle">${esc(title)}</p>${hint ? `<p class="hint">${esc(hint)}</p>` : ""}
      <textarea class="field abig ${long ? "" : "short"}" placeholder="${esc(field.placeholder || "")}" ${field.maxLength > 0 ? `maxlength="${field.maxLength}"` : ""}></textarea>
      <div class="aline"><span class="hint acount"></span><span class="grow"></span><span class="hint">Esc or ⌘↩ to finish</span><button class="btn small primary" data-done>Done</button></div></div>`;
    const ta = $("textarea", pop), count = $(".acount", pop);
    ta.value = field.value;
    const tally = () => { count.textContent = ta.value.length ? `${ta.value.length} characters${field.maxLength > 0 ? ` of ${field.maxLength}` : ""}` : ""; };
    tally();
    ta.oninput = () => { const f = target(); f.value = ta.value; f.dispatchEvent(new Event("input", { bubbles: true })); tally(); };
    const close = () => { pop.remove(); document.removeEventListener("keydown", keys); editing = null; if (document.activeElement === field) field.blur(); };
    const keys = e => { if (e.key === "Escape" || (e.key === "Enter" && (e.metaKey || e.ctrlKey))) { e.preventDefault(); close(); } };
    $("[data-done]", pop).onclick = close; pop.onclick = e => { if (e.target === pop) close(); };
    document.addEventListener("keydown", keys);
    document.body.appendChild(pop);
    ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  }
  // a box opens its pop-up when it's clicked or tabbed into
  function popOn(field, title, hint, find) {
    if (!field || field.dataset.pops) return;
    field.dataset.pops = "1";
    const open = () => editText(field, typeof title === "function" ? title() : title, hint, find);
    field.addEventListener("focus", () => { if (animOn() && !editing) open(); });
    field.addEventListener("mousedown", e => { if (animOn() && !editing) { e.preventDefault(); open(); } });
  }
  const CARD_FIELDS = {
    scene: ["What the camera sees", "Who's in the shot, where, what happens, and how the shot ends. The storyboard panel, if there is one, sets the composition."],
    change: ["The change", ""],
    vo: ["Voice-over", "What's said over the shot."],
    super: ["On-screen text", "Titles or captions drawn over the shot."],
  };
  function wireCardPops() {
    $$("#board .bcard").forEach(card => {
      const no = () => { const i = B.shots.findIndex(x => x.id === card.dataset.s); return i < 0 ? "" : `Shot ${i + 1} · `; };
      const id = card.dataset.s;
      Object.entries(CARD_FIELDS).forEach(([k, [t, h]]) => popOn($(`[data-f="${k}"]`, card), () => no() + t, h, () => $(`#board .bcard[data-s="${id}"] [data-f="${k}"]`)));
    });
  }
  function wireMode(mode) {
    popOn($("#aNotes", mode), "The style, in a few words", "Line weight, palette, grain, the era it recalls: anything a picture alone doesn't say.", () => $("#aNotes"));
    wireCardPops();
    $$(".ath[data-file]", mode).forEach(a => a.onclick = e => { if (e.target.closest("button")) return; inspect(a.dataset.file, a.dataset.what || ""); });
    $$(".astage", mode).forEach(b => b.onclick = () => { const t = $("#" + b.dataset.go); if (t) t.scrollIntoView({ behavior: "smooth", block: "start" }); });
    $("#aStyle", mode).onchange = e => { B.anim.style = e.target.value; saveBoard(); refreshStrip(); };
    $("#aNotes", mode).oninput = e => { B.anim.style_notes = e.target.value.slice(0, 300); saveBoard(); };
    const up = $("#aStyleUp", mode); if (up) up.onclick = () => pickFile("image/png,image/jpeg,image/webp", async f => {
      const r = await uploadRef(f); if (!r) return;
      B.elements.push({ id: rid(), name: "Style", kind: "style", file: r.name }); saveBoard(); renderKit(); renderBoard();
    });
    $("#aAddCast", mode).onclick = () => addEl("cast", "Character");
    $("#aAddProp", mode).onclick = () => addEl("product", "Prop");
    $("#aAddPlace", mode).onclick = () => addEl("place", "Place");
    const all = $("#aSheets", mode); if (all) all.onclick = buildAllSheets;
    $("#aPanels", mode).onclick = pickPanels;
    $("#aWrite", mode).onclick = () => { const h = $("#board .bhead"); if (h) { h.classList.add("showbrief"); h.scrollIntoView({ behavior: "smooth", block: "start" }); const b = $("#bBrief"); if (b) b.focus(); } };
    $$("#aFrom button", mode).forEach(b => b.onclick = () => { B.anim.clips_from = b.dataset.f; saveBoard(); renderBoard(); });
    $$(".arow", mode).forEach(row => {
      const e = elById(row.dataset.e); if (!e) return;
      $("input", row).oninput = ev => { e.name = ev.target.value; saveBoard(); $$(`[data-el="${e.id}"]`).forEach(b => b.textContent = e.name || "Untitled"); };
      $(".alook", row).oninput = ev => { e.look = ev.target.value.slice(0, 600); saveBoard(); };
      popOn($(".alook", row), () => `${e.name || "Untitled"} · ${e.kind === "place" ? "the place" : "who they are"}`, e.kind === "place" ? "Layout, materials, time of day, light. The drawings carry the rest." : "Age, build, face, hair, exactly what they wear. The drawings carry the rest.", () => $(`.arow[data-e="${e.id}"] .alook`));
      $("[data-addf]", row).onclick = () => pickFiles("image/png,image/jpeg,image/webp", async files => {
        for (const f of files.slice(0, 8)) { const r = await uploadRef(f); if (r) (e.files = e.files || []).push(r.name); }
        saveBoard(); renderBoard();
      });
      $$("[data-rmf]", row).forEach(b => b.onclick = () => { e.files = (e.files || []).filter(f => f !== b.dataset.rmf); saveBoard(); renderBoard(); });
      const sh = $("[data-sheet]", row); if (sh) sh.onclick = () => buildSheet(e, true);
      const pl = $("[data-plate]", row); if (pl) pl.onclick = () => paintPlate(e);
    });
    $$("[data-rme]", mode).forEach(b => b.onclick = () => {
      const e = elById(b.dataset.rme); if (!e) return;
      B.elements = B.elements.filter(x => x !== e);
      B.shots.forEach(s => s.elements = s.elements.filter(id => id !== e.id));
      saveBoard(); renderKit(); renderBoard();
    });
  }
  function refreshStrip() { const s = $("#board .astrip"); if (s && animOn()) s.innerHTML = stripHtml(); $$("#board .astage").forEach(b => b.onclick = () => { const t = $("#" + b.dataset.go); if (t) t.scrollIntoView({ behavior: "smooth", block: "start" }); }); }
  const _refreshBoard = refreshBoard;
  refreshBoard = function () { _refreshBoard(); if (animOn()) refreshStrip(); };
  function addEl(kind, label) {
    B.elements.push({ id: rid(), name: `${label} ${B.elements.filter(e => e.kind === kind).length + 1}`, kind, file: "", look: "", files: [] });
    saveBoard(); renderKit(); renderBoard();
    const rows = $$(".amode .arow"), last = rows.filter(r => elById(r.dataset.e) && elById(r.dataset.e).kind === kind).pop();
    if (last) { const i = $("input", last); i.focus(); i.select(); }
  }
  // the kit list on the left shows the style picture too
  const _renderKit = renderKit;
  renderKit = function () {
    _renderKit();
    if (!animOn()) return;
    $$("#kitList .kit").forEach(row => {
      const e = elById(row.dataset.e), sel = $("select", row);
      if (!e || !sel) return;
      if (!$('option[value="style"]', sel)) sel.insertAdjacentHTML("beforeend", `<option value="style" ${e.kind === "style" ? "selected" : ""}>Style</option>`);
      else if (e.kind === "style") sel.value = "style";
    });
  };

  // ── files ──────────────────────────────────────────────────────────────
  function pickFiles(accept, cb) {
    const i = document.createElement("input"); i.type = "file"; i.accept = accept; i.multiple = true; i.style.display = "none";
    document.body.appendChild(i);
    i.onchange = () => { const fs = Array.from(i.files || []); i.remove(); if (fs.length) cb(fs); };
    i.click();
  }
  async function uploadRef(file) {
    await ensureReel();
    const fd = new FormData(); fd.append("file", file);
    const r = await upload(`/reels/${reelId()}/refs`, fd, p => { if (p < 1) toast(`Uploading ${file.name} · ${Math.round(p * 100)}%`); });
    if (r.error) { toast(r.error, true); return null; }
    if (r.kind !== "image") { toast("Drawings and panels are stills.", true); return null; }
    return r;
  }
  async function copyTake(t) {
    const c = await api(`/reels/${reelId()}/refs/copy`, { method: "POST", json: { item: `${reelId()}:${t.id}` } });
    if (c.error) { toast(c.error, true); return null; }
    return c.name;
  }

  // ── one run, with the prompt and the pictures the server picks ─────────
  async function planned(role, model, extra) {
    const p = await api(`/reels/${reelId()}/anim/prompt`, { method: "POST", json: Object.assign({ role, model: model.id, board: boardBits() }, extra || {}) });
    if (p.error) { toast(p.error, true); return null; }
    if (p.note) toast(p.note);
    return p;
  }
  const ACTION = { hero: "design", sheet: "sheet", plate: "plate" };
  async function stillRun(role, e, label, aspect) {
    const m = keyModel(); if (!m) { toast("No still model is available.", true); return null; }
    if (!(m.refs || 0) && ((e.files || []).length || e.hero)) toast(`${m.label} takes no reference pictures, so ${e.name}'s drawings can't reach it. Pick a keyframe model that does — Nano Banana Pro, Seedream.`);
    const p = await planned(role, m, { element: e }); if (!p) return null;
    const aspects = m.aspects && m.aspects.length ? m.aspects : CAT.aspects.map(a => a.id);
    await ensureReel();
    const r = await api(`/reels/${reelId()}/run`, { method: "POST", json: { mode: "image", model: m.id, models: [m.id], scene: p.prompt, direction: {}, enhance: false,
      prompt_override: p.prompt, refs: p.refs, ref_names: p.ref_names, ref_kinds: p.ref_kinds, negative_prompt: m.negative ? p.negative : "",
      aspect: snapAspect(aspect, aspects), resolution: m.resolutions && m.resolutions.includes("2K") ? "2K" : "", takes: 1, seed: m.seed && B.seed ? String(B.seed) : "",
      action: ACTION[role] || "cast", anim: 1 } });
    if (r.error) { refused(r.error, m.id); return null; }
    mergeTakes(r.takes); poll();
    const t = await waitTake(r.takes[0].id, 20);
    if (!t) { toast(`${label} for ${e.name} didn't come back. Try again.`, true); return null; }
    return copyTake(t);
  }
  // a character: the design from their drawings, then the sheet from the design
  function buildSheet(e, again) {
    if (e.sheet && !again) return Promise.resolve(true);
    if (!AN.building.has(e.id)) AN.building.set(e.id, (async () => {
      const set = w => { AN.building.set(e.id, w); renderBoard(); };
      if (!e.look && !(e.files || []).length) { toast(`Say who ${e.name} is, or add a drawing of them, first.`); return false; }
      set("Drawing the design…");
      const hero = await stillRun("hero", e, "The design", "3:4");
      if (!hero) return false;
      e.hero = hero; saveBoard();
      set("Drawing the sheet…");
      const sheet = await stillRun("sheet", e, "The sheet", "16:9");
      if (!sheet) return false;
      e.sheet = sheet; e.file = sheet; B.anim.lineup = ""; B.anim.lineup_key = "";
      saveBoard(); renderKit();
      return true;
    })().finally(() => { AN.building.delete(e.id); renderBoard(); }));
    return AN.building.get(e.id);
  }
  function paintPlate(e) {
    if (!AN.building.has(e.id)) AN.building.set(e.id, (async () => {
      if (!e.look && !(e.files || []).length) { toast(`Describe ${e.name}, or add a drawing of it, first.`); return false; }
      AN.building.set(e.id, "Painting…"); renderBoard();
      const f = await stillRun("plate", e, "The plate", S.aspect);
      if (!f) return false;
      e.file = f; saveBoard(); renderKit();
      return true;
    })().finally(() => { AN.building.delete(e.id); renderBoard(); }));
    return AN.building.get(e.id);
  }
  async function buildAllSheets() {
    const todo = B.elements.filter(e => isCast(e) && !e.sheet);
    if (!todo.length) return;
    toast(`Building ${todo.length} sheet${todo.length > 1 ? "s" : ""}: a design and a turnaround each.`);
    await Promise.all(todo.map(e => buildSheet(e)));
  }
  // one labelled picture of the whole cast, drawn again whenever a sheet changes
  async function ensureLineup() {
    const cast = B.elements.filter(e => isCast(e) && e.sheet);
    if (cast.length < 2) return;
    const key = cast.map(e => `${e.name}:${e.sheet}`).join("|");
    if (B.anim.lineup && B.anim.lineup_key === key) return;
    if (!AN.lineupBusy) AN.lineupBusy = (async () => {
      const r = await api(`/reels/${reelId()}/anim/lineup`, { method: "POST", json: { elements: cast.map(e => ({ name: e.name, sheet: e.sheet })) } });
      if (r.error) { toast(r.error, true); return; }
      B.anim.lineup = r.name; B.anim.lineup_key = key; saveBoard();
      const l = $("#aLineup"); if (l) l.textContent = `Cast line-up ready · ${r.count}`;
    })().finally(() => { AN.lineupBusy = null; });
    await AN.lineupBusy;
  }
  function cardJson(s) {
    return { id: s.id, scene: s.scene, shot: s.shot, move: s.move, duration: s.duration, elements: s.elements, panel: s.panel || "", start: s.start };
  }

  // ── keyframes and clips, the Animation way ─────────────────────────────
  const _keyframe = keyframe, _animate = animate;
  keyframe = async function (s) {
    if (!animOn()) return _keyframe(s);
    if (s.start === "continue") return continueFrom(s);
    if (!s.scene.trim()) return toast(`Write what the camera sees first (or let ${WHO} read the panel).`);
    const m = keyModel(); if (!m) return toast("No still model is available.", true);
    const cast = castOf(s);
    if (cast.length && !(m.refs || 0)) return toast(`${m.label} takes no reference pictures, so the sheets can't reach this keyframe. Pick a keyframe model that does — Nano Banana Pro, Seedream.`, true);
    const unmade = cast.filter(e => !e.sheet && !e.file);
    if (unmade.length) {
      s._casting = true; refreshBoard();
      const ok = (await Promise.all(unmade.map(e => buildSheet(e)))).every(Boolean);
      s._casting = false; refreshBoard();
      if (!ok) return null;
    }
    await ensureLineup();
    const p = await planned("key", m, { card: cardJson(s) }); if (!p) return null;
    const aspects = m.ref_aspects && p.refs.length ? m.ref_aspects : (m.aspects && m.aspects.length ? m.aspects : CAT.aspects.map(a => a.id));
    const res = (m.resolutions || []).length ? (m.default_resolution && m.resolutions.includes(m.default_resolution) ? m.default_resolution : m.resolutions[m.resolutions.length > 1 ? 1 : 0]) : "";
    return runCard(s, { mode: "image", model: m.id, models: [m.id], scene: s.scene, direction: cardDirection(s), enhance: false, prompt_override: p.prompt,
      refs: p.refs, ref_names: p.ref_names, ref_kinds: p.ref_kinds, aspect: snapAspect(S.aspect, aspects), resolution: res, takes: B.variants,
      seed: m.seed ? shotSeed(s) : "", negative_prompt: m.negative ? p.negative : "", action: "keyframe", role: "key", anim: 1 });
  };
  animate = async function (s) {
    if (!animOn()) return _animate(s);
    const m = clipModel(); if (!m) return toast("No video model is available.", true);
    if (!s.scene.trim()) return toast("Write what the camera sees first.");
    if (s.start === "continue" && !pickOf(s, "key") && !await continueFrom(s)) return null;
    const fromRefs = B.anim.clips_from === "refs" && s.start !== "continue";
    const key = fromRefs ? null : pickOf(s, "key");
    if (!fromRefs && !key && m.requires_frame) return toast(`${m.label} starts from a keyframe. Make one first, or draw clips from the references.`, true);
    if (key && !(m.i2v || m.first_frame)) return toast(`${m.label} can't start from a keyframe. Pick a clip model that can — Seedance, Kling, Grok — or draw clips from the references.`, true);
    if ((fromRefs || !key) && !(m.refs || 0)) return toast(`${m.label} takes no reference pictures, so it can't draw this clip from the sheets. Make a keyframe, or pick Seedance, Kling Omni or Grok.`, true);
    const unmade = castOf(s).filter(e => !e.sheet && !e.file);
    if ((fromRefs || !key) && unmade.length) {
      s._casting = true; refreshBoard();
      const ok = (await Promise.all(unmade.map(e => buildSheet(e)))).every(Boolean);
      s._casting = false; refreshBoard();
      if (!ok) return null;
    }
    await ensureLineup();
    const role = key ? "clip_from_key" : "clip_from_refs";
    const p = await planned(role, m, { card: cardJson(s) }); if (!p) return null;
    const v = key ? framed(m) : m, i = B.shots.indexOf(s), next = B.shots[i + 1];
    const nextKey = next && s.land && key ? pickOf(next, "key") : null;
    const durs = v.durations || [];
    return runCard(s, { mode: "video", model: m.id, models: [m.id], scene: s.scene, direction: cardDirection(s), enhance: false, prompt_override: p.prompt,
      refs: p.refs, ref_names: p.ref_names, ref_kinds: p.ref_kinds, first_frame: key ? key.file : null,
      last_frame: nextKey && v.last_frame ? nextKey.file : null, aspect: S.aspect, resolution: S.resolution,
      duration: durs.length ? nearest(+s.duration || 5, durs) : null, audio: S.audio, seed: m.seed ? shotSeed(s) : "",
      negative_prompt: m.negative ? p.negative : "", takes: 1, action: "animate", role: "clip", anim: 1 });
  };
  // a card with a panel and nothing drawn yet shows the panel
  const _frameHtml = frameHtml;
  frameHtml = function (s) {
    const h = _frameHtml(s);
    if (!animOn() || !s.panel || pickOf(s, "key") || pickOf(s, "clip") || s._casting || busyOf(s, "key") || busyOf(s, "clip")) return h;
    return `<img src="${esc(thumbOf(s.panel).replace("w=160", "w=640"))}" alt="" class="apanel" title="The storyboard panel: the keyframe follows its composition"><span class="what">Panel</span>` + h.replace(/<div class="empty">[^<]*<\/div>/, "");
  };

  // ── the storyboard ─────────────────────────────────────────────────────
  function pickPanels() {
    pickFiles("image/png,image/jpeg,image/webp", async files => {
      files.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
      const b = $("#aPanels"); if (b) { b.disabled = true; b.textContent = "Uploading…"; }
      const names = [];
      for (const f of files.slice(0, 40)) { const r = await uploadRef(f); if (r) names.push(r.name); }
      if (!names.length) { renderBoard(); return; }
      if (b) b.textContent = "Reading the panels…";
      const r = await api(`/reels/${reelId()}/anim/storyboard`, { method: "POST", json: { panels: names, elements: B.elements.filter(e => e.kind !== "style").map(e => ({ name: e.name, kind: e.kind })), model: (clipModel() || {}).id } });
      if (r.error) { toast(r.error, true); renderBoard(); return; }
      (r.cast || []).forEach(c => {
        if (!B.elements.some(e => e.name.toLowerCase() === c.name.toLowerCase())) B.elements.push({ id: rid(), name: c.name, kind: c.kind || "cast", file: "", look: c.look, files: [] });
      });
      const byName = new Map(B.elements.map(e => [e.name.toLowerCase(), e.id]));
      let n = nextN();
      const cards = (r.cards || []).map(x => Object.assign(newShot(), { n: n++, scene: x.scene || "", shot: x.shot || "auto", move: x.move || "auto",
        duration: +x.duration || 5, elements: (x.elements || []).map(nm => byName.get(String(nm).toLowerCase())).filter(Boolean),
        start: x.start === "continue" ? "continue" : "cut", vo: x.vo || "", super: x.super || "", panel: x.panel || "", notes: x.notes || "" }));
      if (cards.length && !B.shots.length) cards[0].start = "cut";
      B.shots = B.shots.concat(cards);
      if (r.title && !B.title) B.title = r.title;
      saveBoard(true); renderKit(); renderBoard();
      toast(`${cards.length} panel${cards.length > 1 ? "s" : ""} read into cards${(r.cast || []).length ? ` · ${r.cast.length} new in the cast to build` : ""}. Check the cards, then Keyframe.`);
    });
  }
})();
