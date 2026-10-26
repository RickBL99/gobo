//! A look: everything a grade does to a picture, as a handful of numbers a
//! colourist would recognise. The studio's film stocks and grades are presets
//! of it; a frame's look can be read back into one ("steal the look"); and it
//! renders three ways that always agree: per pixel, as a 3D LUT the grader
//! interpolates, and as a .cube file any editor can load.

use crate::color::{chroma, linear, oklab, smoothstep, Lab, Rgb};
use serde_json::{json, Map, Value};

#[derive(Clone, Debug, PartialEq)]
pub struct Look {
    /// stops, in linear light
    pub exposure: f32,
    /// white balance: + warmer, − cooler; + magenta, − green
    pub temp: f32,
    pub tint: f32,
    /// slope of the tone curve at the pivot (1: unchanged), and the pivot (Oklab L)
    pub contrast: f32,
    pub pivot: f32,
    /// where black and white land (a faded print lifts its blacks; a soft
    /// shoulder lowers its whites)
    pub fade: f32,
    pub white: f32,
    pub sat: f32,
    /// colour pushed into the shadows and the highlights, as Oklab (a, b)
    pub shadows: [f32; 2],
    pub highlights: [f32; 2],
    /// black and white through a filter: how much of R, G and B make the grey
    pub mono: Option<[f32; 3]>,
    /// skipping the bleach: contrast up, colour down, metallic
    pub bleach: f32,
    // spatial: these need the whole picture, so they're not in the LUT
    pub halation: f32,
    pub grain: f32,
    pub grain_size: f32,
    pub vignette: f32,
    /// how much of the look reaches the picture: 1 is all of it, 0 is the
    /// picture as shot (for a take whose prompt already asked for the stock)
    pub amount: f32,
}

impl Default for Look {
    fn default() -> Self {
        Look {
            exposure: 0.0, temp: 0.0, tint: 0.0, contrast: 1.0, pivot: 0.57, fade: 0.0, white: 1.0,
            sat: 1.0, shadows: [0.0; 2], highlights: [0.0; 2], mono: None, bleach: 0.0,
            halation: 0.0, grain: 0.0, grain_size: 1.4, vignette: 0.0, amount: 1.0,
        }
    }
}

const MONO_PAN: [f32; 3] = [0.2126, 0.7152, 0.0722];
const RED_FILTER: [f32; 3] = [0.45, 0.45, 0.10];

fn look(f: impl FnOnce(&mut Look)) -> Look {
    let mut l = Look::default();
    f(&mut l);
    l
}

/// The studio's film stocks (direction "film") as looks.
pub fn film(id: &str) -> Option<Look> {
    Some(match id {
        "clean" | "auto" | "" => Look::default(),
        "portra" => look(|l| {
            l.contrast = 0.9; l.sat = 0.88; l.fade = 0.035; l.white = 0.97; l.temp = 0.12;
            l.shadows = [-0.004, 0.006]; l.highlights = [0.006, 0.014]; l.grain = 0.14; l.halation = 0.12;
        }),
        "t500" => look(|l| {
            l.contrast = 1.05; l.sat = 0.95; l.fade = 0.02; l.shadows = [-0.012, -0.014];
            l.highlights = [0.008, 0.012]; l.grain = 0.3; l.grain_size = 1.6; l.halation = 0.45;
        }),
        "d250" => look(|l| {
            l.temp = 0.08; l.fade = 0.015; l.highlights = [0.004, 0.008]; l.grain = 0.16; l.halation = 0.15;
        }),
        "bw400" => look(|l| {
            l.mono = Some(RED_FILTER); l.sat = 0.0; l.contrast = 1.15; l.fade = 0.02; l.grain = 0.38;
            l.grain_size = 1.7;
        }),
        "slide" => look(|l| {
            l.contrast = 1.3; l.sat = 1.25; l.white = 0.99; l.highlights = [0.004, 0.01]; l.shadows = [0.0, -0.01];
            l.grain = 0.08;
        }),
        "expired" => look(|l| {
            l.contrast = 0.85; l.sat = 0.7; l.fade = 0.09; l.white = 0.94; l.shadows = [0.014, -0.008];
            l.highlights = [-0.006, 0.024]; l.grain = 0.45; l.grain_size = 1.8; l.halation = 0.2; l.vignette = 0.25;
        }),
        "bleach" => look(|l| {
            l.bleach = 0.7; l.contrast = 1.2; l.sat = 0.6; l.grain = 0.2;
        }),
        "instant" => look(|l| {
            l.contrast = 0.85; l.sat = 0.85; l.fade = 0.07; l.white = 0.92; l.shadows = [-0.012, -0.012];
            l.highlights = [0.01, 0.022]; l.grain = 0.15; l.vignette = 0.35;
        }),
        "vhs" => look(|l| {
            l.contrast = 0.9; l.sat = 1.15; l.fade = 0.05; l.white = 0.9; l.shadows = [0.004, -0.02];
            l.grain = 0.25; l.grain_size = 2.2;
        }),
        _ => return None,
    })
}

/// The studio's grades (direction "grade") as looks.
pub fn grade(id: &str) -> Option<Look> {
    Some(match id {
        "auto" | "natural" | "" => Look::default(),
        "teal" => look(|l| {
            l.shadows = [-0.034, -0.018]; l.highlights = [0.02, 0.035]; l.sat = 1.05; l.contrast = 1.05;
        }),
        "noir" => look(|l| {
            l.mono = Some(MONO_PAN); l.sat = 0.0; l.contrast = 1.35;
        }),
        "kodachrome" => look(|l| {
            l.sat = 1.3; l.contrast = 1.15; l.shadows = [-0.004, -0.02]; l.highlights = [0.01, 0.02];
        }),
        "neon" => look(|l| {
            l.shadows = [0.03, -0.035]; l.highlights = [-0.03, -0.02]; l.sat = 1.25; l.contrast = 1.1;
        }),
        "desert" => look(|l| {
            l.temp = 0.25; l.sat = 0.9; l.contrast = 0.95; l.shadows = [0.01, 0.015]; l.highlights = [0.01, 0.03];
        }),
        "nordic" => look(|l| {
            l.temp = -0.2; l.sat = 0.65; l.contrast = 0.95; l.fade = 0.03; l.shadows = [-0.01, -0.02];
        }),
        "pastel" => look(|l| {
            l.contrast = 0.8; l.sat = 0.75; l.fade = 0.1; l.highlights = [0.01, 0.005];
        }),
        _ => return None,
    })
}

pub const FILMS: [&str; 10] = ["clean", "portra", "t500", "d250", "bw400", "slide", "expired", "bleach", "instant", "vhs"];
pub const GRADES: [&str; 8] = ["natural", "teal", "noir", "kodachrome", "neon", "desert", "nordic", "pastel"];

/// A stock printed through a grade: the curves multiply, the colour pushes add.
pub fn compose(a: &Look, b: &Look) -> Look {
    Look {
        exposure: a.exposure + b.exposure,
        temp: a.temp + b.temp,
        tint: a.tint + b.tint,
        contrast: a.contrast * b.contrast,
        pivot: a.pivot,
        fade: 1.0 - (1.0 - a.fade) * (1.0 - b.fade),
        white: a.white * b.white,
        sat: a.sat * b.sat,
        shadows: [a.shadows[0] + b.shadows[0], a.shadows[1] + b.shadows[1]],
        highlights: [a.highlights[0] + b.highlights[0], a.highlights[1] + b.highlights[1]],
        mono: b.mono.or(a.mono),
        bleach: a.bleach.max(b.bleach),
        halation: a.halation.max(b.halation),
        grain: a.grain.max(b.grain),
        grain_size: if b.grain > a.grain { b.grain_size } else { a.grain_size },
        vignette: a.vignette.max(b.vignette),
        amount: a.amount * b.amount,
    }
}

impl Look {
    /// One colour through the look, linear light in and out.
    pub fn apply(&self, c: Rgb) -> Rgb {
        let e = self.exposure.exp2();
        let mut c = [
            c[0] * e * (1.0 + 0.10 * self.temp),
            c[1] * e * (1.0 - 0.08 * self.tint),
            c[2] * e * (1.0 - 0.10 * self.temp),
        ];
        if let Some(m) = self.mono {
            let y = m[0] * c[0] + m[1] * c[1] + m[2] * c[2];
            c = [y, y, y];
        }
        let lab = oklab(c);
        let lab = self.tone(lab);
        let out = linear(lab);
        [out[0].max(0.0), out[1].max(0.0), out[2].max(0.0)]
    }

    fn curve(&self, l: f32) -> f32 {
        let k = self.contrast * (1.0 + 0.35 * self.bleach);
        let p = self.pivot;
        let l = l.clamp(0.0, 1.0);
        let t = if l < p { p * (l / p).powf(k) } else { 1.0 - (1.0 - p) * ((1.0 - l) / (1.0 - p)).powf(k) };
        self.fade + t * (self.white - self.fade)
    }

    fn tone(&self, c: Lab) -> Lab {
        let l = self.curve(c[0]);
        let keep = (self.sat * (1.0 - 0.65 * self.bleach)).max(0.0)
            * smoothstep(0.0, 0.12, l)
            * (1.0 - 0.6 * smoothstep(0.93, 1.0, l));
        let w = smoothstep(0.15, 0.85, l);
        let clean = 1.0 - 0.5 * smoothstep(0.9, 1.0, l);
        let a = c[1] * keep + (self.shadows[0] * (1.0 - w) + self.highlights[0] * w) * clean;
        let b = c[2] * keep + (self.shadows[1] * (1.0 - w) + self.highlights[1] * w) * clean;
        [l, a, b]
    }

    /// What the look does, measured on a chart: where black and white land,
    /// how steep the mid-tones are, how much colour survives, and the colour
    /// it pushes into a grey shadow and a grey highlight. Presets and a frame's
    /// stolen look are compared on these, so they're compared like for like.
    pub fn signature(&self) -> [f32; 9] {
        let grey = |l: f32| oklab(self.apply(linear([l, 0.0, 0.0])));
        let (k, w) = (grey(0.0), grey(1.0));
        let (lo, hi) = (grey(0.35), grey(0.78));
        let (sh, hl) = (grey(0.25), grey(0.85));
        let mut sat = 0.0;
        for i in 0..6 {
            let h = i as f32 * std::f32::consts::PI / 3.0;
            let input: Lab = [0.62, 0.07 * h.cos(), 0.07 * h.sin()];
            let out = oklab(self.apply(linear(input)));
            let tint = grey(0.62);
            sat += chroma([0.0, out[1] - tint[1], out[2] - tint[2]]) / 0.07;
        }
        [k[0], w[0], (hi[0] - lo[0]) / 0.43, sat / 6.0, sh[1], sh[2], hl[1], hl[2], self.grain]
    }

    pub fn to_json(&self) -> Value {
        let r = |v: f32| (v * 10000.0).round() / 10000.0;
        let mut m = json!({
            "exposure": r(self.exposure), "temp": r(self.temp), "tint": r(self.tint), "contrast": r(self.contrast),
            "pivot": r(self.pivot), "fade": r(self.fade), "white": r(self.white), "sat": r(self.sat),
            "shadows": [r(self.shadows[0]), r(self.shadows[1])],
            "highlights": [r(self.highlights[0]), r(self.highlights[1])],
            "bleach": r(self.bleach), "halation": r(self.halation), "grain": r(self.grain),
            "grain_size": r(self.grain_size), "vignette": r(self.vignette), "amount": r(self.amount),
        });
        if let Some(mo) = self.mono {
            m["mono"] = json!([r(mo[0]), r(mo[1]), r(mo[2])]);
        }
        m
    }

    /// A look from JSON: a preset by name ({"film": "portra", "grade": "teal"}),
    /// numbers on top of it, or both; anything missing stays as it was.
    pub fn from_json(v: &Value) -> Result<Look, String> {
        let o = v.as_object().ok_or("a look is a JSON object")?;
        let pick = |key: &str, f: fn(&str) -> Option<Look>| -> Result<Look, String> {
            match o.get(key).and_then(Value::as_str) {
                None => Ok(Look::default()),
                Some(id) => f(id).ok_or_else(|| format!("no {key} called {id}")),
            }
        };
        let mut l = compose(&pick("film", film)?, &pick("grade", grade)?);
        let num = |m: &Map<String, Value>, k: &str| m.get(k).and_then(Value::as_f64).map(|v| v as f32);
        let pair = |m: &Map<String, Value>, k: &str| -> Option<[f32; 2]> {
            let a = m.get(k)?.as_array()?;
            Some([a.first()?.as_f64()? as f32, a.get(1)?.as_f64()? as f32])
        };
        macro_rules! set {
            ($($f:ident),*) => { $( if let Some(v) = num(o, stringify!($f)) { l.$f = v; } )* };
        }
        set!(exposure, temp, tint, contrast, pivot, fade, white, sat, bleach, halation, grain, grain_size, vignette, amount);
        if let Some(p) = pair(o, "shadows") {
            l.shadows = p;
        }
        if let Some(p) = pair(o, "highlights") {
            l.highlights = p;
        }
        match o.get("mono") {
            Some(Value::Bool(true)) => l.mono = Some(MONO_PAN),
            Some(Value::Bool(false)) => l.mono = None,
            Some(Value::Array(a)) if a.len() == 3 => {
                let f = |i: usize| a[i].as_f64().unwrap_or(0.0) as f32;
                l.mono = Some([f(0), f(1), f(2)]);
            }
            _ => {}
        }
        l.contrast = l.contrast.clamp(0.2, 3.0);
        l.fade = l.fade.clamp(0.0, 0.5);
        l.white = l.white.clamp(0.5, 1.0).max(l.fade + 0.2);
        l.grain_size = l.grain_size.clamp(0.5, 6.0);
        l.amount = l.amount.clamp(0.0, 1.0);
        Ok(l)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_look_changes_nothing() {
        let l = Look::default();
        for c in [[0.0, 0.0, 0.0], [0.18, 0.18, 0.18], [0.9, 0.2, 0.05], [1.0, 1.0, 1.0]] {
            let o = l.apply(c);
            for i in 0..3 {
                assert!((o[i] - c[i]).abs() < 2e-3, "{c:?} → {o:?}");
            }
        }
    }

    #[test]
    fn presets_do_what_they_say() {
        let sig = |l: Look| l.signature();
        let clean = sig(Look::default());
        assert!(sig(film("expired").unwrap())[0] > clean[0] + 0.05, "expired lifts the blacks");
        assert!(sig(film("slide").unwrap())[2] > 1.15, "slide is contrasty");
        assert!(sig(grade("noir").unwrap())[3] < 0.05, "noir has no colour");
        let teal = sig(grade("teal").unwrap());
        assert!(teal[4] < -0.02 && teal[5] < -0.01, "teal shadows");
        assert!(teal[7] > 0.02, "warm highlights");
        for id in FILMS {
            assert!(film(id).is_some(), "{id}");
        }
        for id in GRADES {
            assert!(grade(id).is_some(), "{id}");
        }
    }

    #[test]
    fn json_round_trip_and_presets_by_name() {
        let l = Look::from_json(&json!({"film": "portra", "grade": "teal", "grain": 0.5})).unwrap();
        assert_eq!(l.grain, 0.5);
        assert!((l.contrast - 0.9 * 1.05).abs() < 1e-6);
        let back = Look::from_json(&l.to_json()).unwrap();
        assert!((back.sat - l.sat).abs() < 1e-3 && back.mono == l.mono);
        assert!(Look::from_json(&json!({"film": "nope"})).is_err());
    }
}
