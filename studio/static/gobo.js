// Gobo's Rust in the page: studio/static/gobo.wasm, built from crates/gobo-wasm
// (scrim reads, steals and grades a look; flag checks continuity; dingle reads
// strokes as camera moves) by tools/build_wasm.sh.
//
// Pixels never leave the browser: a canvas's ImageData goes into the module's
// memory, Rust reads or grades it there, and a grade is copied back. The same
// file is loaded under Node by tests/test_gobo_wasm.py: it exports makeGobo.
(function (root) {
  "use strict";

  function makeGobo(instance) {
    const x = instance.exports;
    const enc = new TextEncoder(), dec = new TextDecoder();
    const mem = () => new Uint8Array(x.memory.buffer);       // fresh each time: memory can grow
    const put = bytes => { const p = x.alloc(bytes.length); mem().set(bytes, p); return [p, bytes.length]; };
    const json = obj => put(enc.encode(JSON.stringify(obj || {})));
    const text = packed => {
      const p = Number(packed >> 32n), n = Number(packed & 0xffffffffn);
      const s = dec.decode(mem().subarray(p, p + n));
      x.free(p, n);
      return s;
    };
    const obj = packed => JSON.parse(text(packed));
    // a picture is lent, not handed over: the page frees it after the call
    const withPixels = (img, fn) => {
      const [p, n] = put(img.data);
      try { return fn(p, n); } finally { x.free(p, n); }
    };
    return {
      /** What a frame looks like, and what that suggests: {reading, suggest}. */
      read: img => withPixels(img, (p, n) => obj(x.scrim_read(p, n, img.width, img.height))),
      /** Grade ImageData in place with a look ({film, grade, grain, …}); seed moves the grain. */
      grade(img, look, seed) {
        return withPixels(img, (p, n) => {
          const [l, ln] = json(look);
          const r = obj(x.scrim_grade(p, n, img.width, img.height, l, ln, (seed || 0) >>> 0));
          if (!r.error) img.data.set(mem().subarray(p, p + n));
          return r;
        });
      },
      /** A look's numbers, presets resolved. */
      look: l => { const [p, n] = json(l); return obj(x.scrim_look(p, n)); },
      /** The look as a .cube file. */
      cube: (l, size) => { const [p, n] = json(l); return text(x.scrim_cube(p, n, size || 33)); },
      /** Load a .cube for looks to name ({lut3d: {id}}): {id, title, kind, size} or {error}. */
      loadLut: (id, cubeText) => { const [p, n] = json({ id, text: cubeText }); return obj(x.scrim_lut_load(p, n)); },
      /** A scope of ImageData as ImageData, w × h: "waveform", "parade", "vector" or "histogram". */
      scope(img, kind, w, h) {
        const code = { waveform: 0, parade: 1, vector: 2, histogram: 3 }[kind] || 0;
        return withPixels(img, (p, n) => {
          const packed = x.scrim_scope(p, n, img.width, img.height, code, w, h);
          const at = Number(packed >> 32n), len = Number(packed & 0xffffffffn);
          const px = new Uint8ClampedArray(mem().slice(at, at + len));
          const out = typeof ImageData !== "undefined" ? new ImageData(px, w, h) : { width: w, height: h, data: px };
          x.free(at, len);
          return out;
        });
      },
      /** Does B pick up where A stopped? (A's last frame, B's first.) */
      compare(a, b, continuous) {
        return withPixels(a, (pa, na) => withPixels(b, (pb, nb) =>
          obj(x.flag_compare(pa, na, a.width, a.height, pb, nb, b.width, b.height, continuous ? 1 : 0))));
      },
      /** Strokes on a frame → {camera: {move, phrase}, actions, action}. */
      direct: body => { const [p, n] = json(body); return obj(x.dingle_direct(p, n)); },
      about: () => obj(x.gobo_about()),
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { makeGobo };
    return;
  }

  root.gobo = null;
  root.goboReady = (async () => {
    try {
      const wasm = await WebAssembly.instantiateStreaming(fetch("/static/gobo.wasm"));
      root.gobo = makeGobo(wasm.instance);
      return root.gobo;
    } catch (e) {
      console.warn("gobo.wasm didn't load; the Lab is off", e);
      return null;
    }
  })();
})(typeof window !== "undefined" ? window : globalThis);
