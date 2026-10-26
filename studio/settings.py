"""Everything the studio reads from its environment, in one place.

Keys never live in the code. Set them in the environment, or in a .env file
next to where the app is started (see .env.example); a variable already in the
environment wins over the file. STUDIO_ENV_FILE points at another file, or
"" to read none (the tests do that, so a developer's real keys stay out).
"""
import os
import re
import secrets


def _load_env_file(path: str) -> None:
    if not path or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip().removeprefix("export ").strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            else:
                # "STUDIO_PASSWORD=   # one shared password": the comment is not the value
                # (.env.example is written that way; Docker reads it the same)
                v = re.split(r"(?:^|\s)#", v, maxsplit=1)[0].strip()
            os.environ.setdefault(k, v)


_load_env_file(os.getenv("STUDIO_ENV_FILE", ".env"))

# The release: "Take N", from studio/release.json. deploy/azure.sh bumps it on
# every release, so the home page, the sign-in page and the guide always name
# what is actually running. Take 0 is a build that was never released.
def _release():
    import json
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "release.json"), encoding="utf-8") as f:
            r = json.load(f)
        return int(r.get("take") or 0), str(r.get("date") or "")
    except (OSError, ValueError):
        return 0, ""


TAKE, RELEASE_DATE = _release()
RELEASE = f"Take {TAKE}" if TAKE else "Take 0 (unreleased)"
RELEASE_ID = f"take-{TAKE}"                 # the git tag and the image tag
BUILD = RELEASE_DATE or "dev"

# providers
SEGMIND_API_KEY = os.getenv("SEGMIND_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")     # Director and Board planner; optional
# Comfy Router (api.comfy.org): ~260 hosted models behind one key. With it set,
# the studio leads with Comfy's models; STUDIO_ENGINES ("comfy", "segmind" or
# both, comma-separated) picks which catalogues are shown.
COMFY_API_KEY = os.getenv("COMFY_API_KEY", "")
ENGINES = {e.strip().lower() for e in os.getenv("STUDIO_ENGINES", "").split(",") if e.strip()}
# "Prompts from a reference" in the Content Generator: GPT-4o when this is set,
# Claude otherwise.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Reference images are handed to Segmind as URLs. With an Azure storage
# account they go to a private container behind a short-lived SAS link;
# without one, Segmind's own upload endpoint is used instead.
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")

# where the studio lives in the URL space; everything hangs off it
PREFIX = "/" + os.getenv("STUDIO_PREFIX", "/studio").strip("/")

# Sign-in. Either one switches it on:
#   STUDIO_PASSWORD  one shared password; people type their own name with it
#   STUDIO_USERS     name:password pairs, comma-separated, for separate logins
# Neither set: no sign-in at all (the home page says so). The name is what the
# Library files a take under.
PASSWORD = os.getenv("STUDIO_PASSWORD", "")
USERS = {n.strip(): p.strip() for n, _, p in
         (pair.partition(":") for pair in os.getenv("STUDIO_USERS", "").split(","))
         if n.strip() and p.strip()}
SESSION_DAYS = int(os.getenv("STUDIO_SESSION_DAYS", "30"))
# Behind HTTPS (any hosted deployment) the sign-in cookie is marked Secure.
SECURE_COOKIES = os.getenv("STUDIO_SECURE_COOKIES", "") == "1"


# Who pays for the models. "server" (the default): the keys above, for everyone
# who signs in. "visitor": a public demo. Each visitor signs in with their own
# Comfy API key, which is kept in this process's memory only (studio/visitors.py),
# and sees only their own reels; the server's own keys are never spent.
VISITOR_KEYS = os.getenv("STUDIO_KEYS", "server").strip().lower() == "visitor"

# Where the code is, for the sign-in and About pages (a fork points it at its own).
SOURCE_URL = os.getenv("STUDIO_SOURCE_URL", "https://github.com/RickBL99/gobo").rstrip("/")


def sign_in_on() -> bool:
    return VISITOR_KEYS or bool(PASSWORD or USERS)


def session_secret() -> str:
    """STUDIO_SECRET, or one made on first run and kept in the data folder, so
    sign-ins survive a restart without anyone having to invent a key."""
    s = os.getenv("STUDIO_SECRET", "")
    if s:
        return s
    root = os.path.abspath(os.getenv("STUDIO_DIR") or "data")
    path = os.path.join(root, ".session-secret")
    try:
        with open(path, encoding="utf-8") as f:
            s = f.read().strip()
    except OSError:
        s = ""
    if not s:
        s = secrets.token_urlsafe(48)
        os.makedirs(root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)
        os.chmod(path, 0o600)
    return s
