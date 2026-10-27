# Gobo in ComfyUI

Gobo's Rust as ComfyUI nodes: the shot compiler that writes a
cinematographer's prompt for any model, and the look lab that steals, matches
and grades. They run the same Rust the Gobo app runs (`direction.wasm` and
`gobo.wasm`, built from `crates/` by `tools/build_wasm.sh`): a prompt written
here is the prompt Gobo sends, and a take graded here is the grade Gobo
showed, pixel for pixel.

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

## A workflow to start from

**Templates → gobo → Shoot a film in a still's look**: a frame from any film
goes into **Gobo Steal Look**; its direction sets three **Gobo Director**
shots (a ferry leaving a foggy pier, a woman at the rail, the empty pier);
each is filmed by Seedance with its sound off; **Gobo Sequence Match**
matches the three to the first, grades them with the stolen look and cuts
them together as one film. Load your own frame, rewrite the scenes, queue it.
(Seedance is a partner node: sign in to your Comfy account to run it, or
swap in any video node that takes a prompt.)

## The nodes

| Node | What it does |
|---|---|
| **Gobo Director** | The scene in plain words, and the choices a cinematographer makes: camera body, lens, focal length, aperture, framing, angle, camera move, light, film stock, look, grade, composition. Out comes the prompt for any video or image node (Kling, Seedance, MiniMax, Wan, LTX, FLUX…), framing first and the look last, the order models follow best: the prompt Gobo itself sends, shown live in the node as you change it. Also out: the stock and grade as a look, for **Gobo Grade**, and the direction. Connect **Gobo Steal Look**'s direction and a still's choices win. |
| **Gobo Draw the Move** | Draw on the frame, as in Gobo's Lab: the camera pen draws where the camera goes (a loop is an orbit, a line toward the subject a push-in, a rise a crane up…), the subject pen where they go. Gobo's Rust reads the strokes as you draw; wire `drawn` into the **Gobo Director** and its move and action go into the prompt, live. Connect a **Load Image** to draw on the frame. |
| **Gobo Load Take** | A clip or still from Gobo, by the link Gobo gives it (good for a week, no sign-in), kept in ComfyUI's input folder under `gobo/`. Outputs the take, a `VIDEO` and the first frame. |
| **Gobo Look** | A film stock and a grade, then every slider of Gobo's Lab: exposure, contrast and its pivot, saturation, warmth, tint, fade, white point, grain and its size, halation, vignette, and the amount. Picking a stock or grade fills the sliders with its numbers, as the Lab does. The colour box holds the rest of a look from Gobo: wheels, curves, a CDL, split tones. |
| **Gobo LUT** | A `.cube` on the look: after it (a creative LUT) or first (a camera's log-to-video LUT, or what was left of a LUT Gobo read into the sliders). |
| **Gobo Shot Match** | A take brought to a reference take's colour before the grade (a CDL from frames of both: blacks, mid-tones, whites, saturation), so the two cut together. Reports how far apart they were before and after. |
| **Gobo Grade** | Every frame of a take, or of a `VIDEO` from any video node, through the look, the grain moving frame to frame and the sound kept, as a `VIDEO` for **Save Video**. A still comes out as an image. |
| **Gobo Sequence Match** | A film's shots (up to eight `VIDEO`s, in order, from any video nodes) made to cut together: each matched to the hero shot's colour, all graded with one look, and cut together as one silent film at the hero's size and frame rate (a shot of another shape is cropped to the middle; one at another frame rate keeps its length). The report says how far each shot was from the hero before and after, and checks every cut. |
| **Gobo Grade Images** | Any `IMAGE` (or batch of frames) through a look. |
| **Gobo Steal Look** | Reads any frame's look in Rust (tone, colour, grain, the nearest stock and grade) and hands it on as a look, with the direction behind it (focus, composition, stock, grade, each with what gave it away) for the **Gobo Director**: new footage shot in a film's look. |
| **Gobo Continuity** | Does B pick up where A stopped? Gobo's script supervisor on A's last frame and B's first. |

Pictures are read as Gobo reads them: Rec.709 unless the file says Rec.601,
and the graded clip is written as H.264 with the original sound.
