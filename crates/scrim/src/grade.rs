//! Grading pictures. Colour goes through a 3D LUT built from the look (the
//! same table the .cube export writes, so what you see is what your editor
//! gets); then the effects a LUT can't hold, because they need the
//! neighbourhood: halation (red light bleeding round highlights, as it does
//! through film's anti-halation layer), grain that sits in the mid-tones,
//! and a vignette.

use crate::color::{smoothstep, to_linear, to_srgb};
use crate::look::Look;

#[derive(Debug)]
pub struct Lut {
    pub n: usize,
    /// sRGB in, sRGB out, red fastest (the .cube order)
    pub data: Vec<[f32; 3]>,
}

pub fn lut(look: &Look, n: usize) -> Lut {
    let n = n.clamp(2, 65);
    let mut data = Vec::with_capacity(n * n * n);
    let s = (n - 1) as f32;
    for b in 0..n {
        for g in 0..n {
            for r in 0..n {
                let d = [r as f32 / s, g as f32 / s, b as f32 / s];
                // a camera's LUT first, then the colourist's tools, the look,
                // and a creative LUT last (crate::colour), all on display values
                let mut c = d;
                if let Some(e) = look.ext.as_ref().filter(|e| e.first) {
                    c = e.apply(c);
                }
                let mut o = controls(look, c);
                if let Some(e) = look.ext.as_ref().filter(|e| !e.first) {
                    o = e.apply(o);
                }
                // a partial look mixes with the picture as shot, as a colourist's key gain does
                let m = look.amount;
                data.push([0, 1, 2].map(|k| d[k] + (o[k] - d[k]) * m));
            }
        }
    }
    Lut { n, data }
}

/// One display colour through the look's own controls: the CDL, the wheels,
/// the curves, then the stock and grade. Everything but a .cube from elsewhere
/// and the amount, which [`lut`] puts round it.
pub fn controls(look: &Look, d: [f32; 3]) -> [f32; 3] {
    let mut c = d;
    if look.corrects() {
        if let Some(cdl) = &look.cdl {
            c = cdl.apply(c);
        }
        c = look.curves.apply(look.wheels.apply(c).map(|v| v.clamp(0.0, 1.0)));
    }
    look.apply([to_linear(c[0]), to_linear(c[1]), to_linear(c[2])]).map(to_srgb)
}

impl Lut {
    #[inline]
    pub fn sample(&self, c: [f32; 3]) -> [f32; 3] {
        let s = (self.n - 1) as f32;
        let f = |v: f32| {
            let x = v.clamp(0.0, 1.0) * s;
            let i = (x as usize).min(self.n - 2);
            (i, x - i as f32)
        };
        let ((r0, fr), (g0, fg), (b0, fb)) = (f(c[0]), f(c[1]), f(c[2]));
        let at = |r: usize, g: usize, b: usize| self.data[(b * self.n + g) * self.n + r];
        let mut out = [0.0f32; 3];
        for (k, o) in out.iter_mut().enumerate() {
            let c00 = at(r0, g0, b0)[k] * (1.0 - fr) + at(r0 + 1, g0, b0)[k] * fr;
            let c10 = at(r0, g0 + 1, b0)[k] * (1.0 - fr) + at(r0 + 1, g0 + 1, b0)[k] * fr;
            let c01 = at(r0, g0, b0 + 1)[k] * (1.0 - fr) + at(r0 + 1, g0, b0 + 1)[k] * fr;
            let c11 = at(r0, g0 + 1, b0 + 1)[k] * (1.0 - fr) + at(r0 + 1, g0 + 1, b0 + 1)[k] * fr;
            let c0 = c00 * (1.0 - fg) + c10 * fg;
            let c1 = c01 * (1.0 - fg) + c11 * fg;
            *o = c0 * (1.0 - fb) + c1 * fb;
        }
        out
    }

    /// The table as an Adobe/Resolve .cube file.
    pub fn cube(&self, title: &str) -> String {
        let title: String = title.chars().filter(|c| *c != '"' && !c.is_control()).collect();
        let mut s = format!("TITLE \"{title}\"\n# made by Gobo (scrim)\nLUT_3D_SIZE {}\nDOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n", self.n);
        s.reserve(self.data.len() * 27);
        for c in &self.data {
            s.push_str(&format!("{:.6} {:.6} {:.6}\n", c[0], c[1], c[2]));
        }
        s
    }
}

/// Three box blurs make a Gaussian, in O(pixels) whatever the radius.
fn blur(buf: &mut [f32], w: usize, h: usize, radius: usize) {
    let r = radius as isize;
    let k = (2 * radius + 1) as f32;
    let mut tmp = vec![0.0f32; buf.len()];
    // one pass of a running-sum box along a line, edges held
    let pass = |get: &dyn Fn(isize) -> f32, n: usize, put: &mut dyn FnMut(usize, f32)| {
        let mut acc: f32 = (-r..=r).map(get).sum();
        for i in 0..n {
            put(i, acc / k);
            acc += get(i as isize + r + 1) - get(i as isize - r);
        }
    };
    for _ in 0..3 {
        for y in 0..h {
            let row = &buf[y * w..(y + 1) * w];
            pass(&|i| row[i.clamp(0, w as isize - 1) as usize], w, &mut |x, v| tmp[y * w + x] = v);
        }
        for x in 0..w {
            let t = &tmp;
            pass(&|i| t[i.clamp(0, h as isize - 1) as usize * w + x], h, &mut |y, v| buf[y * w + x] = v);
        }
    }
}

#[inline]
fn hash(x: i32, y: i32, seed: u32) -> f32 {
    let mut h = (x as u32).wrapping_mul(0x8da6_b343) ^ (y as u32).wrapping_mul(0xd816_3841) ^ seed.wrapping_mul(0xcb1a_b31f);
    h ^= h >> 13;
    h = h.wrapping_mul(0x5bd1_e995);
    h ^= h >> 15;
    (h & 0xffff) as f32 / 32767.5 - 1.0
}

/// Value noise at a grain size: soft-edged clumps, not single-pixel salt.
#[inline]
fn grain_at(x: f32, y: f32, size: f32, seed: u32) -> f32 {
    let (fx, fy) = (x / size, y / size);
    let (ix, iy) = (fx.floor() as i32, fy.floor() as i32);
    let (tx, ty) = (fx - ix as f32, fy - iy as f32);
    let (sx, sy) = (tx * tx * (3.0 - 2.0 * tx), ty * ty * (3.0 - 2.0 * ty));
    let a = hash(ix, iy, seed) * (1.0 - sx) + hash(ix + 1, iy, seed) * sx;
    let b = hash(ix, iy + 1, seed) * (1.0 - sx) + hash(ix + 1, iy + 1, seed) * sx;
    a * (1.0 - sy) + b * sy
}

/// Grade an RGBA8 picture in place. `seed` picks the grain pattern (change it
/// per frame and the grain dances, as film does).
pub fn grade_rgba(px: &mut [u8], w: usize, h: usize, look: &Look, seed: u32) {
    grade_with(px, w, h, look, &lut(look, 33), seed);
}

/// The same, with the look's LUT already built (a clip grades every frame
/// through one table).
pub fn grade_with(px: &mut [u8], w: usize, h: usize, look: &Look, table: &Lut, seed: u32) {
    if w == 0 || h == 0 || px.len() < w * h * 4 {
        return;
    }
    let n = w * h;
    let mut rgb: Vec<[f32; 3]> = Vec::with_capacity(n);
    for i in 0..n {
        let c = [px[i * 4] as f32 / 255.0, px[i * 4 + 1] as f32 / 255.0, px[i * 4 + 2] as f32 / 255.0];
        rgb.push(table.sample(c));
    }
    let halation = look.halation * look.amount;
    let vignette = look.vignette * look.amount;
    if halation > 0.0 {
        let mut glow: Vec<f32> = rgb.iter().map(|c| smoothstep(0.72, 1.0, 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])).collect();
        blur(&mut glow, w, h, (w.max(h) / 160).max(2));
        // added in display space: a glow, not a measurement, and no powf per pixel
        let k = halation * 0.42;
        let add = [k, 0.42 * k, 0.22 * k];
        for (c, g) in rgb.iter_mut().zip(&glow) {
            for j in 0..3 {
                c[j] += g * add[j];
            }
        }
    }
    let (cx, cy) = (w as f32 / 2.0, h as f32 / 2.0);
    let diag2 = cx * cx + cy * cy;
    // the vignette by squared distance, as a factor on display values
    // (linear light × v is display × v^(1/2.4), near enough)
    const VT: usize = 2048;
    let vtable: Vec<f32> = (0..=VT).map(|i| {
        let d = (i as f32 / VT as f32 * 1.0).sqrt();
        (1.0 - vignette * smoothstep(0.35, 1.05, d)).max(0.0).powf(1.0 / 2.4)
    }).collect();
    let amp = look.grain * look.amount * 0.085;
    let size = look.grain_size * (w.max(h) as f32 / 1920.0).max(0.5);
    for y in 0..h {
        for x in 0..w {
            let i = y * w + x;
            let mut c = rgb[i];
            if vignette > 0.0 {
                let d2 = ((x as f32 - cx).powi(2) + (y as f32 - cy).powi(2)) / diag2;
                let v = vtable[((d2 * VT as f32) as usize).min(VT)];
                for ch in c.iter_mut() {
                    *ch *= v;
                }
            }
            if amp > 0.0 {
                let l = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
                let n = grain_at(x as f32, y as f32, size, seed) * amp * (4.0 * l * (1.0 - l)).max(0.15).sqrt();
                for ch in c.iter_mut() {
                    *ch += n;
                }
            }
            for j in 0..3 {
                px[i * 4 + j] = (c[j].clamp(0.0, 1.0) * 255.0 + 0.5) as u8;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::look::{film, grade};

    #[test]
    fn the_identity_lut_is_the_identity() {
        let t = lut(&Look::default(), 17);
        for c in [[0.0, 0.0, 0.0], [0.3, 0.6, 0.9], [1.0, 1.0, 1.0], [0.5, 0.5, 0.5]] {
            let o = t.sample(c);
            for k in 0..3 {
                assert!((o[k] - c[k]).abs() < 4e-3, "{c:?} → {o:?}");
            }
        }
        let cube = t.cube("x");
        assert!(cube.contains("LUT_3D_SIZE 17"));
        assert_eq!(cube.lines().filter(|l| l.starts_with(|c: char| c.is_ascii_digit())).count(), 17 * 17 * 17);
    }

    #[test]
    fn grading_moves_pixels_the_way_the_look_says() {
        let mut px = vec![128u8; 64 * 64 * 4];
        grade_rgba(&mut px, 64, 64, &grade("noir").unwrap(), 1);
        assert!(px.chunks(4).all(|p| p[0] == p[1] && p[1] == p[2]), "noir is grey");
        let mut px: Vec<u8> = (0..64 * 64).flat_map(|_| [200u8, 90, 40, 255]).collect();
        let before = px.clone();
        grade_rgba(&mut px, 64, 64, &film("expired").unwrap(), 3);
        assert_ne!(px, before);
        // grain changes with the seed, and only then
        let run = |seed| {
            let mut p = vec![120u8; 32 * 32 * 4];
            grade_rgba(&mut p, 32, 32, &Look { grain: 0.8, ..Look::default() }, seed);
            p
        };
        assert_eq!(run(5), run(5));
        assert_ne!(run(5), run(6));
    }

    #[test]
    fn amount_mixes_the_look_with_the_picture() {
        let full = Look { grain: 0.6, vignette: 0.5, halation: 0.4, ..film("bleach").unwrap() };
        let off = Look { amount: 0.0, ..full.clone() };
        let src: Vec<u8> = (0..48 * 27).flat_map(|i| [(i % 251) as u8, (i * 7 % 253) as u8, (i * 13 % 241) as u8, 255]).collect();
        let mut px = src.clone();
        grade_rgba(&mut px, 48, 27, &off, 9);
        assert!(px.iter().zip(&src).all(|(a, b)| a.abs_diff(*b) <= 1), "amount 0 is the picture as shot");
        let c = [0.8, 0.35, 0.2];
        let (a, h) = (lut(&full, 17).sample(c), lut(&Look { amount: 0.5, ..full }, 17).sample(c));
        for k in 0..3 {
            assert!((h[k] - (c[k] + a[k]) / 2.0).abs() < 0.01, "half is halfway: {c:?} {a:?} {h:?}");
        }
    }

    #[test]
    fn the_colourists_tools_are_in_the_table_and_in_order() {
        use crate::colour::{parse_cube, Cdl, Curves, Ext, Wheels};
        use serde_json::json;
        use std::sync::Arc;
        let c = [0.6, 0.4, 0.2];
        // a CDL alone: the table is the CDL
        let cdl = Cdl { slope: [1.1, 1.0, 0.9], ..Cdl::default() };
        let t = lut(&Look { cdl: Some(cdl.clone()), ..Look::default() }, 33);
        let (a, b) = (t.sample(c), cdl.apply(c));
        assert!((0..3).all(|k| (a[k] - b[k]).abs() < 4e-3), "{a:?} {b:?}");
        // wheels then curves: lift the blues, then a curve that crushes the blues
        let w = Wheels { lift: [0.0, 0.0, 0.2, 0.0], ..Wheels::default() };
        let cv = Curves::from_json(&json!({"b": [[0, 0], [0.5, 0.0], [1, 1]]})).unwrap();
        let t = lut(&Look { wheels: w, curves: cv, ..Look::default() }, 33);
        assert!(t.sample([0.0, 0.0, 0.0])[2] < 0.01, "the curve comes after the wheel");
        // a LUT that swaps red and blue: first, it's under the look; last, over it
        let mut text = String::from("LUT_3D_SIZE 2\n");
        for b in 0..2 { for g in 0..2 { for r in 0..2 { text += &format!("{b} {g} {r}\n"); } } }
        let cube = Arc::new(parse_cube(&text).unwrap());
        let swap = |first| Some(Ext { id: "swap".into(), mix: 1.0, first, cube: cube.clone() });
        let o = lut(&Look { ext: swap(false), ..Look::default() }, 33).sample(c);
        assert!((o[0] - 0.2).abs() < 4e-3 && (o[2] - 0.6).abs() < 4e-3, "{o:?}");
        let warm = Look { temp: 0.6, ..Look::default() };
        let (before, after) = (lut(&Look { ext: swap(true), ..warm.clone() }, 33).sample(c), lut(&Look { ext: swap(false), ..warm }, 33).sample(c));
        // first: swapped, then warmed (red up a touch); last: warmed, then swapped (the warmth lands in blue)
        assert!(before[0] > after[0] && before[2] < after[2], "{before:?} {after:?}");
        let half = lut(&Look { ext: Some(Ext { mix: 0.5, ..swap(false).unwrap() }), ..Look::default() }, 33).sample(c);
        assert!((half[0] - 0.4).abs() < 4e-3, "a half mix is halfway");
    }

    #[test]
    fn blur_keeps_a_flat_field_flat() {
        let mut b = vec![0.5f32; 50 * 30];
        blur(&mut b, 50, 30, 4);
        assert!(b.iter().all(|v| (v - 0.5).abs() < 1e-4));
    }
}
