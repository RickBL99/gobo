// The shot compiler, in Rust, running in the page (studio/static/direction.wasm,
// built from crates/direction by tools/build_wasm.sh).
//
// The studio asks POST {API}/compile for the prompt preview every time a
// control changes. Once the WebAssembly is loaded, those requests are answered
// here, in microseconds and without the network; until then, or if anything
// goes wrong, they go to the server exactly as before, which gives the same
// answer (tests/test_direction_rust.py holds the two to the same bytes).
//
// The same file is loaded by that test under Node: it exports makeCompiler.
(function (root) {
  "use strict";

  function makeCompiler(instance, catalogue) {
    const x = instance.exports;
    const enc = new TextEncoder(), dec = new TextDecoder();
    const send = (obj, fn) => {
      const bytes = enc.encode(JSON.stringify(obj));
      const ptr = x.alloc(bytes.length);
      new Uint8Array(x.memory.buffer, ptr, bytes.length).set(bytes);
      return fn(ptr, bytes.length);                 // the module owns (and frees) the input
    };
    if (send(catalogue, x.load_catalogue) !== 0) throw new Error("direction: catalogue rejected");
    return function compile(body) {
      const packed = send(body, x.compile);
      const ptr = Number(packed >> 32n), len = Number(packed & 0xffffffffn);
      const out = dec.decode(new Uint8Array(x.memory.buffer, ptr, len));
      x.free(ptr, len);
      return JSON.parse(out);
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { makeCompiler };
    return;
  }

  // ── in the page ──────────────────────────────────────────────────────────
  const engine = root.__direction = { ready: false, served: 0, fallbacks: 0 };
  let compile = null;
  const url = (typeof API === "string" ? API : "/studio") + "/compile";

  (async () => {
    try {
      const wasm = await WebAssembly.instantiateStreaming(fetch("/static/direction.wasm"));
      compile = makeCompiler(wasm.instance, CAT);       // CAT: the page's own catalogue
      engine.ready = true;
      const segs = document.getElementById("segs");
      const tag = segs && segs.previousElementSibling && segs.previousElementSibling.classList.contains("sectitle")
        ? segs.previousElementSibling : null;
      if (tag && !tag.querySelector(".rust")) {
        const b = document.createElement("span");
        b.className = "rust";
        b.title = "This preview is compiled in your browser by the studio's Rust shot compiler (WebAssembly)";
        b.textContent = "Rust · live";
        b.style.cssText = "margin-left:8px;font-size:.62rem;font-weight:700;letter-spacing:.06em;" +
          "padding:1px 7px;border-radius:99px;color:#f2c14e;border:1px solid #4d4116;background:#2a2410;vertical-align:1px";
        tag.insertBefore(b, tag.querySelector(".r"));
      }
    } catch (e) {
      console.warn("direction.wasm not loaded; previews use the server", e);
    }
  })();

  const net = root.fetch.bind(root);
  root.fetch = function (input, init) {
    const target = typeof input === "string" ? input : input && input.url;
    if (compile && target === url && init && (init.method || "").toUpperCase() === "POST" && typeof init.body === "string") {
      try {
        const out = compile(JSON.parse(init.body));
        if (!out.error) {
          engine.served++;
          return Promise.resolve(new Response(JSON.stringify(out), {
            status: 200, headers: { "Content-Type": "application/json", "X-Compiled-By": "rust-wasm" } }));
        }
      } catch (e) { /* fall through to the server */ }
      engine.fallbacks++;
    }
    return net(input, init);
  };
})(typeof window !== "undefined" ? window : globalThis);
