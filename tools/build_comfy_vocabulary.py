"""The shot compiler's vocabulary, for Gobo's ComfyUI nodes.

    .venv/bin/python tools/build_comfy_vocabulary.py

The words of every direction choice live in studio/direction.py; the Gobo
Director node (comfyui/nodes.py) runs the same direction.wasm the page does
and needs them too, without importing Gobo. This writes them to
comfyui/direction.json (tools/build_wasm.sh runs it), and
tests/test_comfy_nodes.py says when it's stale.
"""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from studio import direction as D  # noqa: E402


def vocabulary() -> dict:
    return {
        "groups": {g: {"title": v["title"], "options": [{"id": o["id"], "label": o["label"], "phrase": o["phrase"]}
                                                       for o in v["options"]]}
                   for g, v in D.GROUPS.items()},
        "defaults": D.DEFAULTS,
        "order": D.CAMERA + ["shot", "angle", "move", "light", "film", "look", "grade", "compose"],
    }


if __name__ == "__main__":
    out = os.path.join(ROOT, "comfyui", "direction.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(vocabulary(), f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"comfyui/direction.json  {os.path.getsize(out)} bytes")
