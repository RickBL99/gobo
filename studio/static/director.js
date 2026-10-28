// Make a film: Gobo's front door ("the Director" in the code; on screen it's
// "Make a film", since the studio's Director is the prompt rewriter, and here
// the person is the director). Say the film in one line, pick a look, a
// length, a shape and how good, and press Roll camera. Autopilot
// (autopilot.js) does the work (the shot list, keyframes, clips, the
// continuity check, the cut and the grade) and this screen shows each step in
// plain words, then plays the film with Share and Download.
//
// Every tool is still underneath: "Direct it yourself" opens the studio, and
// the studio's Director button comes back here. STUDIO_FRONT=studio
// (settings.py) makes the home page the front door again, as it was.
(function () {
  "use strict";
  if (typeof DIRECTOR_ON === "undefined" || !DIRECTOR_ON || !window.autopilot) return;
  const AP = window.autopilot;
  const usd = v => `$${v < 10 ? v.toFixed(2) : v.toFixed(0)}`;
  const mmss = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

  // ── the choices, in plain words ─────────────────────────────────────────
  // each look is a genre (the planner writes for it) plus the stock and grade
  // the finished film is graded through
  const LOOKS = [
    ["auto", "Let Gobo decide", "look-auto", {}],
    ["drama", "Drama", "look-drama", { look: "drama", film: "t500", grade: "nordic" }],
    ["thriller", "Thriller", "look-thriller", { look: "thriller", film: "t500", grade: "teal" }],
    ["scifi", "Sci-fi", "look-scifi", { look: "scifi", film: "clean", grade: "teal" }],
    ["western", "Western", "look-western", { look: "western", film: "slide", grade: "desert" }],
    ["horror", "Horror", "look-horror", { look: "horror", film: "bleach", grade: "nordic" }],
    ["neonoir", "Neon night", "look-neonoir", { look: "neonoir", film: "t500", grade: "neon" }],
    ["musical", "Music video", "look-musical", { look: "musical", film: "slide", grade: "kodachrome" }],
    ["road", "Road trip", "look-road", { look: "road", film: "portra", grade: "kodachrome" }],
    ["doc", "Documentary", "look-doc", { look: "doc", film: "d250", grade: "natural" }],
    ["commercial", "Advert", "look-commercial", { look: "commercial", film: "clean", grade: "natural" }],
    ["bw", "Black & white", "film-bw400", { look: "drama", film: "bw400", grade: "noir" }],
    ["vhs", "Home video", "film-vhs", { look: "doc", film: "vhs", grade: "natural" }],
  ];
  const LENGTHS = [10, 15, 30];
  const SHAPES = [["16:9", "Wide", "Screens and YouTube"], ["9:16", "Tall", "Phones, Reels and TikTok"], ["1:1", "Square", "Feeds"]];
  // the first of each list that this server offers wins. LTX is Gobo's core
  // video model and every quality films on it: Draft on LTX 2.5 Fast,
  // Standard (the default) and Best on LTX 2.5 Pro. Best's difference is
  // its first frames, painted by Nano Banana Pro
  const QUALITY = [
    { id: "draft", label: "Draft", note: "Cheapest. For trying an idea.",
      clips: ["ltx/ltx-2-5-fast"], keys: ["vertexai/gemini-nano-banana-2.1", "bfl/flux-3-image"] },
    { id: "standard", label: "Standard", note: "Good and quick.",
      clips: ["ltx/ltx-2-5-pro"], keys: ["bfl/flux-3-image", "vertexai/gemini-nano-banana-2.1"] },
    { id: "best", label: "Best", note: "Sharper first frames. Costs a little more.",
      clips: ["ltx/ltx-2-5-pro"], keys: ["vertexai/gemini-3-pro-image", "bfl/flux-3-image"] },
  ];
  const IDEAS = [
    "A golden retriever's first day at the beach: waves, wet sand, pure joy.",
    "A grandmother teaches her grandson to fold dumplings on a rainy afternoon.",
    "A lone astronaut finds a flower growing in the red dust of Mars.",
    "A skateboarder rides an empty city at dawn, the streetlights going out one by one.",
  ];
  const STEPS = [["plan", "Writing the shots"], ["keys", "Painting the first frames"], ["clips", "Filming"],
                 ["check", "Checking continuity"], ["cut", "Editing"], ["grade", "Colour grading"]];

  const pick = (list, ids) => ids.map(id => list.find(m => m.id === id)).find(Boolean)
    || list.find(m => AP.startsFromFrame(m)) || list[0];
  const models = q => {
    const vids = AP.videoModels(), stills = AP.stillModels();
    return { clip: pick(vids, q.clips), key: pick(stills, q.keys) };
  };

  const st = {
    look: LS.get("director.look", "auto"), seconds: LS.get("director.seconds", 15),
    shape: LS.get("director.shape", "16:9"), quality: LS.get("director.quality", "standard"),
    line: LS.get("director.line", ""), running: false, t0: 0, last: null, film: null,
  };
  if (!LOOKS.some(l => l[0] === st.look)) st.look = "auto";
  if (!LENGTHS.includes(st.seconds)) st.seconds = 15;
  if (!SHAPES.some(s => s[0] === st.shape)) st.shape = "16:9";
  if (!QUALITY.some(q => q.id === st.quality)) st.quality = "standard";

  // ── styles ──────────────────────────────────────────────────────────────
  const css = document.createElement("style");
  css.textContent = `
#director { position: fixed; inset: 0; z-index: 58; background: radial-gradient(1200px 600px at 50% -10%, #241d0e 0, var(--bg) 60%);
  overflow-y: auto; color: var(--text); }
#director.off { display: none; }
#director [hidden] { display: none !important; }
#director .dtop { display: flex; align-items: center; gap: 10px; padding: 12px clamp(16px, 4vw, 40px); position: sticky; top: 0;
  background: rgba(11,11,13,.75); backdrop-filter: blur(10px); z-index: 2; }
#director .dlogo { display: flex; align-items: center; gap: 9px; font-weight: 800; font-size: 1.02rem; color: var(--text); text-decoration: none; }
#director .dlogo svg { width: 26px; height: 26px; }
#director .grow { flex: 1; }
#director .dchip { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: var(--panel);
  border-radius: 99px; padding: 5px 12px; font-size: .8rem; color: var(--muted); white-space: nowrap; text-decoration: none; cursor: pointer; }
#director .dchip:hover { border-color: var(--line2); color: var(--text); }
#director .dchip.low { border-color: #6b4a12; color: var(--accent); }
#director .dchip.yours { border-color: var(--accent); color: var(--accent); font-weight: 700; }
#director .wrap { max-width: 920px; margin: 0 auto; padding: clamp(18px, 5vh, 56px) 16px 60px; }
#director h1 { font-size: clamp(2rem, 5.4vw, 3.6rem); line-height: 1.04; letter-spacing: -.035em; margin: 0 0 10px; font-weight: 800; text-align: center; }
#director h1 em { font-style: normal; background: linear-gradient(90deg, #f2c14e, #f08a4b 60%, #e05a6a); -webkit-background-clip: text;
  background-clip: text; color: transparent; }
#director .sub { color: var(--muted); text-align: center; margin: 0 auto 26px; max-width: 620px; font-size: 1.04rem; }
#director textarea { width: 100%; min-height: 96px; resize: vertical; border-radius: 16px; border: 1px solid var(--line2);
  background: var(--panel); color: var(--text); font: 500 1.15rem/1.45 Inter, system-ui, sans-serif; padding: 16px 18px; outline: none; }
#director textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px #f2c14e22; }
#director .ideas { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 26px; align-items: center; }
#director .ideas span { color: var(--dim); font-size: .8rem; margin-right: 2px; }
#director .ideas button { border: 1px dashed var(--line2); background: none; color: var(--muted); border-radius: 99px; padding: 4px 11px;
  font: 500 .78rem Inter, system-ui, sans-serif; cursor: pointer; text-align: left; }
#director .ideas button:hover { color: var(--text); border-color: var(--muted); }
#director .lab { font-size: .74rem; font-weight: 700; text-transform: uppercase; letter-spacing: .12em; color: var(--dim); margin: 0 0 8px; }
#director .looks { display: grid; grid-auto-flow: column; grid-auto-columns: 132px; gap: 10px; overflow-x: auto; padding: 2px 2px 10px;
  scroll-snap-type: x proximity; margin-bottom: 18px; }
#director .look { position: relative; border-radius: 12px; overflow: hidden; border: 2px solid transparent; cursor: pointer; padding: 0;
  background: var(--panel); aspect-ratio: 4/3; scroll-snap-align: start; }
#director .look img { width: 100%; height: 100%; object-fit: cover; display: block; transition: transform .25s; }
#director .look:hover img { transform: scale(1.05); }
#director .look span { position: absolute; left: 0; right: 0; bottom: 0; padding: 16px 8px 6px; font: 700 .8rem Inter, system-ui, sans-serif;
  color: #fff; background: linear-gradient(180deg, transparent, rgba(0,0,0,.85)); text-align: left; }
#director .look.on { border-color: var(--accent); box-shadow: 0 0 0 3px #f2c14e33; }
#director .opts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-bottom: 24px; }
#director .seg { display: flex; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 3px; gap: 3px; }
#director .seg button { flex: 1; border: 0; background: none; color: var(--muted); border-radius: 9px; padding: 9px 6px; cursor: pointer;
  font: 600 .88rem Inter, system-ui, sans-serif; display: flex; flex-direction: column; align-items: center; gap: 2px; }
#director .seg button small { font-weight: 500; font-size: .66rem; color: var(--dim); }
#director .seg button.on { background: var(--text); color: #111; } #director .seg button.on small { color: #555; }
#director .shape { display: inline-block; border: 2px solid currentColor; border-radius: 3px; }
#director .go { display: flex; flex-direction: column; align-items: center; gap: 10px; }
#director .roll { height: 58px; padding: 0 34px; border-radius: 16px; border: 0; background: var(--accent); color: var(--accent-ink);
  font: 800 1.12rem Inter, system-ui, sans-serif; cursor: pointer; box-shadow: 0 10px 40px #f2c14e33; }
#director .roll:hover { filter: brightness(1.06); } #director .roll:disabled { opacity: .55; cursor: default; }
#director .cost { color: var(--muted); font-size: .86rem; text-align: center; }
#director .warn { border: 1px solid #6b4a12; background: #1c160a; color: #e9cf8a; border-radius: 12px; padding: 10px 14px; font-size: .88rem; margin-top: 4px; }
#director .warn a { color: var(--accent); }
#director .self { text-align: center; margin-top: 26px; color: var(--dim); font-size: .86rem; }
#director .self button { background: none; border: 0; color: var(--muted); text-decoration: underline; cursor: pointer; font: inherit; }
#director .films { margin-top: 44px; }
#director .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 10px; }
#director .fcard { position: relative; border-radius: 12px; overflow: hidden; border: 1px solid var(--line); background: var(--panel);
  aspect-ratio: 16/9; cursor: pointer; padding: 0; }
#director .fcard img { width: 100%; height: 100%; object-fit: cover; display: block; }
#director .fcard span { position: absolute; left: 0; right: 0; bottom: 0; padding: 16px 9px 7px; font: 600 .76rem Inter, system-ui, sans-serif;
  color: #eee; background: linear-gradient(180deg, transparent, rgba(0,0,0,.85)); text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#director .fcard i { position: absolute; right: 8px; top: 8px; font-style: normal; font-size: .66rem; font-weight: 700; padding: 2px 7px; border-radius: 99px;
  background: #2a2410; color: var(--accent); border: 1px solid #4d4116; }
/* shooting */
#director .shoot h2, #director .done h2 { font-size: clamp(1.4rem, 3.4vw, 2.2rem); letter-spacing: -.02em; margin: 0 0 4px; text-align: center; }
#director .clock { text-align: center; color: var(--muted); font: 600 .9rem ui-monospace, Menlo, monospace; margin-bottom: 22px; }
#director .clock b { color: #ff4d3d; } #director .clock b::before { content: "● "; animation: dblink 1.2s steps(1) infinite; }
@keyframes dblink { 50% { opacity: .2; } }
#director .steps { list-style: none; padding: 0; margin: 0 auto 22px; max-width: 520px; }
#director .steps li { display: grid; grid-template-columns: 28px 1fr; gap: 4px 10px; padding: 9px 0; border-bottom: 1px solid var(--line); color: var(--dim); }
#director .steps li i { font-style: normal; text-align: center; font-weight: 700; }
#director .steps li small { grid-column: 2; color: var(--muted); font-size: .78rem; }
#director .steps li.now { color: var(--text); } #director .steps li.now i { color: var(--accent); display: inline-block; animation: dspin 1s linear infinite; }
#director .steps li.ok { color: var(--ok); } #director .steps li.bad { color: var(--bad); } #director .steps li.skip { color: var(--dim); }
@keyframes dspin { to { transform: rotate(360deg); } }
#director .frames { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
#director .fr { border-radius: 10px; overflow: hidden; background: var(--panel); border: 1px solid var(--line); }
#director .fr .pic { aspect-ratio: 16/9; background: #0e0e11 center/cover no-repeat; display: grid; place-items: center; color: var(--dim); font-size: .74rem; }
#director .fr p { margin: 0; padding: 6px 8px; font-size: .72rem; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
#director .fr b { color: var(--text); font-weight: 600; }
#director .hint2 { text-align: center; color: var(--dim); font-size: .82rem; margin-top: 18px; }
#director .acts { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 14px; }
#director .acts .btn { height: 42px; padding: 0 16px; border-radius: 11px; font-weight: 700; display: inline-flex; align-items: center; }
/* the film */
#director .screen { background: #000; border-radius: 16px; overflow: hidden; border: 1px solid var(--line); display: flex; justify-content: center; margin-bottom: 16px; }
#director .screen video { display: block; max-width: 100%; max-height: 66vh; }
#director .meta { text-align: center; color: var(--muted); font-size: .86rem; margin: 0 0 4px; }
#director .sharebox { display: flex; gap: 6px; max-width: 560px; margin: 14px auto 0; }
#director .sharebox input { flex: 1; min-width: 0; height: 42px; border-radius: 11px; border: 1px solid var(--line2); background: var(--panel);
  color: var(--text); padding: 0 12px; font: 500 .86rem ui-monospace, Menlo, monospace; }
#director .more { text-align: center; margin-top: 22px; font-size: .82rem; color: var(--dim); }
#director .more a, #director .more button { color: var(--muted); background: none; border: 0; text-decoration: underline; cursor: pointer; font: inherit; padding: 0 4px; }
@media (max-width: 720px) {
  #director .opts { grid-template-columns: 1fr; }
  #director .dtop .hide-s { display: none; }
  #director .looks { grid-auto-columns: 112px; }
}
`;
  document.head.appendChild(css);

  // ── the screen ──────────────────────────────────────────────────────────
  const D = document.createElement("div");
  D.id = "director";
  D.className = DIRECTOR ? "" : "off";
  D.setAttribute("role", "dialog");
  D.setAttribute("aria-label", "Make a film");
  // home is wherever the studio's own Home button goes: "/" on a studio-first
  // Gobo, /home where this screen is the front door
  const HOME = (document.querySelector(".homebtn") || { getAttribute: () => "/home" }).getAttribute("href") || "/home";
  D.innerHTML = `${(document.querySelector(".ssbar") || {}).outerHTML || ""}
  <header class="dtop">
    <a class="dlogo" href="${HOME}" title="Home: every tool"><svg viewBox="0 0 48 48" aria-hidden="true"><rect x="3" y="9" width="42" height="30" rx="5" fill="none" stroke="#f2c14e" stroke-width="3"/>
      <circle cx="24" cy="24" r="8.5" fill="none" stroke="#ecebe8" stroke-width="3"/><circle cx="24" cy="24" r="3" fill="#f2c14e"/>
      <rect x="8" y="13" width="5" height="3" rx="1" fill="#ecebe8"/></svg>Gobo</a>
    <span class="grow"></span>
    <span class="dchip" id="dCredits" title="Your balance"></span>
    <a class="dchip" id="dHome" href="${HOME}" title="Home: every tool">Home</a>
    <a class="dchip hide-s" href="/about" target="_blank" rel="noopener">About</a>
    <button class="dchip" id="dSelf" title="Every control: camera, lens, light, the Board, the Lab and every model">Direct it yourself →</button>
    ${SIGN_IN ? `<a class="dchip hide-s" href="/logout">Sign out</a>` : ""}
  </header>
  <div class="wrap">
    <section class="compose" id="dCompose">
      <h1>What's your <em>film?</em></h1>
      <p class="sub">Say it in one line. Gobo writes the shots, films them, cuts them together and grades them like film. No nodes, no settings.</p>
      <textarea id="dLine" maxlength="600" placeholder="A lighthouse keeper's last night before the light goes automatic. A storm rolls in."></textarea>
      <div class="ideas"><span>Try:</span>${IDEAS.map((t, i) => `<button data-i="${i}">${esc(t)}</button>`).join("")}</div>
      <p class="lab">The look</p>
      <div class="looks" id="dLooks">${LOOKS.map(([id, label, img]) => `<button class="look" data-look="${id}" aria-pressed="false" title="${esc(label)}">
        <img src="/static/samples/${img}.webp" alt="" loading="lazy"><span>${esc(label)}</span></button>`).join("")}</div>
      <div class="opts">
        <div><p class="lab">Length</p><div class="seg" id="dLen">${LENGTHS.map(s => `<button data-v="${s}">${s} sec</button>`).join("")}</div></div>
        <div><p class="lab">Shape</p><div class="seg" id="dShape">${SHAPES.map(([a, label, note]) => {
          const [w, h] = a.split(":").map(Number), k = 16 / Math.max(w, h);
          return `<button data-v="${a}" title="${esc(note)}"><span class="shape" style="width:${Math.round(w * k)}px;height:${Math.round(h * k)}px"></span>${label}</button>`; }).join("")}</div></div>
        <div><p class="lab">Quality</p><div class="seg" id="dQual">${QUALITY.map(q => `<button data-v="${q.id}" title="${esc(q.note)}">${q.label}<small>${esc(q.note)}</small></button>`).join("")}</div></div>
      </div>
      <div class="go">
        <button class="roll" id="dRoll">🎬 Roll camera</button>
        <div class="cost" id="dCost"></div>
        <div class="warn" id="dWarn" hidden></div>
      </div>
      <p class="self">Want the camera, the lens, the light and every model? <button id="dSelf2">Direct it yourself</button></p>
      <div class="films" id="dFilms" hidden><p class="lab">Your films</p><div class="grid" id="dFilmGrid"></div></div>
    </section>
    <section class="shoot" id="dShoot" hidden>
      <h2 id="dTitle"></h2>
      <div class="clock" id="dClock"></div>
      <ol class="steps" id="dSteps"></ol>
      <div class="frames" id="dFrames"></div>
      <div class="acts" id="dFail" hidden></div>
      <p class="hint2" id="dHint">Usually ready in 5 to 10 minutes. Keep this tab open; you can look around the studio while it rolls.</p>
    </section>
    <section class="done" id="dDone" hidden></section>
  </div>`;
  document.body.appendChild(D);
  const $d = s => D.querySelector(s);

  // ── compose ─────────────────────────────────────────────────────────────
  const line = $d("#dLine");
  line.value = st.line;
  line.oninput = () => { st.line = line.value; LS.set("director.line", st.line); };
  line.onkeydown = e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) roll(); };
  D.querySelectorAll(".ideas button").forEach(b => b.onclick = () => { line.value = IDEAS[+b.dataset.i]; line.oninput(); line.focus(); });

  function paint() {
    D.querySelectorAll(".look").forEach(b => { const on = b.dataset.look === st.look; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    const seg = (id, v) => D.querySelectorAll(`${id} button`).forEach(b => b.classList.toggle("on", b.dataset.v === String(v)));
    seg("#dLen", st.seconds); seg("#dShape", st.shape); seg("#dQual", st.quality);
    const q = QUALITY.find(x => x.id === st.quality), m = models(q);
    const e = m.clip ? AP.estimate(m.clip, m.key, st.seconds) : null;
    $d("#dCost").textContent = (e == null ? "The price is shown when it's done" : `About ${usd(e)} on your Comfy account`)
      + ` · usually ready in 5 to 10 minutes`;
    $d("#dWarn").hidden = true;
  }
  D.querySelectorAll(".look").forEach(b => b.onclick = () => { st.look = b.dataset.look; LS.set("director.look", st.look); paint(); });
  const segClick = (id, key, conv) => D.querySelectorAll(`${id} button`).forEach(b => b.onclick = () => {
    st[key] = conv(b.dataset.v); LS.set(`director.${key}`, st[key]); paint();
  });
  segClick("#dLen", "seconds", Number); segClick("#dShape", "shape", String); segClick("#dQual", "quality", String);

  function warn(html) { const w = $d("#dWarn"); w.innerHTML = html; w.hidden = false; }

  async function roll() {
    if (st.running) return show("shoot");
    const text = line.value.trim();
    if (!text) { line.focus(); return warn("Write one line about your film first, or pick one of the ideas."); }
    const q = QUALITY.find(x => x.id === st.quality), m = models(q);
    if (!m.clip) return warn("No video model is available on this server right now.");
    const est = AP.estimate(m.clip, m.key, st.seconds);
    const btn = $d("#dRoll"); btn.disabled = true;
    const bal = await api("/credits");
    btn.disabled = false;
    if (bal && bal.credits != null && est != null && bal.credits < est) {
      return warn(`Your Comfy account has ${usd(bal.credits)}, and this film needs about ${usd(est)}. `
        + `<a href="https://platform.comfy.org/" target="_blank" rel="noopener">Add credits</a>, or pick Draft or a shorter film.`);
    }
    const look = LOOKS.find(l => l[0] === st.look);
    Object.keys(S.direction).forEach(k => { S.direction[k] = "auto"; });
    Object.assign(S.direction, look[3]);
    S.aspect = st.shape;
    if (typeof onShotChange === "function") onShotChange();
    st.last = { line: text, seconds: st.seconds, clip: m.clip.id, key: m.key ? m.key.id : "", look: st.look === "auto" ? "auto" : "shot", grade: 0.65 };
    shoot(st.last);
  }
  $d("#dRoll").onclick = roll;

  // ── shooting ────────────────────────────────────────────────────────────
  let clockT = null, framesT = null;
  const ui = {
    start(title) {
      st.running = true; st.t0 = Date.now(); st.film = null;
      $d("#dTitle").textContent = title;
      $d("#dSteps").innerHTML = STEPS.map(([k, label]) => `<li data-k="${k}"><i>·</i><span>${label}</span><small></small></li>`).join("");
      $d("#dFrames").innerHTML = ""; $d("#dFail").hidden = true; $d("#dHint").hidden = false;
      show("shoot"); paintButton();
      clearInterval(clockT); clockT = setInterval(tick, 1000); tick();
      clearInterval(framesT); framesT = setInterval(frames, 1500);
    },
    step(k, state, note) {
      const li = k && $d(`#dSteps li[data-k="${k}"]`);
      if (li) {
        li.className = state;
        li.querySelector("i").textContent = { now: "◐", ok: "✓", bad: "✕", skip: "–" }[state] || "·";
        if (note != null) li.querySelector("small").textContent = note;
      } else if (note) {
        fail(note);
      }
    },
    chips() { frames(); },
    title(t) { $d("#dTitle").textContent = t; },
    done(r) { finished(r.final, r.film, r.title, r.final.reel || (S.reel && S.reel.id)); },
  };
  function tick() { if (st.running) $d("#dClock").innerHTML = `<b>Rolling</b> · ${mmss((Date.now() - st.t0) / 1000)}`; }
  function frames() {
    if (typeof B === "undefined" || !B.shots) return;
    const shots = B.shots.filter(s => s.type !== "footage");
    $d("#dFrames").innerHTML = shots.map((s, i) => {
      const k = pickOf(s, "key"), c = pickOf(s, "clip");
      const pic = (c && c.thumb_url) || (k && k.thumb_url) || "";
      const state = c ? "filmed" : busyOf(s, "clip") ? "filming…" : k ? "framed" : busyOf(s, "key") ? "painting…" : "waiting";
      return `<div class="fr"><div class="pic" style="${pic ? `background-image:url('${pic}')` : ""}">${pic ? "" : esc(state)}</div>
        <p><b>Shot ${i + 1}</b> · ${esc(state)}<br>${esc(s.scene || "")}</p></div>`;
    }).join("");
  }
  function stopShooting() {
    st.running = false; clearInterval(clockT); clearInterval(framesT); frames(); paintButton();
    const el = $d("#dClock"); if (el && st.t0) el.textContent = `${mmss((Date.now() - st.t0) / 1000)} from start to finish`;
  }
  function fail(msg) {
    stopShooting();
    $d("#dHint").hidden = true;
    const f = $d("#dFail");
    f.innerHTML = `<p style="width:100%;text-align:center;color:var(--bad);margin:0 0 6px">${esc(msg || "The film couldn't be finished.")}</p>
      <button class="btn primary" id="dAgain">Try again</button><button class="btn" id="dLook">See what happened in the studio</button><button class="btn" id="dBack">Change the line</button>`;
    f.hidden = false;
    f.querySelector("#dAgain").onclick = () => shoot(st.last);
    f.querySelector("#dLook").onclick = () => { hide(); setView("board"); };
    f.querySelector("#dBack").onclick = () => show("compose");
  }
  async function shoot(o) {
    try {
      const r = await AP.run(o, ui);
      if (!r) {
        const bad = D.querySelector("#dSteps li.bad small");
        fail(bad && bad.textContent ? bad.textContent : "The film couldn't be finished.");
      }
    } catch (e) { fail(e.message || String(e)); }
  }

  // ── the film ────────────────────────────────────────────────────────────
  function finished(final, film, title, reel) {
    stopShooting();
    st.film = { final, film, title, reel };
    const shared = final.share ? `${location.origin}/f/${final.share}` : "";
    $d("#dDone").innerHTML = `
      <div class="screen"><video src="${esc(final.url)}" ${final.thumb_url ? `poster="${esc(final.thumb_url.replace(/\?w=\d+/, "?w=1280"))}"` : ""} controls playsinline autoplay></video></div>
      <h2>${esc(title)}</h2>
      <p class="meta">${final.seconds ? `${Math.round(final.seconds)} seconds · ` : ""}${st.t0 && st.film.film ? $d("#dClock").textContent : "Made with Gobo"}</p>
      <div class="acts">
        <button class="btn primary" id="dShare">${shared ? "Copy the share link" : "Share"}</button>
        <a class="btn" href="${esc(final.url)}?download=1">Download</a>
        <button class="btn" id="dNew">Make another</button>
        <button class="btn" id="dEdit" title="The Board: change any shot, then cut again">Change a shot</button>
        ${film ? `<button class="btn" id="dRegrade" title="The Lab, on the film as shot">Regrade it</button>` : ""}
      </div>
      <div class="sharebox" id="dShareBox" ${shared ? "" : "hidden"}><input readonly id="dShareUrl" value="${esc(shared)}" aria-label="The public link">
        <button class="btn" id="dCopy">Copy</button><button class="btn" id="dUnshare" title="Take the public page down">Unshare</button></div>
      <p class="more">For ComfyUI users: <a href="${API}/reels/${esc(reel)}/comfyui">the whole film as a ComfyUI workflow</a></p>`;
    show("done");
    const R = `/reels/${reel}/takes/${final.id}/share`;
    const box = $d("#dShareBox"), url = $d("#dShareUrl");
    $d("#dShare").onclick = async () => {
      let link = url.value;
      if (!link) {
        const r = await api(R, { method: "POST", json: { title } });
        if (r.error) return toast(r.error, true);
        if (r.take) { final.share = r.take.share; if (S.reel && S.reel.id === reel) mergeTakes([r.take]); }
        link = location.origin + r.url; url.value = link; box.hidden = false; $d("#dShare").textContent = "Copy the share link";
      }
      if (navigator.share && /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)) {
        try { await navigator.share({ title, url: link }); return; } catch (e) { /* closed: copy instead */ }
      }
      copyLink(link);
    };
    $d("#dCopy").onclick = () => copyLink(url.value);
    $d("#dUnshare").onclick = async () => {
      const r = await api(R, { method: "DELETE" });
      if (r.error) return toast(r.error, true);
      final.share = ""; if (S.reel && S.reel.id === reel && r.take) mergeTakes([r.take]);
      url.value = ""; box.hidden = true; $d("#dShare").textContent = "Share";
      toast("Unshared: that link no longer works.");
    };
    $d("#dNew").onclick = () => { show("compose"); line.focus(); line.select(); };
    $d("#dEdit").onclick = async () => {
      if (!S.reel || S.reel.id !== reel) await loadReel(reel);
      hide(); setView("board");
    };
    const rg = $d("#dRegrade");
    if (rg) rg.onclick = async () => {
      if (!S.reel || S.reel.id !== reel) await loadReel(reel);
      hide(); setView("shot"); select(film.id);
      const l = document.getElementById("labBtn");
      if (l) { l.click(); const t = document.querySelector('#lab [data-tab="grade"]'); if (t) t.click(); }
    };
  }

  // ── your films: the latest cut of each reel, graded if it was ──────────
  async function yourFilms() {
    const r = await api("/library?kind=video&limit=200");
    const seen = new Set(), out = [];
    for (const t of (r.items || []).slice().sort((a, b) => (a.id < b.id ? 1 : -1))) {
      if (!["grade", "stitch"].includes(t.action) || seen.has(t.reel)) continue;
      seen.add(t.reel); out.push(t);
      if (out.length >= 6) break;
    }
    $d("#dFilms").hidden = !out.length;
    $d("#dFilmGrid").innerHTML = out.map((t, i) => `<button class="fcard" data-i="${i}">
      ${t.thumb_url ? `<img src="${esc(t.thumb_url)}" alt="" loading="lazy">` : ""}${t.share ? "<i>Shared</i>" : ""}<span>${esc(t.reel_name || t.label || "A film")}</span></button>`).join("");
    D.querySelectorAll(".fcard").forEach(b => b.onclick = () => {
      const t = out[+b.dataset.i];
      st.t0 = 0;
      finished(t, null, t.reel_name || t.label || "Your film", t.reel);
    });
  }

  // ── views, the header and the studio's way back ─────────────────────────
  function show(view) {
    D.classList.remove("off");
    ["compose", "shoot", "done"].forEach(v => { $d(`#d${v[0].toUpperCase()}${v.slice(1)}`).hidden = v !== view; });
    if (view !== "done") D.querySelectorAll("#dDone video").forEach(v => v.pause());
    if (view === "compose") { paint(); yourFilms(); }
    D.scrollTop = 0;
  }
  function hide() { D.classList.add("off"); D.querySelectorAll("#dDone video").forEach(v => v.pause()); }
  $d("#dSelf").onclick = hide; $d("#dSelf2").onclick = hide;

  const back = document.createElement("button");
  back.className = "chipbtn"; back.id = "dirBtn"; back.textContent = "🎬 Make a film";
  back.title = "One line in, a finished film out";
  back.onclick = () => show(st.running ? "shoot" : st.film ? "done" : "compose");
  const anchor = document.getElementById("apBtn") || document.getElementById("labBtn") || document.getElementById("credits");
  if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(back, anchor);
  function paintButton() { back.textContent = st.running ? "🎬 Your film · rolling" : "🎬 Make a film"; back.classList.toggle("on", st.running); }

  // the balance, as the studio's header shows it
  const credits = document.getElementById("credits");
  const mirror = () => {
    const c = $d("#dCredits");
    c.textContent = credits ? credits.textContent.trim() : "";
    c.hidden = !c.textContent;
    c.classList.toggle("low", !!(credits && credits.classList.contains("low")));
  };
  if (credits) new MutationObserver(mirror).observe(credits, { childList: true, characterData: true, subtree: true, attributes: true });
  mirror();

  window.director = { show, hide, state: st, looks: LOOKS, quality: QUALITY, models };
  if (DIRECTOR) { show("compose"); setTimeout(() => line.focus(), 50); }
})();
