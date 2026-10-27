# Gobo in ComfyUI

Gobo's colour tools as ComfyUI nodes, running the same Rust the Gobo app runs
(`gobo.wasm`, built from `crates/` by `tools/build_wasm.sh`). A take graded
here is the grade Gobo showed, pixel for pixel.

In Gobo, **Open in ComfyUI** (the Lab's Grade tab, or the Colour page) opens
your ComfyUI with the take on screen and its look already laid out as these
nodes. Change anything and queue it.

## Install (once)

1. Get the nodes: the `comfyui/` folder of this repo, or **Get the Gobo nodes**
   in Gobo (`/comfyui/gobo-nodes.zip`, which unzips as `gobo/`).
2. Put it in `ComfyUI/custom_nodes/` as `gobo`.
3. In ComfyUI's Python: `pip install -r custom_nodes/gobo/requirements.txt`
   (it adds `wasmtime`; ComfyUI already has torch, numpy, PyAV and Pillow).
4. Restart ComfyUI. The nodes are under **Gobo** in the node library.

Gobo needs to know where your ComfyUI is: it asks the first time (the desktop
app is usually `http://127.0.0.1:8000`, a ComfyUI you run yourself
`http://127.0.0.1:8188`); shift-click **Open in ComfyUI** to change it.

## The nodes

| Node | What it does |
|---|---|
| **Gobo Load Take** | A clip or still from Gobo, by the link Gobo gives it (good for a week, no sign-in), kept in ComfyUI's input folder under `gobo/`. Outputs the take, a `VIDEO` and the first frame. |
| **Gobo Look** | A film stock and a grade, then every slider of Gobo's Lab: exposure, contrast and its pivot, saturation, warmth, tint, fade, white point, grain and its size, halation, vignette, and the amount. Picking a stock or grade fills the sliders with its numbers, as the Lab does. The colour box holds the rest of a look from Gobo: wheels, curves, a CDL, split tones. |
| **Gobo LUT** | A `.cube` on the look: after it (a creative LUT) or first (a camera's log-to-video LUT, or what was left of a LUT Gobo read into the sliders). |
| **Gobo Shot Match** | A take brought to a reference take's colour before the grade (a CDL from frames of both: blacks, mid-tones, whites, saturation), so the two cut together. Reports how far apart they were before and after. |
| **Gobo Grade** | Every frame of a take through the look, the grain moving frame to frame and the sound kept, as a `VIDEO` for **Save Video**. A still comes out as an image. |
| **Gobo Grade Images** | Any `IMAGE` (or batch of frames) through a look. |
| **Gobo Steal Look** | Reads any frame's look in Rust (tone, colour, grain, the nearest stock and grade) and hands it on as a look. |
| **Gobo Continuity** | Does B pick up where A stopped? Gobo's script supervisor on A's last frame and B's first. |

Pictures are read as Gobo reads them: Rec.709 unless the file says Rec.601,
and the graded clip is written as H.264 with the original sound.
