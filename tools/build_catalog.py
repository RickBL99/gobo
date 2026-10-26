"""Build studio/catalog.json — the Segmind catalogue Gobo
ships with.

    python tools/build_catalog.py <research-catalogue.json>

The input is the full research catalogue (one record per Segmind model with
parsed inputs, media slots, prices). The studio can also refresh itself from
Segmind's live API (Refresh models, in the studio); this snapshot is what it
falls back to, and what fills in models whose live schema is empty.
"""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from studio.catalog import compact_from_scratch, BUNDLED  # noqa: E402


def main(src: str) -> None:
    with open(src) as f:
        rows = json.load(f)
    models = [compact_from_scratch(x) for x in rows if x.get("slug")]
    models.sort(key=lambda m: m["slug"])
    with open(BUNDLED, "w") as f:
        json.dump({"built_from": os.path.basename(src), "models": models}, f,
                  separators=(",", ":"))
    print(f"{len(models)} models → {BUNDLED} ({os.path.getsize(BUNDLED) // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
