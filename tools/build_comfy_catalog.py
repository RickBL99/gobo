"""Snapshot Comfy Router's models into studio/comfy_catalog.json.

    python tools/build_comfy_catalog.py                  # from the live API (COMFY_API_KEY)
    python tools/build_comfy_catalog.py --schemas DIR    # from schemas saved earlier

Lists every model on api.comfy.org, reads each one's OpenAPI schema, and keeps
the image and video models the studio can drive, in the studio's own record
format (studio/comfy.py does the translating). The app reads the snapshot at
start-up, so it never waits on the network for its model list; run this again
when Comfy adds models.
"""
import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)                                   # settings reads ./.env

from studio import comfy, settings              # noqa: E402


def _get(url: str, tries: int = 4) -> requests.Response:
    for i in range(tries):
        try:
            r = requests.get(url, headers={"X-API-Key": settings.COMFY_API_KEY}, timeout=30)
            if r.status_code not in (429, 500, 502, 503, 504):
                return r
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    raise SystemExit(f"Comfy Router didn't answer: {url}")


def listing() -> list:
    out, cursor = [], None
    while True:
        r = _get(f"{comfy.BASE}/v2/models?limit=100" + (f"&cursor={cursor}" if cursor else ""))
        r.raise_for_status()
        d = r.json()
        out += d["data"]
        cursor = d.get("next_cursor")
        if not d.get("has_more"):
            return out


def schema(mid: str, cache: str = "") -> dict:
    if cache:
        p = os.path.join(cache, mid.replace("/", "__") + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    r = _get(f"{comfy.BASE}/v2/models/{mid}/openapi.json")
    return r.json() if r.status_code == 200 else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schemas", default="", help="a folder of saved {provider}__{model}.json schemas")
    a = ap.parse_args()
    if not settings.COMFY_API_KEY:
        raise SystemExit("Set COMFY_API_KEY (in .env or the environment) first.")
    models = listing()
    with cf.ThreadPoolExecutor(6) as ex:
        schemas = list(ex.map(lambda m: (m, schema(m["id"], a.schemas)), models))
    recs, skipped = [], []
    for m, s in schemas:
        rec = comfy.record(s) if s else None
        if rec:
            rec["billing"] = m.get("billing") or {}
            recs.append(rec)
        else:
            skipped.append(m["id"])
    recs.sort(key=lambda r: (-r["pop"], r["provider"], r["slug"]))
    missing = sorted(set(comfy.HERO_LABELS) - {r["slug"] for r in recs})
    with open(comfy.CATALOG, "w", encoding="utf-8") as f:
        json.dump({"built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "listed": len(models), "models": recs}, f, indent=1, sort_keys=True)
    by = {}
    for r in recs:
        by[r["category"]] = by.get(r["category"], 0) + 1
    print(f"{len(recs)} of {len(models)} models usable: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
    if missing:
        print("heroes not in the catalogue (check comfy.HEROES):", ", ".join(missing))


if __name__ == "__main__":
    main()
