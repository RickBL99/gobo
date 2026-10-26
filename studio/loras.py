"""Your trained LoRAs, found on Segmind.

A style LoRA trained on Segmind is just an endpoint on your account, so this
asks Segmind for the account's fine-tunes and writes the registry the model
picker reads as "Your LoRAs" (models.LORA_REGISTRY, in the data folder).
Segmind only: with no SEGMIND_API_KEY it does nothing.

It runs once when the server starts, in the background. If Segmind can't be
reached the registry is left as it was: the picker loses nothing it had.
"""
import json
import os
import threading

import requests

from . import models as M
from . import settings

FINETUNE = "https://api.segmind.com/finetune"


def _headers():
    return {"x-api-key": settings.SEGMIND_API_KEY, "Content-Type": "application/json"}


def _jobs() -> list:
    r = requests.get(f"{FINETUNE}/request/list", headers=_headers(), timeout=30)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    for key in ("finetunes", "data", "results", "requests"):     # Segmind has answered in each shape
        if isinstance(data.get(key), list):
            return data[key]
    return [data] if ("finetune_id" in data or "request_id" in data) else []


def _slug(job: dict) -> str:
    url = job.get("inference_api_url") or ""
    if url:
        return url.rstrip("/").split("/")[-1]
    if job.get("segmind_model_path"):
        return job["segmind_model_path"].rstrip("/").split("/")[-1]
    fid = job.get("finetune_id") or job.get("request_id") or ""
    if not fid:
        return ""
    r = requests.get(f"{FINETUNE}/request/details", headers=_headers(), json={"request_id": fid}, timeout=30)
    if not r.ok:
        return ""
    info = r.json().get("finetune", r.json())
    url = info.get("inference_api_url") or (f"https://api.segmind.com/v1/{info['slug']}" if info.get("slug") else "")
    return url.rstrip("/").split("/")[-1] if url else ""


def discover() -> int:
    """Write the registry from Segmind. Returns how many LoRAs are usable."""
    if not settings.SEGMIND_API_KEY:
        return 0
    try:
        out = []
        for job in _jobs():
            if (job.get("status") or "").upper() != "AVAILABLE":
                continue
            fid = job.get("finetune_id") or job.get("request_id") or job.get("id") or ""
            slug = _slug(job)
            if not slug:
                continue
            out.append({"id": fid, "finetune_id": fid, "name": job.get("name") or fid[:8],
                        "trigger_word": job.get("trigger_word") or "", "status": "AVAILABLE",
                        "endpoint_slug": slug, "created_at": job.get("created_ts") or ""})
    except Exception as e:                      # noqa: BLE001 - keep what we had
        print(f"[studio] LoRAs: Segmind not reachable ({e}); keeping the last list")
        return -1
    os.makedirs(os.path.dirname(M.LORA_REGISTRY), exist_ok=True)
    tmp = M.LORA_REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, M.LORA_REGISTRY)
    print(f"[studio] LoRAs: {len(out)} on your Segmind account")
    return len(out)


def start() -> None:
    if settings.VISITOR_KEYS:          # LoRAs live on the server's Segmind account, which the demo never uses
        return
    threading.Thread(target=discover, name="lora-discovery", daemon=True).start()
