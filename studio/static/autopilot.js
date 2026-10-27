// Autopilot: one line in, a finished film out. It drives the Board the way a
// person would (studio_app.js: planSpot, keyframe, animate, continueFrom,
// cutSpot), so every step stays visible and editable on the cards:
//
//   1. the planner writes the shot list (a language model on Comfy Router, on the Comfy key)
//   2. keyframes for every new setup, all at once
//   3. clips: each shot animates from its keyframe; a shot that continues the
//      one before starts on that clip's last frame, so the chain holds
//   4. the script supervisor (Rust, gobo.wasm) checks every cut
//   5. the cut
//   6. the grade: every frame through the shot's stock and grade, in Rust on
//      the server (the same gobo.wasm, POST …/takes/{id}/grade), at the
//      strength picked, since the prompt already asked the model for the stock
//
// A model that can't start from a frame runs every shot from its words, all
// straight cuts (LTX can: its frame-start shots go through Comfy's LTX proxy).
// A shot that fails is retried once, then left out of the cut rather than
// stopping the film.
//
// run(opts, ui) reports to ui (start, step, chips, title, done); the corner
// card here is the default, and the Director (director.js) brings its own.
(function () {
  "use strict";
  const h = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const usd = v => `$${v < 10 ? v.toFixed(2) : v.toFixed(0)}`;
  const optLabel = (g, id) => { const o = ((CAT.groups[g] || {}).options || []).find(o => o.id === id); return o ? o.label : id; };

  const style = document.createElement("style");
  style.textContent = `
#apModal { position: fixed; inset: 0; z-index: 60; background: #000a; display: flex; align-items: center; justify-content: center; }
#apModal .box { width: min(560px, calc(100vw - 32px)); background: var(--panel); border: 1px solid var(--line2); border-radius: 16px; padding: 22px; box-shadow: 0 30px 80px #000c; }
#apModal h2 { margin: 0 0 4px; font-size: 1.25rem; } #apModal .hint { margin: 0 0 14px; }
#apModal textarea { width: 100%; min-height: 84px; font-size: 1rem; }
#apModal .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0; }
#apModal label { font-size: .74rem; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
#apModal .cost { font-size: .82rem; color: var(--muted); margin: 6px 0 14px; }
#apModal .go { display: flex; gap: 8px; justify-content: flex-end; }
#apRun { position: fixed; right: 18px; bottom: 18px; z-index: 55; width: min(380px, calc(100vw - 36px)); background: var(--panel);
  border: 1px solid var(--line2); border-radius: 14px; padding: 14px 16px; box-shadow: 0 20px 60px #000b; font-size: .82rem; }
#apRun .t { font-weight: 700; margin-bottom: 8px; display: flex; gap: 8px; align-items: center; }
#apRun ol { margin: 0; padding-left: 0; list-style: none; }
#apRun li { padding: 4px 0; color: var(--dim); display: flex; gap: 8px; }
#apRun li.now { color: var(--text); } #apRun li.ok { color: var(--ok); } #apRun li.bad { color: var(--bad); }
#apRun li i { font-style: normal; width: 16px; text-align: center; }
#apRun .chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0 2px 24px; }
#apRun .chips span { font-size: .66rem; padding: 1px 6px; border-radius: 99px; background: var(--panel2); border: 1px solid var(--line); }
#apRun .chips span.ok { border-color: #2c5a3a; color: var(--ok); } #apRun .chips span.run { border-color: #5a4d1c; color: var(--warn); }
#apRun .chips span.bad { border-color: #5a2a24; color: var(--bad); }
#apRun .end { margin-top: 10px; display: flex; gap: 6px; flex-wrap: wrap; }
#apRun .note { color: var(--muted); margin-top: 6px; }
@keyframes apspin { to { transform: rotate(360deg); } }
#apRun li.now i { display: inline-block; animation: apspin 1s linear infinite; }
`;
  document.head.appendChild(style);

  const btn = document.createElement("button");
  btn.className = "chipbtn"; btn.id = "apBtn"; btn.textContent = "▶ Autopilot";
  btn.title = "One line in, a finished film out: shot list, keyframes, clips, continuity check, the cut";
  const anchor = document.getElementById("labBtn") || document.getElementById("credits");
  if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(btn, anchor);

  const videoModels = () => byKind("video").filter(m => m.featured && m.engine === "comfy" && m.category !== "video-edit"
    && m.category !== "video-upscale-enhance" && !m.requires_source);
  const stillModels = () => byKind("image").filter(m => m.featured && m.engine === "comfy");
  const startsFromFrame = m => !!(m && (m.i2v || m.first_frame));

  function estimate(clip, key, seconds) {
    const shots = Math.max(1, Math.round(seconds / 5));
    const c = clip && clip.price != null ? (clip.price_per === "second" ? clip.price * seconds : clip.price * shots) : null;
    const k = startsFromFrame(clip) && key && key.price != null ? key.price * shots : 0;
    return c == null ? null : c + k + 0.02;
  }

  function openDialog() {
    const vids = videoModels(), stills = stillModels();
    const pick = (list, ids) => ids.map(id => list.find(m => m.id === id)).find(Boolean) || list[0];
    const dv = pick(vids, ["ltx/ltx-2-5-pro", "ltx/ltx-2-5-fast", "byteplus/dreamina-seedance-2-0-fast-260128", "wan/wan3.0-video"]);
    const ds = pick(stills, ["bfl/flux-3-image", "vertexai/gemini-nano-banana-2.1"]);
    const m = document.createElement("div");
    m.id = "apModal";
    m.innerHTML = `<div class="box" role="dialog" aria-label="Autopilot">
      <h2>▶ Autopilot</h2>
      <p class="hint">One line. Autopilot writes the shot list, every shot is keyframed and animated, the script supervisor checks the cuts, and the film is cut together. It all lands on the Board, where you can change any shot after.</p>
      <textarea class="field" id="apLine" placeholder="A lighthouse keeper's last night before the light goes automatic. A storm, 1970s, Kodachrome."></textarea>
      <div class="grid">
        <label>Length<select class="field" id="apLen">${[10, 15, 20, 30, 45].map(s => `<option value="${s}" ${s === 15 ? "selected" : ""}>${s} seconds</option>`).join("")}</select></label>
        <label>Look<select class="field" id="apLook"><option value="shot">The look set on the left</option><option value="auto">Let the shot list decide</option></select></label>
        <label>Clips<select class="field" id="apClip">${vids.map(v => `<option value="${h(v.id)}" ${v === dv ? "selected" : ""}>${h(v.label)}${startsFromFrame(v) ? "" : " (from words, straight cuts)"}</option>`).join("")}</select></label>
        <label>Keyframes<select class="field" id="apKey">${stills.map(v => `<option value="${h(v.id)}" ${v === ds ? "selected" : ""}>${h(v.label)}</option>`).join("")}</select></label>
        <label>Grade (Rust, free)<select class="field" id="apGrade">${[[0, "Off: as shot"], [0.4, "Light"], [0.65, "Medium"], [1, "Full"]].map(([v, l]) => `<option value="${v}" ${v === 0.65 ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      </div>
      <div class="cost" id="apCost"></div>
      <div class="go"><button class="btn" id="apCancel">Cancel</button><button class="btn primary" id="apGo">Roll camera</button></div></div>`;
    document.body.appendChild(m);
    const $m = s => m.querySelector(s);
    const upd = () => {
      const clip = modelById($m("#apClip").value), key = modelById($m("#apKey").value), sec = +$m("#apLen").value;
      const e = estimate(clip, key, sec);
      $m("#apKey").disabled = !startsFromFrame(clip);
      $m("#apCost").textContent = (e == null ? `${clip.label} doesn't publish a price; the run will report what it cost.` : `About ${usd(e)} on your Comfy account for ${sec} seconds`)
        + (startsFromFrame(clip) ? " · each shot starts from its keyframe; shots that continue start on the last frame of the one before." : " · this model starts from words, so every shot is its own setup.");
    };
    ["#apClip", "#apKey", "#apLen"].forEach(s => $m(s).onchange = upd); upd();
    $m("#apCancel").onclick = () => m.remove();
    m.onclick = e => { if (e.target === m) m.remove(); };
    $m("#apLine").focus();
    $m("#apGo").onclick = () => {
      const line = $m("#apLine").value.trim();
      if (!line) return $m("#apLine").focus();
      const opts = { line, seconds: +$m("#apLen").value, clip: $m("#apClip").value, key: $m("#apKey").value, look: $m("#apLook").value, grade: +$m("#apGrade").value };
      m.remove();
      run(opts).catch(e => { cardUI.step(null, "bad", e.message || String(e)); toast(e.message || String(e), true); });
    };
  }
  btn.onclick = () => { if (!document.getElementById("apRun") || confirm("Autopilot is already rolling. Start another film?")) openDialog(); };

  // ── the progress card ───────────────────────────────────────────────────
  const STEPS = [["plan", "The shot list"], ["keys", "Keyframes"], ["clips", "Clips"], ["check", "Script supervisor (Rust)"], ["cut", "The cut"], ["grade", "The grade (Rust)"]];
  let panel = null;
  function card(title) {
    if (panel) panel.remove();
    panel = document.createElement("div");
    panel.id = "apRun";
    panel.innerHTML = `<div class="t">▶ <span id="apTitle">${h(title)}</span><span style="flex:1"></span><button class="iconbtn" id="apHide" title="Hide (it keeps rolling)">–</button></div>
      <ol>${STEPS.map(([k, label]) => `<li data-k="${k}"><i>·</i><span>${label}</span></li><div class="chips" data-c="${k}"></div>`).join("")}</ol>
      <div class="note" id="apNote"></div><div class="end" id="apEnd"></div>`;
    document.body.appendChild(panel);
    panel.querySelector("#apHide").onclick = () => panel.classList.toggle("min");
  }
  function step(k, state, note) {
    if (!panel) return;
    if (k) {
      const li = panel.querySelector(`li[data-k="${k}"]`);
      li.className = state; li.querySelector("i").textContent = { now: "◐", ok: "✓", bad: "✕", skip: "–" }[state] || "·";
    }
    if (note != null) panel.querySelector("#apNote").textContent = note;
  }
  function chips(k, items) {
    const c = panel && panel.querySelector(`[data-c="${k}"]`);
    if (c) c.innerHTML = items.map(([label, st]) => `<span class="${st || ""}">${h(label)}</span>`).join("");
  }
  function cardDone({ final, film }) {
    const end = panel.querySelector("#apEnd");
    end.innerHTML = `<button class="btn small primary" id="apWatch">Watch it</button><button class="btn small" id="apGrade" title="The Lab, on the film as shot">Regrade in the Lab</button>
      <a class="btn small" href="${h(final.url)}?download=1">Download</a>
      <a class="btn small" href="${API}/reels/${S.reel.id}/comfyui" title="The whole film as one ComfyUI workflow: every keyframe node wired into its clip">Open in ComfyUI</a>
      <button class="btn small" id="apClose">Close</button>`;
    end.querySelector("#apWatch").onclick = () => { setView("shot"); select(final.id); };
    end.querySelector("#apGrade").onclick = () => { setView("shot"); select(film.id); const l = document.getElementById("labBtn"); if (l) { l.click(); const t = document.querySelector('#lab [data-tab="grade"]'); if (t) t.click(); } };
    end.querySelector("#apClose").onclick = () => { panel.remove(); panel = null; };
  }
  const cardUI = { start: card, step, chips, done: cardDone,
                   title: t => { const el = panel && panel.querySelector("#apTitle"); if (el) el.textContent = t; } };

  // ── waiting on takes (S.takes is kept fresh by the page's poll) ───────────
  function waitFor(s, role, minutes) {
    const t0 = Date.now();
    return new Promise((ok, no) => {
      const tick = setInterval(() => {
        const ts = cardTakes(s, role);
        const done = ts.find(t => t.status === "done");
        if (done) { clearInterval(tick); return ok(done); }
        if (ts.length && ts.every(t => t.status === "failed" || t.status === "cancelled")) {
          clearInterval(tick); return no(new Error((ts[ts.length - 1].error || "the take failed").slice(0, 200)));
        }
        if (Date.now() - t0 > minutes * 60000) { clearInterval(tick); no(new Error("timed out waiting for the provider")); }
      }, 1500);
    });
  }
  async function attempt(s, role, start, minutes) {
    for (let i = 0; i < 2; i++) {
      const r = await start(s);
      if (!r) { if (i) throw new Error("the take was refused"); continue; }
      try { return await waitFor(s, role, minutes); }
      catch (e) { if (i) throw e; }
    }
  }

  // ── the run ─────────────────────────────────────────────────────────────
  async function run(o, ui = cardUI) {
    if (!S.mode || S.mode !== "video") setMode("video", true);
    setView("board");
    await newReel();
    $("#reelName").value = o.line.length > 48 ? o.line.slice(0, 46) + "…" : o.line;
    S.model.video = o.clip; S.model.image = o.key;
    const clip = modelById(o.clip), fromFrame = startsFromFrame(clip);
    B.brief = o.line; B.target = o.seconds;
    if (o.look === "auto") Object.keys(S.direction).forEach(k => { if (k !== "move" && k !== "shot") S.direction[k] = "auto"; });
    saveBoard(true); renderBoard();
    ui.start(o.line);

    ui.step("plan", "now", "Writing the shot list…");
    const plan = await planSpot(true);
    if (!plan || plan.error || !B.shots.length) { ui.step("plan", "bad", (plan && plan.error) || "No shot list came back."); return; }
    if (!fromFrame) B.shots.forEach(s => { s.start = "cut"; });
    saveBoard(true); renderBoard();
    if (B.title) ui.title(B.title);
    ui.step("plan", "ok", `${B.shots.length} shots: ${B.notes || ""}`);
    const shots = B.shots.filter(s => s.type !== "footage");
    const name = s => `Shot ${B.shots.indexOf(s) + 1}`;
    const state = new Map(shots.map(s => [s.id, { key: fromFrame && s.start !== "continue" ? "run" : "", clip: "" }]));
    const show = () => {
      ui.chips("keys", shots.filter(s => fromFrame && s.start !== "continue").map(s => [name(s), state.get(s.id).key]));
      ui.chips("clips", shots.map(s => [name(s) + (s.start === "continue" ? " ↪" : ""), state.get(s.id).clip]));
    };

    // keyframes for every new setup, together
    if (fromFrame) {
      ui.step("keys", "now", "Every new setup gets its keyframe, all at once.");
      show();
      await Promise.all(shots.filter(s => s.start !== "continue").map(async s => {
        try { await attempt(s, "key", keyframe, 15); state.get(s.id).key = "ok"; }
        catch (e) { state.get(s.id).key = "bad"; s._apErr = e.message; }
        show();
      }));
      const bad = shots.filter(s => state.get(s.id).key === "bad");
      ui.step("keys", bad.length ? "bad" : "ok", bad.length ? `${bad.map(name).join(", ")} had no keyframe: ${bad[0]._apErr}` : "");
    } else ui.step("keys", "skip", `${clip.label} starts from words: no keyframes.`);

    // clips: in parallel, except a continuing shot waits for the one before it
    ui.step("clips", "now", "Animating. A shot marked ↪ starts on the last frame of the one before.");
    const tasks = [];
    shots.forEach((s, i) => {
      tasks[i] = (async () => {
        const st = state.get(s.id);
        if (fromFrame && s.start !== "continue" && st.key === "bad") { st.clip = "bad"; show(); return null; }
        if (s.start === "continue") {
          const prev = await tasks[i - 1];
          if (!prev) { st.clip = "bad"; show(); return null; }
          try { await attempt(s, "key", continueFrom, 5); }
          catch (e) { st.clip = "bad"; show(); return null; }
        }
        st.clip = "run"; show();
        try { const t = await attempt(s, "clip", animate, 45); st.clip = "ok"; show(); return t; }
        catch (e) { st.clip = "bad"; s._apErr = e.message; show(); return null; }
      })();
    });
    const clips = await Promise.all(tasks);
    const made = clips.filter(Boolean);
    if (!made.length) { ui.step("clips", "bad", "No clip came back: " + (shots[0]._apErr || "")); return; }
    ui.step("clips", made.length === shots.length ? "ok" : "bad", made.length === shots.length ? "" : `${shots.length - made.length} shot(s) left out: ${(shots.find(s => s._apErr) || {})._apErr || ""}`);

    // the script supervisor, in Rust
    ui.step("check", "now", "Reading every cut…");
    const g = await window.goboReady;
    const flags = [];
    if (g && made.length > 1) {
      const frame = async (t, at) => {
        const v = document.createElement("video");
        Object.assign(v, { muted: true, preload: "auto", src: t.url });
        await new Promise((ok, no) => { v.onloadeddata = ok; v.onerror = () => no(new Error("clip didn't load")); });
        await new Promise(ok => { v.onseeked = ok; v.currentTime = at === "last" ? Math.max(0, v.duration - 0.1) : 0.04; });
        const c = document.createElement("canvas"), k = Math.min(1, 480 / v.videoWidth);
        c.width = Math.round(v.videoWidth * k); c.height = Math.round(v.videoHeight * k);
        const x = c.getContext("2d", { willReadFrequently: true }); x.drawImage(v, 0, 0, c.width, c.height);
        return x.getImageData(0, 0, c.width, c.height);
      };
      try {
        for (let i = 1; i < made.length; i++) {
          const card = B.shots.find(s => s.id === made[i].board) || {};
          const r = g.compare(await frame(made[i - 1], "last"), await frame(made[i], "first"), card.start === "continue");
          flags.push([`${i}→${i + 1} ${r.verdict}`, r.verdict === "match" ? "ok" : r.verdict === "drift" ? "run" : "bad", r]);
        }
      } catch (e) { flags.length = 0; }
    }
    ui.chips("check", flags.map(([l, st]) => [l, st]));
    const jumps = flags.filter(f => f[2].verdict === "jump");
    ui.step("check", "ok", jumps.length ? `Cut ${jumps.map(f => f[0].split(" ")[0]).join(", ")} jumps: ${jumps[0][2].notes.slice(0, 2).join("; ")}. Open the Lab's Continuity tab for fixes.` : "Light and colour carry across the cuts.");

    // the cut
    ui.step("cut", "now", "Cutting the film…");
    await flushBoard();
    const film = await cutSpot(false);
    if (!film) { ui.step("cut", "bad", "The cut didn't come back."); return; }
    ui.step("cut", "ok", `${B.title || "The film"} · ${Math.round(film.seconds || 0)} s`);

    // the grade, in Rust: the stock and grade the shots were made with
    let final = film;
    const stock = S.direction.film && S.direction.film !== "auto" ? S.direction.film : null;
    const tone = S.direction.grade && S.direction.grade !== "auto" ? S.direction.grade : null;
    if (!o.grade) ui.step("grade", "skip", "Grading was off: the film is as shot.");
    else if (!stock && !tone) ui.step("grade", "skip", "No stock or grade was set, so the film is as shot.");
    else {
      const name = [stock && optLabel("film", stock), tone && optLabel("grade", tone)].filter(Boolean).join(" + ");
      ui.step("grade", "now", `Grading every frame through ${name}…`);
      const r = await api(`/reels/${S.reel.id}/takes/${film.id}/grade`, { method: "POST",
        json: { look: { film: stock || "clean", grade: tone || "natural", amount: o.grade }, label: `${B.title || "The film"} · ${name}` } });
      // a film grades in the background (minutes on a small server): wait for it
      let t = r.take;
      if (t) mergeTakes([t]);
      while (t && (t.status === "running" || t.status === "queued")) {
        await new Promise(res => setTimeout(res, 2000));
        const now = await api(`/reels/${S.reel.id}`);
        t = (now.takes || []).find(x => x.id === t.id) || t;
        if (t.progress) ui.step("grade", "now", `Grading every frame through ${name}: ${t.progress}`);
      }
      if (r.error || !t || t.status !== "done") ui.step("grade", "bad", `The grade didn't take (${r.error || (t && t.error) || "it stopped"}); the film is as shot.`);
      else {
        mergeTakes([t]); final = t;
        ui.step("grade", "ok", `${name} at ${Math.round(o.grade * 100)}%: ${t.frames} frames in ${Math.round(t.grade_seconds)} s`);
      }
    }
    ui.done({ final, film, title: B.title || "The film" });
    toast(`${B.title || "The film"} is cut.`);
    return { final, film };
  }
  window.autopilot = { run, openDialog, estimate, startsFromFrame, videoModels, stillModels };
  if (new URLSearchParams(location.search).get("autopilot")) setTimeout(openDialog, 900);
})();
