# Gobo

**Say the film. Gobo shoots it.**

> 🎬 **Need real clips?** [Request your Gobo discount at SuperStock →](https://www.superstock.com/footage)

![The Last Watch: a ten-second film Autopilot made from one line](docs/media/the-last-watch.gif)

*The Last Watch.* One line typed into Gobo: *"A lighthouse keeper's last night before the light goes automatic: he climbs the stairs in a storm, lights the lamp one final time, and watches the beam sweep the sea."* About two minutes and 80¢ of Comfy credit later, there was a film. Autopilot wrote four shots. FLUX 3 drew a keyframe for each, and Wan 3.0 animated each shot from its keyframe. Gobo's Rust checked every cut, the film was cut together, and the Lab graded it in Rust as Portra 400 printed through Kodachrome. [Watch the MP4](docs/media/the-last-watch.mp4) · [Open it in ComfyUI](workflows/the-last-watch/workflow_api.json)

![The four keyframes](docs/media/keyframes.jpg)

Gobo is an open-source AI film studio for people who would rather direct than wire up nodes. Write one line, pick a look, a length and a shape, and Gobo writes the shots, films them on [Comfy Router](https://platform.comfy.org)'s video models, cuts them together and grades them like film, in Rust. Then share it with a link.

When you want more, every control a cinematographer uses is underneath: camera body, lens, focal length, aperture, framing, angle, camera move, light, film stock, look, grade and composition, turned into the prompt each of 134 image and video models responds to best, all behind one API key.

Built for Comfy's **Open Source Higgsfield** challenge (October 2026), and inspired by Higgsfield's Cinema Studio.

- **The tour:** [80 seconds of what's inside](studio/static/tour/gobo-tour.mp4), the film that plays on the demo's sign-in page.
- **Live demo:** [try.gobo.video](https://try.gobo.video). Bring your own Comfy API key: it's held in memory while you work, never stored, and every run is charged to your account.
- **Questions:** [help@gobo.video](mailto:help@gobo.video).
- **Made by** [PictureNet Corp](https://picturenetcorp.com), from the AI cinema system it built in-house for commercial production. See [Where Gobo came from](#where-gobo-came-from) and [How Gobo is built](#how-gobo-is-built).

---

## What it does

- **Make a film.** Gobo opens on one box: *What's your film?* Write a line, pick a look (Drama, Western, Neon night, Black & white and eight more), 10, 15 or 30 seconds, wide, tall or square, and Draft, Standard or Best (LTX 2.5 Fast, LTX 2.5 Pro, or Seedance 2.5 for the finest clips). The cost is shown before anything runs, each step is told in plain words while it rolls, and the finished film plays with **Share** (a public page anyone with the link can watch) and **Download**. **Direct it yourself** opens the full studio, where any film it made can be changed shot by shot.
- **Autopilot.** The engine behind Make a film. One line becomes a film. Autopilot writes a shot list with a title and a length. Every new setup gets a keyframe, and every shot is animated from it. A shot marked *continue* starts on the last frame of the shot before, so the action carries across the cut. A Rust script supervisor checks every cut, the film is cut together, and the whole film is graded in Rust. Everything lands on the Board, so you can change any shot afterwards.
- **The Board.** Shot cards for a spot or a short: keyframe them, animate them, continue one shot into the next, trim them, and cut a timed animatic or the film.
- **Direction you can see.** Every control adds the words a cinematographer would use, in the order models respond to: framing first, the look last. That includes 28 camera moves, from push-ins and dolly zooms to bullet time, FPV dives, snorricam and an Earth zoom-out. The prompt preview is compiled in your browser by a Rust shot compiler, and the server sends exactly the same text.
- **134 models, one key.** LTX 2.5, Wan 3.0, Seedance 2.x, Veo 3.1, Kling 3 Omni, MiniMax H3, FLUX 3, Nano Banana and the rest of Comfy Router's image, video, editing and upscaling models. Each shot is mapped onto each model's own inputs: aspect, resolution, length, sound, first and last frames, references. Claude (through Router, on the same key) is the **Director**, who rewrites your shot for the model you picked, and the **planner** behind Autopilot.
- **The Lab: Rust in your browser.**
  - **Steal the Look:** drop in any frame. In about 9 ms it reads the tone curve, colour, palette, grain, letterbox, focus and light, names the nearest film stock and grade, and sets them for you.
  - **Grade:** film stocks (Portra, 500T, bleach bypass, slide, VHS and more) and grades, with halation, moving grain and a vignette, live on video at 10 to 16 ms a frame. An Amount control sets the strength, and the look exports as a `.cube` file for Resolve, Premiere or Final Cut.
  - **Colour:** the advanced grade, full screen and in the spirit of DaVinci Resolve's Color page: lift, gamma, gain and offset wheels, custom curves, a waveform, RGB parade, vectorscope and histogram, and `.cube` LUTs (3D or 1D) and ASC CDLs imported from any editor. An imported LUT is read back into the sliders (fitted to its table), with what they can't show kept as a small LUT before them, so the picture is still exactly the LUT's; a CDL lands on the wheels exactly. All of it folds into the look's one 3D LUT, so the live preview, the exported `.cube` and the server's grade match.
  - **Match:** grade other takes like this one. An extension, another angle or a retake is matched to its colour first (a CDL worked out in Rust from frames of both, lining up blacks, mid-tones, whites and saturation), then given the same grade, so they cut together.
  - **Continuity:** compares where one take ends with where the next begins. A straight cut must keep the grade. A shot that continues the last one must match the picture too.
  - **Draw the Move:** draw a loop and get a 360° orbit, a line toward the subject and get a push-in, a zigzag and get handheld.
- **Grade the take.** The server puts every frame of a clip through the look with the same Rust module, one frame per core, keeps the sound, and saves the result as a new take. It matches the Lab's preview byte for byte, and it runs in the background, showing which frame it's on.
- **Gobo's own ComfyUI nodes.** **Gobo Director** is Gobo's Rust shot compiler as a node: the scene in plain words and a cinematographer's choices become the prompt for any video or image model in ComfyUI, shown live as you change them; **Open in ComfyUI** under the prompt downloads the shot you're directing that way, wired to the model's own node, to drop on ComfyUI. **Gobo Steal Look** reads any frame's look and the choices behind it, so new footage is shot and graded in a film's look. And in the Lab, **Open in ComfyUI** downloads the take and its look as a workflow of Gobo's nodes to drop on ComfyUI (Load Take, Look with every slider, LUT, Shot Match, Grade), graded there by the same Rust. The nodes live in [`comfyui/`](comfyui) and Gobo serves them as a download. See [Gobo in ComfyUI](comfyui/README.md).
- **Open in ComfyUI.** Any take, or a whole film, downloads as a ComfyUI workflow. Each keyframe node is wired into the clip it starts, and the result runs in ComfyUI on the same partner nodes.
- **And:** a public share link for any take, a model shootout, a library (pick takes from any reel and put them on a board, in film order, to cut them), accounts an admin adds with their own passwords, frame grabs, extending, changing and leading into a clip, upscaling, and stitching mixed models into one file.

---

## Quick start

You need Docker and a Comfy API key.

```sh
git clone https://github.com/RickBL99/gobo && cd gobo
cp .env.example .env          # put your COMFY_API_KEY and a STUDIO_PASSWORD in it
docker compose up --build     # then open http://localhost:8000
```

Sign in with any name and the password you set.

### Your Comfy API key

1. Sign in at [platform.comfy.org/login](https://platform.comfy.org/login) with the account you use for ComfyUI.
2. Under **API Keys**, click **+ New**, give the key a name and click **Generate**. Copy it straight away: it's shown only once.
3. Add credits. In ComfyUI, open **Settings → Workspace → Plan & Credits** ([Comfy's guide](https://docs.comfy.org/interface/credits)). Gobo shows your balance in the header and the price next to every model.
4. Put it in `.env` as `COMFY_API_KEY=…`, and set `STUDIO_PASSWORD` too. Without a password, anyone who can reach the address can spend your credits.

Some typical prices: a FLUX 3 keyframe is about 7¢, and Nano Banana about 6¢. Clips are priced per second: Wan 3.0 and Veo 3.1 Fast about 10¢, LTX 2.5 Fast and MiniMax H3 about 13¢, LTX 2.5 Pro about 17¢, Seedance 2.0 Fast about 17¢. A ten-second Autopilot film costs roughly $0.80 to $3, depending on the models.

That one key runs everything, including the Director and the planner. `ANTHROPIC_API_KEY` is an optional extra (see [Settings](#settings)).

### Without Docker

Python 3.12 and ffmpeg (`brew install ffmpeg`, or `apt install ffmpeg`):

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # add COMFY_API_KEY and STUDIO_PASSWORD
.venv/bin/uvicorn studio.app:app --port 8020     # http://127.0.0.1:8020
```

On a Mac, `./run` does all of that and opens the browser.

### Building the Rust

The compiled WebAssembly ships in `studio/static/` (`direction.wasm`, `gobo.wasm`), so you don't need Rust to run Gobo. To change the Rust:

```sh
rustup target add wasm32-unknown-unknown
tools/build_wasm.sh          # rebuilds both .wasm files and stamps them with their source hash
cargo test --workspace       # the crates' own tests
```

The Python tests fail if a `.wasm` is older than its Rust source, so a stale build can't slip through.

### Settings

Everything lives in `.env` (see [.env.example](.env.example)). The ones that matter:

| Setting | What it does |
|---|---|
| `COMFY_API_KEY` | Required. Every model, the Director and the planner run on it. |
| `STUDIO_PASSWORD` | One shared password; people sign in with their own name. Or use `STUDIO_USERS=ana:pw1,ben:pw2` for separate logins. |
| `STUDIO_ADMINS` | Names (comma-separated) who get the **Users** page: add people with their own username and password, reset or remove them. Passwords are kept as scrypt hashes in the data folder; a name with an account can't sign in with the shared password. |
| `STUDIO_KEYS=visitor` | Public-demo mode: see below. |
| `STUDIO_DIR` | Where reels live (`/data` in Docker). Reels untouched for `STUDIO_REEL_TTL_DAYS` (60) are cleared. |
| `ANTHROPIC_API_KEY` | Optional: the Director and the planner straight from Anthropic, instead of through Comfy Router. |
| `STUDIO_COMFY_SYNC=1` | Run every take on Router's direct route. Gobo switches to it on its own when Router's queue stops moving. |
| `STUDIO_SOURCE_URL` | Where the sign-in and About pages say the code lives. Defaults to this repo; a fork points it at its own. |
| `STUDIO_CONTACT` | The address the pages give for questions. Defaults to help@gobo.video; a fork sets its own, or empty to show none. |
| `STUDIO_FRONT=studio` | What `/` opens. Unset (or `director`): Make a film, the one-line front door. `studio`: the home page with every tool on it, as before Make a film; it's still at `/direct`. |
| `STUDIO_KEEP_GB` | A ceiling on the data folder, for a public demo on shared storage. Over it, whole reels go, the one touched longest ago first; a reel used in the last hour stays. Unset (the default): no ceiling, and nothing is ever deleted for space. |

### Running a public demo

`STUDIO_KEYS=visitor` turns Gobo into a bring-your-own-key demo, which is how [try.gobo.video](https://try.gobo.video) runs:

- Each visitor signs in with their own Comfy key. Comfy checks it, and Gobo keeps it in the server's memory only: never on disk, never in a cookie, never in a log.
- Every Comfy call made for that visitor uses their key. That includes takes the server finishes after they close the tab.
- Visitors see only their own reels.
- The server's own keys are never spent: no Anthropic or OpenAI key is used.
- A restart forgets every key. A browser that was asked to remember the key signs straight back in, and a visitor's reels are still there: they belong to the key, not the session.
- On shared storage, set `STUDIO_KEEP_GB` so visitors' films can't fill it. The demo keeps 4 GB.

### The sign-in page, the About page and the tour

The sign-in page plays the tour (`studio/static/tour/gobo-tour.mp4`, muted, on a loop) beside the sign-in card, and links to the About page, the source, the license and the challenge. A visitor without a key is told how to get one. The About page (`/about`) is open to anyone, signed in or not: where Gobo came from, what's inside, how it's built, the license and who made it. Every page has an **About** pill at the top.

The tour is recorded, not edited by hand. `tools/make_tour.py` drives the real studio in headless Chrome and films it with the DevTools screencast. It opens the takes, Board and films of a reel Autopilot made, and walks through Autopilot, direction, the models, the Lab and ComfyUI. The captions and the pointer are drawn into the page itself, so they're set in Gobo's own type. ffmpeg joins the twelve scenes with dissolves. Nothing is generated or spent: every page the recorder opens answers anything but a GET with an empty success, so no take can start whatever a scene clicks. Run it again after a change to the UI:

```sh
.venv/bin/python tools/make_tour.py http://127.0.0.1:8020 --reel <reel id>   # sign-in off; needs Chrome and ffmpeg
.venv/bin/python tools/make_tour.py http://127.0.0.1:8020 --reel <reel id> --only 5   # one scene, to look at
```

---

## How it works

```
 Browser                                          Server (FastAPI, Python 3.12)
 ┌─────────────────────────────────────┐          ┌──────────────────────────────────────────┐
 │ studio page, Board, Autopilot (JS)   │  HTTPS   │ routes.py   takes, the line, the pump     │
 │ direction.wasm  the prompt, as typed │ ───────► │ rust.py     the same .wasm, via wasmtime  │
 │ gobo.wasm       Lab: read, grade,    │          │             (the prompt, film grading)    │
 │                 continuity, drawing  │          │ comfy.py    Comfy Router ──► 134 models,   │
 └─────────────────────────────────────┘          │             the Director, file storage    │
                                                  │ ffmpeg      cuts, frames, encodes         │
                                                  └──────────────────────────────────────────┘
```

- **One build of the Rust, two places.** `direction.wasm` and `gobo.wasm` run in the browser for the previews and the Lab. On the server, the same files run through [wasmtime](https://github.com/bytecodealliance/wasmtime-py) to compile the prompt that is actually sent and to grade films. The page and the server can't disagree: a test grades a frame in V8 and in wasmtime and requires identical bytes.
- **Takes wait their turn.** Every paid run waits in line on disk, and each provider gets only so many at once (four clips by default, fewer once a provider names its limit). A background pump finishes takes whether or not the page is open, because a provider's result link expires.
- **Comfy Router, in practice.** `studio/comfy.py` reads each model's published input schema and maps a shot onto it. Frames that a model needs as URLs go to Comfy's own storage. LTX starts from a keyframe through ComfyUI's LTX route. If Router's queue stops moving for an account (it did, for hours, while we built this), Gobo takes the run out of line, which costs nothing, and sends it on the direct route instead.
- **The catalogue** comes from Router's model list and schemas (`tools/build_comfy_catalog.py`, 143 usable models out of 263). It is bundled, so the app starts without a network call.

### The Rust

| Crate | Lines | What it does |
|---|---:|---|
| [`direction`](crates/direction) | 416 | The shot compiler: your direction, scene, references and palette become the prompt, in labelled pieces, so the page can show which control wrote which words. |
| [`scrim`](crates/scrim) | 1,575 | The look lab. It reads a frame (Oklab tone curve, k-means palette, grain, letterbox, sharpness, light), steals its look, and grades pictures through a 3D LUT with halation, grain and a vignette. Film stocks and grades are presets of one `Look`, and `.cube` export uses the same table. |
| [`flag`](crates/flag) | 252 | The script supervisor: exposure, black level, white balance, contrast, saturation, grain and framing across a cut. It has a strict mode for continuous action, with perceptual hashes and a subject-jump check, and finds the odd one out in a sequence. |
| [`dingle`](crates/dingle) | 389 | Draw to direct: strokes are simplified (Ramer–Douglas–Peucker) and read as a camera move and a line of action. |
| [`gobo-wasm`](crates/gobo-wasm) | 136 | scrim, flag and dingle as one WebAssembly module with a tiny C ABI (`alloc`, `free` and a packed `u64` answer). No bindgen. |

---

## ComfyUI workflows

Any take or film in Gobo downloads as a ComfyUI API workflow: open a take and choose **Open in ComfyUI**, or download a whole reel. Two examples are in [`workflows/`](workflows):

- [`the-last-watch`](workflows/the-last-watch/workflow_api.json): the film above, as a 16-node graph. Four FLUX 3 keyframe nodes are each wired into a Wan 3.0 image-to-video node.
- [`flux-keyframe-to-ltx`](workflows/flux-keyframe-to-ltx/workflow_api.json): a FLUX 3 keyframe animated by LTX 2.5 Pro, eight seconds at 1080p with sound.

Drag a `workflow_api.json` onto ComfyUI, sign in to your Comfy account (these are partner nodes and run on your credits), and queue it. Every exported workflow is checked against ComfyUI's own node definitions: required inputs, dropdown values with the right JSON types (`"8"` is not `8`), and links between outputs of the right type.

---

## Tests

```sh
.venv/bin/python -m pytest tests/      # about 130 tests: hermetic, with every provider mocked; ffmpeg is real
cargo test --workspace                 # the crates
.venv/bin/python tests/browser_studio.py   # the page in headless Chrome, against a running server
```

The tests that matter most:
- `test_direction_rust.py` pins the prompt for 2,000 generated shots and checks that the page and the server agree on thousands more.
- `test_gobo_wasm.py` runs the shipped `.wasm` under Node and requires wasmtime to grade the same frame byte for byte.
- `test_comfy_export.py` validates every export, including the ones in `workflows/`.
- `test_visitors.py` covers the public-demo mode.

---

## Where Gobo came from

PictureNet built an in-house AI cinema system for **[SuperStock](https://www.superstock.com)** and **[Stewart Cohen Productions](https://www.stewartcohen.com)**. They needed a bespoke system that could handle full commercial production augmentation with real shot content: footage and stills from real commercial shoots, extended, restyled, matched and cut alongside generated shots, not a box that only makes clips from nothing.

That's why Gobo works the way it does. Footage you shot comes in and can be restyled, changed or made longer. Steal the Look reads the grade off a real frame, so the generated shots can match it. The script supervisor checks every cut for jumps in light, colour and grain. Every look exports as a `.cube` file for the editor the rest of the job lives in.

For Comfy's Open Source Higgsfield challenge we took that system standalone, moved it onto Comfy Router, and opened the code. Gobo is **inspired by Higgsfield's Cinema Studio**, which showed how good directing AI video with real camera choices can feel. Gobo is our take on that idea, built in the open.

---

## How Gobo is built

The architecture is in [How it works](#how-it-works): a Python 3.12 server (FastAPI), a plain-JavaScript studio page with no front-end framework, ffmpeg, and four Rust crates compiled to WebAssembly that run both in the browser and, through wasmtime, on the server.

### The grade and the LUTs

A look (`crates/scrim/src/look.rs`) is a dozen numbers a colourist would recognise: exposure in stops, white balance and tint, contrast around a pivot, where black and white land, saturation, colour pushed into the shadows and the highlights, black-and-white filters, and bleach bypass. The film stocks (Portra 400, Tungsten 500T, Daylight 250D, slide, expired, instant, VHS and more) and the grades (teal and orange, Kodachrome, noir, neon and more) are presets of it, and a stock and a grade compose into one look. The colour maths runs in linear light and Oklab.

```
your picture ─► 33×33×33 3D LUT ─► halation ─► vignette ─► grain
                (the .cube export)   (these need the whole picture, so they stay out of the LUT)
```

- **One table.** The look is baked into a 33×33×33 3D LUT (`crates/scrim/src/grade.rs`), sRGB in and sRGB out, trilinear between the points. That same table is what the `.cube` export writes, so what you see in Gobo is what Resolve, Premiere or Final Cut get.
- **What a LUT can't hold** is done per pixel after it: halation (red light bleeding round highlights, as it does through film's anti-halation layer), a vignette by squared distance from a 2,048-entry lookup table, and grain that sits in the mid-tones and moves from frame to frame (the frame number is its seed).
- **A LUT read into the sliders** (`crates/scrim/src/unpack.rs`): a `.cube` is a table, not settings, so the sliders are fitted to it a part at a time (tone off the greys' lightness, a cast off the greys' colour, saturation off how colourful colours come out), and what they can't say becomes the rest: for every colour, the colour that the sliders turn into the LUT's, found by inverting them. The rest goes first, so nothing the sliders throw away is lost before it can be put back. A LUT the sliders can't come near (a negative, a posterise) is kept whole.
- **Amount** sets how much of the look reaches the picture: it blends the LUT in display space and scales each effect, for a take whose prompt already asked for the stock.
- **Fast enough to be live.** An 854×480 frame grades in 10 to 16 ms in the browser, so the Lab grades a playing clip. To grade a take, the server decodes it to raw RGBA frames, puts each through the same `.wasm`, and encodes it again with the sound copied untouched. A test grades one frame in V8 and in wasmtime and requires identical bytes.
- **Steal the Look runs the other way.** It reads a frame's tone curve in Oklab, a k-means palette, the grain, the letterbox, the sharpness and the light in about 9 ms, turns the reading back into a look (`crates/scrim/src/steal.rs`), and names the nearest stock and grade.

### The approach

- **Prove it live, on a budget.** The main paths were tried against the real Router: FLUX 3 stills, LTX from words and from a keyframe, Wan 3.0 from a keyframe, the Director and the planner, and a full Autopilot film. The whole build spent a few dollars of credit, because each live check was the smallest run that answered the question.
- **Measure, don't assume.** Three examples:
  - The first grading pipeline darkened every film about two levels before any look touched it. That showed up by grading a film with the look turned off and comparing averages. The cause was ffmpeg's fast YUV↔RGB rounding, and the fix was a two-flag change.
  - The vignette and halation cost 130 ms each on a frame, because of `powf` per pixel. A lookup table and a display-space glow took a frame from 281 ms to 15.6 ms.
  - The continuity checker first flagged ordinary cuts as errors. It now judges a straight cut on the grade alone, and flags a take only when it sits at least 2.5 times the median distance from the rest.
- **Port without changing a word.** The prompt compiler began in Python. The Rust port had to give identical output on thousands of generated shots before it ran anywhere, the Python's answers were pinned, and then the Python was deleted. One compiler now runs in both places.
- **Test what ships.** The browser tests drive a real Chrome. The WebAssembly tests run the shipped `.wasm`, not a native build. The ComfyUI exports are checked against ComfyUI's own node definitions.
- **Use good tools, keep the same bar.** Parts of the code were written with Claude Code, held to the same rules as the rest: measured, tested against what ships, and reviewed before every release.
- **Write down what the platforms do.** The Router facts we learned the hard way are in the code where they matter:
  - a stuck queue and the fallback to the direct route;
  - LTX taking only text on Router;
  - video inputs needing public URLs;
  - 211 credits to the dollar;
  - Seedance refusing keyframes of realistic people.

This public repo is generated from the one we work in by a script that copies the code and refuses to publish if anything looks like a key or an internal system. That's why its history starts fresh.

---

## About

Gobo is made by **[PictureNet Corp](https://picturenetcorp.com)** in Pasadena, California, run by Rick Leckrone and Maryam Mostafavi. Since 1995, PictureNet has built the technology, infrastructure and content businesses behind commercial media licensing. Those include [SuperStock](https://www.superstock.com), a stock photography and media licensing agency, and [VistaLab](https://vistalab.ai), an AI-native stock imagery platform.

What we do for clients:
- **Full-stack engineering and cloud:** Azure architecture, DevOps and CI/CD, and e-commerce and DAM systems serving tens of millions of licensed images.
- **AI and MCP:** AI and ML systems, and Model Context Protocol servers that give AI agents access to a company's media and business systems.
- **Forward-deployed engineering:** AI strategy audits, agentic integration, and pilot-to-production delivery for small and mid-sized media businesses.
- **Media licensing and content:** rights management, DAM and royalty engineering.
- **Creative production:** photography and film, aerial and drone work, 3D animation, storyboarding and music.

**Rick Leckrone** is a principal engineer and polyglot developer who has spent his career where pictures meet software. He was Director of Tech & Content at Digital Stock and Director of Commercial Photography Worldwide at Corbis, founded Blend Images, is CTO and co-owner of SuperStock, and created vistalab.ai. [LinkedIn](https://www.linkedin.com/in/rickleckrone/) · [picturenetcorp.com](https://picturenetcorp.com)

---

## Contributing

Issues and pull requests are welcome; every change is reviewed before it's merged. See [CONTRIBUTING.md](CONTRIBUTING.md), and [SECURITY.md](SECURITY.md) for reporting a security problem privately.

---

## License

- **Code:** dual-licensed under [MIT](LICENSE-MIT) or [Apache 2.0](LICENSE-APACHE), at your option.
- **Example films, images and workflows** (`docs/media`, `workflows`, `studio/static/samples`): [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

What you make with Gobo is subject to the terms of the model providers you run it on. Gobo is an independent project, inspired by Higgsfield; it is not affiliated with Comfy Org or Higgsfield.
