//! Reading a frame: what a colourist and a DP would say about it before
//! anything else. Tone (where black and white sit, how contrasty, low or high
//! key), colour (how much, which way, what's pushed into the shadows and the
//! highlights), the five colours it's made of, the letterbox it's framed in,
//! how sharp it is and where (shallow focus), how much grain, the vignette,
//! which side the light comes from, where the subject sits and where the
//! empty space is. And a fingerprint for telling whether two frames match.

use crate::color::{chroma, hex, hue_name, oklab, Decode, Lab};
use serde_json::{json, Value};

/// An RGBA8 picture, as a canvas hands it over.
pub struct Frame<'a> {
    pub px: &'a [u8],
    pub w: usize,
    pub h: usize,
}

#[derive(Clone, Debug)]
pub struct Reading {
    pub w: usize,
    pub h: usize,
    /// the picture inside any letterbox or pillarbox, as fractions of the frame
    pub active: [f32; 4],
    pub letterbox: bool,
    pub aspect: f32,
    /// lightness percentiles 1, 5, 10, 25, 50, 75, 90, 95, 99, and the mean
    pub l: [f32; 10],
    pub crushed: f32,
    pub clipped: f32,
    pub chroma: f32,
    pub chroma_p95: f32,
    pub mean_ab: [f32; 2],
    pub shadows: [f32; 2],
    pub highlights: [f32; 2],
    pub palette: Vec<(Lab, f32)>,
    pub sharp: f32,
    pub sharp_center: f32,
    pub sharp_edge: f32,
    pub grain: f32,
    pub vignette: f32,
    /// mean lightness right minus left, and top minus bottom
    pub side: f32,
    pub top: f32,
    pub subject: [f32; 2],
    /// share of calm, empty cells in the left, right, top and bottom halves
    pub calm: [f32; 4],
    /// layout fingerprints: each cell brighter than its left neighbour, and
    /// than the one above
    pub dhash: u64,
    pub vhash: u64,
}

const SMALL: usize = 256;

impl Reading {
    pub fn mono(&self) -> bool {
        self.chroma < 0.012 && self.chroma_p95 < 0.03
    }
    pub fn key(&self) -> &'static str {
        match self.l[4] {
            m if m < 0.42 => "low",
            m if m > 0.74 => "high",
            _ => "mid",
        }
    }
    pub fn spread(&self) -> f32 {
        self.l[6] - self.l[2]
    }
}

struct Small {
    w: usize,
    h: usize,
    lab: Vec<Lab>,
}

fn shrink(f: &Frame, dec: &Decode) -> Small {
    let s = f.w.max(f.h).div_ceil(SMALL).max(1);
    let (w, h) = (f.w.div_ceil(s), f.h.div_ceil(s));
    let mut lab = Vec::with_capacity(w * h);
    for by in 0..h {
        for bx in 0..w {
            let mut acc = [0.0f32; 3];
            let mut n = 0.0;
            for y in by * s..((by + 1) * s).min(f.h) {
                for x in bx * s..((bx + 1) * s).min(f.w) {
                    let i = (y * f.w + x) * 4;
                    let c = dec.px(f.px[i], f.px[i + 1], f.px[i + 2]);
                    acc[0] += c[0];
                    acc[1] += c[1];
                    acc[2] += c[2];
                    n += 1.0;
                }
            }
            lab.push(oklab([acc[0] / n, acc[1] / n, acc[2] / n]));
        }
    }
    Small { w, h, lab }
}

/// Rows (or columns) of near-black bars at both ends: a letterbox.
fn bars(sm: &Small) -> (usize, usize, usize, usize) {
    let dark_row = |y: usize| {
        let row = &sm.lab[y * sm.w..(y + 1) * sm.w];
        let mean = row.iter().map(|c| c[0]).sum::<f32>() / sm.w as f32;
        mean < 0.12 && row.iter().all(|c| c[0] < 0.2)
    };
    let dark_col = |x: usize| {
        let mut mean = 0.0;
        for y in 0..sm.h {
            let l = sm.lab[y * sm.w + x][0];
            if l >= 0.2 {
                return false;
            }
            mean += l;
        }
        mean / (sm.h as f32) < 0.12
    };
    let run = |n: usize, dark: &dyn Fn(usize) -> bool, rev: bool| {
        (0..n / 3).take_while(|&i| dark(if rev { n - 1 - i } else { i })).count()
    };
    let (t, b) = (run(sm.h, &dark_row, false), run(sm.h, &dark_row, true));
    if t + b >= sm.h / 25 && t.abs_diff(b) <= 2 + sm.h / 50 && t > 0 && b > 0 {
        return (0, t, sm.w, sm.h - b);
    }
    let (l, r) = (run(sm.w, &dark_col, false), run(sm.w, &dark_col, true));
    if l + r >= sm.w / 25 && l.abs_diff(r) <= 2 + sm.w / 50 && l > 0 && r > 0 {
        return (l, 0, sm.w - r, sm.h);
    }
    (0, 0, sm.w, sm.h)
}

fn percentile(sorted: &[f32], p: f32) -> f32 {
    if sorted.is_empty() {
        return 0.0;
    }
    sorted[((sorted.len() - 1) as f32 * p).round() as usize]
}

/// Five colours by k-means in Oklab, seeded at lightness quantiles so the
/// answer is the same every time, darkest first.
fn palette(px: &[Lab]) -> Vec<(Lab, f32)> {
    let step = (px.len() / 4000).max(1);
    let mut s: Vec<Lab> = px.iter().step_by(step).copied().collect();
    if s.is_empty() {
        return vec![];
    }
    s.sort_by(|a, b| a[0].total_cmp(&b[0]));
    let k = 5.min(s.len());
    let mut centers: Vec<Lab> = (0..k).map(|i| s[(s.len() - 1) * (2 * i + 1) / (2 * k)]).collect();
    let mut owner = vec![0usize; s.len()];
    for _ in 0..14 {
        for (i, c) in s.iter().enumerate() {
            owner[i] = (0..k)
                .min_by(|&a, &b| dist(c, &centers[a]).total_cmp(&dist(c, &centers[b])))
                .unwrap_or(0);
        }
        let mut acc = vec![[0.0f32; 4]; k];
        for (i, c) in s.iter().enumerate() {
            let a = &mut acc[owner[i]];
            a[0] += c[0];
            a[1] += c[1];
            a[2] += c[2];
            a[3] += 1.0;
        }
        for (j, a) in acc.iter().enumerate() {
            if a[3] > 0.0 {
                centers[j] = [a[0] / a[3], a[1] / a[3], a[2] / a[3]];
            }
        }
    }
    let mut out: Vec<(Lab, f32)> = (0..k)
        .map(|j| (centers[j], owner.iter().filter(|&&o| o == j).count() as f32 / s.len() as f32))
        .filter(|(_, share)| *share > 0.0)
        .collect();
    out.sort_by(|a, b| a.0[0].total_cmp(&b.0[0]));
    out
}

fn dist(a: &Lab, b: &Lab) -> f32 {
    // lightness counts a little less than hue: a palette is about colour
    0.6 * (a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)
}

/// Noise in flat areas of the full-resolution centre: film grain, sensor
/// noise. (A small, heavily compressed JPEG reads as clean.)
fn grain(f: &Frame, dec: &Decode, active: [f32; 4]) -> f32 {
    let (x0, y0) = ((active[0] * f.w as f32) as usize, (active[1] * f.h as f32) as usize);
    let (aw, ah) = (((active[2] - active[0]) * f.w as f32) as usize, ((active[3] - active[1]) * f.h as f32) as usize);
    let side = 384.min(aw).min(ah);
    if side < 16 {
        return 0.0;
    }
    let (cx, cy) = (x0 + (aw - side) / 2, y0 + (ah - side) / 2);
    let mut l = vec![0.0f32; side * side];
    for y in 0..side {
        for x in 0..side {
            let i = ((cy + y) * f.w + cx + x) * 4;
            l[y * side + x] = oklab(dec.px(f.px[i], f.px[i + 1], f.px[i + 2]))[0];
        }
    }
    let mut res = Vec::with_capacity(side * side / 4);
    for y in 1..side - 1 {
        for x in 1..side - 1 {
            let at = |dx: isize, dy: isize| l[(y as isize + dy) as usize * side + (x as isize + dx) as usize];
            let gx = (at(1, -1) + 2.0 * at(1, 0) + at(1, 1)) - (at(-1, -1) + 2.0 * at(-1, 0) + at(-1, 1));
            let gy = (at(-1, 1) + 2.0 * at(0, 1) + at(1, 1)) - (at(-1, -1) + 2.0 * at(0, -1) + at(1, -1));
            if gx.abs() + gy.abs() > 0.12 {
                continue; // an edge, not grain
            }
            let mut m = 0.0;
            for dy in -1..=1 {
                for dx in -1..=1 {
                    m += at(dx, dy);
                }
            }
            let r = at(0, 0) - m / 9.0;
            res.push(r * r);
        }
    }
    if res.len() < 64 {
        return 0.0;
    }
    res.sort_by(f32::total_cmp);
    (percentile(&res, 0.5) * 9.0 / 8.0).sqrt() * 1.48
}

/// Lightness on a small grid, each cell brighter than its neighbour or not
/// (`across`: 9×8, left neighbour; else 8×9, the one above): two frames of
/// the same shot differ by a few bits, a cut by dozens.
fn dhash(sm: &Small, a: (usize, usize, usize, usize), across: bool) -> u64 {
    let (aw, ah) = (a.2 - a.0, a.3 - a.1);
    let (nx, ny) = if across { (9, 8) } else { (8, 9) };
    let cell = |gx: usize, gy: usize| {
        let (xa, xb) = (a.0 + gx * aw / nx, a.0 + ((gx + 1) * aw / nx).max(gx * aw / nx + 1));
        let (ya, yb) = (a.1 + gy * ah / ny, a.1 + ((gy + 1) * ah / ny).max(gy * ah / ny + 1));
        let mut s = 0.0;
        let mut n = 0.0;
        for y in ya..yb.min(a.3) {
            for x in xa..xb.min(a.2) {
                s += sm.lab[y * sm.w + x][0];
                n += 1.0;
            }
        }
        if n > 0.0 { s / n } else { 0.0 }
    };
    let mut h = 0u64;
    for gy in 0..8 {
        for gx in 0..8 {
            let brighter = if across { cell(gx + 1, gy) > cell(gx, gy) } else { cell(gx, gy + 1) > cell(gx, gy) };
            h = (h << 1) | brighter as u64;
        }
    }
    h
}

pub fn read(f: &Frame) -> Result<Reading, String> {
    if f.w < 8 || f.h < 8 || f.px.len() < f.w * f.h * 4 {
        return Err("the picture is too small or its pixels are missing".into());
    }
    let dec = Decode::default();
    let sm = shrink(f, &dec);
    let (ax0, ay0, ax1, ay1) = bars(&sm);
    let active = [ax0 as f32 / sm.w as f32, ay0 as f32 / sm.h as f32, ax1 as f32 / sm.w as f32, ay1 as f32 / sm.h as f32];
    let letterbox = (ax0, ay0, ax1, ay1) != (0, 0, sm.w, sm.h);
    let aspect = ((active[2] - active[0]) * f.w as f32) / ((active[3] - active[1]) * f.h as f32);

    let (aw, ah) = (ax1 - ax0, ay1 - ay0);
    let px: Vec<Lab> = (ay0..ay1).flat_map(|y| (ax0..ax1).map(move |x| (x, y))).map(|(x, y)| sm.lab[y * sm.w + x]).collect();
    let n = px.len() as f32;
    let mut ls: Vec<f32> = px.iter().map(|c| c[0]).collect();
    ls.sort_by(f32::total_cmp);
    let mut l = [0.0f32; 10];
    for (i, p) in [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99].iter().enumerate() {
        l[i] = percentile(&ls, *p);
    }
    l[9] = ls.iter().sum::<f32>() / n;
    let crushed = ls.iter().filter(|&&v| v < 0.12).count() as f32 / n;
    let clipped = ls.iter().filter(|&&v| v > 0.97).count() as f32 / n;

    let mut cs: Vec<f32> = px.iter().map(|c| chroma(*c)).collect();
    let chroma_mean = cs.iter().sum::<f32>() / n;
    cs.sort_by(f32::total_cmp);
    let chroma_p95 = percentile(&cs, 0.95);
    let avg_ab = |pred: &dyn Fn(f32) -> bool| {
        let (mut a, mut b, mut k) = (0.0, 0.0, 0.0);
        for c in px.iter().filter(|c| pred(c[0])) {
            a += c[1];
            b += c[2];
            k += 1.0;
        }
        if k > 0.0 { [a / k, b / k] } else { [0.0, 0.0] }
    };
    let mean_ab = avg_ab(&|v| (0.2..0.88).contains(&v));
    let (q1, q3) = (l[3], l[5]);
    let shadows = avg_ab(&|v| v <= q1);
    let highlights = avg_ab(&|v| v >= q3);

    // sharpness: the Laplacian of lightness, overall, in the middle, at the edges
    let at = |x: usize, y: usize| sm.lab[y * sm.w + x][0];
    let (mut s_all, mut s_mid, mut s_edge, mut n_all, mut n_mid, mut n_edge) = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
    for y in ay0 + 1..ay1.saturating_sub(1) {
        for x in ax0 + 1..ax1.saturating_sub(1) {
            let lap = 4.0 * at(x, y) - at(x - 1, y) - at(x + 1, y) - at(x, y - 1) - at(x, y + 1);
            let v = lap * lap;
            s_all += v;
            n_all += 1.0;
            let (fx, fy) = ((x - ax0) as f32 / aw as f32, (y - ay0) as f32 / ah as f32);
            if (0.3..0.7).contains(&fx) && (0.25..0.75).contains(&fy) {
                s_mid += v;
                n_mid += 1.0;
            } else if !(0.15..0.85).contains(&fx) || !(0.15..0.85).contains(&fy) {
                s_edge += v;
                n_edge += 1.0;
            }
        }
    }
    let avg = |s: f32, n: f32| if n > 0.0 { (s / n).sqrt() } else { 0.0 };

    // regions: corners vs centre, left vs right, top vs bottom
    let mean_in = |fx0: f32, fy0: f32, fx1: f32, fy1: f32| {
        let (x0, x1) = (ax0 + (fx0 * aw as f32) as usize, ax0 + ((fx1 * aw as f32) as usize).max(1));
        let (y0, y1) = (ay0 + (fy0 * ah as f32) as usize, ay0 + ((fy1 * ah as f32) as usize).max(1));
        let (mut s, mut k) = (0.0, 0.0);
        for y in y0..y1.min(ay1) {
            for x in x0..x1.min(ax1) {
                s += at(x, y);
                k += 1.0;
            }
        }
        if k > 0.0 { s / k } else { 0.0 }
    };
    let corners = (mean_in(0.0, 0.0, 0.12, 0.12) + mean_in(0.88, 0.0, 1.0, 0.12)
        + mean_in(0.0, 0.88, 0.12, 1.0) + mean_in(0.88, 0.88, 1.0, 1.0)) / 4.0;
    let centre = mean_in(0.35, 0.35, 0.65, 0.65);
    let vignette = if centre > 0.05 { corners / centre } else { 1.0 };
    let side = mean_in(0.67, 0.0, 1.0, 1.0) - mean_in(0.0, 0.0, 0.33, 1.0);
    let top = mean_in(0.0, 0.0, 1.0, 0.33) - mean_in(0.0, 0.67, 1.0, 1.0);

    // the subject: where detail and contrast gather; calm cells are copy space
    const G: usize = 8;
    let mut cells = vec![(0.0f32, 0.0f32, 0.0f32); G * G];
    for (gy, row) in cells.chunks_mut(G).enumerate() {
        for (gx, cell) in row.iter_mut().enumerate() {
            let (x0, x1) = (ax0 + gx * aw / G, ax0 + ((gx + 1) * aw / G).max(gx * aw / G + 1));
            let (y0, y1) = (ay0 + gy * ah / G, ay0 + ((gy + 1) * ah / G).max(gy * ah / G + 1));
            let (mut s, mut s2, mut ch, mut k) = (0.0, 0.0, 0.0, 0.0);
            for y in y0..y1.min(ay1) {
                for x in x0..x1.min(ax1) {
                    let c = sm.lab[y * sm.w + x];
                    s += c[0];
                    s2 += c[0] * c[0];
                    ch += (chroma(c) - chroma_mean).abs();
                    k += 1.0;
                }
            }
            if k > 0.0 {
                let m = s / k;
                *cell = ((s2 / k - m * m).max(0.0).sqrt(), ch / k, (m - l[4]).abs());
            }
        }
    }
    let weight: Vec<f32> = cells.iter().map(|(sd, ch, dl)| sd + 0.6 * ch + 0.5 * dl).collect();
    let mut sorted = weight.clone();
    sorted.sort_by(f32::total_cmp);
    let cut = percentile(&sorted, 0.75);
    let (mut sx, mut sy, mut sw) = (0.0, 0.0, 0.0);
    for (i, w) in weight.iter().enumerate() {
        if *w >= cut && *w > 0.0 {
            sx += w * ((i % G) as f32 + 0.5) / G as f32;
            sy += w * ((i / G) as f32 + 0.5) / G as f32;
            sw += w;
        }
    }
    let subject = if sw > 0.0 { [sx / sw, sy / sw] } else { [0.5, 0.5] };
    let calm_cell = |i: usize| cells[i].0 < 0.035 && cells[i].1 < 0.02;
    let share = |f: &dyn Fn(usize, usize) -> bool| {
        let idx: Vec<usize> = (0..G * G).filter(|&i| f(i % G, i / G)).collect();
        idx.iter().filter(|&&i| calm_cell(i)).count() as f32 / idx.len() as f32
    };
    let calm = [share(&|x, _| x < G / 2), share(&|x, _| x >= G / 2), share(&|_, y| y < G / 2), share(&|_, y| y >= G / 2)];

    Ok(Reading {
        w: f.w,
        h: f.h,
        active,
        letterbox,
        aspect,
        l,
        crushed,
        clipped,
        chroma: chroma_mean,
        chroma_p95,
        mean_ab,
        shadows,
        highlights,
        palette: palette(&px),
        sharp: avg(s_all, n_all),
        sharp_center: avg(s_mid, n_mid),
        sharp_edge: avg(s_edge, n_edge),
        grain: grain(f, &dec, active),
        vignette,
        side,
        top,
        subject,
        calm,
        dhash: dhash(&sm, (ax0, ay0, ax1, ay1), true),
        vhash: dhash(&sm, (ax0, ay0, ax1, ay1), false),
    })
}

/// The width:height a letterboxed frame was framed for, by name.
pub fn aspect_name(a: f32) -> Option<&'static str> {
    const KNOWN: [(f32, &str); 10] = [
        (2.76, "2.76:1"), (2.39, "2.39:1"), (2.0, "2:1"), (1.85, "1.85:1"), (1.778, "16:9"), (1.5, "3:2"),
        (1.333, "4:3"), (1.0, "1:1"), (0.8, "4:5"), (0.5625, "9:16"),
    ];
    KNOWN.iter().find(|(r, _)| (a / r - 1.0).abs() < 0.035).map(|(_, n)| *n)
}

impl Reading {
    pub fn to_json(&self) -> Value {
        let r = |v: f32| (v * 1000.0).round() / 1000.0;
        json!({
            "size": [self.w, self.h],
            "frame": {"active": self.active.map(r), "letterbox": self.letterbox, "aspect": r(self.aspect),
                      "aspect_name": aspect_name(self.aspect)},
            "tone": {"black": r(self.l[0]), "shadows": r(self.l[3]), "median": r(self.l[4]), "highlights": r(self.l[5]),
                     "white": r(self.l[8]), "mean": r(self.l[9]), "spread": r(self.spread()), "key": self.key(),
                     "crushed": r(self.crushed), "clipped": r(self.clipped)},
            "colour": {"chroma": r(self.chroma), "chroma_p95": r(self.chroma_p95), "mono": self.mono(),
                       "cast": hue_name(self.mean_ab[0] * 2.0, self.mean_ab[1] * 2.0),
                       "shadows": hue_name(self.shadows[0], self.shadows[1]),
                       "highlights": hue_name(self.highlights[0], self.highlights[1]),
                       "mean_ab": self.mean_ab.map(r), "shadows_ab": self.shadows.map(r),
                       "highlights_ab": self.highlights.map(r)},
            "palette": self.palette.iter().map(|(c, s)| json!({"hex": hex(*c), "share": r(*s),
                                                                "name": hue_name(c[1], c[2])})).collect::<Vec<_>>(),
            "focus": {"sharpness": r(self.sharp), "centre": r(self.sharp_center), "edges": r(self.sharp_edge)},
            "grain": r(self.grain),
            "vignette": r(self.vignette),
            "light": {"right_minus_left": r(self.side), "top_minus_bottom": r(self.top)},
            "subject": self.subject.map(r),
            "calm": {"left": r(self.calm[0]), "right": r(self.calm[1]), "top": r(self.calm[2]), "bottom": r(self.calm[3])},
            "fingerprint": format!("{:016x}{:016x}", self.dhash, self.vhash),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(w: usize, h: usize, mut f: impl FnMut(usize, usize) -> [u8; 3]) -> Vec<u8> {
        let mut v = Vec::with_capacity(w * h * 4);
        for y in 0..h {
            for x in 0..w {
                let c = f(x, y);
                v.extend_from_slice(&[c[0], c[1], c[2], 255]);
            }
        }
        v
    }

    #[test]
    fn a_letterboxed_frame_reads_as_scope() {
        let (w, h) = (1280, 720);
        let bar = (h as f32 - w as f32 / 2.39) as usize / 2;
        let px = frame(w, h, |x, y| if y < bar || y >= h - bar { [2, 2, 2] } else { [(x % 256) as u8, 120, 200] });
        let r = read(&Frame { px: &px, w, h }).unwrap();
        assert!(r.letterbox, "{:?}", r.active);
        assert_eq!(aspect_name(r.aspect), Some("2.39:1"), "{}", r.aspect);
    }

    #[test]
    fn tone_colour_and_split_toning() {
        // a ramp, teal in the dark half and orange in the bright half
        let px = frame(400, 200, |x, _| {
            let v = (x as f32 / 399.0 * 255.0) as u8;
            if x < 200 { [v / 3, v, v] } else { [v, (v as f32 * 0.6) as u8, v / 4] }
        });
        let r = read(&Frame { px: &px, w: 400, h: 200 }).unwrap();
        assert_eq!(hue_name(r.shadows[0], r.shadows[1]), "teal");
        assert_eq!(hue_name(r.highlights[0], r.highlights[1]), "orange");
        assert!(!r.mono() && r.palette.len() == 5);
        assert!(r.palette[0].0[0] < r.palette[4].0[0], "darkest first");
    }

    #[test]
    fn grey_noise_is_grain_and_a_flat_card_is_not() {
        let mut seed = 7u32;
        let mut rnd = move || {
            seed ^= seed << 13;
            seed ^= seed >> 17;
            seed ^= seed << 5;
            (seed % 21) as i32 - 10
        };
        let noisy = frame(512, 512, |_, _| {
            let v = (128 + rnd()) as u8;
            [v, v, v]
        });
        let flat = frame(512, 512, |_, _| [128, 128, 128]);
        let g = |px: &[u8]| read(&Frame { px, w: 512, h: 512 }).unwrap();
        assert!(g(&noisy).grain > 0.01, "{}", g(&noisy).grain);
        assert!(g(&flat).grain < 0.001);
        assert!(g(&flat).mono());
    }

    #[test]
    fn the_same_frame_has_the_same_fingerprint() {
        let a = frame(320, 180, |x, y| [(x / 2) as u8, (y) as u8, 90]);
        let b = frame(320, 180, |x, y| [(x / 2) as u8 + 3, (y) as u8, 90]);
        let c = frame(320, 180, |x, y| [(255 - x / 2) as u8, (180 - y) as u8, 90]);
        let h = |px: &[u8]| read(&Frame { px, w: 320, h: 180 }).unwrap().dhash;
        assert!((h(&a) ^ h(&b)).count_ones() <= 4);
        assert!((h(&a) ^ h(&c)).count_ones() > 32);
    }
}
