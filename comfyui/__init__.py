"""Gobo's Rust for ComfyUI: direct a shot (the Gobo Director writes the
prompt for any model), steal a look, grade, match and check continuity, with
the same Rust the Gobo app runs (direction.wasm, gobo.wasm), and open a take
from Gobo straight into a workflow.

Install: put this folder in ComfyUI/custom_nodes (as gobo), then
`pip install -r requirements.txt` in ComfyUI's Python. Gobo's "Open in
ComfyUI" then opens ComfyUI with the take and its look already wired up.
"""
from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, preset
from . import engine

# the page side: a workflow Gobo hands over (#gobo=…), and the Look node's sliders
WEB_DIRECTORY = "./web"

try:
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.get("/gobo/look")
    async def gobo_look(request):
        """A stock and grade's own numbers, for the Look node's sliders."""
        try:
            return web.json_response(preset(request.query.get("film", "clean"), request.query.get("grade", "natural")))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    @PromptServer.instance.routes.post("/gobo/compile")
    async def gobo_compile(request):
        """The Director's prompt as its choices change, for the node's live preview."""
        try:
            b = await request.json()
            drawn = engine.read_drawing(b["drawing"]) if isinstance(b.get("drawing"), dict) else None
            out = engine.compile_shot(str(b.get("scene") or ""), dict(b.get("picks") or {}),
                                      video=b.get("shoot", "video") == "video", cinematic=bool(b.get("cinematic", True)), drawn=drawn)
            return web.json_response(out)
        except (ValueError, TypeError) as e:
            return web.json_response({"error": str(e)}, status=400)

    @PromptServer.instance.routes.post("/gobo/draw")
    async def gobo_draw(request):
        """What a drawing reads as, for Draw the Move's readout."""
        try:
            return web.json_response(engine.read_drawing(await request.json()))
        except (ValueError, TypeError) as e:
            return web.json_response({"error": str(e)}, status=400)
except Exception:                                 # loaded outside ComfyUI (tests): no page to serve
    pass

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
