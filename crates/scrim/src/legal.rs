//! Broadcast legality: is a graded frame deliverable? Measured the way a QC
//! house does, against EBU R 103 (the limits most broadcasters and delivery
//! specs quote): every RGB component within −5% … 105%, luma within −1% …
//! 103%, with up to 1% of a frame's pixels allowed outside.
//!
//! The grader clamps every pixel to 0 … 1 and the film is encoded in video
//! range, so its levels can't break R 103; what it can do is hit the ends:
//! whites clipped flat and blacks crushed flat, which is what a QC report
//! flags as "clipping". So the check reports both: the levels (legal by
//! construction, measured anyway, never assumed) and how much of the frame
//! sits at either end. `legal` on a look (crate::look) is the fix: a soft
//! limiter that folds the ends in to 1% … 98% with a knee, as a video
//! limiter does, so nothing is clipped flat.
//!
//! Display values in, like the scopes; a big frame is sampled on a grid.

use serde_json::{json, Value};

/// Where the limiter puts black and white: 1% and 98%, the headroom QC asks for.
pub const LEGAL_LO: f32 = 0.01;
pub const LEGAL_HI: f32 = 0.98;
/// R 103: RGB −5% … 105%, luma −1% … 103%, 1% of pixels may be outside.
const RGB_LO: f32 = -0.05;
const RGB_HI: f32 = 1.05;
const LUMA_LO: f32 = -0.01;
const LUMA_HI: f32 = 1.03;
pub const TOLERANCE: f32 = 0.01;
/// A channel this close to the ends is at the clip (one code value of 8 bits).
const AT_BLACK: f32 = 0.5 / 255.0;
const AT_WHITE: f32 = 254.5 / 255.0;
const MAX_SAMPLES: usize = 160_000;

#[derive(Clone, Debug, PartialEq)]
pub struct Legal {
    pub luma_min: f32,
    pub luma_max: f32,
    pub rgb_min: f32,
    pub rgb_max: f32,
    /// share of pixels with a channel outside R 103's RGB range, and luma outside its range
    pub out_rgb: f32,
    pub out_luma: f32,
    /// share of pixels with a channel flat at white, and at black
    pub clipped: f32,
    pub crushed: f32,
    /// within R 103 (the tolerance allowed)
    pub legal: bool,
}

impl Legal {
    pub fn to_json(&self) -> Value {
        let r = |v: f32| (v * 10000.0).round() / 10000.0;
        json!({
            "luma_min": r(self.luma_min), "luma_max": r(self.luma_max),
            "rgb_min": r(self.rgb_min), "rgb_max": r(self.rgb_max),
            "out_rgb": r(self.out_rgb), "out_luma": r(self.out_luma),
            "clipped": r(self.clipped), "crushed": r(self.crushed),
            "legal": self.legal,
        })
    }
}

/// Measure an RGBA8 frame.
pub fn check(px: &[u8], w: usize, h: usize) -> Legal {
    let mut l = Legal { luma_min: 1.0, luma_max: 0.0, rgb_min: 1.0, rgb_max: 0.0, out_rgb: 0.0, out_luma: 0.0,
                        clipped: 0.0, crushed: 0.0, legal: true };
    if w == 0 || h == 0 || px.len() < w * h * 4 {
        return l;
    }
    let step = (((w * h) as f32 / MAX_SAMPLES as f32).sqrt().ceil() as usize).max(1);
    let (mut n, mut out_rgb, mut out_luma, mut clipped, mut crushed) = (0usize, 0usize, 0usize, 0usize, 0usize);
    for y in (0..h).step_by(step) {
        for x in (0..w).step_by(step) {
            let i = (y * w + x) * 4;
            let c = [px[i] as f32 / 255.0, px[i + 1] as f32 / 255.0, px[i + 2] as f32 / 255.0];
            let luma = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
            let (lo, hi) = (c[0].min(c[1]).min(c[2]), c[0].max(c[1]).max(c[2]));
            l.luma_min = l.luma_min.min(luma);
            l.luma_max = l.luma_max.max(luma);
            l.rgb_min = l.rgb_min.min(lo);
            l.rgb_max = l.rgb_max.max(hi);
            if lo < RGB_LO || hi > RGB_HI {
                out_rgb += 1;
            }
            if !(LUMA_LO..=LUMA_HI).contains(&luma) {
                out_luma += 1;
            }
            if hi >= AT_WHITE {
                clipped += 1;
            }
            if lo <= AT_BLACK {
                crushed += 1;
            }
            n += 1;
        }
    }
    let n = n.max(1) as f32;
    l.out_rgb = out_rgb as f32 / n;
    l.out_luma = out_luma as f32 / n;
    l.clipped = clipped as f32 / n;
    l.crushed = crushed as f32 / n;
    l.legal = l.out_rgb <= TOLERANCE && l.out_luma <= TOLERANCE;
    l
}

/// The limiter: a display value folded into LEGAL_LO … LEGAL_HI with a soft
/// knee at each end (the middle is untouched), so highlights roll off instead
/// of clipping and blacks keep a little detail.
#[inline]
pub fn limit(v: f32) -> f32 {
    const HI_KNEE: f32 = 0.94;
    const LO_KNEE: f32 = 0.03;
    if v > HI_KNEE {
        HI_KNEE + (LEGAL_HI - HI_KNEE) * ((v - HI_KNEE) / (LEGAL_HI - HI_KNEE) * 0.9).tanh() / 0.9f32.tanh() * 0.9f32.tanh()
    } else if v < LO_KNEE {
        LO_KNEE - (LO_KNEE - LEGAL_LO) * ((LO_KNEE - v) / (LO_KNEE - LEGAL_LO) * 0.9).tanh() / 0.9f32.tanh() * 0.9f32.tanh()
    } else {
        v
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(fill: impl Fn(usize) -> [u8; 3], n: usize) -> Vec<u8> {
        (0..n).flat_map(|i| { let c = fill(i); [c[0], c[1], c[2], 255] }).collect()
    }

    #[test]
    fn a_normal_frame_is_legal_and_unclipped() {
        let px = frame(|i| [(20 + i % 200) as u8, (30 + i % 180) as u8, (40 + i % 160) as u8], 64 * 64);
        let l = check(&px, 64, 64);
        assert!(l.legal && l.clipped == 0.0 && l.crushed == 0.0, "{l:?}");
        assert!(l.luma_max < 1.0 && l.luma_min > 0.0);
    }

    #[test]
    fn clipped_whites_and_crushed_blacks_are_counted_as_shares() {
        let px = frame(|i| if i % 10 == 0 { [255, 255, 255] } else if i % 10 == 1 { [0, 0, 0] } else { [120, 100, 90] }, 100 * 100);
        let l = check(&px, 100, 100);
        assert!((l.clipped - 0.1).abs() < 0.01 && (l.crushed - 0.1).abs() < 0.01, "{l:?}");
        assert!(l.legal, "flat white and black are at the limits, not past them");
        assert_eq!((l.rgb_max, l.rgb_min), (1.0, 0.0));
    }

    #[test]
    fn the_limiter_keeps_the_middle_and_folds_the_ends() {
        for v in [0.1, 0.3, 0.5, 0.8, 0.93] {
            assert!((limit(v) - v).abs() < 1e-6, "{v}");
        }
        assert!(limit(1.0) <= LEGAL_HI + 1e-4 && limit(1.0) > 0.97, "{}", limit(1.0));
        assert!(limit(0.0) >= LEGAL_LO - 1e-4 && limit(0.0) < 0.02, "{}", limit(0.0));
        // monotone, no step at the knees
        let mut last = -1.0;
        for i in 0..=1000 {
            let o = limit(i as f32 / 1000.0);
            assert!(o >= last - 1e-6);
            last = o;
        }
        assert!((limit(0.9401) - 0.9401).abs() < 1e-3 && (limit(0.0299) - 0.0299).abs() < 1e-3);
    }

    #[test]
    fn json_has_the_numbers_a_page_needs() {
        let l = check(&frame(|_| [255, 255, 255], 16), 4, 4).to_json();
        assert_eq!(l["clipped"], 1.0);
        assert_eq!(l["legal"], true);
        assert!(l["luma_max"].as_f64().unwrap() > 0.99);
    }
}
