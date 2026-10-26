"""What a standalone app needs around the studio: the home page, sign-in, the
About page, and the Content Generator's own address.

Sign-in is deliberately small for now: one shared password (people type their
own name with it) or a short name:password list, and a signed session cookie.
The name is what the Library files work under. A public demo signs visitors
in with their own Comfy key instead (STUDIO_KEYS=visitor, visitors.py).
"""
import hmac
import os
import time
from collections import defaultdict, deque
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import comfy, models as M, routes, settings, visitors

HERE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
router = APIRouter()

# Paths anyone may load: the sign-in page itself, the About page it links to,
# the health check, and the static files both are drawn with.
OPEN_PATHS = ("/login", "/logout", "/about", "/healthz", "/favicon.ico")
TOUR = "/static/tour/gobo-tour.mp4"

_failures = defaultdict(deque)        # client → times of recent wrong passwords
MAX_FAILURES, WINDOW = 8, 600


def is_open(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith("/static/")


def wants_page(request: Request) -> bool:
    return request.method == "GET" and "text/html" in request.headers.get("accept", "")


def login_url(request: Request) -> str:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return "/login" + ("" if target == "/" else f"?next={quote(target, safe='')}")


def _safe_next(nxt: str) -> str:
    """Only ever send people back into this app."""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"


def _samples() -> list:
    d = os.path.join(HERE, "static", "samples")
    return sorted(f"/static/samples/{n}" for n in os.listdir(d) if n.endswith(".webp"))


def _page(request: Request, **extra) -> dict:
    """What the sign-in and About pages share: the release, the code's home
    and the tour, if it has been made (tools/make_tour.py)."""
    tour = os.path.isfile(os.path.join(HERE, TOUR.lstrip("/")))
    return {"release": settings.RELEASE, "visitor": settings.VISITOR_KEYS, "source": settings.SOURCE_URL,
            "tour": TOUR if tour else "", "tour_poster": TOUR[:-4] + ".jpg" if tour else "",
            "model_count": len(M.MODELS), **extra}


def check_login(name: str, password: str):
    """The name to file work under, or None."""
    name = " ".join((name or "").split())[:60]
    if settings.USERS:
        for user, pw in settings.USERS.items():
            if user.lower() == name.lower() and hmac.compare_digest(password.encode(), pw.encode()):
                return user
        return None
    if settings.PASSWORD and name and hmac.compare_digest(password.encode(), settings.PASSWORD.encode()):
        return name
    return None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    if settings.VISITOR_KEYS and not visitors.key_for(request.session.get("user_name", "")):
        # the demo restarted and forgot this visitor's key: they sign in again here
        # (sending them home would bounce them straight back: a redirect loop)
        request.session.clear()
    if not settings.sign_in_on() or request.session.get("user_name"):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", _page(
        request, next=_safe_next(next), error="", name="", samples=_samples(), named_users=bool(settings.USERS)))


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, name: str = Form(""), password: str = Form(""), key: str = Form(""),
                next: str = Form("/")):
    who = request.client.host if request.client else "?"
    recent = _failures[who]
    now = time.time()
    while recent and now - recent[0] > WINDOW:
        recent.popleft()
    if len(recent) >= MAX_FAILURES:
        error = "Too many tries. Wait ten minutes and try again."
    elif settings.VISITOR_KEYS:
        # the demo: the visitor's own Comfy key, checked with Comfy, kept in memory only
        error = await run_in_threadpool(comfy.check_key, key)
        if not error:
            request.session.clear()
            request.session["user_name"] = visitors.remember(key.strip())
            return RedirectResponse(_safe_next(next), status_code=303)
        recent.append(now)
    else:
        user = check_login(name, password)
        if user:
            request.session.clear()
            request.session["user_name"] = user
            return RedirectResponse(_safe_next(next), status_code=303)
        recent.append(now)
        error = ("That name and password don't match." if settings.USERS
                 else "Type your name, and the studio's password." if not name.strip()
                 else "That password isn't right.")
    return templates.TemplateResponse(request, "login.html", _page(
        request, next=_safe_next(next), error=error, name=name, samples=_samples(),
        named_users=bool(settings.USERS)), status_code=401)


@router.get("/logout")
async def logout(request: Request):
    if settings.VISITOR_KEYS:
        visitors.forget(request.session.get("user_name", ""))
    request.session.clear()
    # ?bye: the sign-in page forgets a key this browser remembered, too
    return RedirectResponse(("/login?bye=1" if settings.VISITOR_KEYS else "/login") if settings.sign_in_on() else "/",
                            status_code=303)


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    """Who made Gobo, why, and how it's built. Open to anyone, signed in or not."""
    signed = bool(request.session.get("user_name")) or not settings.sign_in_on()
    return templates.TemplateResponse(request, "about.html", _page(
        request, signed_in=signed, api=settings.PREFIX, release_date=settings.RELEASE_DATE))


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {
        "api": settings.PREFIX, "visitor": settings.VISITOR_KEYS,
        "user": "Your Comfy key" if settings.VISITOR_KEYS else request.session.get("user_name", ""),
        "sign_in": settings.sign_in_on(), "samples": _samples(), "release": settings.RELEASE,
        "release_date": settings.RELEASE_DATE,
        "model_count": len(M.MODELS),
        "keys": {"comfy": bool(comfy.api_key()),
                 "claude": M.claude_on(), "anthropic": bool(settings.ANTHROPIC_API_KEY) and not settings.VISITOR_KEYS,
                 "openai": bool(settings.OPENAI_API_KEY) and not settings.VISITOR_KEYS}})


# The Content Generator: the studio limited to stills, with the batch tools.
@router.get(settings.PREFIX + "/stills", response_class=HTMLResponse,
            dependencies=[Depends(routes._switched_on)])
async def content_generator(request: Request):
    return await routes.studio_page(request, stills=1)
