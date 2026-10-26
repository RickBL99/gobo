//! gobo.wasm: scrim, flag and dingle for the page (studio/static/gobo.js).
//!
//! The ABI is the shot compiler's: the page asks `alloc(len)` for a block,
//! writes into it, and calls a function. JSON arguments are handed over (the
//! module frees them); pixel buffers are lent (the page frees them with
//! `free(ptr, len)` after reading back what a grade wrote). Answers come back
//! as one u64, pointer in the high half and length in the low, for the page
//! to read and then `free`.
//!
//! WebAssembly only: a native build would export a second `free` beside the C
//! library's, so `cargo test --workspace` sees an empty crate here (its
//! tests are tests/test_gobo_wasm.py, which runs the shipped .wasm).
#![cfg(target_arch = "wasm32")]

use serde_json::{json, Value};
use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::Arc;

thread_local! {
    static LAST: RefCell<Option<(scrim::Look, scrim::Lut)>> = const { RefCell::new(None) };
    // .cube files the page (or the server) has loaded, by id: a look names one
    // ({"lut3d": {"id"}}) instead of carrying its table on every frame
    static CUBES: RefCell<HashMap<String, Arc<scrim::Cube>>> = RefCell::new(HashMap::new());
}

fn look_from(v: &Value) -> Result<scrim::Look, String> {
    CUBES.with(|c| {
        let cubes = c.borrow();
        scrim::Look::from_json_with(v, &|id| cubes.get(id).cloned())
    })
}

fn take_json(ptr: *mut u8, len: usize) -> Result<Value, String> {
    // SAFETY: the page wrote `len` bytes at a block `alloc(len)` gave it, and hands it over.
    let bytes = unsafe { Vec::from_raw_parts(ptr, len, len) };
    serde_json::from_slice(&bytes).map_err(|e| e.to_string())
}

fn lend<'a>(ptr: *mut u8, len: usize) -> &'a mut [u8] {
    // SAFETY: the page owns this block (from `alloc(len)`) and keeps it alive across the call.
    unsafe { std::slice::from_raw_parts_mut(ptr, len) }
}

fn answer(bytes: Vec<u8>) -> u64 {
    let bytes = bytes.into_boxed_slice();
    let len = bytes.len() as u64;
    let ptr = Box::into_raw(bytes) as *mut u8 as u64;
    (ptr << 32) | len
}

fn reply(v: Value) -> u64 {
    answer(v.to_string().into_bytes())
}

fn frame<'a>(px: &'a [u8], w: u32, h: u32) -> scrim::Frame<'a> {
    scrim::Frame { px, w: w as usize, h: h as usize }
}

#[no_mangle]
pub extern "C" fn alloc(len: usize) -> *mut u8 {
    let mut v = Vec::<u8>::with_capacity(len);
    let p = v.as_mut_ptr();
    std::mem::forget(v);
    p
}

#[no_mangle]
pub extern "C" fn free(ptr: *mut u8, len: usize) {
    // SAFETY: (ptr, len) came from `alloc(len)` or an answer.
    unsafe { drop(Box::from_raw(std::ptr::slice_from_raw_parts_mut(ptr, len))) }
}

/// Read a frame: the measurements and what they suggest (direction, the
/// stolen look, the nearest stock and grade, a sentence).
#[no_mangle]
pub extern "C" fn scrim_read(px: *mut u8, len: usize, w: u32, h: u32) -> u64 {
    match scrim::read(&frame(lend(px, len), w, h)) {
        Ok(r) => reply(json!({"reading": r.to_json(), "suggest": scrim::suggest(&r)})),
        Err(e) => reply(json!({"error": e})),
    }
}

/// Grade pixels in place with a look ({"film": "portra", "grade": "teal", "grain": 0.3, …}).
#[no_mangle]
pub extern "C" fn scrim_grade(px: *mut u8, len: usize, w: u32, h: u32, look: *mut u8, look_len: usize, seed: u32) -> u64 {
    let lk = match take_json(look, look_len).and_then(|v| look_from(&v)) {
        Ok(l) => l,
        Err(e) => return reply(json!({"error": e})),
    };
    // one table per look: a clip grading at 24 fps doesn't rebuild it every frame
    LAST.with(|cell| {
        let mut last = cell.borrow_mut();
        if last.as_ref().map_or(true, |(l, _)| *l != lk) {
            *last = Some((lk.clone(), scrim::lut(&lk, 33)));
        }
        let (_, table) = last.as_ref().unwrap();
        scrim::grade_with(lend(px, len), w as usize, h as usize, &lk, table, seed);
    });
    reply(json!({"ok": true}))
}

/// A look resolved to its numbers (presets by name, then overrides).
#[no_mangle]
pub extern "C" fn scrim_look(look: *mut u8, look_len: usize) -> u64 {
    match take_json(look, look_len).and_then(|v| look_from(&v)) {
        Ok(l) => reply(l.to_json()),
        Err(e) => reply(json!({"error": e})),
    }
}

/// The look as a .cube file (text, not JSON). Size 17–65; 33 is standard.
#[no_mangle]
pub extern "C" fn scrim_cube(look: *mut u8, look_len: usize, size: u32) -> u64 {
    let v = take_json(look, look_len).unwrap_or(Value::Null);
    let title = v.get("title").and_then(Value::as_str).unwrap_or("Gobo look").to_string();
    match look_from(&v) {
        Ok(l) => answer(scrim::lut(&l, size as usize).cube(&title).into_bytes()),
        Err(e) => answer(format!("# error: {e}\n").into_bytes()),
    }
}

/// Load a .cube ({"id", "text"}) for looks to name: {id, title, kind, size}.
#[no_mangle]
pub extern "C" fn scrim_lut_load(body: *mut u8, len: usize) -> u64 {
    let v = match take_json(body, len) {
        Ok(v) => v,
        Err(e) => return reply(json!({"error": e})),
    };
    let (Some(id), Some(text)) = (v.get("id").and_then(Value::as_str), v.get("text").and_then(Value::as_str)) else {
        return reply(json!({"error": "a LUT is {id, text}"}));
    };
    match scrim::parse_cube(text) {
        Ok(c) => {
            let out = json!({"id": id, "title": c.title, "kind": c.kind, "size": c.size});
            CUBES.with(|m| m.borrow_mut().insert(id.to_string(), Arc::new(c)));
            reply(out)
        }
        Err(e) => reply(json!({"error": e})),
    }
}

/// A scope of a picture as RGBA pixels, `ow` × `oh`: kind 0 waveform,
/// 1 RGB parade, 2 vectorscope, 3 histogram. (Bytes, not JSON.)
#[no_mangle]
pub extern "C" fn scrim_scope(px: *mut u8, len: usize, w: u32, h: u32, kind: u32, ow: u32, oh: u32) -> u64 {
    let k = scrim::scope::Kind::from_code(kind).unwrap_or(scrim::scope::Kind::Waveform);
    answer(scrim::scope::scope(lend(px, len), w as usize, h as usize, k, ow as usize, oh as usize))
}

/// Does shot B pick up where shot A left off? (A's last frame, B's first.)
#[no_mangle]
pub extern "C" fn flag_compare(a: *mut u8, a_len: usize, aw: u32, ah: u32,
                               b: *mut u8, b_len: usize, bw: u32, bh: u32, continuous: u32) -> u64 {
    let ra = scrim::read(&frame(lend(a, a_len), aw, ah));
    let rb = scrim::read(&frame(lend(b, b_len), bw, bh));
    match (ra, rb) {
        (Ok(ra), Ok(rb)) => reply(flag::compare(&ra, &rb, continuous != 0)),
        (Err(e), _) | (_, Err(e)) => reply(json!({"error": e})),
    }
}

/// Strokes drawn on a frame → the camera move and the action.
#[no_mangle]
pub extern "C" fn dingle_direct(body: *mut u8, len: usize) -> u64 {
    match take_json(body, len) {
        Ok(v) => reply(dingle::direct(&v)),
        Err(e) => reply(json!({"error": e})),
    }
}

/// What this module is, for the page's "Rust · live" badge.
#[no_mangle]
pub extern "C" fn gobo_about() -> u64 {
    reply(json!({"crates": ["scrim", "flag", "dingle"], "version": env!("CARGO_PKG_VERSION")}))
}
