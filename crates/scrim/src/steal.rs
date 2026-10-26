//! Steal the look: a frame's reading turned back into settings. The grade
//! itself (a Look that puts the same tone and colour on any picture, so it
//! exports as a .cube), the nearest of the studio's film stocks and grades,
//! and what the frame says about light, focus, framing and shape, as the
//! studio's own direction ids, with the reason for each.

use crate::color::hue_name;
use crate::look::{compose, film, grade, Look, FILMS, GRADES};
use crate::read::{aspect_name, Reading};
use serde_json::{json, Map, Value};

/// A look that would put this frame's tone and colour on a neutral picture.
pub fn stolen(r: &Reading) -> Look {
    let mut l = Look::default();
    let mono = r.mono();
    l.fade = (r.l[0] - 0.11).clamp(0.0, 0.3);   // a well-exposed frame's 1st percentile sits near 0.11
    l.white = (r.l[8] + 0.02).clamp(0.8, 1.0);
    let range = (l.white - l.fade).max(0.3);
    l.contrast = (r.spread() / 0.50 / range).clamp(0.6, 1.7);
    l.sat = if mono { 0.0 } else { (r.chroma / 0.05).clamp(0.2, 1.8) };
    if mono {
        l.mono = Some([0.2126, 0.7152, 0.0722]);
    }
    let push = |ab: [f32; 2]| [ab[0].clamp(-0.05, 0.05), ab[1].clamp(-0.05, 0.05)];
    if !mono {
        l.shadows = push(r.shadows);
        l.highlights = push(r.highlights);
    }
    l.grain = ((r.grain - 0.003) / 0.02).clamp(0.0, 1.0);
    l.vignette = if r.vignette < 0.88 { ((0.88 - r.vignette) * 1.5).clamp(0.0, 0.6) } else { 0.0 };
    l
}

const WEIGHTS: [(f32, f32); 9] = [
    // (scale, weight) per signature element: black, white, contrast, colour,
    // shadow a/b, highlight a/b, grain. Contrast and colour depend on what's
    // in the picture as much as on the grade, so the toning counts for more;
    // and few frames reach pure white, so a stock's soft shoulder counts least.
    (0.05, 1.2), (0.06, 0.25), (0.25, 0.7), (0.3, 0.7), (0.012, 1.3), (0.012, 1.3), (0.012, 1.3), (0.012, 1.3),
    (0.3, 1.0),
];

fn distance(a: &[f32; 9], b: &[f32; 9]) -> f32 {
    a.iter().zip(b).zip(WEIGHTS).map(|((x, y), (s, w))| w * ((x - y) / s).powi(2)).sum()
}

/// The studio's stock + grade pairs nearest the stolen look, best first.
pub fn nearest(look: &Look, n: usize) -> Vec<(&'static str, &'static str, f32)> {
    let want = look.signature();
    let mut all: Vec<(&str, &str, f32)> = Vec::with_capacity(FILMS.len() * GRADES.len());
    for f in FILMS {
        for g in GRADES {
            let sig = compose(&film(f).unwrap(), &grade(g).unwrap()).signature();
            all.push((f, g, distance(&want, &sig)));
        }
    }
    all.sort_by(|a, b| a.2.total_cmp(&b.2));
    all.truncate(n);
    all
}

/// Light, from tone and colour: (direction id, why). None when nothing fits.
fn light(r: &Reading) -> Option<(&'static str, String)> {
    let (key, spread) = (r.l[4], r.spread());
    let warm = r.mean_ab[1];
    let mut best: Option<(&str, f32, String)> = None;
    let mut consider = |id: &'static str, score: f32, why: String| {
        if score > 0.0 && best.as_ref().map_or(true, |b| score > b.1) {
            best = Some((id, score, why));
        }
    };
    let neonish = r.palette.iter().any(|(c, s)| {
        let ch = (c[1] * c[1] + c[2] * c[2]).sqrt();
        let n = hue_name(c[1], c[2]);
        *s > 0.05 && ch > 0.11 && matches!(n, "magenta" | "pink" | "purple" | "teal" | "blue")
    });
    if r.crushed > 0.25 && r.clipped > 0.04 && key < 0.45 {
        consider("silhouette", 3.0, "deep blacks against a bright background".into());
    }
    if key < 0.45 && neonish {
        consider("neon", 2.6, "a dark frame lit by saturated magenta and cyan".into());
    }
    if key < 0.36 && warm > 0.035 {
        consider("candle", 2.2 + warm * 10.0, "dark and very warm".into());
    }
    if key < 0.38 && warm < -0.015 && r.chroma < 0.045 {
        consider("moon", 2.0, "dark, cool and quiet".into());
    }
    if key < 0.4 && r.chroma_p95 > 0.08 && !neonish {
        consider("night", 1.8, "a dark frame with bright coloured practicals".into());
    }
    if key < 0.42 && spread > 0.42 {
        consider("lowkey", 1.6, format!("low key with {:.0}% of the frame in shadow", r.crushed * 100.0));
    }
    if r.side.abs() > 0.12 && key > 0.3 {
        let s = if r.side > 0.0 { "right" } else { "left" };
        consider("window", 1.5 + r.side.abs() * 4.0, format!("soft light falling from the {s}"));
    }
    if warm > 0.02 && (0.45..0.82).contains(&key) && r.highlights[1] > 0.015 {
        consider("golden", 1.4 + warm * 20.0, "warm, low-angle light in the highlights".into());
    }
    if warm < -0.015 && key < 0.62 {
        consider("blue", 1.3 - warm * 20.0, "cool twilight".into());
    }
    if r.l[0] > 0.22 && spread < 0.35 && key > 0.55 {
        consider("fog", 1.4, "lifted blacks and little contrast, as if through haze".into());
    }
    if spread > 0.55 && key > 0.6 && r.clipped > 0.03 {
        consider("noon", 1.2, "hard, bright and contrasty".into());
    }
    if spread < 0.38 && warm.abs() < 0.015 && (0.5..0.82).contains(&key) {
        consider("overcast", 1.0, "soft, even and neutral".into());
    }
    best.map(|(id, _, why)| (id, why))
}

pub fn suggest(r: &Reading) -> Value {
    let look = stolen(r);
    let near = nearest(&look, 3);
    let mut dir = Map::new();
    let mut why = Map::new();
    let (f, g, _) = near[0];
    dir.insert("film".into(), json!(f));
    dir.insert("grade".into(), json!(g));
    if let Some((id, reason)) = light(r) {
        dir.insert("light".into(), json!(id));
        why.insert("light".into(), json!(reason));
    }
    // focus: a sharp middle against soft edges is a fast lens
    let ratio = if r.sharp_edge > 1e-4 { r.sharp_center / r.sharp_edge } else { 1.0 };
    let aperture = if ratio > 2.6 { "1.4" } else if ratio > 1.7 { "2.8" } else if r.sharp_edge > 0.02 { "8" } else { "4" };
    dir.insert("aperture".into(), json!(aperture));
    why.insert("aperture".into(), json!(match aperture {
        "1.4" => "the subject is sharp and the edges melt away: very shallow focus",
        "2.8" => "the background falls off: shallow focus",
        "8" => "sharp from front to back: deep focus",
        _ => "moderate depth of field",
    }));
    // framing
    let [sx, sy] = r.subject;
    let compose_id = if r.calm[0] > 0.55 && sx > 0.55 || r.calm[1] > 0.55 && sx < 0.45 {
        "space"
    } else if (sx - 0.5).abs() < 0.07 && (sy - 0.5).abs() < 0.12 {
        "center"
    } else if (sx - 1.0 / 3.0).abs() < 0.09 || (sx - 2.0 / 3.0).abs() < 0.09 {
        "thirds"
    } else {
        "auto"
    };
    if compose_id != "auto" {
        dir.insert("compose".into(), json!(compose_id));
        why.insert("compose".into(), json!(format!("the subject sits at {:.0}% across, {:.0}% down", sx * 100.0, sy * 100.0)));
    }
    // shape: a scope letterbox is an anamorphic tell
    let shape = aspect_name(r.aspect);
    let studio_aspect = match shape {
        Some("2.76:1" | "2.39:1" | "2:1") => Some("21:9"),
        Some("1.85:1" | "16:9") => Some("16:9"),
        Some("3:2") => Some("3:2"),
        Some("4:3") => Some("4:3"),
        Some("1:1") => Some("1:1"),
        Some("4:5") => Some("4:5"),
        Some("9:16") => Some("9:16"),
        _ => None,
    };
    if r.letterbox && matches!(shape, Some("2.76:1" | "2.39:1")) {
        dir.insert("lens".into(), json!("anamorphic"));
        why.insert("lens".into(), json!("framed for scope, the anamorphic shape"));
    }
    if r.grain > 0.012 && !matches!(f, "clean") {
        why.insert("film".into(), json!(format!("visible grain ({:.3}) and the tone of {f}", r.grain)));
    } else {
        why.insert("film".into(), json!(format!("tone and colour closest to {f}")));
    }
    why.insert("grade".into(), json!(format!(
        "{} shadows, {} highlights",
        hue_name(r.shadows[0], r.shadows[1]),
        hue_name(r.highlights[0], r.highlights[1])
    )));
    json!({
        "direction": dir,
        "why": why,
        "aspect": studio_aspect,
        "look": look.to_json(),
        "nearest": near.iter().map(|(f, g, d)| json!({"film": f, "grade": g, "fit": ((-d / 6.0).exp() * 100.0).round() / 100.0}))
            .collect::<Vec<_>>(),
        "phrase": phrase(r),
    })
}

/// The look in a sentence, the way a colourist would brief it.
pub fn phrase(r: &Reading) -> String {
    let mut bits: Vec<String> = Vec::new();
    bits.push(match (r.key(), r.spread()) {
        ("low", s) if s > 0.45 => "low-key and contrasty".into(),
        ("low", _) => "low-key and moody".into(),
        ("high", s) if s < 0.35 => "high-key and airy".into(),
        ("high", _) => "bright and crisp".into(),
        (_, s) if s > 0.6 => "punchy mid-tones".into(),
        (_, s) if s < 0.35 => "soft, flat contrast".into(),
        _ => "natural contrast".into(),
    });
    if r.mono() {
        bits.push("black and white".into());
    } else {
        let (sh, hl) = (hue_name(r.shadows[0], r.shadows[1]), hue_name(r.highlights[0], r.highlights[1]));
        if sh != "neutral" && hl != "neutral" && sh != hl {
            bits.push(format!("{sh} shadows and {hl} highlights"));
        } else if sh != "neutral" {
            bits.push(format!("{sh} in the shadows"));
        } else if hl != "neutral" {
            bits.push(format!("{hl} highlights"));
        }
        bits.push(match r.chroma {
            c if c < 0.03 => "muted colour".into(),
            c if c > 0.09 => "saturated colour".into(),
            _ => "restrained colour".into(),
        });
    }
    if r.l[0] > 0.22 {
        bits.push("faded, lifted blacks".into());
    } else if r.crushed > 0.2 {
        bits.push("crushed blacks".into());
    }
    let ratio = if r.sharp_edge > 1e-4 { r.sharp_center / r.sharp_edge } else { 1.0 };
    if ratio > 1.7 {
        bits.push("shallow depth of field".into());
    }
    if r.grain > 0.02 {
        bits.push("heavy film grain".into());
    } else if r.grain > 0.01 {
        bits.push("fine grain".into());
    }
    if r.vignette < 0.75 {
        bits.push("a dark vignette".into());
    }
    if r.letterbox {
        if let Some(a) = aspect_name(r.aspect) {
            bits.push(format!("letterboxed {a}"));
        }
    }
    let mut s = bits.join(", ");
    if let Some(f) = s.get_mut(0..1) {
        f.make_ascii_uppercase();
    }
    s + "."
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::grade::grade_rgba;
    use crate::read::{read, Frame};

    /// A photo-like test card: gentle colour across, near-black to near-white down.
    fn card(w: usize, h: usize) -> Vec<u8> {
        let mut v = Vec::with_capacity(w * h * 4);
        for y in 0..h {
            for x in 0..w {
                let t = x as f32 / w as f32 * 6.283;
                let l = 0.03 + 0.94 * y as f32 / h as f32;
                let c = |p: f32| ((l * (1.0 + 0.22 * (t + p).sin())).clamp(0.0, 1.0) * 255.0) as u8;
                v.extend_from_slice(&[c(0.0), c(2.1), c(4.2), 255]);
            }
        }
        v
    }

    fn read_graded(f: &str, g: &str) -> (Reading, Value) {
        let mut px = card(480, 270);
        let look = compose(&film(f).unwrap(), &grade(g).unwrap());
        grade_rgba(&mut px, 480, 270, &Look { grain: 0.0, halation: 0.0, vignette: 0.0, ..look }, 1);
        let r = read(&Frame { px: &px, w: 480, h: 270 }).unwrap();
        let s = suggest(&r);
        (r, s)
    }

    fn top(s: &Value) -> Vec<(String, String)> {
        s["nearest"].as_array().unwrap().iter()
            .map(|n| (n["film"].as_str().unwrap().into(), n["grade"].as_str().unwrap().into())).collect()
    }

    #[test]
    fn a_graded_card_is_recognised() {
        let (_, s) = read_graded("bw400", "natural");
        assert!(top(&s).iter().any(|(a, b)| a == "bw400" || b == "noir"), "{:?}", top(&s));
        let (_, s) = read_graded("clean", "teal");
        assert!(top(&s).iter().any(|(_, b)| b == "teal"), "{:?}", top(&s));
        assert!(s["phrase"].as_str().unwrap().contains("teal shadows"), "{}", s["phrase"]);
    }

    #[test]
    fn a_faded_stock_reads_faded() {
        let (clean, _) = read_graded("clean", "natural");
        let (old, s) = read_graded("expired", "natural");
        assert!(old.l[0] > clean.l[0] + 0.05, "blacks lift: {} vs {}", old.l[0], clean.l[0]);
        assert!(old.chroma < clean.chroma, "colour drains");
        let lk = stolen(&old);
        assert!(lk.fade > stolen(&clean).fade && lk.sat < stolen(&clean).sat);
        assert!(s["phrase"].as_str().unwrap().contains("lifted blacks"), "{}", s["phrase"]);
    }
}

#[cfg(test)]
mod probe {
    use super::*;
    use crate::grade::grade_rgba;
    use crate::read::{read, Frame};
    #[test]
    #[ignore]
    fn print_signatures() {
        let card = |w: usize, h: usize| {
            let mut v = Vec::with_capacity(w * h * 4);
            for y in 0..h { for x in 0..w {
                let t = x as f32 / w as f32 * 6.283;
                let l = 0.03 + 0.94 * y as f32 / h as f32;
                let c = |p: f32| ((l * (1.0 + 0.22 * (t + p).sin())).clamp(0.0, 1.0) * 255.0) as u8;
                v.extend_from_slice(&[c(0.0), c(2.1), c(4.2), 255]);
            }}
            v
        };
        for (f, g) in [("clean", "natural"), ("clean", "teal"), ("expired", "natural"), ("clean", "kodachrome")] {
            let mut px = card(480, 270);
            let lk = compose(&film(f).unwrap(), &grade(g).unwrap());
            grade_rgba(&mut px, 480, 270, &Look { grain: 0.0, halation: 0.0, vignette: 0.0, ..lk.clone() }, 1);
            let r = read(&Frame { px: &px, w: 480, h: 270 }).unwrap();
            let st = stolen(&r);
            println!("{f}+{g}: preset sig {:?}", lk.signature().map(|v| (v * 1000.0).round() / 1000.0));
            println!("   stolen sig {:?}", st.signature().map(|v| (v * 1000.0).round() / 1000.0));
            println!("   p1 {:.3} p50 {:.3} p99 {:.3} spread {:.3} chroma {:.3} sh {:?} hi {:?}", r.l[0], r.l[4], r.l[8], r.spread(), r.chroma, r.shadows, r.highlights);
        }
    }
}
