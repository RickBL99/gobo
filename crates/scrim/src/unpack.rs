//! A .cube read back into the sliders.
//!
//! A LUT is a table, not settings: it says which colour comes out for each
//! colour that goes in, and nothing about how it was made. So the sliders are
//! fitted to it: exposure, contrast, fade and the white point for its tone;
//! saturation, warmth and tint for its colour; then the lift and gain wheels
//! for the colour it pushes into the shadows and the highlights. What those
//! can't say (one hue turned on its own, say) is kept as a table of its own,
//! the rest, so the rest and the sliders together are the LUT exactly, and a
//! slider moved moves the picture from there.
//!
//! The rest comes first, before the sliders: it hands them the colour that
//! they turn into the LUT's. So nothing the sliders let go of (colour taken
//! out, a highlight clipped) is lost before the rest can put it back.
//!
//! Some LUTs aren't grades at all (a negative, a posterise, one channel
//! only): no setting of the sliders comes near them, so they're left whole.

use crate::color::{oklab, to_linear, Rgb};
use crate::colour::{Cube, Wheels};
use crate::grade::{controls, Lut};
use crate::look::Look;

/// A LUT as sliders and the rest.
pub struct Unpacked {
    /// the plain stock and grade with the fitted numbers (no grain, halation
    /// or vignette: a LUT has none)
    pub look: Look,
    /// what the sliders can't say, first: the colour they turn into the LUT's
    pub rest: Cube,
    /// how far the sliders alone land from the LUT, and the rest with the
    /// sliders (mean ΔE in Oklab ×100: about 1 is the least an eye can see)
    pub sliders_off: f32,
    pub rest_off: f32,
    /// how much of what the LUT does the sliders show (0 to 1)
    pub shown: f32,
    /// why it should stay whole instead: "nothing" (it barely changes the
    /// picture) or "beyond" (no setting of the sliders comes near it)
    pub whole: Option<&'static str>,
}

const REST: usize = 33;

/// A fitted number: where it may go, where it rests, and what moving it costs
/// (so a cast goes on Warmth before the wheels, and a slider the LUT doesn't
/// need stays where it was).
struct Knob {
    lo: f32,
    hi: f32,
    at: f32,
    cost: f32,
}

const KNOBS: [Knob; 12] = [
    Knob { lo: -2.0, hi: 2.0, at: 0.0, cost: 0.3 },   // exposure
    Knob { lo: 0.5, hi: 2.0, at: 1.0, cost: 0.5 },    // contrast
    Knob { lo: 0.0, hi: 0.25, at: 0.0, cost: 2.0 },   // fade
    Knob { lo: 0.7, hi: 1.0, at: 1.0, cost: 2.0 },    // white point
    Knob { lo: -0.8, hi: 0.8, at: 0.0, cost: 0.4 },   // the gamma wheel's brightness (a power on the mid-tones)
    Knob { lo: 0.0, hi: 2.0, at: 1.0, cost: 0.5 },    // saturation
    // a unit of Warmth moves red and blue by a tenth; a unit of a wheel's
    // colour by one and a half: priced per effect, Warmth and Tint come first
    Knob { lo: -1.0, hi: 1.0, at: 0.0, cost: 0.05 },  // warmth
    Knob { lo: -1.0, hi: 1.0, at: 0.0, cost: 0.05 },  // tint
    Knob { lo: -0.12, hi: 0.12, at: 0.0, cost: 20.0 }, // lift: Cb, Cr
    Knob { lo: -0.12, hi: 0.12, at: 0.0, cost: 20.0 },
    Knob { lo: -0.3, hi: 0.3, at: 0.0, cost: 20.0 }, // gain: Cb, Cr
    Knob { lo: -0.3, hi: 0.3, at: 0.0, cost: 20.0 },
];

/// BT.709: a wheel's colour as an RGB push that leaves brightness alone (the
/// page's `toRgb`, so the dot lands where the fit put it).
fn push(cb: f32, cr: f32) -> [f32; 4] {
    [1.5748 * cr, -0.1873 * cb - 0.4681 * cr, 1.8556 * cb, 0.0]
}

fn look_of(p: &[f32; 12]) -> Look {
    Look {
        exposure: p[0],
        contrast: p[1],
        fade: p[2],
        white: p[3],
        sat: p[5],
        temp: p[6],
        tint: p[7],
        wheels: Wheels {
            lift: push(p[8], p[9]),
            gamma: [0.0, 0.0, 0.0, p[4]],
            gain: push(p[10], p[11]),
            ..Wheels::default()
        },
        ..Look::default()
    }
}

/// Display values to Oklab, clipped as the picture is.
fn lab(c: Rgb) -> Rgb {
    oklab(c.map(|v| to_linear(v.clamp(0.0, 1.0))))
}

fn delta(a: Rgb, b: Rgb) -> f32 {
    let (a, b) = (lab(a), lab(b));
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt() * 100.0
}

/// Where the fit is measured: a 7³ grid of the cube, and the greys again,
/// three times over.
fn samples() -> Vec<(Rgb, f32)> {
    let n = 7;
    let s = (n - 1) as f32;
    let mut out = Vec::with_capacity(n * n * n + 17);
    for b in 0..n {
        for g in 0..n {
            for r in 0..n {
                out.push(([r as f32 / s, g as f32 / s, b as f32 / s], 1.0));
            }
        }
    }
    for i in 0..=16 {
        out.push(([i as f32 / 16.0; 3], 3.0));
    }
    out
}

/// What a stage of the fit reads. Each slider is read off what it means, not
/// off every colour at once: a LUT that turns one hue (no slider does that)
/// would otherwise drag Saturation down and Warmth to the end of its range to
/// shrink the mismatch, which reads as nothing a colourist would set.
#[derive(Clone, Copy)]
enum Part {
    /// the greys' lightness: exposure, contrast, fade, white point, gamma
    Tone,
    /// the colour the greys pick up: warmth, tint, the lift and gain wheels
    Cast,
    /// how colourful colours come out, whatever their hue: saturation
    Colour,
}

impl Part {
    fn of(self, o: Rgb, t: Rgb, out: &mut Vec<f32>, w: f32) {
        match self {
            Part::Tone => out.push((o[0] - t[0]) * w),
            Part::Cast => out.extend([(o[1] - t[1]) * w, (o[2] - t[2]) * w]),
            Part::Colour => out.push((o[1].hypot(o[2]) - t[1].hypot(t[2])) * w),
        }
    }
}

/// Solve `a x = b` (n × n, row major) by elimination with partial pivoting.
fn solve(mut a: Vec<f64>, mut b: Vec<f64>, n: usize) -> Option<Vec<f64>> {
    for c in 0..n {
        let p = (c..n).max_by(|&i, &j| a[i * n + c].abs().total_cmp(&a[j * n + c].abs()))?;
        if a[p * n + c].abs() < 1e-12 {
            return None;
        }
        if p != c {
            for k in 0..n {
                a.swap(p * n + k, c * n + k);
            }
            b.swap(p, c);
        }
        for r in c + 1..n {
            let f = a[r * n + c] / a[c * n + c];
            for k in c..n {
                a[r * n + k] -= f * a[c * n + k];
            }
            b[r] -= f * b[c];
        }
    }
    let mut x = vec![0.0; n];
    for r in (0..n).rev() {
        let s: f64 = (r + 1..n).map(|k| a[r * n + k] * x[k]).sum();
        x[r] = (b[r] - s) / a[r * n + r];
    }
    Some(x)
}

/// Fit the knobs in `free` to the LUT (Levenberg–Marquardt, each knob held
/// to its range), the others staying as they are.
fn fit(p: &mut [f32; 12], free: std::ops::Range<usize>, part: Part, pts: &[Rgb], want: &[Rgb]) {
    let k = free.len();
    let norm = 100.0 / (pts.len() as f32).sqrt();
    let resid = |p: &[f32; 12], out: &mut Vec<f32>| {
        out.clear();
        let look = look_of(p);
        for (x, t) in pts.iter().zip(want) {
            part.of(lab(controls(&look, *x)), *t, out, norm);
        }
        out.extend(free.clone().map(|i| (p[i] - KNOBS[i].at) * KNOBS[i].cost));
    };
    let cost = |r: &[f32]| r.iter().map(|v| (*v as f64).powi(2)).sum::<f64>();
    let (mut r0, mut r1) = (Vec::new(), Vec::new());
    resid(p, &mut r0);
    let mut now = cost(&r0);
    let m = r0.len();
    let mut jac = vec![0.0f64; m * k];
    let mut lambda = 1e-3;
    for _ in 0..40 {
        for (j, i) in free.clone().enumerate() {
            let h = (KNOBS[i].hi - KNOBS[i].lo) * 1e-3;
            let mut q = *p;
            q[i] = if q[i] + h <= KNOBS[i].hi { q[i] + h } else { q[i] - h };
            let step = q[i] - p[i];
            resid(&q, &mut r1);
            for row in 0..m {
                jac[row * k + j] = ((r1[row] - r0[row]) / step) as f64;
            }
        }
        let mut a = vec![0.0f64; k * k];
        let mut g = vec![0.0f64; k];
        for row in 0..m {
            let jr = &jac[row * k..row * k + k];
            for j in 0..k {
                g[j] -= jr[j] * r0[row] as f64;
                for l in 0..k {
                    a[j * k + l] += jr[j] * jr[l];
                }
            }
        }
        let mut better = false;
        for _ in 0..10 {
            let mut damped = a.clone();
            for j in 0..k {
                damped[j * k + j] += lambda * a[j * k + j].max(1e-9);
            }
            let Some(d) = solve(damped, g.clone(), k) else {
                lambda *= 10.0;
                continue;
            };
            let mut q = *p;
            for (j, i) in free.clone().enumerate() {
                q[i] = (q[i] + d[j] as f32).clamp(KNOBS[i].lo, KNOBS[i].hi);
            }
            resid(&q, &mut r1);
            let then = cost(&r1);
            if then < now {
                better = now - then > now * 1e-5;
                *p = q;
                std::mem::swap(&mut r0, &mut r1);
                now = then;
                lambda = (lambda / 3.0).max(1e-9);
                break;
            }
            lambda *= 4.0;
        }
        if !better {
            break;
        }
    }
}

/// The display colour the look's controls turn into `y`, or the nearest they
/// can, from a first guess (damped Gauss–Newton, kept inside the cube).
fn invert(look: &Look, y: Rgb, x0: Rgb) -> Rgb {
    let err = |x: Rgb| {
        let o = controls(look, x);
        [o[0] - y[0], o[1] - y[1], o[2] - y[2]]
    };
    let size = |e: Rgb| e[0] * e[0] + e[1] * e[1] + e[2] * e[2];
    let (mut x, mut e) = (x0, err(x0));
    let mut n = size(e);
    for _ in 0..16 {
        if n < 1e-11 {
            break;
        }
        let mut j = [[0.0f64; 3]; 3];
        for c in 0..3 {
            let mut xh = x;
            xh[c] = if x[c] + 1e-3 <= 1.0 { x[c] + 1e-3 } else { x[c] - 1e-3 };
            let eh = err(xh);
            for r in 0..3 {
                j[r][c] = ((eh[r] - e[r]) / (xh[c] - x[c])) as f64;
            }
        }
        let mut a = vec![0.0f64; 9];
        let mut g = vec![0.0f64; 3];
        for r in 0..3 {
            for c in 0..3 {
                g[c] -= j[r][c] * e[r] as f64;
                for l in 0..3 {
                    a[c * 3 + l] += j[r][c] * j[r][l];
                }
            }
        }
        for c in 0..3 {
            a[c * 4] += 1e-7;
        }
        let Some(d) = solve(a, g, 3) else { break };
        let mut t = 1.0f32;
        let mut moved = false;
        for _ in 0..6 {
            let xn = [0, 1, 2].map(|c| (x[c] + t * d[c] as f32).clamp(0.0, 1.0));
            let en = err(xn);
            if size(en) < n {
                (x, e, n) = (xn, en, size(en));
                moved = true;
                break;
            }
            t *= 0.5;
        }
        if !moved {
            break;
        }
    }
    x
}

/// Each channel's way back along the greys, as a first guess for [`invert`].
struct Guess([Vec<f32>; 3]);

impl Guess {
    fn new(look: &Look) -> Guess {
        let n = 256;
        let mut fwd = [vec![0.0; n], vec![0.0; n], vec![0.0; n]];
        for i in 0..n {
            let o = controls(look, [i as f32 / (n - 1) as f32; 3]);
            for c in 0..3 {
                // kept rising, so there is one way back
                fwd[c][i] = if i == 0 { o[c] } else { o[c].max(fwd[c][i - 1]) };
            }
        }
        Guess(fwd)
    }

    fn at(&self, y: Rgb) -> Rgb {
        [0, 1, 2].map(|c| {
            let f = &self.0[c];
            let i = f.partition_point(|v| *v < y[c]).clamp(1, f.len() - 1);
            let (a, b) = (f[i - 1], f[i]);
            let t = if b > a { ((y[c] - a) / (b - a)).clamp(0.0, 1.0) } else { 0.0 };
            ((i - 1) as f32 + t) / (f.len() - 1) as f32
        })
    }
}

/// Read a LUT into the sliders, and keep what they can't say as the rest.
pub fn unpack(cube: &Cube) -> Unpacked {
    let greys: Vec<Rgb> = (0..=32).map(|i| [i as f32 / 32.0; 3]).collect();
    let grid: Vec<Rgb> = samples().into_iter().map(|(x, _)| x).collect();
    let want = |xs: &[Rgb]| xs.iter().map(|x| lab(cube.apply(*x))).collect::<Vec<_>>();
    let (grey_want, grid_want) = (want(&greys), want(&grid));
    let mut p = KNOBS.map(|k| k.at);
    // the tone, then the cast (Warmth and Tint before the wheels), then how
    // colourful, and the tone once more now the colour is set
    fit(&mut p, 0..5, Part::Tone, &greys, &grey_want);
    fit(&mut p, 6..12, Part::Cast, &greys, &grey_want);
    fit(&mut p, 5..6, Part::Colour, &grid, &grid_want);
    fit(&mut p, 0..5, Part::Tone, &greys, &grey_want);
    let look = look_of(&p);
    let pts = samples();

    let guess = Guess::new(&look);
    let off = |x: Rgb, y: Rgb| {
        let o = controls(&look, x);
        (o[0] - y[0]).powi(2) + (o[1] - y[1]).powi(2) + (o[2] - y[2]).powi(2)
    };
    let s = (REST - 1) as f32;
    let mut data = Vec::with_capacity(REST * REST * REST);
    for b in 0..REST {
        for g in 0..REST {
            for r in 0..REST {
                // the colour going into the sliders that comes out as the LUT's
                let at = [r as f32 / s, g as f32 / s, b as f32 / s];
                let y = cube.apply(at).map(|v| v.clamp(0.0, 1.0));
                let g0 = guess.at(y);
                let x0 = if off(at, y) < off(g0, y) { at } else { g0 };
                data.push(invert(&look, y, x0));
            }
        }
    }
    let title = if cube.title.is_empty() { "LUT".to_string() } else { cube.title.clone() };
    let rest = Cube {
        title: format!("{title} (the rest)"),
        table: Lut { n: REST, data },
        min: [0.0; 3],
        max: [1.0; 3],
        kind: "3D",
        size: REST,
    };
    let n = pts.len() as f32;
    let mean = |f: &dyn Fn(Rgb) -> Rgb| pts.iter().map(|(x, _)| delta(f(*x), cube.apply(*x))).sum::<f32>() / n;
    let sliders_off = mean(&|x| controls(&look, x));
    let rest_off = mean(&|x| controls(&look, rest.apply(x)));
    let (plain, as_is) = (mean(&|x| controls(&Look::default(), x)), mean(&|x| x));
    let shown = if plain > 0.0 { (1.0 - sliders_off / plain).clamp(0.0, 1.0) } else { 0.0 };
    let whole = if as_is < 0.5 {
        Some("nothing")
    } else if rest_off > 1.0 || shown < 0.25 {
        Some("beyond")
    } else {
        None
    };
    Unpacked { look, rest, sliders_off, rest_off, shown, whole }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::colour::parse_cube;
    use crate::grade::lut;

    fn baked(look: &Look) -> Cube {
        parse_cube(&lut(look, 33).cube("baked")).unwrap()
    }

    /// A 17³ .cube from a function, red fastest.
    fn table(title: &str, f: impl Fn(Rgb) -> Rgb) -> Cube {
        let n = 17;
        let mut text = format!("TITLE \"{title}\"\nLUT_3D_SIZE {n}\n");
        for b in 0..n {
            for g in 0..n {
                for r in 0..n {
                    let o = f([r, g, b].map(|i| i as f32 / (n - 1) as f32));
                    text += &format!("{} {} {}\n", o[0], o[1], o[2]);
                }
            }
        }
        parse_cube(&text).unwrap()
    }

    #[test]
    fn a_lut_the_sliders_made_comes_back_as_those_sliders() {
        let made = Look { exposure: 0.3, contrast: 1.25, sat: 0.8, temp: 0.3, fade: 0.04, ..Look::default() };
        let u = unpack(&baked(&made));
        let l = &u.look;
        assert!((l.exposure - 0.3).abs() < 0.1, "exposure {}", l.exposure);
        assert!((l.contrast - 1.25).abs() < 0.1, "contrast {}", l.contrast);
        assert!((l.sat - 0.8).abs() < 0.08, "saturation {}", l.sat);
        assert!((l.temp - 0.3).abs() < 0.1, "warmth {}", l.temp);
        assert!((l.fade - 0.04).abs() < 0.02, "fade {}", l.fade);
        assert!(u.sliders_off < 0.8, "the sliders alone are the LUT: {}", u.sliders_off);
        assert!(u.rest_off < 0.6 && u.whole.is_none(), "{} {:?}", u.rest_off, u.whole);
        assert!((l.grain, l.halation, l.vignette, l.amount) == (0.0, 0.0, 0.0, 1.0));
    }

    #[test]
    fn a_warm_contrasty_lut_moves_the_sliders_and_the_rest_stays_small() {
        let made = Look { contrast: 1.3, temp: 0.4, highlights: [0.01, 0.03], shadows: [-0.02, -0.03], ..Look::default() };
        let u = unpack(&baked(&made));
        assert!(u.look.contrast > 1.1 && u.look.temp > 0.15, "{:?}", (u.look.contrast, u.look.temp));
        assert!(u.sliders_off < 2.5 && u.rest_off < 0.6 && u.whole.is_none(), "{} {}", u.sliders_off, u.rest_off);
        // the rest is near nothing: a mid grey through it barely moves
        let g = u.rest.apply([0.5; 3]);
        assert!((0..3).all(|k| (g[k] - 0.5).abs() < 0.03), "{g:?}");
        assert_eq!(u.rest.title, "baked (the rest)");
        let back = parse_cube(&u.rest.table.cube(&u.rest.title)).unwrap();
        assert_eq!((back.size, back.kind), (33, "3D"));
    }

    #[test]
    fn a_cameras_lut_adds_up() {
        // log to video: flat in, contrasty and colourful out
        let made = Look { contrast: 1.6, exposure: 0.5, sat: 1.4, ..Look::default() };
        let u = unpack(&baked(&made));
        assert!(u.rest_off < 1.0 && u.whole.is_none(), "{}", u.rest_off);
        assert!(u.look.contrast > 1.3 && u.look.sat > 1.2, "{:?}", (u.look.contrast, u.look.sat));
    }

    #[test]
    fn colour_taken_out_is_put_back_by_the_rest_first() {
        // Rec.709 grey: Saturation goes to nothing, and the picture is still the LUT's
        let grey = table("Grey", |c| [0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; 3]);
        let u = unpack(&grey);
        assert!(u.look.sat < 0.05 && u.whole.is_none(), "{} {:?}", u.look.sat, u.whole);
        assert!(u.rest_off < 0.3, "{}", u.rest_off);
    }

    #[test]
    fn a_gamma_goes_on_the_gamma_wheel() {
        let up = table("Gamma", |c| c.map(|v| v.powf(1.0 / 2.2)));
        let u = unpack(&up);
        assert!(u.look.wheels.gamma[3] > 0.5 && u.whole.is_none(), "{:?}", u.look.wheels.gamma);
        assert!(u.rest_off < 1.0, "{}", u.rest_off);
    }

    #[test]
    fn what_no_slider_does_stays_whole() {
        let none = unpack(&table("Identity", |c| c));
        assert_eq!(none.whole, Some("nothing"));
        // red and blue swapped, a negative: the sliders can't, so the LUT stays as it is
        let swap = unpack(&table("Swap", |c| [c[2], c[1], c[0]]));
        assert!(swap.sliders_off > 10.0 && swap.shown < 0.25, "{} {}", swap.sliders_off, swap.shown);
        assert_eq!(swap.whole, Some("beyond"));
        assert_eq!(unpack(&table("Invert", |c| c.map(|v| 1.0 - v))).whole, Some("beyond"));
    }
}
