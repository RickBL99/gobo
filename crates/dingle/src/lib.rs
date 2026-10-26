//! dingle — draw to direct. (A dingle is a leafy branch a grip holds in
//! front of a lamp so the light falls dappled: a sketch in front of the light.)
//!
//! Draw on the frame with two pens. The camera pen says how the camera
//! moves: a line toward the middle is a push-in, a sweep across is a track,
//! an arc is an arc, a full loop an orbit, a spiral inward a crash into the
//! eyes, a scribble is handheld; drawn fast it's a whip. The subject pen says
//! where things go: from the left foreground to the right background,
//! toward camera, rising, circling. Each stroke is simplified, measured
//! (length, straightness, how far it turns, which way, how fast) and named;
//! the result is a move id from the studio's vocabulary and a line of action.

use serde_json::{json, Value};

type P = [f32; 2];

#[derive(Debug, Clone, PartialEq)]
pub struct Stroke {
    pub camera: bool,
    pub label: Option<String>,
    pub pts: Vec<P>,
    /// milliseconds from the first point, when the page sent them
    pub ms: Option<f32>,
}

#[derive(Debug)]
pub struct Shape {
    pub kind: &'static str,
    pub length: f32,
    pub straight: f32,
    /// signed degrees: + clockwise on screen (y grows downward)
    pub turn: f32,
    pub reversals: usize,
    pub start: P,
    pub end: P,
    pub chord: P,
    /// how the distance to the stroke's own centre changes, end vs start
    pub radius_trend: f32,
    pub speed: Option<f32>,
}

fn sub(a: P, b: P) -> P {
    [a[0] - b[0], a[1] - b[1]]
}
fn len(a: P) -> f32 {
    (a[0] * a[0] + a[1] * a[1]).sqrt()
}

/// Ramer–Douglas–Peucker: keep the points that carry the shape.
pub fn simplify(pts: &[P], eps: f32) -> Vec<P> {
    if pts.len() < 3 {
        return pts.to_vec();
    }
    let (a, b) = (pts[0], pts[pts.len() - 1]);
    let ab = sub(b, a);
    let l = len(ab);
    let (mut far, mut idx) = (0.0, 0);
    for (i, p) in pts.iter().enumerate().take(pts.len() - 1).skip(1) {
        let d = if l < 1e-6 { len(sub(*p, a)) } else { ((ab[0] * (a[1] - p[1]) - ab[1] * (a[0] - p[0])).abs()) / l };
        if d > far {
            far = d;
            idx = i;
        }
    }
    if far > eps {
        let mut left = simplify(&pts[..=idx], eps);
        let right = simplify(&pts[idx..], eps);
        left.pop();
        left.extend(right);
        left
    } else {
        vec![a, b]
    }
}

pub fn measure(s: &Stroke) -> Option<Shape> {
    let mut pts: Vec<P> = Vec::with_capacity(s.pts.len());
    for p in &s.pts {
        if pts.last().map_or(true, |q| len(sub(*p, *q)) > 1e-4) {
            pts.push(*p);
        }
    }
    if pts.len() < 2 {
        return None;
    }
    let length: f32 = pts.windows(2).map(|w| len(sub(w[1], w[0]))).sum();
    let (start, end) = (pts[0], pts[pts.len() - 1]);
    let chord = sub(end, start);
    let simple = simplify(&pts, 0.012);
    let (mut turn, mut reversals, mut last_sign) = (0.0f32, 0usize, 0.0f32);
    for w in simple.windows(3) {
        let (u, v) = (sub(w[1], w[0]), sub(w[2], w[1]));
        let a = (u[0] * v[1] - u[1] * v[0]).atan2(u[0] * v[0] + u[1] * v[1]).to_degrees();
        turn += a;
        if a.abs() > 60.0 {
            if last_sign != 0.0 && a.signum() != last_sign {
                reversals += 1;
            }
            last_sign = a.signum();
        }
    }
    // also count turns on the raw points (a tight scribble simplifies away)
    let n = pts.len() as f32;
    let centre = pts.iter().fold([0.0, 0.0], |acc, p| [acc[0] + p[0] / n, acc[1] + p[1] / n]);
    let quarter = (pts.len() / 4).max(1);
    let r = |sl: &[P]| sl.iter().map(|p| len(sub(*p, centre))).sum::<f32>() / sl.len() as f32;
    let radius_trend = r(&pts[pts.len() - quarter..]) - r(&pts[..quarter]);
    let straight = if length > 0.0 { len(chord) / length } else { 0.0 };
    let speed = s.ms.filter(|ms| *ms > 30.0).map(|ms| length / (ms / 1000.0));
    let kind = if length < 0.035 {
        "dot"
    } else if reversals >= 3 && straight < 0.6 {
        "zigzag"
    } else if turn.abs() >= 290.0 {
        if radius_trend < -0.06 {
            "spiral-in"
        } else if radius_trend > 0.06 {
            "spiral-out"
        } else {
            "loop"
        }
    } else if turn.abs() >= 55.0 && straight < 0.9 {
        if reversals >= 1 && straight > 0.4 { "s-curve" } else { "arc" }
    } else {
        "line"
    };
    Some(Shape { kind, length, straight, turn, reversals, start, end, chord, radius_trend, speed })
}

fn region(p: P) -> String {
    let x = match p[0] {
        x if x < 0.34 => "left",
        x if x < 0.66 => "centre",
        _ => "right",
    };
    let y = match p[1] {
        y if y < 0.34 => "background",
        y if y < 0.66 => "middle distance",
        _ => "foreground",
    };
    if x == "centre" { format!("{y}, centre frame") } else { format!("{x} {y}") }
}

fn pace(s: &Shape) -> &'static str {
    match s.speed {
        Some(v) if v > 1.6 => "fast",
        Some(v) if v < 0.45 => "slow",
        _ => "steady",
    }
}

/// The camera pen: (move id, how a director would say it, why).
pub fn camera_move(s: &Shape) -> (&'static str, String, String) {
    let fast = pace(s) == "fast";
    let slow = pace(s) == "slow";
    let speed = if fast { "fast " } else if slow { "slow " } else { "" };
    let side = |dx: f32| if dx < 0.0 { "left" } else { "right" };
    let cw = if s.turn > 0.0 { "clockwise" } else { "counter-clockwise" };
    let mid: P = [0.5, 0.5];
    let (d0, d1) = (len(sub(s.start, mid)), len(sub(s.end, mid)));
    let why = format!("a {} stroke, {:.0}% of the frame long, turning {:.0}°", s.kind, s.length * 100.0, s.turn.abs());
    match s.kind {
        "dot" => ("static", "locked-off static camera".into(), "just a dot: the camera holds still".into()),
        "zigzag" => ("handheld", "handheld camera, restless and close, following the action".into(), why),
        "loop" => ("orbit360", format!("{speed}360-degree orbit {cw} around the subject"), why),
        "spiral-in" => ("eyesin", "rapid push-in that spirals straight into the subject's eyes".into(), why),
        "spiral-out" => ("earth", "pull back and up, away from the subject, rising through the sky until the earth curves below".into(), why),
        "s-curve" => ("fpv", "FPV drone dive, swooping low and weaving through the scene".into(), why),
        "arc" => {
            let around = len(sub([(s.start[0] + s.end[0]) / 2.0, (s.start[1] + s.end[1]) / 2.0], mid)) < 0.3;
            if fast && s.turn.abs() > 120.0 {
                ("bullet", "bullet time: the moment freezes as the camera whips around the subject".into(), why)
            } else if around {
                ("arc", format!("{speed}arc {} around the subject", side(s.chord[0])), why)
            } else {
                ("orbit", format!("{speed}orbit {cw} around the subject"), why)
            }
        }
        _ => {
            let [dx, dy] = s.chord;
            // push and pull arrows run diagonally into or out of the middle;
            // a level stroke is a pan or a track, an upright one a crane or tilt
            let slant = dy.abs().atan2(dx.abs()).to_degrees();
            let diagonal = (22.0..68.0).contains(&slant);
            let toward = diagonal && d0 - d1 > 0.12 && d1 < 0.22;
            let away = diagonal && d1 - d0 > 0.12 && d0 < 0.22;
            if toward {
                if fast {
                    ("zoom", "sudden crash zoom into the subject".into(), why)
                } else {
                    ("push", format!("{}push-in toward the subject", if slow { "slow " } else { "steady " }), why)
                }
            } else if away {
                ("pull", format!("{}pull-out revealing the scene", if fast { "fast " } else { "slow " }), why)
            } else if dy.abs() < 0.45 * dx.abs() {
                if fast {
                    ("whip", format!("fast whip pan to the {}", side(dx)), why)
                } else if s.length > 0.42 {
                    ("track", format!("{speed}tracking shot moving {} alongside the subject", side(dx)), why)
                } else {
                    ("pan", format!("{speed}pan to the {}", side(dx)), why)
                }
            } else if dx.abs() < 0.45 * dy.abs() {
                if dy < 0.0 {
                    if s.start[1] > 0.55 { ("crane", format!("{speed}crane shot rising up"), why) } else { ("tilt", "tilt up".into(), why) }
                } else if s.start[1] < 0.45 {
                    ("cranedown", format!("{speed}crane down from above to the subject"), why)
                } else {
                    ("tiltdown", "tilt down".into(), why)
                }
            } else if dy < 0.0 {
                ("drone", format!("aerial drone move rising to the {}", side(dx)), why)
            } else {
                ("fpv", format!("FPV drone diving down to the {}", side(dx)), why)
            }
        }
    }
}

/// The subject pen: what the subject does, in a line of action.
pub fn subject_action(s: &Shape, who: &str) -> String {
    let (from, to) = (region(s.start), region(s.end));
    let [dx, dy] = s.chord;
    let quick = match pace(s) {
        "fast" => " quickly",
        "slow" => " slowly",
        _ => "",
    };
    match s.kind {
        "dot" => format!("{who} stays put in the {from}"),
        "zigzag" => format!("{who} weaves{quick} from the {from} to the {to}"),
        "loop" | "spiral-in" | "spiral-out" => format!("{who} circles{quick} around in the {from}"),
        "arc" | "s-curve" => format!("{who} curves{quick} from the {from} round to the {to}"),
        _ if dy > 0.25 && dy.abs() > 1.5 * dx.abs() => format!("{who} walks{quick} toward camera, from the {from} to the {to}"),
        _ if dy < -0.25 && dy.abs() > 1.5 * dx.abs() && s.start[1] > 0.5 => {
            format!("{who} walks{quick} away from camera, into the {to}")
        }
        _ if dy < -0.12 && dx.abs() < 0.08 => format!("{who} rises{quick} up the frame"),
        _ if dy > 0.12 && dx.abs() < 0.08 => format!("{who} drops{quick} down the frame"),
        _ => format!("{who} moves{quick} from the {from} to the {to}"),
    }
}

fn parse(v: &Value) -> Result<(Vec<Stroke>, f32, f32), String> {
    let w = v.get("w").and_then(Value::as_f64).unwrap_or(1.0) as f32;
    let h = v.get("h").and_then(Value::as_f64).unwrap_or(1.0) as f32;
    if w <= 0.0 || h <= 0.0 {
        return Err("w and h must be positive".into());
    }
    let strokes = v.get("strokes").and_then(Value::as_array).ok_or("no strokes")?;
    let mut out = Vec::new();
    for s in strokes {
        let pts = s.get("points").and_then(Value::as_array).ok_or("a stroke has no points")?;
        let mut p = Vec::with_capacity(pts.len());
        let (mut t0, mut t1) = (None, None);
        for q in pts {
            let a = q.as_array().ok_or("a point is [x, y] or [x, y, ms]")?;
            let x = a.first().and_then(Value::as_f64).ok_or("bad x")? as f32 / w;
            let y = a.get(1).and_then(Value::as_f64).ok_or("bad y")? as f32 / h;
            p.push([x, y]);
            if let Some(t) = a.get(2).and_then(Value::as_f64) {
                t0.get_or_insert(t as f32);
                t1 = Some(t as f32);
            }
        }
        out.push(Stroke {
            camera: s.get("pen").and_then(Value::as_str) != Some("subject"),
            label: s.get("label").and_then(Value::as_str).map(str::trim).filter(|l| !l.is_empty()).map(String::from),
            pts: p,
            ms: match (t0, t1) {
                (Some(a), Some(b)) if b > a => Some(b - a),
                _ => None,
            },
        });
    }
    Ok((out, w, h))
}

fn sentence(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
        None => String::new(),
    }
}

/// {"w", "h", "strokes": [{"pen": "camera"|"subject", "label"?, "points": [[x, y, ms?], …]}]}
/// → the camera move (the last camera stroke wins), each subject's action, and
/// the action as one paragraph.
pub fn direct(v: &Value) -> Value {
    let (strokes, _, _) = match parse(v) {
        Ok(x) => x,
        Err(e) => return json!({"error": e}),
    };
    let mut camera = Value::Null;
    let mut actions = Vec::new();
    let mut shapes = Vec::new();
    let mut nth = 0;
    for s in &strokes {
        let Some(m) = measure(s) else { continue };
        shapes.push(json!({
            "pen": if s.camera { "camera" } else { "subject" }, "shape": m.kind,
            "length": (m.length * 100.0).round() / 100.0, "straightness": (m.straight * 100.0).round() / 100.0,
            "turn": m.turn.round(), "pace": pace(&m), "from": region(m.start), "to": region(m.end),
        }));
        if s.camera {
            let (id, phrase, why) = camera_move(&m);
            camera = json!({"move": id, "phrase": phrase, "why": why});
        } else {
            nth += 1;
            let who = s.label.clone().unwrap_or_else(|| match nth {
                1 => "the subject".into(),
                2 => "a second figure".into(),
                _ => "another figure".into(),
            });
            actions.push(sentence(&subject_action(&m, &who)));
        }
    }
    let action = if actions.is_empty() { String::new() } else { actions.join(". ") + "." };
    json!({"camera": camera, "actions": actions, "action": action, "strokes": shapes})
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line(a: P, b: P, n: usize) -> Vec<Value> {
        (0..=n).map(|i| {
            let t = i as f32 / n as f32;
            json!([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
        }).collect()
    }

    fn arc(cx: f32, cy: f32, r: f32, from: f32, to: f32, shrink: f32, n: usize) -> Vec<Value> {
        (0..=n).map(|i| {
            let t = i as f32 / n as f32;
            let a = (from + (to - from) * t).to_radians();
            let rr = r * (1.0 - shrink * t);
            json!([cx + rr * a.cos(), cy + rr * a.sin()])
        }).collect()
    }

    fn cam(points: Vec<Value>) -> Value {
        direct(&json!({"w": 1.0, "h": 1.0, "strokes": [{"pen": "camera", "points": points}]}))
    }

    #[test]
    fn camera_strokes_read_as_moves() {
        assert_eq!(cam(line([0.1, 0.1], [0.48, 0.47], 20))["camera"]["move"], "push");
        assert_eq!(cam(line([0.5, 0.5], [0.95, 0.9], 20))["camera"]["move"], "pull");
        assert_eq!(cam(line([0.1, 0.8], [0.9, 0.78], 20))["camera"]["move"], "track");
        assert_eq!(cam(line([0.5, 0.9], [0.52, 0.2], 20))["camera"]["move"], "crane");
        assert_eq!(cam(arc(0.5, 0.5, 0.3, 0.0, 360.0, 0.0, 60))["camera"]["move"], "orbit360");
        assert_eq!(cam(arc(0.5, 0.5, 0.35, 0.0, 700.0, 0.8, 120))["camera"]["move"], "eyesin");
        assert_eq!(cam(arc(0.5, 0.5, 0.3, 200.0, 340.0, 0.0, 30))["camera"]["move"], "arc");
        let zig: Vec<Value> = (0..12).map(|i| json!([0.2 + 0.05 * i as f32, if i % 2 == 0 { 0.4 } else { 0.55 }])).collect();
        assert_eq!(cam(zig)["camera"]["move"], "handheld");
        assert_eq!(cam(vec![json!([0.5, 0.5]), json!([0.51, 0.5])])["camera"]["move"], "static");
    }

    #[test]
    fn speed_turns_a_pan_into_a_whip_and_a_push_into_a_crash() {
        let timed = |a: P, b: P, ms: f32| -> Vec<Value> {
            (0..=10).map(|i| {
                let t = i as f32 / 10.0;
                json!([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, t * ms])
            }).collect()
        };
        assert_eq!(cam(timed([0.6, 0.5], [0.9, 0.52], 90.0))["camera"]["move"], "whip");
        assert_eq!(cam(timed([0.1, 0.1], [0.48, 0.47], 120.0))["camera"]["move"], "zoom");
    }

    #[test]
    fn subjects_get_a_line_of_action() {
        let out = direct(&json!({"w": 1920, "h": 1080, "strokes": [
            {"pen": "subject", "label": "the rider", "points": [[100, 900], [900, 700], [1700, 400]]},
            {"pen": "subject", "points": [[960, 300], [960, 600], [970, 1000]]}]}));
        let a = out["action"].as_str().unwrap();
        assert!(a.starts_with("The rider moves from the left foreground to the right"), "{a}");
        assert!(a.contains("A second figure walks toward camera"), "{a}");
        assert_eq!(out["camera"], Value::Null);
    }

    #[test]
    fn rdp_keeps_corners() {
        let pts = [[0.0, 0.0], [0.5, 0.001], [1.0, 0.0], [1.0, 1.0]];
        assert_eq!(simplify(&pts, 0.01), vec![[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]);
    }
}
