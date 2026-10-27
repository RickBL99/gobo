//! One shot brought to another's colour: the shot match a colourist does
//! before a scene is graded, so a take and its extension (or two angles of
//! one scene) take the same grade the same way.
//!
//! It is an ASC CDL, the form a colourist's shot match travels in: per
//! channel, slope, offset and power that put the take's tones where the
//! reference has them (its darkest, its mid-tones and its brightest, read off
//! the spread of every channel), then the saturation that makes it as
//! colourful. The two needn't show the same picture: it's their tones that
//! are matched, not their pixels.

use crate::color::{oklab, to_linear, Rgb};
use crate::colour::Cdl;

/// A take matched to a reference: the CDL, and how far apart they were
/// before and after it (mean ΔE in Oklab ×100 across their tones).
pub struct Matched {
    pub cdl: Cdl,
    pub before: f32,
    pub after: f32,
}

/// Where along each channel's spread the two are lined up.
const Q: [f32; 13] = [0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98];
const MAX_PIXELS: usize = 200_000;

/// RGBA bytes as display colours, every pixel or an even sample of them.
fn colours(px: &[u8]) -> Vec<Rgb> {
    let n = px.len() / 4;
    let step = n.div_ceil(MAX_PIXELS).max(1);
    (0..n)
        .step_by(step)
        .filter(|i| px[i * 4 + 3] > 0)
        .map(|i| [px[i * 4] as f32 / 255.0, px[i * 4 + 1] as f32 / 255.0, px[i * 4 + 2] as f32 / 255.0])
        .collect()
}

fn spread(cs: &[Rgb]) -> [[f32; 13]; 3] {
    let mut out = [[0.0; 13]; 3];
    for (k, row) in out.iter_mut().enumerate() {
        let mut v: Vec<f32> = cs.iter().map(|c| c[k]).collect();
        v.sort_by(f32::total_cmp);
        for (q, cell) in Q.iter().zip(row.iter_mut()) {
            *cell = v[((v.len() - 1) as f32 * q).round() as usize];
        }
    }
    out
}

fn colourful(cs: &[Rgb]) -> f32 {
    let n = cs.len().max(1) as f32;
    cs.iter()
        .map(|c| {
            let y = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
            ((c[0] - y).powi(2) + (c[1] - y).powi(2) + (c[2] - y).powi(2)).sqrt()
        })
        .sum::<f32>()
        / n
}

/// Slope, offset and power that carry one channel's tones `t` onto `r`.
fn channel(t: &[f32; 13], r: &[f32; 13]) -> (f32, f32, f32) {
    let off = |s: f32, o: f32, p: f32| {
        t.iter().zip(r).map(|(a, b)| ((a * s + o).clamp(0.0, 1.0).powf(p) - b).powi(2)).sum::<f32>()
    };
    // for a power, the slope and offset are a straight line through r^(1/p)
    let line = |p: f32| {
        let y: Vec<f32> = r.iter().map(|v| v.max(1e-4).powf(1.0 / p)).collect();
        let n = t.len() as f32;
        let (mt, my) = (t.iter().sum::<f32>() / n, y.iter().sum::<f32>() / n);
        let var: f32 = t.iter().map(|a| (a - mt).powi(2)).sum();
        let cov: f32 = t.iter().zip(&y).map(|(a, b)| (a - mt) * (b - my)).sum();
        let s = if var > 1e-6 { (cov / var).clamp(0.25, 4.0) } else { 1.0 };
        (s, (my - s * mt).clamp(-0.5, 0.5))
    };
    let try_power = |p: f32, best: &mut (f32, f32, f32, f32)| {
        let (s, o) = line(p);
        let e = off(s, o, p);
        if e < best.3 {
            *best = (s, o, p, e);
        }
    };
    let mut best = (1.0, 0.0, 1.0, off(1.0, 0.0, 1.0));
    for i in 0..=40 {
        try_power(0.5 * 4f32.powf(i as f32 / 40.0), &mut best); // 0.5 to 2, evenly in log
    }
    let p0 = best.2;
    for i in 0..=40 {
        try_power(p0 * 1.035f32.powf(i as f32 / 20.0 - 1.0), &mut best); // and finer round the best
    }
    (best.0, best.1, best.2)
}

fn tones_apart(a: &[[f32; 13]; 3], b: &[[f32; 13]; 3], cdl: Option<&Cdl>) -> f32 {
    let lab = |c: Rgb| oklab(c.map(|v| to_linear(v.clamp(0.0, 1.0))));
    (0..Q.len())
        .map(|i| {
            let mut x = [a[0][i], a[1][i], a[2][i]];
            if let Some(c) = cdl {
                x = c.apply(x);
            }
            let (p, q) = (lab(x), lab([b[0][i], b[1][i], b[2][i]]));
            ((p[0] - q[0]).powi(2) + (p[1] - q[1]).powi(2) + (p[2] - q[2]).powi(2)).sqrt() * 100.0
        })
        .sum::<f32>()
        / Q.len() as f32
}

/// Match `take` (RGBA bytes: one frame, or several laid end to end) to
/// `reference`. None if either has no pixels.
pub fn match_shot(take: &[u8], reference: &[u8]) -> Option<Matched> {
    let (tc, rc) = (colours(take), colours(reference));
    if tc.is_empty() || rc.is_empty() {
        return None;
    }
    let (ts, rs) = (spread(&tc), spread(&rc));
    let mut cdl = Cdl::default();
    for k in 0..3 {
        let (s, o, p) = channel(&ts[k], &rs[k]);
        cdl.slope[k] = s;
        cdl.offset[k] = o;
        cdl.power[k] = p;
    }
    // then as colourful as the reference, measured after the tones are matched
    let sample: Vec<Rgb> = tc.iter().step_by((tc.len() / 20_000).max(1)).map(|c| cdl.apply(*c)).collect();
    let (now, want) = (colourful(&sample), colourful(&rc));
    cdl.sat = if now > 0.005 { (want / now).clamp(0.5, 1.8) } else { 1.0 };
    let before = tones_apart(&ts, &rs, None);
    let after = tones_apart(&ts, &rs, Some(&Cdl { sat: 1.0, ..cdl.clone() }));
    Some(Matched { cdl, before, after })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A scene: a gradient and colour patches, as RGBA bytes.
    fn scene(f: impl Fn(Rgb) -> Rgb) -> Vec<u8> {
        let mut out = Vec::new();
        for y in 0..60 {
            for x in 0..120 {
                let base = [x as f32 / 119.0, y as f32 / 59.0, ((x + y) % 40) as f32 / 39.0];
                let c = f([0.15 + 0.7 * base[0], 0.1 + 0.6 * (base[0] * 0.5 + base[1] * 0.5), 0.2 + 0.5 * base[2]]);
                out.extend(c.map(|v| (v.clamp(0.0, 1.0) * 255.0).round() as u8));
                out.push(255);
            }
        }
        out
    }

    #[test]
    fn a_take_off_in_colour_comes_back_to_the_reference() {
        let reference = scene(|c| c);
        // the same scene darker, bluer, flatter and greyer, as an extension drifts
        let take = scene(|c| {
            let d = [(c[0] * 0.82 + 0.02).powf(1.12), (c[1] * 0.9).powf(1.05), (c[2] * 1.05 + 0.04).powf(0.95)];
            let y = 0.2126 * d[0] + 0.7152 * d[1] + 0.0722 * d[2];
            d.map(|v| y + 0.8 * (v - y))
        });
        let m = match_shot(&take, &reference).unwrap();
        assert!(m.before > 3.0 && m.after < m.before / 3.0, "{} → {}", m.before, m.after);
        // pixel by pixel, the matched take is the reference to within a few levels
        let worst = take
            .chunks(4)
            .zip(reference.chunks(4))
            .map(|(a, b)| {
                let c = m.cdl.apply([a[0], a[1], a[2]].map(|v| v as f32 / 255.0));
                (0..3).map(|k| (c[k] - b[k] as f32 / 255.0).abs()).fold(0.0, f32::max)
            })
            .sum::<f32>()
            / (take.len() / 4) as f32;
        assert!(worst < 0.03, "mean worst channel {worst}");
        assert!(m.cdl.sat > 1.05, "the greyer take is made as colourful: {}", m.cdl.sat);
    }

    #[test]
    fn a_take_that_already_matches_is_left_alone() {
        let a = scene(|c| c);
        let m = match_shot(&a, &a).unwrap();
        let c = &m.cdl;
        assert!(c.slope.iter().all(|s| (s - 1.0).abs() < 0.03) && c.offset.iter().all(|o| o.abs() < 0.02), "{c:?}");
        assert!(c.power.iter().all(|p| (p - 1.0).abs() < 0.05) && (c.sat - 1.0).abs() < 0.03, "{c:?}");
        assert!(m.before < 0.01 && m.after < 0.5);
        assert!(match_shot(&[], &a).is_none());
    }
}
