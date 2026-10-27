"""What a standalone app needs around the studio: the front door (the
Director, or the home page), sign-in, the About page, shared films, and the
Content Generator's own address.

Sign-in is deliberately small: accounts an admin adds on the Users page, each
with its own password (accounts.py); one shared password (people type their
own name with it) or a short name:password list; and a signed session cookie.
The name is what the Library files work under. A public demo signs visitors
in with their own Comfy key instead (STUDIO_KEYS=visitor, visitors.py).
"""
import hmac
import os
import time
from collections import defaultdict, deque
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import accounts, comfy, jobs as J, models as M, routes, settings, visitors

HERE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
router = APIRouter()

# Paths anyone may load: the sign-in page itself, the About page it links to,
# the health check, the static files they are drawn with, and shared films.
OPEN_PATHS = ("/login", "/logout", "/about", "/healthz", "/favicon.ico")
OPEN_PREFIXES = ("/static/", "/f/")
TOUR = "/static/tour/gobo-tour.mp4"

_failures = defaultdict(deque)        # client → times of recent wrong passwords
MAX_FAILURES, WINDOW = 8, 600


def is_open(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


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
            "contact": settings.CONTACT,
            "tour": TOUR if tour else "", "tour_poster": TOUR[:-4] + ".jpg" if tour else "",
            "model_count": len(M.MODELS), "front": settings.FRONT, **extra}


def check_login(name: str, password: str):
    """(the name to file work under, the account's version or None), or None."""
    name = accounts.norm(name)
    if accounts.has(name):
        # an account signs in with its own password, never the shared one
        return accounts.check(name, password)
    if settings.USERS:
        for user, pw in settings.USERS.items():
            if user.lower() == name.lower() and hmac.compare_digest(password.encode(), pw.encode()):
                return user, None
        return None
    if settings.PASSWORD and name and hmac.compare_digest(password.encode(), settings.PASSWORD.encode()):
        return name, None
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
        found = check_login(name, password)
        if found:
            user, rev = found
            request.session.clear()
            request.session["user_name"] = user
            if rev is not None:
                request.session["account"] = rev
                await run_in_threadpool(accounts.signed_in, user)
            return RedirectResponse(_safe_next(next), status_code=303)
        recent.append(now)
        error = ("That name and password don't match." if settings.USERS or accounts.has(name)
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


def _director_on() -> bool:
    return os.getenv("STUDIO_ENABLED", "1").strip() != "0"


@router.get("/", response_class=HTMLResponse)
async def front(request: Request):
    """The Director (STUDIO_FRONT=director, the default) or the home page."""
    if settings.FRONT == "director" and _director_on():
        return await routes.studio_page(request, director=True)
    return await home(request)


@router.get("/direct", response_class=HTMLResponse, dependencies=[Depends(routes._switched_on)])
async def direct(request: Request):
    """The Director, whatever the front door is."""
    return await routes.studio_page(request, director=True)


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {
        "api": settings.PREFIX, "visitor": settings.VISITOR_KEYS,
        "user": "Your Comfy key" if settings.VISITOR_KEYS else request.session.get("user_name", ""),
        "admin": _admin(request), "sign_in": settings.sign_in_on(), "samples": _samples(), "release": settings.RELEASE,
        "front": settings.FRONT, "contact": settings.CONTACT,
        "release_date": settings.RELEASE_DATE,
        "model_count": len(M.MODELS),
        "keys": {"comfy": bool(comfy.api_key()),
                 "claude": M.claude_on(), "anthropic": bool(settings.ANTHROPIC_API_KEY) and not settings.VISITOR_KEYS,
                 "openai": bool(settings.OPENAI_API_KEY) and not settings.VISITOR_KEYS}})


# ── Users: an admin adds people with their own name and password ────────────
def _admin(request: Request) -> bool:
    return not settings.VISITOR_KEYS and accounts.is_admin(request.session.get("user_name", ""))


def _not_admin() -> JSONResponse:
    return JSONResponse({"error": "Only an admin can manage who signs in."}, status_code=403)


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    if not _admin(request):
        return RedirectResponse("/home", status_code=303)
    me = request.session.get("user_name", "")
    return templates.TemplateResponse(request, "users.html", _page(
        request, me=me, my_account=accounts.has(me), min_password=accounts.MIN_PASSWORD,
        shared=bool(settings.PASSWORD)))


@router.get("/users/list", response_class=JSONResponse)
async def users_list(request: Request):
    if not _admin(request):
        return _not_admin()
    return {"users": accounts.listing(), "me": request.session.get("user_name", "")}


@router.post("/users/add", response_class=JSONResponse)
async def users_add(request: Request):
    if not _admin(request):
        return _not_admin()
    b = await request.json()
    try:
        u = await run_in_threadpool(accounts.add, str(b.get("name") or ""), str(b.get("password") or ""),
                                    request.session.get("user_name", ""))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"user": u}


@router.post("/users/password", response_class=JSONResponse)
async def users_password(request: Request):
    """A new password for someone; they're signed out wherever they were."""
    if not _admin(request):
        return _not_admin()
    b = await request.json()
    name = str(b.get("name") or "")
    try:
        u = await run_in_threadpool(accounts.set_password, name, str(b.get("password") or ""),
                                    request.session.get("user_name", ""))
    except KeyError:
        return JSONResponse({"error": "No such user."}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if accounts.norm(name).lower() == accounts.norm(request.session.get("user_name", "")).lower():
        request.session["account"] = accounts.version(name)      # your own: you stay signed in here
    return {"user": u}


@router.post("/users/remove", response_class=JSONResponse)
async def users_remove(request: Request):
    """Someone can't sign in any more; their takes stay in the Library under their name."""
    if not _admin(request):
        return _not_admin()
    name = str((await request.json()).get("name") or "")
    if accounts.norm(name).lower() == accounts.norm(request.session.get("user_name", "")).lower():
        return JSONResponse({"error": "You can't remove yourself."}, status_code=400)
    try:
        await run_in_threadpool(accounts.remove, name)
    except KeyError:
        return JSONResponse({"error": "No such user."}, status_code=404)
    return {"removed": accounts.norm(name)}


# The Content Generator: the studio limited to stills, with the batch tools.
@router.get(settings.PREFIX + "/stills", response_class=HTMLResponse,
            dependencies=[Depends(routes._switched_on)])
async def content_generator(request: Request):
    return await routes.studio_page(request, stills=1)


# ── shared films ────────────────────────────────────────────────────────────
# /f/<token>: one finished take its maker chose to share (routes.share_take).
# Open to anyone; kept out of search engines; gone when it's unshared, deleted,
# or cleared for space.
def _shared(token: str):
    s = J.get_share(token)
    try:
        t = J.get_take(s["reel"], s["take"]) if s else None
    except ValueError:
        t = None
    if not t or t.get("share") != token or t.get("status") != "done" or not t.get("file"):
        return None, None, None
    p = J.out_path(s["reel"], t["file"])
    return (s, t, p) if os.path.isfile(p) else (None, None, None)


def _poster_path(s: dict, t: dict):
    name = t.get("poster") if t.get("kind") == "video" else t.get("file")
    p = J.out_path(s["reel"], name) if name else None
    return p if p and os.path.isfile(p) else None


@router.get("/f/{token}", response_class=HTMLResponse)
async def shared_film(request: Request, token: str):
    s, t, _ = _shared(token)
    if not s:
        return templates.TemplateResponse(request, "film.html", {"film": None, "release": settings.RELEASE,
                                                                 "source": settings.SOURCE_URL, "contact": settings.CONTACT},
                                          status_code=404)
    base = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(request, "film.html", {
        "film": {"title": s.get("title") or "A film made with Gobo", "kind": t["kind"],
                 "src": f"/f/{token}/film", "poster": f"/f/{token}/poster" if _poster_path(s, t) else "",
                 "w": t.get("w"), "h": t.get("h"), "seconds": t.get("seconds")},
        "page": f"{base}/f/{token}", "base": base, "release": settings.RELEASE, "source": settings.SOURCE_URL,
        "contact": settings.CONTACT})


@router.get("/f/{token}/film")
async def shared_film_file(token: str, download: int = 0):
    s, t, p = _shared(token)
    if not s:
        return HTMLResponse("Not found.", status_code=404)
    return FileResponse(p, media_type=routes._media_type(p),
                        filename=("gobo-" + os.path.basename(p)) if download else None,
                        headers={"Cache-Control": "public, max-age=3600"})


@router.get("/f/{token}/poster")
async def shared_film_poster(token: str):
    s, t, _ = _shared(token)
    p = _poster_path(s, t) if s else None
    if not p:
        return HTMLResponse("Not found.", status_code=404)
    return FileResponse(p, media_type=routes._media_type(p), headers={"Cache-Control": "public, max-age=3600"})
