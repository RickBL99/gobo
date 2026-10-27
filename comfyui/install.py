"""The one thing Gobo needs that ComfyUI doesn't already have: wasmtime, which
runs Gobo's Rust. It installs itself the first time ComfyUI starts with Gobo in
custom_nodes, into ComfyUI's own Python, so nobody has to find a terminal or
know which Python that is. ComfyUI Manager runs this file too when it installs
the pack.
"""
import importlib
import subprocess
import sys

# (module, what pip installs): the same as requirements.txt
NEEDS = (("wasmtime", "wasmtime>=30"),)


def ensure(needs=NEEDS, run=subprocess.check_call):
    """Install whatever of `needs` won't import; returns what it installed."""
    missing = []
    for module, spec in needs:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(spec)
    if not missing:
        return []
    print(f"[Gobo] first start: installing {', '.join(missing)} into ComfyUI's Python (once)…", flush=True)
    pip = [sys.executable, "-m", "pip", "install", *missing]
    try:
        run(pip)
    except (subprocess.CalledProcessError, OSError):
        # a Python set up without pip (a bare venv) has one to hand: ensurepip
        try:
            run([sys.executable, "-m", "ensurepip", "--upgrade"])
            run(pip)
        except (subprocess.CalledProcessError, OSError) as e:
            raise ImportError(
                f"Gobo couldn't install {', '.join(missing)} by itself ({e}). Install it once, then restart ComfyUI:\n"
                f'  "{sys.executable}" -m pip install ' + " ".join(f'"{s}"' for s in missing)) from e
    importlib.invalidate_caches()
    return missing


if __name__ == "__main__":
    ensure()
