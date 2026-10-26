"""Render Gobo's sample tiles: one fixed scene per direction
group, run once per option with the same model and seed, so the tiles in a
picker differ only by the choice they illustrate.

    python tools/build_samples.py                 # light, film, look
    python tools/build_samples.py --only light:golden,light:neon
    python tools/build_samples.py --groups grade --force

Spends real Comfy credit (Nano Banana 2.1, about 6¢ a tile; it takes a seed,
FLUX 3 does not). Existing tiles are kept unless --force. Writes
studio/static/samples/<group>-<id>.webp plus
samples.json (model, seed, scene and the exact prompt behind every tile), which
the page picks up on its next load.
"""
import argparse
import io
import json
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from studio import direction as D  # noqa: E402
from studio import models as M  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "studio", "static", "samples")
TILE = (480, 270)  # 16:9, 2× the picker's tile width

# One scene per group, chosen so every option in it has something to act on:
# a window seat takes golden hour, candlelight, neon outside and fog alike; a
# red coat and a grey street show what each film stock does to colour and
# skin; a figure walking to a car can belong to any genre. The seed alone
# doesn't hold a layout once the prompt changes, so each scene also says where
# things sit in the frame.
SCENES = {
    "light": "A woman sits alone at a small café table on the left of the frame, a cup in her hands, "
             "looking out of a large window on the right at the street.",
    "film": "A young woman in a red wool coat stands at a city tram stop on the right of the frame, "
            "parked cars and shop fronts behind her on the left.",
    # no setting: a genre mostly IS its setting, so the look gets to choose it
    "look": "A man in a long coat walks away from the camera, centred in the frame, "
            "toward a lone parked car in the distance.",
}


def prompt_for(group: str, oid: str) -> str:
    # everything else Auto: the tile shows this one choice and nothing more
    return D.compile_prompt(SCENES[group], {group: oid})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default="light,film,look")
    ap.add_argument("--only", default="", help="comma list of group:id")
    ap.add_argument("--model", default="vertexai/gemini-nano-banana-2.1")
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    man_path = os.path.join(OUT, "samples.json")
    try:
        with open(man_path) as f:
            man = json.load(f)
    except (OSError, ValueError):
        man = {"tiles": {}}

    if a.only:
        jobs = [tuple(x.split(":", 1)) for x in a.only.split(",") if ":" in x]
    else:
        jobs = [(g, o["id"]) for g in a.groups.split(",") for o in D.GROUPS[g]["options"]]
    jobs = [(g, o) for g, o in jobs
            if a.force or not os.path.exists(os.path.join(OUT, f"{g}-{o}.webp"))]
    if not jobs:
        print("Nothing to render (use --force to redo).")
        return
    print(f"{len(jobs)} tiles on {a.model}, seed {a.seed}")

    running = {}
    for g, o in jobs:
        prompt = prompt_for(g, o)
        req = {"prompt": prompt, "negative_prompt": "", "aspect": "16:9", "resolution": "",
               "seed": a.seed, "refs": []}
        call = M.build(a.model, req, lambda n: None)
        for attempt in range(3):
            try:
                running[(g, o)] = dict(M.submit(call), prompt=prompt, endpoint=call["endpoint"])
                break
            except M.RunError as e:
                if e.reason != "rateLimit" or attempt == 2:
                    print(f"  {g}:{o} not submitted — {e}")
                    break
                time.sleep(20)
        time.sleep(0.4)

    spent = 0.0
    while running:
        time.sleep(4)
        for key, job in list(running.items()):
            g, o = key
            try:
                res = M.poll(job["poll_url"], a.model)
            except M.RunError as e:
                print(f"  {g}:{o} failed — {e}")
                del running[key]
                continue
            if res.get("pending"):
                continue
            del running[key]
            data, _ = M.fetch_output(res)
            im = Image.open(io.BytesIO(data)).convert("RGB")
            # cover-crop to 16:9, then downscale
            w, h = im.size
            tw = min(w, int(h * TILE[0] / TILE[1]))
            th = int(tw * TILE[1] / TILE[0])
            im = im.crop(((w - tw) // 2, (h - th) // 2, (w - tw) // 2 + tw, (h - th) // 2 + th))
            im = im.resize(TILE, Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=80, method=6)
            with open(os.path.join(OUT, f"{g}-{o}.webp"), "wb") as f:
                f.write(buf.getvalue())
            cost = float(res.get("cost") or 0)
            spent += cost
            man["tiles"].setdefault(g, {})[o] = {
                "prompt": job["prompt"], "model": a.model, "endpoint": job["endpoint"],
                "seed": a.seed, "cost": cost}
            print(f"  {g}:{o} ✓  {len(buf.getvalue()) // 1024} KB  ${cost:.3f}")

    man.update(scenes=SCENES, made=date.today().isoformat())
    tmp = man_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(man, f, indent=1, sort_keys=True)
    os.replace(tmp, man_path)
    print(f"done — ${spent:.2f}")


if __name__ == "__main__":
    main()
