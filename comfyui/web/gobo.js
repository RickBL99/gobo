// Gobo's nodes on the ComfyUI page.
//
// * A workflow Gobo hands over: "Open in ComfyUI" in Gobo opens this page with
//   #gobo=<the workflow, base64url JSON> and it loads here, nodes and all.
// * The Look node behaves as Gobo's Lab does: picking a film stock or a grade
//   fills the sliders with its numbers (from /gobo/look, the same Rust).
import { app } from "../../scripts/app.js";

const SLIDERS = ["exposure", "contrast", "pivot", "saturation", "warmth", "tint", "fade", "white_point",
  "grain", "grain_size", "halation", "vignette"];
const PRESET_OWNED = ["shadows", "highlights", "bleach", "mono"];

function sent() {
  const m = location.hash.match(/[#&]gobo=([^&]+)/);
  if (!m) return null;
  history.replaceState(null, "", location.pathname + location.search);
  // a reload of the same address doesn't open the same workflow a second time
  const seen = (() => { try { return sessionStorage.getItem("gobo.opened"); } catch (e) { return null; } })();
  if (seen === m[1].slice(-64)) return null;
  try { sessionStorage.setItem("gobo.opened", m[1].slice(-64)); } catch (e) { /* then it may open twice */ }
  const b64 = m[1].replace(/-/g, "+").replace(/_/g, "/");
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

app.registerExtension({
  name: "gobo.nodes",
  async setup() {
    let got = null;
    try { got = sent(); } catch (e) { console.error("Gobo: that link didn't read", e); }
    if (!got || !got.workflow) return;
    await app.loadApiJson(got.workflow, got.title || "From Gobo");
    // left to right, the way the picture flows
    try { if (app.graph && app.graph.arrange) app.graph.arrange(80); } catch (e) { /* leave it as loaded */ }
    app.canvas && app.canvas.setDirty(true, true);
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.category !== "Gobo") return;
    const made = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const out = made ? made.apply(this, arguments) : undefined;
      this.color = "#4a3a10"; this.bgcolor = "#2b2310";         // Gobo amber
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
