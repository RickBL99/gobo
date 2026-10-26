//! sRGB, linear light and Oklab (Björn Ottosson, 2020): the perceptual space
//! every measurement and every grade in scrim works in. Oklab's L is
//! lightness as the eye sees it (0 black, 1 white; a mid grey card is about
//! 0.57), and a/b are green–red and blue–yellow, so "how colourful" is the
//! length of (a, b) and "which colour" is its angle.

pub type Rgb = [f32; 3];
pub type Lab = [f32; 3];

pub fn to_linear(c: f32) -> f32 {
    if c <= 0.040_45 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

pub fn to_srgb(c: f32) -> f32 {
    let c = c.clamp(0.0, 1.0);
    if c <= 0.003_130_8 {
        12.92 * c
    } else {
        1.055 * c.powf(1.0 / 2.4) - 0.055
    }
}

/// sRGB bytes to linear light, by table (the reader decodes millions).
pub struct Decode([f32; 256]);

impl Default for Decode {
    fn default() -> Self {
        let mut t = [0.0; 256];
        for (i, v) in t.iter_mut().enumerate() {
            *v = to_linear(i as f32 / 255.0);
        }
        Decode(t)
    }
}

impl Decode {
    #[inline]
    pub fn px(&self, r: u8, g: u8, b: u8) -> Rgb {
        [self.0[r as usize], self.0[g as usize], self.0[b as usize]]
    }
}

pub fn oklab(c: Rgb) -> Lab {
    let l = 0.412_221_47 * c[0] + 0.536_332_55 * c[1] + 0.051_445_995 * c[2];
    let m = 0.211_903_5 * c[0] + 0.680_699_5 * c[1] + 0.107_396_96 * c[2];
    let s = 0.088_302_46 * c[0] + 0.281_718_85 * c[1] + 0.629_978_7 * c[2];
    let (l, m, s) = (l.max(0.0).cbrt(), m.max(0.0).cbrt(), s.max(0.0).cbrt());
    [
        0.210_454_26 * l + 0.793_617_8 * m - 0.004_072_047 * s,
        1.977_998_5 * l - 2.428_592_2 * m + 0.450_593_7 * s,
        0.025_904_037 * l + 0.782_771_77 * m - 0.808_675_77 * s,
    ]
}

pub fn linear(c: Lab) -> Rgb {
    let l = c[0] + 0.396_337_78 * c[1] + 0.215_803_76 * c[2];
    let m = c[0] - 0.105_561_346 * c[1] - 0.063_854_17 * c[2];
    let s = c[0] - 0.089_484_18 * c[1] - 1.291_485_5 * c[2];
    let (l, m, s) = (l * l * l, m * m * m, s * s * s);
    [
        4.076_741_7 * l - 3.307_711_6 * m + 0.230_969_94 * s,
        -1.268_438 * l + 2.609_757_4 * m - 0.341_319_38 * s,
        -0.004_196_086_3 * l - 0.703_418_6 * m + 1.707_614_7 * s,
    ]
}

pub fn chroma(c: Lab) -> f32 {
    (c[1] * c[1] + c[2] * c[2]).sqrt()
}

pub fn hex(c: Lab) -> String {
    let r = linear(c);
    let q = |v: f32| (to_srgb(v) * 255.0).round() as u8;
    format!("#{:02x}{:02x}{:02x}", q(r[0]), q(r[1]), q(r[2]))
}

/// A colour's everyday name from its Oklab hue; "neutral" when there's too
/// little colour to name. (Oklab angles: red ≈ 29°, orange ≈ 60°, yellow ≈
/// 110°, green ≈ 142°, cyan ≈ 195°, blue ≈ 264°, magenta ≈ 328°.)
pub fn hue_name(a: f32, b: f32) -> &'static str {
    if (a * a + b * b).sqrt() < 0.012 {
        return "neutral";
    }
    let h = b.atan2(a).to_degrees().rem_euclid(360.0);
    match h {
        h if h < 15.0 => "pink",
        h if h < 45.0 => "red",
        h if h < 80.0 => "orange",
        h if h < 125.0 => "yellow",
        h if h < 170.0 => "green",
        h if h < 215.0 => "teal",
        h if h < 285.0 => "blue",
        h if h < 315.0 => "purple",
        h if h < 345.0 => "magenta",
        _ => "pink",
    }
}

pub fn smoothstep(e0: f32, e1: f32, x: f32) -> f32 {
    let t = ((x - e0) / (e1 - e0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn oklab_round_trips_and_names_hues() {
        for c in [[1.0, 0.0, 0.0], [0.2, 0.5, 0.9], [0.18, 0.18, 0.18]] {
            let back = linear(oklab(c));
            for i in 0..3 {
                assert!((back[i] - c[i]).abs() < 1e-3, "{c:?} → {back:?}");
            }
        }
        let grey = oklab([0.18, 0.18, 0.18]);
        assert!((grey[0] - 0.565).abs() < 0.01 && chroma(grey) < 1e-3);
        let name = |r: f32, g: f32, b: f32| {
            let l = oklab([to_linear(r), to_linear(g), to_linear(b)]);
            hue_name(l[1], l[2])
        };
        assert_eq!(name(1.0, 0.0, 0.0), "red");
        assert_eq!(name(1.0, 0.5, 0.0), "orange");
        assert_eq!(name(0.0, 0.5, 0.5), "teal");
        assert_eq!(name(0.0, 0.0, 1.0), "blue");
        assert_eq!(name(1.0, 0.0, 1.0), "magenta");
        assert_eq!(hex(oklab([1.0, 1.0, 1.0])), "#ffffff");
    }
}
