//! The scopes a colourist grades by, drawn as RGBA pictures the page paints
//! straight onto a canvas (its scale lines and labels are drawn over them):
//!
//! * waveform: every column's brightness (Rec.709 luma), bottom black, top white;
//! * parade: the same for red, green and blue side by side;
//! * vectorscope: hue round the circle, saturation out from the middle;
//! * histogram: how many pixels at each level, per channel.
//!
//! They read display values (what the screen shows), like a grading panel on
//! Rec.709. A big frame is sampled on a grid, so a scope costs about the same
//! whatever the picture's size.

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Kind {
    Waveform,
    Parade,
    Vector,
    Histogram,
}

impl Kind {
    pub fn from_code(c: u32) -> Option<Kind> {
        Some(match c {
            0 => Kind::Waveform,
            1 => Kind::Parade,
            2 => Kind::Vector,
            3 => Kind::Histogram,
            _ => return None,
        })
    }
}

const MAX_SAMPLES: usize = 160_000;

/// Draw a scope of an RGBA8 picture, `ow` × `oh`, transparent where empty.
pub fn scope(px: &[u8], w: usize, h: usize, kind: Kind, ow: usize, oh: usize) -> Vec<u8> {
    let (ow, oh) = (ow.clamp(16, 2048), oh.clamp(16, 2048));
    let mut out = vec![0u8; ow * oh * 4];
    if w == 0 || h == 0 || px.len() < w * h * 4 {
        return out;
    }
    let step = (((w * h) as f32 / MAX_SAMPLES as f32).sqrt().ceil() as usize).max(1);
    let samples = || (0..h).step_by(step).flat_map(move |y| (0..w).step_by(step).map(move |x| (x, y)));
    let at = |x: usize, y: usize| {
        let i = (y * w + x) * 4;
        [px[i] as f32 / 255.0, px[i + 1] as f32 / 255.0, px[i + 2] as f32 / 255.0]
    };
    let count = samples().count().max(1) as f32;
    let mut put = |i: usize, c: [f32; 3], a: f32| {
        let o = &mut out[i * 4..i * 4 + 4];
        for k in 0..3 {
            o[k] = (o[k] as f32 + c[k] * 255.0 * a).min(255.0) as u8;
        }
        o[3] = (o[3] as f32).max(a * 255.0).min(255.0) as u8;
    };
    match kind {
        Kind::Waveform | Kind::Parade => {
            let bands: Vec<(usize, [f32; 3])> = if kind == Kind::Waveform {
                vec![(3, [0.62, 1.0, 0.7])]
            } else {
                vec![(0, [1.0, 0.36, 0.32]), (1, [0.4, 0.95, 0.48]), (2, [0.36, 0.6, 1.0])]
            };
            let bw = ow / bands.len();
            for (b, (ch, colour)) in bands.iter().enumerate() {
                let mut grid = vec![0u32; bw * oh];
                let mut entries = 0usize;
                for (x, y) in samples() {
                    let c = at(x, y);
                    let v = if *ch == 3 { 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] } else { c[*ch] };
                    let row = ((1.0 - v) * (oh - 1) as f32).round() as usize;
                    // a sample stands for `step` columns of the picture: on a scope wider
                    // than the samples it fills every column they cover, so there are no gaps
                    let c0 = (x * bw / w).min(bw - 1);
                    let c1 = ((x + step) * bw / w).clamp(c0 + 1, bw);
                    for col in c0..c1 {
                        grid[row.min(oh - 1) * bw + col] += 1;
                    }
                    entries += c1 - c0;
                }
                // each column holds entries/bw; a fifth of them in one cell is full
                let k = 5.0 * bw as f32 / entries.max(1) as f32;
                for row in 0..oh {
                    for col in 0..bw {
                        let n = grid[row * bw + col];
                        if n > 0 {
                            put(row * ow + b * bw + col, *colour, 1.0 - (-(n as f32) * k).exp() * 0.92);
                        }
                    }
                }
            }
        }
        Kind::Vector => {
            let side = ow.min(oh);
            let (cx, cy) = ((ow - 1) as f32 / 2.0, (oh - 1) as f32 / 2.0);
            // the edge of the circle is full chroma (Cb or Cr of ±0.5)
            let scale = side as f32 * 0.46 / 0.5;
            let mut grid = vec![(0u32, [0.0f32; 3]); ow * oh];
            for (x, y) in samples() {
                let c = at(x, y);
                let cb = -0.1146 * c[0] - 0.3854 * c[1] + 0.5 * c[2];
                let cr = 0.5 * c[0] - 0.4542 * c[1] - 0.0458 * c[2];
                let (u, v) = ((cx + cb * scale).round() as isize, (cy - cr * scale).round() as isize);
                // each sample a small spot (centre twice its neighbours), so a flat colour reads as a dot, not a pixel
                for (dx, dy) in (-1..=1).flat_map(|dy| (-1..=1).map(move |dx| (dx, dy))) {
                    let (x, y) = (u + dx, v + dy);
                    if x < 0 || y < 0 || x >= ow as isize || y >= oh as isize {
                        continue;
                    }
                    let wgt = if dx == 0 && dy == 0 { 2 } else { 1 };
                    let cell = &mut grid[y as usize * ow + x as usize];
                    cell.0 += wgt;
                    for (sum, v) in cell.1.iter_mut().zip(c) {
                        *sum += v * wgt as f32;
                    }
                }
            }
            let k = 400.0 / count;
            for (i, (n, sum)) in grid.iter().enumerate() {
                if *n == 0 {
                    continue;
                }
                // the trace in its own colour, lifted so dark colours still show
                let avg = sum.map(|s| s / *n as f32);
                let peak = avg[0].max(avg[1]).max(avg[2]).max(0.05);
                let colour = avg.map(|v| 0.35 + 0.65 * v / peak);
                put(i, colour, 1.0 - (-(*n as f32) * k).exp() * 0.9);
            }
        }
        Kind::Histogram => {
            let mut bins = vec![[0u32; 3]; ow];
            for (x, y) in samples() {
                let c = at(x, y);
                for k in 0..3 {
                    bins[((c[k] * (ow - 1) as f32).round() as usize).min(ow - 1)][k] += 1;
                }
            }
            // scaled to the tallest bar away from the clipped ends, which would flatten the rest
            let tallest = bins[1..ow - 1].iter().flat_map(|b| b.iter()).copied().max().unwrap_or(1).max(1) as f32;
            let colours = [[1.0, 0.3, 0.3], [0.3, 1.0, 0.4], [0.3, 0.5, 1.0]];
            for (x, b) in bins.iter().enumerate() {
                for k in 0..3 {
                    let top = ((b[k] as f32 / tallest).min(1.0) * (oh - 1) as f32).round() as usize;
                    for row in (oh - top)..oh {
                        put(row * ow + x, colours[k], 0.55);
                    }
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn flat(c: [u8; 3], w: usize, h: usize) -> Vec<u8> {
        (0..w * h).flat_map(|_| [c[0], c[1], c[2], 255]).collect()
    }

    fn lit(img: &[u8], ow: usize, x: usize, y: usize) -> bool {
        img[(y * ow + x) * 4 + 3] > 0
    }

    #[test]
    fn a_grey_frame_is_one_line_on_the_waveform_and_a_dot_on_the_vectorscope() {
        let px = flat([128, 128, 128], 80, 45);
        let (ow, oh) = (100, 101);
        let wf = scope(&px, 80, 45, Kind::Waveform, ow, oh);
        let row = ((1.0 - 128.0 / 255.0) * 100.0f32).round() as usize;
        assert!((0..ow).all(|x| lit(&wf, ow, x, row)), "every column at mid grey");
        assert!(!lit(&wf, ow, 10, 5) && !lit(&wf, ow, 10, 95));
        let vs = scope(&px, 80, 45, Kind::Vector, 101, 101);
        assert!(lit(&vs, 101, 50, 50), "grey sits in the middle");
        assert_eq!(vs.chunks(4).filter(|p| p[3] > 0).count(), 9, "one spot");
    }

    #[test]
    fn red_goes_where_red_goes() {
        let px = flat([255, 0, 0], 40, 30);
        let (ow, oh) = (90, 100);
        let pd = scope(&px, 40, 30, Kind::Parade, ow, oh);
        assert!(lit(&pd, ow, 10, 0), "red at the top of the red band");
        assert!(lit(&pd, ow, 40, oh - 1) && lit(&pd, ow, 70, oh - 1), "green and blue at the bottom");
        let vs = scope(&px, 40, 30, Kind::Vector, 101, 101);
        let i = vs.chunks(4).enumerate().max_by_key(|(_, p)| p[3]).unwrap().0;
        let (x, y) = (i % 101, i / 101);
        assert!(x < 50 && y < 20, "red is up and to the left: {x},{y}");
        let hs = scope(&px, 40, 30, Kind::Histogram, 64, 50);
        assert!(lit(&hs, 64, 63, 49) && lit(&hs, 64, 0, 49));
    }
}
