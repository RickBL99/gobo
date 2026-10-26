//! flag — Gobo's script supervisor. (On set a flag is the black card a grip
//! puts up to keep light off what it shouldn't touch; here it's what goes up
//! when two shots don't belong together.)
//!
//! A generated film breaks where one shot's light, colour or grain doesn't
//! carry into the next: the cut that suddenly turns warm, the extension that
//! comes out a stop brighter. `compare` reads the end of one shot and the
//! start of the next (with scrim) and says, in a supervisor's words, what
//! jumped and by how much. `continuous` is for a shot that is meant to pick
//! up exactly where the last one stopped (an extension, a chained clip): then
//! exposure, white balance and palette must carry, and the picture itself too.
//! A cut to a new setup is allowed all of that (outside to in, wide to
//! insert, daylight to lamplight); what it must keep is the grade: where the
//! blacks sit, how hard the contrast is, how much colour, the grain, black and
//! white or not, and the shape of the frame.

use scrim::color::hue_name;
use scrim::read::aspect_name;
use scrim::Reading;
use serde_json::{json, Value};

pub struct Check {
    pub score: f32,
    pub notes: Vec<String>,
    pub fixes: Vec<String>,
}

fn stops(a: f32, b: f32) -> f32 {
    // Oklab lightness is roughly the cube root of luminance
    3.0 * (b.max(0.02) / a.max(0.02)).log2()
}

/// How far apart two five-colour palettes are: each colour of one to its
/// nearest in the other, weighted by how much of the frame it covers.
fn palette_gap(a: &Reading, b: &Reading) -> f32 {
    let one_way = |x: &Reading, y: &Reading| -> f32 {
        x.palette.iter().map(|(c, share)| {
            let d = y.palette.iter().map(|(d, _)| {
                ((c[0] - d[0]).powi(2) * 0.5 + (c[1] - d[1]).powi(2) + (c[2] - d[2]).powi(2)).sqrt()
            }).fold(f32::MAX, f32::min);
            d * share
        }).sum()
    };
    (one_way(a, b) + one_way(b, a)) / 2.0
}

pub fn check(a: &Reading, b: &Reading, continuous: bool) -> Check {
    let mut notes = Vec::new();
    let mut fixes = Vec::new();
    let mut penalty = 0.0f32;

    let ev = stops(a.l[4], b.l[4]);
    let ev_ok = if continuous { 0.35 } else { 1.6 };
    if ev.abs() > ev_ok {
        let word = if ev > 0.0 { "brighter" } else { "darker" };
        notes.push(format!("exposure jumps {:.1} stops {word}", ev.abs()));
        fixes.push(format!("{} the next shot by {:.1} stops, or relight it to match", if ev > 0.0 { "darken" } else { "brighten" }, ev.abs()));
        penalty += (ev.abs() - ev_ok + 0.05) * 1.2;
    }
    // the grade: where the blacks are printed
    let lift = b.l[0] - a.l[0];
    if lift.abs() > 0.08 && !continuous {
        notes.push(format!("the blacks {}", if lift > 0.0 { "lift (a softer, faded print)" } else { "crush (a harder print)" }));
        fixes.push("match the black level: same film stock and grade on both".into());
        penalty += (lift.abs() - 0.08) * 6.0;
    }
    let (dwarm, dtint) = (b.mean_ab[1] - a.mean_ab[1], b.mean_ab[0] - a.mean_ab[0]);
    if continuous && dwarm.abs() > 0.012 {
        notes.push(format!("white balance turns {}", if dwarm > 0.0 { "warmer" } else { "cooler" }));
        fixes.push(format!("cool/warm the grade back: temperature {:+.2}", -dwarm * 8.0));
        penalty += (dwarm.abs() - 0.012) * 20.0;
    }
    if continuous && dtint.abs() > 0.012 {
        notes.push(format!("colour shifts toward {}", if dtint > 0.0 { "magenta" } else { "green" }));
        penalty += (dtint.abs() - 0.012) * 15.0;
    }
    let sat = (b.chroma + 0.005) / (a.chroma + 0.005);
    let sat_ok = if continuous { 0.72..=1.38 } else { 0.5..=2.0 };
    if !sat_ok.contains(&sat) {
        notes.push(format!("colour gets {} ({:.0}% {})", if sat > 1.0 { "more saturated" } else { "more muted" },
                           (sat - 1.0).abs() * 100.0, if sat > 1.0 { "more" } else { "less" }));
        penalty += ((sat.ln()).abs() - if continuous { 0.32 } else { 0.69 }).max(0.0) * 1.5;
    }
    if a.mono() != b.mono() {
        notes.push(if b.mono() { "the next shot is black and white".into() } else { "colour comes back in".to_string() });
        penalty += 1.0;
    }
    let contrast = (b.spread() + 0.02) / (a.spread() + 0.02);
    let con_ok = if continuous { 0.78..=1.28 } else { 0.65..=1.55 };
    if !con_ok.contains(&contrast) {
        notes.push(format!("contrast {}", if contrast > 1.0 { "hardens" } else { "flattens" }));
        penalty += ((contrast.ln()).abs() - if continuous { 0.25 } else { 0.43 }).max(0.0) * 1.5;
    }
    let (sh_a, sh_b) = (hue_name(a.shadows[0], a.shadows[1]), hue_name(b.shadows[0], b.shadows[1]));
    if continuous && sh_a != sh_b && sh_a != "neutral" && sh_b != "neutral" {
        notes.push(format!("shadows go from {sh_a} to {sh_b}"));
        penalty += 0.3;
    }
    if (b.grain - a.grain).abs() > 0.008 {
        notes.push(format!("grain {}", if b.grain > a.grain { "gets heavier" } else { "disappears" }));
        penalty += 0.25;
    }
    if a.letterbox != b.letterbox || (a.aspect / b.aspect - 1.0).abs() > 0.04 {
        notes.push(format!("the frame changes shape ({} → {})",
                           aspect_name(a.aspect).unwrap_or("?"), aspect_name(b.aspect).unwrap_or("?")));
        fixes.push("render the next shot at the same aspect ratio".into());
        penalty += 1.0;
    }
    let gap = palette_gap(a, b);
    if continuous && gap > 0.06 {
        notes.push("the palette changes".into());
        penalty += (gap - 0.06) * 6.0;
    }
    if continuous {
        let bits = (a.dhash ^ b.dhash).count_ones() + (a.vhash ^ b.vhash).count_ones();
        if bits > 22 {
            notes.push(format!("the picture itself jumps ({bits}/128 of the layout differs): not a clean continuation"));
            fixes.push("start the next shot from this shot's last frame".into());
            penalty += (bits as f32 - 22.0) / 24.0;
        }
        let (dx, dy) = (b.subject[0] - a.subject[0], b.subject[1] - a.subject[1]);
        if (dx * dx + dy * dy).sqrt() > 0.2 {
            notes.push("the subject jumps across the frame".into());
            penalty += 0.4;
        }
    }
    Check { score: (-penalty).exp(), notes, fixes }
}

pub fn verdict(score: f32) -> &'static str {
    match score {
        s if s > 0.8 => "match",
        s if s > 0.45 => "drift",
        _ => "jump",
    }
}

pub fn compare(a: &Reading, b: &Reading, continuous: bool) -> Value {
    let c = check(a, b, continuous);
    json!({
        "score": (c.score * 100.0).round() / 100.0,
        "verdict": verdict(c.score),
        "notes": c.notes,
        "fixes": c.fixes,
        "continuous": continuous,
    })
}

/// A whole sequence: each cut, and the shot whose look sits furthest from
/// the rest (the one to regrade first).
pub fn sequence(shots: &[(Reading, Reading)], continuous: &[bool]) -> Value {
    let cuts: Vec<Value> = shots.windows(2).enumerate().map(|(i, w)| {
        let mut v = compare(&w[0].1, &w[1].0, *continuous.get(i).unwrap_or(&false));
        v["from"] = json!(i);
        v["to"] = json!(i + 1);
        v
    }).collect();
    let far: Vec<f32> = (0..shots.len()).map(|i| {
        (0..shots.len()).filter(|&j| j != i).map(|j| 1.0 - check(&shots[j].0, &shots[i].0, false).score).sum::<f32>()
            / (shots.len().max(2) - 1) as f32
    }).collect();
    json!({"cuts": cuts, "outlier": outlier(&far)})
}

/// The shot whose look sits clearly further from the rest than they do from
/// each other: at least 0.1 away on average, and 2.5× the typical shot.
pub fn outlier(far: &[f32]) -> Option<usize> {
    if far.len() < 3 {
        return None;
    }
    let (i, d) = far.iter().copied().enumerate().max_by(|a, b| a.1.total_cmp(&b.1))?;
    let mut rest: Vec<f32> = far.iter().copied().enumerate().filter(|(j, _)| *j != i).map(|(_, v)| v).collect();
    rest.sort_by(f32::total_cmp);
    let typical = rest[rest.len() / 2];
    (d > 0.1 && d > 2.5 * typical.max(0.01)).then_some(i)
}

#[cfg(test)]
mod tests {
    use super::*;
    use scrim::{grade_rgba, read, Frame, Look};

    fn card(w: usize, h: usize, shift: u8) -> Vec<u8> {
        let mut v = Vec::with_capacity(w * h * 4);
        for y in 0..h {
            for x in 0..w {
                let l = 0.05 + 0.9 * y as f32 / h as f32;
                let t = (x + shift as usize) as f32 / w as f32 * 6.283;
                let c = |p: f32| ((l * (1.0 + 0.2 * (t + p).sin())).clamp(0.0, 1.0) * 255.0) as u8;
                v.extend_from_slice(&[c(0.0), c(2.1), c(4.2), 255]);
            }
        }
        v
    }

    fn reading(px: &[u8]) -> Reading {
        read(&Frame { px, w: 320, h: 180 }).unwrap()
    }

    #[test]
    fn the_same_look_matches_and_a_regrade_is_flagged() {
        let a = reading(&card(320, 180, 0));
        let b = reading(&card(320, 180, 4));
        let same = compare(&a, &b, true);
        assert_eq!(same["verdict"], "match", "{same}");

        let mut warm = card(320, 180, 0);
        grade_rgba(&mut warm, 320, 180, &Look { temp: 1.6, exposure: 0.8, ..Look::default() }, 1);
        let c = compare(&a, &reading(&warm), true);
        assert_ne!(c["verdict"], "match", "{c}");
        let notes = c["notes"].to_string();
        assert!(notes.contains("brighter") && notes.contains("warmer"), "{notes}");
    }

    #[test]
    fn a_cut_may_change_the_light_but_not_the_grade() {
        let a = reading(&card(320, 180, 0));
        let mut warmer = card(320, 180, 0);
        grade_rgba(&mut warmer, 320, 180, &Look { temp: 1.6, exposure: 0.8, ..Look::default() }, 1);
        assert_eq!(compare(&a, &reading(&warmer), false)["verdict"], "match", "lamplight after daylight is a cut, not a mistake");
        let mut faded = card(320, 180, 0);
        grade_rgba(&mut faded, 320, 180, &Look { fade: 0.2, sat: 0.4, ..Look::default() }, 1);
        let c = compare(&a, &reading(&faded), false);
        assert_ne!(c["verdict"], "match", "{c}");
        assert!(c["notes"].to_string().contains("blacks lift"), "{c}");
        let mut mono = card(320, 180, 0);
        grade_rgba(&mut mono, 320, 180, &scrim::grade_preset("noir").unwrap(), 1);
        assert_ne!(compare(&a, &reading(&mono), false)["verdict"], "match");
    }

    #[test]
    fn a_continuation_must_continue_the_picture() {
        let a = reading(&card(320, 180, 0));
        let rows: Vec<Vec<u8>> = card(320, 180, 0).chunks(320 * 4).map(|r| r.to_vec()).collect();
        let flipped: Vec<u8> = rows.into_iter().rev().flatten().collect(); // same look, upside down
        let b = reading(&flipped);
        assert_eq!(compare(&a, &b, false)["verdict"], "match");
        assert_ne!(compare(&a, &b, true)["verdict"], "match");
    }

    #[test]
    fn the_odd_one_out() {
        let n = reading(&card(320, 180, 0));
        let mut cold = card(320, 180, 0);
        grade_rgba(&mut cold, 320, 180, &Look { temp: -1.2, sat: 0.4, ..Look::default() }, 1);
        let o = reading(&cold);
        let seq = vec![(n.clone(), n.clone()), (n.clone(), n.clone()), (o.clone(), o), (n.clone(), n)];
        let s = sequence(&seq, &[false, false, false]);
        assert_eq!(s["outlier"], 2, "{s}");
        assert_eq!(s["cuts"].as_array().unwrap().len(), 3);
    }
}
