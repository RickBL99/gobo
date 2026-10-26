"""Gobo: the studio as its own app.

    ./run                                       # then open http://127.0.0.1:8020/
    uvicorn studio.app:app --port 8020          # the same, by hand

The studio itself is one router (routes.py) under settings.PREFIX; this file
adds what a standalone server needs around it: the home page and sign-in
(shell.py), the reference-to-prompts tool (prompts.py), the sample tiles and a
health check.
"""
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import jobs as J, loras, prompts, settings, shell, visitors
from .routes import router

HERE = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app):
    loras.start()            # your trained LoRAs, from Segmind, in the background
    yield


app = FastAPI(title="Gobo", lifespan=lifespan,
              description="Direct a shot like a cinematographer; run it on any Comfy Router (or Segmind) image or video model.")


@app.middleware("http")
async def signed_in(request: Request, call_next):
    """Everything but the sign-in and About pages, the health check and the
    static files needs a signed-in name. A page asks for it; the API answers 401.

    With sign-in off (no STUDIO_PASSWORD or STUDIO_USERS), everyone is "local",
    so the Library still has a name to file work under."""
    if not request.session.get("user_name"):
        if not settings.sign_in_on():
            request.session["user_name"] = "local"
        elif not shell.is_open(request.url.path):
            if shell.wants_page(request):
                return RedirectResponse(shell.login_url(request), status_code=303)
            return JSONResponse({"error": "Sign in first."}, status_code=401)
    if settings.VISITOR_KEYS:
        return await _as_visitor(request, call_next)
    return await call_next(request)


_REEL_PATH = re.compile("^" + re.escape(settings.PREFIX) + r"/reels/([a-z0-9-]{6,40})(?:/|$)")


async def _as_visitor(request: Request, call_next):
    """The public demo (STUDIO_KEYS=visitor): the request runs on the visitor's
    own Comfy key, and a reel with someone else's id on it isn't there. One
    check here covers every route under /reels/{reel}."""
    vid = request.session.get("user_name") or ""
    path = request.url.path
    if vid and not visitors.key_for(vid) and not shell.is_open(path):
        # a restart forgot the key: the sign-in page sends a remembered one straight back
        if shell.wants_page(request):
            return RedirectResponse(shell.login_url(request), status_code=303)
        return JSONResponse({"error": "Sign in again with your Comfy key.", "signin": True}, status_code=401)
    with visitors.acting_for(vid):
        m = _REEL_PATH.match(path)
        if m:
            try:
                reel = J.get_reel(m.group(1))
            except ValueError:
                reel = None
            if reel and not visitors.owns(reel):
                return JSONResponse({"error": "No such reel."}, status_code=404)
        return await call_next(request)


# Added after the gate, so it wraps it: the gate reads the session this sets up.
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret(), session_cookie="cinematic",
                   max_age=settings.SESSION_DAYS * 86400, same_site="lax",
                   https_only=settings.SECURE_COOKIES)

app.include_router(shell.router)
app.include_router(prompts.router)
app.include_router(router)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # every page gets it, including the ones the sync brings across
    return FileResponse(os.path.join(HERE, "static", "icons", "favicon.ico"), media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return JSONResponse({"ok": True, "name": "Gobo", "segmind_key": bool(settings.SEGMIND_API_KEY),
                         "comfy_key": bool(settings.COMFY_API_KEY),
                         "keys": "visitor" if settings.VISITOR_KEYS else "server",
                         "sign_in": settings.sign_in_on(), "release": settings.RELEASE,
                         "release_id": settings.RELEASE_ID})
