//! The colourist's tools, as DaVinci Resolve's Color page has them (in
//! miniature): an ASC CDL a colourist hands over, the primary wheels (lift,
//! gamma, gain and offset), custom curves, and a LUT from any editor (.cube).
//!
//! All of them change one colour at a time, so all of them go into the same
//! 3D LUT as the look ([`crate::grade::lut`]): they cost nothing per pixel,
//! and the .cube Gobo exports carries them. They work on display values
//! (sRGB, 0–1), as a grading panel on Rec.709 footage does.

use crate::grade::Lut;
use serde_json::{json, Value};
use std::sync::Arc;

pub type Rgb = [f32; 3];

const LUMA: Rgb = [0.2126, 0.7152, 0.0722];

#[inline]
fn luma(c: Rgb) -> f32 {
    LUMA[0] * c[0] + LUMA[1] * c[1] + LUMA[2] * c[2]
}

fn num(v: Option<&Value>) -> Option<f32> {
    v.and_then(Value::as_f64).map(|x| x as f32)
}

fn trio(v: Option<&Value>, default: f32) -> Result<Rgb, String> {
    match v {
        None | Some(Value::Null) => Ok([default; 3]),
        Some(Value::Number(n)) => Ok([n.as_f64().unwrap_or(default as f64) as f32; 3]),
        Some(Value::Array(a)) if a.len() == 3 => {
            let mut o = [default; 3];
            for (k, x) in a.iter().enumerate() {
                o[k] = x.as_f64().ok_or("a CDL value is a number")? as f32;
            }
            Ok(o)
        }
        _ => Err("a CDL slope, offset or power is three numbers".into()),
    }
}

// ── ASC CDL ─────────────────────────────────────────────────────────────────
/// The American Society of Cinematographers' Color Decision List: per
/// channel `out = (in × slope + offset) ^ power`, then saturation about
/// Rec.709 luma. Every grading tool reads and writes it (.cdl, .cc, .ccc).
#[derive(Clone, Debug, PartialEq)]
pub struct Cdl {
    pub slope: Rgb,
    pub offset: Rgb,
    pub power: Rgb,
    pub sat: f32,
}

impl Default for Cdl {
    fn default() -> Self {
        Cdl { slope: [1.0; 3], offset: [0.0; 3], power: [1.0; 3], sat: 1.0 }
    }
}

impl Cdl {
    pub fn apply(&self, c: Rgb) -> Rgb {
        let mut o = [0.0; 3];
        for k in 0..3 {
            o[k] = (c[k] * self.slope[k] + self.offset[k]).clamp(0.0, 1.0).powf(self.power[k]);
        }
        let y = luma(o);
        o.map(|v| (y + self.sat * (v - y)).clamp(0.0, 1.0))
    }

    pub fn from_json(v: &Value) -> Result<Cdl, String> {
        let o = v.as_object().ok_or("a CDL is {slope, offset, power, sat}")?;
        let c = Cdl {
            slope: trio(o.get("slope"), 1.0)?.map(|x| x.clamp(0.0, 8.0)),
            offset: trio(o.get("offset"), 0.0)?.map(|x| x.clamp(-1.0, 1.0)),
            power: trio(o.get("power"), 1.0)?.map(|x| x.clamp(0.1, 8.0)),
            sat: num(o.get("sat")).unwrap_or(1.0).clamp(0.0, 4.0),
        };
        Ok(c)
    }

    pub fn to_json(&self) -> Value {
        let r = |v: f32| (v as f64 * 100000.0).round() / 100000.0;
        json!({"slope": self.slope.map(r), "offset": self.offset.map(r), "power": self.power.map(r), "sat": r(self.sat)})
    }
}

// ── the primary wheels ──────────────────────────────────────────────────────
/// Lift, gamma, gain and offset, each as red, green, blue and a master, 0
/// being untouched. Lift moves the blacks (white stays put), gain the whites
/// (black stays put), gamma the mid-tones, offset everything evenly:
/// `out = (gain × (in + lift × (1 − in))) ^ 2^−gamma + offset`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Wheels {
    pub lift: [f32; 4],
    pub gamma: [f32; 4],
    pub gain: [f32; 4],
    pub offset: [f32; 4],
}

impl Wheels {
    pub fn is_identity(&self) -> bool {
        *self == Wheels::default()
    }

    pub fn apply(&self, c: Rgb) -> Rgb {
        let mut o = [0.0; 3];
        for k in 0..3 {
            let lift = self.lift[k] + self.lift[3];
            let gamma = self.gamma[k] + self.gamma[3];
            let gain = 1.0 + self.gain[k] + self.gain[3];
            let v = (gain * (c[k] + lift * (1.0 - c[k]))).max(0.0);
            o[k] = v.powf((-gamma).exp2()) + self.offset[k] + self.offset[3];
        }
        o
    }

    pub fn from_json(v: &Value) -> Result<Wheels, String> {
        let o = v.as_object().ok_or("the wheels are {lift, gamma, gain, offset}")?;
        let four = |k: &str, lim: f32| -> Result<[f32; 4], String> {
            match o.get(k) {
                None | Some(Value::Null) => Ok([0.0; 4]),
                Some(Value::Array(a)) if a.len() == 4 => {
                    let mut w = [0.0; 4];
                    for (i, x) in a.iter().enumerate() {
                        w[i] = (x.as_f64().ok_or("a wheel value is a number")? as f32).clamp(-lim, lim);
                    }
                    Ok(w)
                }
                _ => Err(format!("{k} is four numbers: red, green, blue and master")),
            }
        };
        Ok(Wheels { lift: four("lift", 1.0)?, gamma: four("gamma", 1.0)?, gain: four("gain", 2.0)?, offset: four("offset", 1.0)? })
    }

    pub fn to_json(&self) -> Value {
        let r = |v: f32| (v as f64 * 10000.0).round() / 10000.0;
        json!({"lift": self.lift.map(r), "gamma": self.gamma.map(r), "gain": self.gain.map(r), "offset": self.offset.map(r)})
    }
}

// ── curves ──────────────────────────────────────────────────────────────────
/// One curve through points, monotone between them (Fritsch–Carlson): it
/// never overshoots a point the way a plain spline does, so dragging one
/// point can't fold the curve back on itself.
#[derive(Clone, Debug, PartialEq)]
pub struct Curve {
    x: Vec<f32>,
    y: Vec<f32>,
    m: Vec<f32>,
}

impl Curve {
    pub fn new(points: &[[f32; 2]]) -> Result<Curve, String> {
        let mut p: Vec<[f32; 2]> = points.iter().map(|q| [q[0].clamp(0.0, 1.0), q[1].clamp(0.0, 1.0)]).collect();
        p.sort_by(|a, b| a[0].partial_cmp(&b[0]).unwrap_or(std::cmp::Ordering::Equal));
        p.dedup_by(|a, b| (a[0] - b[0]).abs() < 1e-4);
        if p.is_empty() {
            return Err("a curve needs a point".into());
        }
        if p.len() > 32 {
            return Err("a curve takes up to 32 points".into());
        }
        // the ends hold flat to the first and last point
        if p[0][0] > 0.0 {
            p.insert(0, [0.0, p[0][1]]);
        }
        if p[p.len() - 1][0] < 1.0 {
            let last = p[p.len() - 1][1];
            p.push([1.0, last]);
        }
        let n = p.len();
        let x: Vec<f32> = p.iter().map(|q| q[0]).collect();
        let y: Vec<f32> = p.iter().map(|q| q[1]).collect();
        let d: Vec<f32> = (0..n - 1).map(|i| (y[i + 1] - y[i]) / (x[i + 1] - x[i])).collect();
        let mut m = vec![0.0f32; n];
        m[0] = d[0];
        m[n - 1] = d[n - 2];
        for i in 1..n - 1 {
            m[i] = if d[i - 1] * d[i] <= 0.0 { 0.0 } else { (d[i - 1] + d[i]) / 2.0 };
        }
        for i in 0..n - 1 {
            if d[i] == 0.0 {
                m[i] = 0.0;
                m[i + 1] = 0.0;
                continue;
            }
            let (a, b) = (m[i] / d[i], m[i + 1] / d[i]);
            let s = a * a + b * b;
            if s > 9.0 {
                let t = 3.0 / s.sqrt();
                m[i] = t * a * d[i];
                m[i + 1] = t * b * d[i];
            }
        }
        Ok(Curve { x, y, m })
    }

    pub fn is_identity(&self) -> bool {
        self.x.iter().zip(&self.y).all(|(a, b)| (a - b).abs() < 1e-4) && self.m.iter().all(|m| (m - 1.0).abs() < 1e-3)
    }

    pub fn at(&self, v: f32) -> f32 {
        let v = v.clamp(0.0, 1.0);
        let n = self.x.len();
        let i = match self.x.iter().position(|&x| x > v) {
            Some(0) => 0,
            Some(i) => i - 1,
            None => n - 2,
        };
        let h = self.x[i + 1] - self.x[i];
        let t = ((v - self.x[i]) / h).clamp(0.0, 1.0);
        let (t2, t3) = (t * t, t * t * t);
        let out = (2.0 * t3 - 3.0 * t2 + 1.0) * self.y[i]
            + (t3 - 2.0 * t2 + t) * h * self.m[i]
            + (-2.0 * t3 + 3.0 * t2) * self.y[i + 1]
            + (t3 - t2) * h * self.m[i + 1];
        out.clamp(0.0, 1.0)
    }

    fn points(&self) -> Value {
        let r = |v: f32| (v as f64 * 10000.0).round() / 10000.0;
        Value::Array(self.x.iter().zip(&self.y).map(|(a, b)| json!([r(*a), r(*b)])).collect())
    }
}

/// The custom curves: Y (all three channels, as a master) and then red,
/// green and blue on their own.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Curves {
    pub y: Option<Curve>,
    pub r: Option<Curve>,
    pub g: Option<Curve>,
    pub b: Option<Curve>,
}

impl Curves {
    pub fn apply(&self, c: Rgb) -> Rgb {
        let mut o = c;
        if let Some(y) = &self.y {
            o = o.map(|v| y.at(v));
        }
        for (k, cv) in [&self.r, &self.g, &self.b].into_iter().enumerate() {
            if let Some(cv) = cv {
                o[k] = cv.at(o[k]);
            }
        }
        o
    }

    pub fn is_identity(&self) -> bool {
        [&self.y, &self.r, &self.g, &self.b].iter().all(|c| c.as_ref().is_none_or(Curve::is_identity))
    }

    pub fn from_json(v: &Value) -> Result<Curves, String> {
        let o = v.as_object().ok_or("the curves are {y, r, g, b}, each a list of [x, y] points")?;
        let one = |k: &str| -> Result<Option<Curve>, String> {
            let Some(a) = o.get(k).and_then(Value::as_array) else { return Ok(None) };
            let pts: Option<Vec<[f32; 2]>> = a.iter().map(|p| {
                let p = p.as_array()?;
                Some([p.first()?.as_f64()? as f32, p.get(1)?.as_f64()? as f32])
            }).collect();
            let c = Curve::new(&pts.ok_or_else(|| format!("the {k} curve's points are [x, y] pairs"))?)?;
            Ok(if c.is_identity() { None } else { Some(c) })
        };
        Ok(Curves { y: one("y")?, r: one("r")?, g: one("g")?, b: one("b")? })
    }

    pub fn to_json(&self) -> Value {
        let mut m = serde_json::Map::new();
        for (k, c) in [("y", &self.y), ("r", &self.r), ("g", &self.g), ("b", &self.b)] {
            if let Some(c) = c {
                m.insert(k.into(), c.points());
            }
        }
        Value::Object(m)
    }
}

// ── a LUT from elsewhere ────────────────────────────────────────────────────
/// A .cube file as Gobo holds it: a 3D table over [0, 1] (a 1D file's three
/// curves are sampled into one), and the input range it was written for.
#[derive(Debug)]
pub struct Cube {
    pub title: String,
    pub table: Lut,
    pub min: Rgb,
    pub max: Rgb,
    /// "3D" or "1D", as the file was
    pub kind: &'static str,
    /// the file's own size (a 1D file's entries, a 3D file's edge)
    pub size: usize,
}

/// Read a .cube (Adobe's format, written by Resolve, Premiere, Final Cut,
/// Photoshop and most LUT packs): 3D or 1D, with DOMAIN_MIN/MAX or Resolve's
/// LUT_*_INPUT_RANGE.
pub fn parse_cube(text: &str) -> Result<Cube, String> {
    let (mut title, mut n3, mut n1) = (String::new(), 0usize, 0usize);
    let (mut min, mut max) = ([0.0f32; 3], [1.0f32; 3]);
    let mut rows: Vec<Rgb> = Vec::new();
    let three = |parts: &[&str]| -> Option<Rgb> {
        if parts.len() < 3 {
            return None;
        }
        Some([parts[0].parse().ok()?, parts[1].parse().ok()?, parts[2].parse().ok()?])
    };
    for (no, raw) in text.lines().enumerate() {
        let line = raw.split('#').next().unwrap_or("").trim();
        if line.is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        let key = parts[0].to_ascii_uppercase();
        match key.as_str() {
            "TITLE" => title = line[5..].trim().trim_matches('"').chars().take(120).collect(),
            "LUT_3D_SIZE" => n3 = parts.get(1).and_then(|s| s.parse().ok()).ok_or("LUT_3D_SIZE needs a number")?,
            "LUT_1D_SIZE" => n1 = parts.get(1).and_then(|s| s.parse().ok()).ok_or("LUT_1D_SIZE needs a number")?,
            "DOMAIN_MIN" => min = three(&parts[1..]).ok_or("DOMAIN_MIN needs three numbers")?,
            "DOMAIN_MAX" => max = three(&parts[1..]).ok_or("DOMAIN_MAX needs three numbers")?,
            "LUT_3D_INPUT_RANGE" | "LUT_1D_INPUT_RANGE" => {
                let lo: f32 = parts.get(1).and_then(|s| s.parse().ok()).ok_or("an input range needs two numbers")?;
                let hi: f32 = parts.get(2).and_then(|s| s.parse().ok()).ok_or("an input range needs two numbers")?;
                min = [lo; 3];
                max = [hi; 3];
            }
            _ if key.starts_with(|c: char| c.is_ascii_digit() || c == '-' || c == '+' || c == '.') => {
                rows.push(three(&parts).ok_or_else(|| format!("line {}: a row is three numbers", no + 1))?);
            }
            _ => {} // keywords other editors add (LUT_IN_VIDEO_RANGE, …)
        }
    }
    if (0..3).any(|k| max[k] <= min[k]) {
        return Err("the input range is empty (DOMAIN_MAX must be above DOMAIN_MIN)".into());
    }
    if n3 > 0 {
        if !(2..=129).contains(&n3) {
            return Err(format!("a 3D LUT of size {n3} isn't one Gobo can hold (2 to 129)"));
        }
        if rows.len() != n3 * n3 * n3 {
            return Err(format!("a size-{n3} 3D LUT has {} rows; this file has {}", n3 * n3 * n3, rows.len()));
        }
        return Ok(Cube { title, table: Lut { n: n3, data: rows }, min, max, kind: "3D", size: n3 });
    }
    if n1 > 0 {
        if !(2..=65536).contains(&n1) || rows.len() != n1 {
            return Err(format!("a size-{n1} 1D LUT has {n1} rows; this file has {}", rows.len()));
        }
        // three curves → one 3D table (each channel through its own curve)
        let curve = |v: f32, k: usize| {
            let x = v.clamp(0.0, 1.0) * (n1 - 1) as f32;
            let i = (x as usize).min(n1 - 2);
            let f = x - i as f32;
            rows[i][k] * (1.0 - f) + rows[i + 1][k] * f
        };
        let n = 33;
        let s = (n - 1) as f32;
        let mut data = Vec::with_capacity(n * n * n);
        for b in 0..n {
            for g in 0..n {
                for r in 0..n {
                    data.push([curve(r as f32 / s, 0), curve(g as f32 / s, 1), curve(b as f32 / s, 2)]);
                }
            }
        }
        return Ok(Cube { title, table: Lut { n, data }, min, max, kind: "1D", size: n1 });
    }
    Err("not a .cube LUT: no LUT_3D_SIZE or LUT_1D_SIZE".into())
}

impl Cube {
    pub fn apply(&self, c: Rgb) -> Rgb {
        let mut x = [0.0; 3];
        for k in 0..3 {
            x[k] = (c[k] - self.min[k]) / (self.max[k] - self.min[k]);
        }
        self.table.sample(x)
    }
}

/// A loaded .cube on a look: how much of it, and whether it comes first (a
/// camera's log-to-video LUT) or after the look (a creative LUT).
#[derive(Clone, Debug)]
pub struct Ext {
    pub id: String,
    pub mix: f32,
    pub first: bool,
    pub cube: Arc<Cube>,
}

impl PartialEq for Ext {
    fn eq(&self, o: &Ext) -> bool {
        // ids are the file's own hash: one id, one table
        self.id == o.id && self.mix == o.mix && self.first == o.first
    }
}

impl Ext {
    pub fn apply(&self, c: Rgb) -> Rgb {
        let s = self.cube.apply(c);
        [0, 1, 2].map(|k| c[k] + (s[k] - c[k]) * self.mix)
    }

    pub fn from_json(v: &Value, find: &dyn Fn(&str) -> Option<Arc<Cube>>) -> Result<Ext, String> {
        let id = v.get("id").and_then(Value::as_str).ok_or("a LUT on a look needs its id")?;
        let cube = find(id).ok_or_else(|| format!("the LUT {id} isn't loaded"))?;
        Ok(Ext {
            id: id.to_string(),
            mix: num(v.get("mix")).unwrap_or(1.0).clamp(0.0, 1.0),
            first: v.get("first").and_then(Value::as_bool).unwrap_or(false),
            cube,
        })
    }

    pub fn to_json(&self) -> Value {
        json!({"id": self.id, "mix": (self.mix as f64 * 1000.0).round() / 1000.0, "first": self.first, "title": self.cube.title})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: Rgb, b: Rgb, tol: f32) -> bool {
        (0..3).all(|k| (a[k] - b[k]).abs() <= tol)
    }

    #[test]
    fn the_cdl_is_the_asc_formula() {
        let c = Cdl { slope: [1.2, 1.0, 0.8], offset: [0.02, 0.0, -0.01], power: [1.0, 0.9, 1.1], sat: 1.0 };
        let o = c.apply([0.5, 0.5, 0.5]);
        assert!(close(o, [0.62, 0.5f32.powf(0.9), 0.39f32.powf(1.1)], 1e-5), "{o:?}");
        let grey = Cdl { sat: 0.0, ..Cdl::default() }.apply([0.8, 0.3, 0.1]);
        assert!(close(grey, [luma([0.8, 0.3, 0.1]); 3], 1e-5));
        assert_eq!(Cdl::from_json(&Cdl::default().to_json()).unwrap(), Cdl::default());
    }

    #[test]
    fn wheels_move_blacks_mids_and_whites() {
        let id = Wheels::default();
        assert!(id.is_identity() && close(id.apply([0.3, 0.6, 0.9]), [0.3, 0.6, 0.9], 1e-6));
        let lift = Wheels { lift: [0.0, 0.0, 0.1, 0.0], ..Wheels::default() };
        assert!(lift.apply([0.0; 3])[2] > 0.09 && (lift.apply([1.0; 3])[2] - 1.0).abs() < 1e-6, "lift moves black, not white");
        let gain = Wheels { gain: [0.0, 0.0, 0.0, -0.2], ..Wheels::default() };
        assert!(gain.apply([0.0; 3])[0].abs() < 1e-6 && (gain.apply([1.0; 3])[0] - 0.8).abs() < 1e-6, "gain moves white, not black");
        let gamma = Wheels { gamma: [0.0, 0.0, 0.0, 0.3], ..Wheels::default() };
        let mid = gamma.apply([0.5; 3])[1];
        assert!(mid > 0.55 && gamma.apply([0.0; 3])[1] == 0.0 && (gamma.apply([1.0; 3])[1] - 1.0).abs() < 1e-6, "gamma lifts mids only: {mid}");
        let w = Wheels::from_json(&json!({"lift": [0.01, 0, 0, 0], "gain": [0, 0.05, 0, 0.1]})).unwrap();
        assert_eq!(Wheels::from_json(&w.to_json()).unwrap(), w);
        assert!(Wheels::from_json(&json!({"lift": [1, 2]})).is_err());
    }

    #[test]
    fn curves_pass_through_their_points_and_never_overshoot() {
        let c = Curve::new(&[[0.0, 0.0], [0.25, 0.15], [0.75, 0.85], [1.0, 1.0]]).unwrap();
        for (x, y) in [(0.0, 0.0), (0.25, 0.15), (0.75, 0.85), (1.0, 1.0)] {
            assert!((c.at(x) - y).abs() < 1e-5);
        }
        let mut last = -1.0;
        for i in 0..=200 {
            let v = c.at(i as f32 / 200.0);
            assert!(v >= last - 1e-6, "monotone");
            last = v;
        }
        assert!(Curve::new(&[[0.0, 0.0], [1.0, 1.0]]).unwrap().is_identity());
        let cs = Curves::from_json(&json!({"y": [[0, 0], [1, 1]], "b": [[0, 0.1], [1, 0.9]]})).unwrap();
        assert!(cs.y.is_none(), "an identity curve is dropped");
        assert!(close(cs.apply([0.0, 0.0, 0.0]), [0.0, 0.0, 0.1], 1e-5));
        assert_eq!(Curves::from_json(&cs.to_json()).unwrap(), cs);
    }

    #[test]
    fn a_cube_file_reads_and_applies() {
        // a 2-point 3D LUT that swaps red and blue
        let mut s = String::from("# a test\nTITLE \"Swap\"\nLUT_3D_SIZE 2\n");
        for b in 0..2 {
            for g in 0..2 {
                for r in 0..2 {
                    s += &format!("{b} {g} {r}\n");
                }
            }
        }
        let c = parse_cube(&s).unwrap();
        assert_eq!((c.title.as_str(), c.kind, c.size), ("Swap", "3D", 2));
        assert!(close(c.apply([0.8, 0.4, 0.2]), [0.2, 0.4, 0.8], 1e-5));
        // a 1D LUT that halves everything, over a 0–2 input range
        let one = "LUT_1D_SIZE 3\nLUT_1D_INPUT_RANGE 0.0 2.0\n0 0 0\n0.5 0.5 0.5\n1 1 1\n";
        let c = parse_cube(one).unwrap();
        assert!(close(c.apply([1.0, 0.5, 2.0]), [0.5, 0.25, 1.0], 1e-3), "{:?}", c.apply([1.0, 0.5, 2.0]));
        assert!(parse_cube("LUT_3D_SIZE 3\n0 0 0\n").unwrap_err().contains("27 rows"));
        assert!(parse_cube("hello").is_err());
    }
}
