//! Gobo's shot compiler, in Rust.
//!
//! Direction choices (framing, camera, light, film, look, grade, composition,
//! movement), the scene, the references and a colour palette become the prompt
//! a cinematographer would write, in labelled pieces so the page can show which
//! control wrote which words.
//!
//! It began as Gobo's Python compiler and replaced it, byte for byte, along
//! with the request handling of `POST /studio/compile`. The vocabulary (every
//! option's id and phrase) lives in `studio/direction.py` and is handed in as
//! the catalogue JSON the page already has, so the words are written in one
//! place. What lives here is the logic: the order the pieces go in and how
//! each one is worded.
//!
//! The page loads this as WebAssembly (`studio/static/direction.js`) and
//! answers its own prompt previews without a trip to the server; the server
//! runs the same .wasm (`studio/rust.py`). `tests/test_direction_rust.py` pins
//! its output for 2,000 generated shots.

use serde_json::{json, Map, Value};
use std::collections::HashMap;

const FRAMING: [&str; 2] = ["shot", "angle"];
const CAMERA: [&str; 4] = ["body", "lens", "focal", "aperture"];
const LOOKS: [&str; 4] = ["light", "film", "look", "grade"];
// A custom palette replaces the preset grade (Comfy sets grade=auto).
const LOOKS_UNDER_A_PALETTE: [&str; 3] = ["light", "film", "look"];

const ENHANCE_STILL: &str = "Cinematic film still, natural texture.";
const ENHANCE_VIDEO: &str = "Cinematic film shot, continuous motion, natural texture.";

/// Every group's options as (id, phrase), from the catalogue.
pub struct Vocabulary {
    groups: HashMap<String, Vec<(String, String)>>,
}

impl Vocabulary {
    pub fn from_catalogue(cat: &Value) -> Result<Self, String> {
        let groups = cat
            .get("groups")
            .and_then(Value::as_object)
            .ok_or("the catalogue has no groups")?;
        let mut out = HashMap::new();
        for (name, g) in groups {
            let opts = g
                .get("options")
                .and_then(Value::as_array)
                .ok_or_else(|| format!("group {name} has no options"))?;
            let mut v = Vec::with_capacity(opts.len());
            for o in opts {
                let id = o.get("id").and_then(Value::as_str).unwrap_or_default();
                let phrase = o.get("phrase").and_then(Value::as_str).unwrap_or_default();
                v.push((id.to_string(), phrase.to_string()));
            }
            out.insert(name.clone(), v);
        }
        for g in FRAMING.iter().chain(&CAMERA).chain(&LOOKS).chain(&["move", "compose"]) {
            if !out.contains_key(*g) {
                return Err(format!("the catalogue is missing group {g}"));
            }
        }
        Ok(Vocabulary { groups: out })
    }

    /// The chosen option's phrase. An unknown or missing id is Auto, which says
    /// nothing: a stale saved shot never breaks, it just says less.
    fn phrase(&self, group: &str, direction: &Map<String, Value>) -> &str {
        let Some(Value::String(want)) = direction.get(group) else { return "" };
        self.groups
            .get(group)
            .and_then(|opts| opts.iter().find(|(id, _)| id == want))
            .map(|(_, p)| p.as_str())
            .unwrap_or("")
    }

    fn phrases(&self, groups: &[&str], direction: &Map<String, Value>) -> Vec<String> {
        groups
            .iter()
            .map(|g| self.phrase(g, direction))
            .filter(|p| !p.is_empty())
            .map(str::to_string)
            .collect()
    }
}

// ── Python's rules, where the output depends on them ────────────────────────

/// `str.isspace()`: Unicode White_Space plus the four information separators
/// (U+001C..U+001F), which Rust's `char::is_whitespace` leaves out.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

/// Python truthiness of a JSON value.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map_or(true, |f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `str(x)` for the scalars a request can carry.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

/// What `for x in value` walks in Python, for `value or []`.
fn py_items(v: Option<&Value>) -> Vec<Value> {
    match v {
        Some(Value::Array(a)) => a.clone(),
        Some(Value::String(s)) => s.chars().map(|c| Value::String(c.to_string())).collect(),
        Some(Value::Object(o)) => o.keys().map(|k| Value::String(k.clone())).collect(),
        _ => Vec::new(),
    }
}

/// `^#[0-9a-fA-F]{6}$` as Python's `re.match` reads it: `$` also matches
/// just before a final newline.
fn hex6(s: &str) -> bool {
    let core = s.strip_suffix('\n').unwrap_or(s);
    core.len() == 7 && core.starts_with('#') && core[1..].bytes().all(|b| b.is_ascii_hexdigit())
}

fn hex6_value(v: &Value) -> bool {
    matches!(v, Value::String(s) if hex6(s))
}

/// "Wide shot, eye-level camera" → "Wide shot, eye-level camera."
fn sentence(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        None => String::new(),
        Some(first) => format!("{}{}.", first.to_uppercase(), chars.as_str()),
    }
}

fn palette_lines(colors: &[Value], main: Option<&Value>) -> Vec<String> {
    let valid: Vec<&str> = colors
        .iter()
        .filter_map(|c| c.as_str().filter(|s| hex6(s)))
        .take(8)
        .collect();
    if valid.is_empty() {
        return Vec::new();
    }
    let mut out = vec![format!(
        "Color grade: dominant colors {}. Keep skin tones believable.",
        valid.join(", ")
    )];
    // isinstance(main, int): a bool is an int in Python
    let main = match main {
        Some(Value::Bool(b)) => Some(*b as i64),
        Some(Value::Number(n)) if n.is_i64() => n.as_i64(),
        Some(Value::Number(n)) if n.is_u64() => Some(i64::MAX),
        _ => None,
    };
    if let Some(m) = main {
        if m >= 0 && (m as usize) < colors.len() && hex6(&py_str(&colors[m as usize])) {
            out.push(format!(
                "Palette priority: {} is the main color; use the other palette colors as supporting \
                 accents. Preserve believable skin tones and readable contrast.",
                py_str(&colors[m as usize])
            ));
        }
    }
    out
}

/// The prompt in labelled pieces: [(text, source)].
pub fn segments(vocab: &Vocabulary, body: &Value) -> Vec<(String, &'static str)> {
    let empty = Map::new();
    let b = body.as_object().unwrap_or(&empty);
    let d = b.get("direction").and_then(Value::as_object).unwrap_or(&empty);

    let scene = match b.get("scene") {
        Some(Value::String(s)) => py_strip(s).to_string(),
        _ => String::new(),
    };
    let enhance = b.get("enhance").map_or(true, truthy);
    let video = matches!(b.get("mode"), Some(Value::String(m)) if m == "video");
    let refs = match b.get("refs") {
        Some(v) if truthy(v) => py_items(Some(v)).len(),
        _ => 0,
    };
    let colors = match b.get("colors") {
        Some(v) if truthy(v) => py_items(Some(v)),
        _ => Vec::new(),
    };
    let ref_names = match b.get("ref_names") {
        Some(v) if truthy(v) => py_items(Some(v)),
        _ => Vec::new(),
    };
    // what each reference is: "cast", "product" or "place" (a kit's own words)
    let ref_kinds = match b.get("ref_kinds") {
        Some(v) if truthy(v) => py_items(Some(v)),
        _ => Vec::new(),
    };

    let frame = vocab.phrases(&FRAMING, d);
    let cam = vocab.phrases(&CAMERA, d);
    let mut look = vocab.phrases(&LOOKS, d);
    let mv = if video { vocab.phrase("move", d) } else { "" };
    let comp = vocab.phrase("compose", d);
    if colors.iter().any(hex6_value) {
        look = vocab.phrases(&LOOKS_UNDER_A_PALETTE, d);
    }

    // a spot's kit names its references, so the model knows which picture is who
    let names: Vec<String> = if refs > 0 {
        ref_names
            .iter()
            .map(|n| py_strip(&py_str(n)).to_string())
            .filter(|n| !n.is_empty())
            .take(refs)
            .collect()
    } else {
        Vec::new()
    };
    let named = refs > 0 && names.len() == refs;

    let mut out: Vec<(String, &'static str)> = Vec::new();
    if !frame.is_empty() {
        out.push((sentence(&frame.join(", ")), "direction"));
    }
    if !scene.is_empty() {
        let ends = scene.ends_with(['.', '!', '?']);
        out.push((if ends { scene.clone() } else { format!("{scene}.") }, "scene"));
    }
    if enhance {
        out.push(((if video { ENHANCE_VIDEO } else { ENHANCE_STILL }).to_string(), "enhance"));
    }
    if !cam.is_empty() {
        out.push((format!("Shot on {}.", cam.join(", ")), "direction"));
    }
    if !mv.is_empty() {
        out.push((sentence(&format!("camera movement: {mv}")), "direction"));
    }
    if !look.is_empty() {
        out.push((sentence(&look.join(", ")), "direction"));
    }
    if !comp.is_empty() {
        out.push((sentence(comp), "direction"));
    }
    // a place's picture is its look, not the shot: copied as it is, every
    // keyframe came out as that picture's framing
    let place = |i: usize| ref_kinds.get(i).map(|k| py_strip(&py_str(k)) == "place").unwrap_or(false);
    let places: Vec<&String> = names.iter().enumerate().filter(|(i, _)| place(*i)).map(|(_, n)| n).collect();
    if named && !places.is_empty() {
        let who: Vec<String> = names
            .iter()
            .enumerate()
            .map(|(i, n)| format!("image {} is {}", i + 1, n))
            .collect();
        let mut t = format!("Reference images: {}.", who.join(", "));
        if places.len() < names.len() {
            t.push_str(" Keep the people and products exactly as shown.");
        }
        for n in places {
            t.push_str(&format!(
                " {n} is the place: match its design, materials and light, but frame the shot as described, not as in its picture."
            ));
        }
        out.push((t, "reference"));
    } else if named {
        let who: Vec<String> = names
            .iter()
            .enumerate()
            .map(|(i, n)| format!("image {} is {}", i + 1, n))
            .collect();
        out.push((
            format!(
                "Reference images: {}. Keep {} exactly as shown.",
                who.join(", "),
                if refs > 1 { "them" } else { "it" }
            ),
            "reference",
        ));
    } else if refs == 1 {
        out.push(("Keep the character from reference image 1.".into(), "reference"));
    } else if refs > 1 {
        out.push(("Keep the characters and products from the reference images.".into(), "reference"));
    }
    for line in palette_lines(&colors, b.get("main_color")) {
        out.push((line, "direction"));
    }
    out
}

/// What `POST /studio/compile` answers: {"prompt", "segments"}.
pub fn compile(vocab: &Vocabulary, body: &Value) -> Value {
    let segs = segments(vocab, body);
    let prompt = segs.iter().map(|(t, _)| t.as_str()).collect::<Vec<_>>().join(" ");
    json!({
        "prompt": prompt,
        "segments": segs.iter().map(|(t, s)| json!({"text": t, "source": s})).collect::<Vec<_>>(),
    })
}

// ── WebAssembly interface ───────────────────────────────────────────────────
//
// No bindgen: the page writes UTF-8 JSON into memory it got from `alloc`,
// calls `load_catalogue` once and `compile` per preview, and reads the answer
// from the (pointer << 32 | length) that `compile` returns, then frees it.

#[cfg(target_arch = "wasm32")]
mod wasm {
    use super::*;
    use std::cell::RefCell;

    thread_local! {
        static VOCAB: RefCell<Option<Vocabulary>> = const { RefCell::new(None) };
    }

    fn input(ptr: *mut u8, len: usize) -> Result<Value, String> {
        // SAFETY: the page wrote `len` bytes at a block it got from `alloc(len)`.
        let bytes = unsafe { Vec::from_raw_parts(ptr, len, len) };
        serde_json::from_slice(&bytes).map_err(|e| e.to_string())
    }

    fn output(v: Value) -> u64 {
        let bytes = v.to_string().into_bytes().into_boxed_slice();
        let len = bytes.len() as u64;
        let ptr = Box::into_raw(bytes) as *mut u8 as u64;
        (ptr << 32) | len
    }

    #[no_mangle]
    pub extern "C" fn alloc(len: usize) -> *mut u8 {
        let mut v = Vec::<u8>::with_capacity(len);
        let p = v.as_mut_ptr();
        std::mem::forget(v);
        p
    }

    /// Frees an answer from `compile`.
    #[no_mangle]
    pub extern "C" fn free(ptr: *mut u8, len: usize) {
        // SAFETY: (ptr, len) is a boxed slice `output` handed out.
        unsafe { drop(Box::from_raw(std::ptr::slice_from_raw_parts_mut(ptr, len))) }
    }

    /// 0 when the catalogue is usable.
    #[no_mangle]
    pub extern "C" fn load_catalogue(ptr: *mut u8, len: usize) -> i32 {
        match input(ptr, len).and_then(|c| Vocabulary::from_catalogue(&c)) {
            Ok(v) => {
                VOCAB.with(|cell| *cell.borrow_mut() = Some(v));
                0
            }
            Err(_) => 1,
        }
    }

    #[no_mangle]
    pub extern "C" fn compile(ptr: *mut u8, len: usize) -> u64 {
        let body = match input(ptr, len) {
            Ok(b) => b,
            Err(e) => return output(json!({"error": e})),
        };
        VOCAB.with(|cell| match cell.borrow().as_ref() {
            Some(v) => output(super::compile(v, &body)),
            None => output(json!({"error": "no catalogue loaded"})),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vocab() -> Vocabulary {
        let opt = |id: &str, p: &str| json!({"id": id, "phrase": p});
        let auto = opt("auto", "");
        let g = |rows: Vec<Value>| {
            let options: Vec<Value> = std::iter::once(auto.clone()).chain(rows).collect();
            json!({ "options": options })
        };
        Vocabulary::from_catalogue(&json!({"groups": {
            "shot": g(vec![opt("wide", "wide shot")]), "angle": g(vec![opt("eye", "eye-level camera")]),
            "body": g(vec![opt("large", "large format cinema camera")]), "lens": g(vec![]),
            "focal": g(vec![opt("50", "50mm")]), "aperture": g(vec![]),
            "light": g(vec![opt("night", "night lit by practical lights")]), "film": g(vec![]),
            "look": g(vec![]), "grade": g(vec![opt("teal", "teal and orange grade")]),
            "move": g(vec![opt("push", "slow push-in toward the subject")]), "compose": g(vec![]),
        }}))
        .unwrap()
    }

    #[test]
    fn a_shot_reads_like_a_dp_wrote_it() {
        let out = compile(&vocab(), &json!({
            "scene": "  A boat ", "mode": "video",
            "direction": {"shot": "wide", "angle": "eye", "body": "large", "focal": "50",
                          "light": "night", "grade": "teal", "move": "push"}}));
        assert_eq!(out["prompt"], "Wide shot, eye-level camera. A boat. Cinematic film shot, continuous motion, \
            natural texture. Shot on large format cinema camera, 50mm. Camera movement: slow push-in toward the \
            subject. Night lit by practical lights, teal and orange grade.");
    }

    #[test]
    fn a_palette_replaces_the_grade_and_names_the_main_colour() {
        let out = compile(&vocab(), &json!({"scene": "x!", "enhance": false,
            "direction": {"grade": "teal", "light": "night"}, "colors": ["#AABBCC", "nope"], "main_color": 0}));
        let p = out["prompt"].as_str().unwrap();
        assert!(p.starts_with("x! Night lit by practical lights. Color grade: dominant colors #AABBCC."));
        assert!(p.contains("Palette priority: #AABBCC is the main color"));
        assert!(!p.contains("teal"));
    }

    #[test]
    fn references_are_named_only_when_every_one_has_a_name() {
        let v = vocab();
        let named = compile(&v, &json!({"refs": [1, 2], "ref_names": ["Maya", " the bottle "], "enhance": 0}));
        assert_eq!(named["prompt"], "Reference images: image 1 is Maya, image 2 is the bottle. Keep them exactly as shown.");
        let short = compile(&v, &json!({"refs": [1, 2], "ref_names": ["Maya", "  "], "enhance": 0}));
        assert_eq!(short["prompt"], "Keep the characters and products from the reference images.");
    }

    #[test]
    fn a_place_is_matched_but_not_copied() {
        let v = vocab();
        let mixed = compile(&v, &json!({"refs": [1, 2], "ref_names": ["Maya", "The café"], "ref_kinds": ["cast", "place"], "enhance": 0}));
        assert_eq!(mixed["prompt"], "Reference images: image 1 is Maya, image 2 is The café. Keep the people and products exactly as shown. \
The café is the place: match its design, materials and light, but frame the shot as described, not as in its picture.");
        let only = compile(&v, &json!({"refs": [1], "ref_names": ["The café"], "ref_kinds": ["place"], "enhance": 0}));
        assert_eq!(only["prompt"], "Reference images: image 1 is The café. \
The café is the place: match its design, materials and light, but frame the shot as described, not as in its picture.");
        // no kinds, or no place among them: the words are what they always were
        let cast = compile(&v, &json!({"refs": [1, 2], "ref_names": ["Maya", "Leo"], "ref_kinds": ["cast", "cast"], "enhance": 0}));
        assert_eq!(cast["prompt"], "Reference images: image 1 is Maya, image 2 is Leo. Keep them exactly as shown.");
    }

    #[test]
    fn python_quirks() {
        assert!(hex6("#a1B2c3\n"));                     // re.match's $ before a final newline
        assert_eq!(py_strip("\u{1f} x \u{3000}"), "x"); // str.strip's whitespace
        assert_eq!(sentence("ßeta"), "SSeta.");
        assert_eq!(py_str(&Value::Null), "None");
    }
}
