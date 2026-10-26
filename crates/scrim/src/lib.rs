//! scrim — Gobo's look lab. (On set, a scrim is the wire mesh slid in front
//! of a lamp to take it down a touch: the gentlest way to shape light.)
//!
//! * [`read`]: what a frame looks like — tone, colour, palette, letterbox,
//!   focus, grain, vignette, light direction, subject, empty space — and a
//!   fingerprint for matching frames.
//! * [`steal`]: that reading turned back into settings: a grade that puts the
//!   same look on any picture, the nearest of the studio's film stocks and
//!   grades, and the light, focus, framing and shape as direction ids.
//! * [`look`]: the grade itself, a handful of colourist's numbers, with the
//!   studio's stocks and grades as presets.
//! * [`grade`]: pictures through a look (via a 3D LUT, then halation, grain
//!   and vignette), and the LUT as a .cube file for any editor.
//!
//! Everything runs in the page as WebAssembly (crates/gobo-wasm), on the
//! pixels of a canvas: nothing is uploaded to read or grade a frame.

pub mod color;
pub mod grade;
pub mod look;
pub mod read;
pub mod steal;

pub use grade::{grade_rgba, grade_with, lut, Lut};
pub use look::{compose, film, grade as grade_preset, Look};
pub use read::{read, Frame, Reading};
pub use steal::{stolen, suggest};
