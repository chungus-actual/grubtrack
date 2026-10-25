"""The CLI against a GrubTrack server instead of a local food.db.

When the database lives somewhere else (GrubTrack in a container on a NAS, the agent on another
machine), every command is sent to GrubTrack's /api/cli and runs there, with the same arguments
and the same output. Files a command names travel with it: a receipt JSON or recipe collection
given with --file, a folder (zipped on the way), and any photo a JSON payload points at ("image",
"images": [{"path": ...}]), so the agent's habits don't change.

Configured by GRUBTRACK_URL / GRUBTRACK_TOKEN / GRUBTRACK_CA, or the installer's grubtrack.json
({"url", "token", "ca"}). FOODDB_PATH, or GT_CLI_LOCAL (GrubTrack sets it for its own runs),
means a local database. Standard library only: an agent's skill folder has nothing to install.
"""

from __future__ import annotations

import base64
import io
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}
MAX_UPLOAD = 60 * 1024 * 1024


def config(skill_dir: str) -> dict | None:
    if os.environ.get("GT_CLI_LOCAL") or os.environ.get("FOODDB_PATH"):
        return None
    pinned = {}
    try:
        with open(os.path.join(skill_dir, "grubtrack.json"), encoding="utf-8") as f:
            pinned = json.load(f)
    except (OSError, ValueError):
        pass
    url = os.environ.get("GRUBTRACK_URL") or pinned.get("url")
    if not url:
        return None
    ca = os.environ.get("GRUBTRACK_CA") or pinned.get("ca")
    return {"url": url.rstrip("/"), "token": os.environ.get("GRUBTRACK_TOKEN") or pinned.get("token"),
            "ca": os.path.expanduser(ca) if ca else None}


def request(remote: dict, method: str, path: str, body: dict | None = None, timeout: float = 120):
    ctx = ssl.create_default_context(cafile=remote["ca"]) if remote.get("ca") else None
    headers = {"Content-Type": "application/json"}
    if remote.get("token"):
        headers["Authorization"] = f"Bearer {remote['token']}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(remote["url"] + path, data=data, method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _local_file(path: str, skill_dir: str) -> Path | None:
    """A path as an agent wrote it: absolute, from the working folder, or from a folder above the
    skill (an OpenClaw workspace records photos as data/food-inventory/images/x.jpg)."""
    p = Path(os.path.expanduser(path))
    if p.is_absolute():
        return p if p.is_file() else None
    for base in [Path.cwd(), *Path(skill_dir).resolve().parents[:4]]:
        if (base / p).is_file():
            return base / p
    return None


class _Upload:
    def __init__(self, skill_dir: str):
        self.skill_dir = skill_dir
        self.files: dict[str, str] = {}
        self.names: dict[str, str] = {}

    def add(self, data: bytes, name: str) -> str:
        key = f"f{len(self.files)}"
        if len(data) > MAX_UPLOAD:
            raise SystemExit(f"{name} is over {MAX_UPLOAD // 1024 // 1024} MB")
        self.files[key] = base64.b64encode(data).decode("ascii")
        self.names[key] = name
        return f"@file:{key}"

    def photos(self, obj):
        """Upload the photos a JSON payload points at, and point it at the uploads instead."""
        def one(v):
            if isinstance(v, str) and not v.startswith(("http:", "https:", "@file:")):
                f = _local_file(v, self.skill_dir)
                if f and f.suffix.lower() in IMAGE_EXT:
                    return self.add(f.read_bytes(), f.name)
            return v
        if isinstance(obj, list):
            return [self.photos(x) for x in obj]
        if not isinstance(obj, dict):
            return obj
        out = {}
        for k, v in obj.items():
            if k in ("image", "imagePath"):
                out[k] = one(v)
            elif k == "images" and isinstance(v, list):
                out[k] = [one(x) if isinstance(x, str) else {**x, "path": one(x.get("path"))} if isinstance(x, dict) else x
                          for x in v]
            elif k == "lines" or isinstance(v, (dict, list)):
                out[k] = self.photos(v)
            else:
                out[k] = v
        return out

    def payload(self, text: str) -> str:
        try:
            return json.dumps(self.photos(json.loads(text)))
        except ValueError:
            return text


def run(remote: dict, argv: list[str], skill_dir: str) -> int:
    up = _Upload(skill_dir)
    args, stdin = list(argv), None
    i = 0
    while i < len(args):
        a = args[i]
        flag, eq, val = a.partition("=")
        if flag in ("--file", "--json"):
            if not eq:
                if i + 1 >= len(args):
                    break
                val = args[i + 1]
            if flag == "--json" and val == "-":
                stdin = up.payload(sys.stdin.read())
            elif flag == "--json":
                val = up.payload(val)
            else:
                f = Path(os.path.expanduser(val))
                if f.is_dir():
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                        for p in sorted(f.rglob("*")):
                            if p.is_file() and not p.name.startswith("."):
                                z.write(p, p.relative_to(f).as_posix())
                    val = up.add(buf.getvalue(), f.name + ".zip")
                elif f.is_file():
                    raw = f.read_bytes()
                    if f.suffix.lower() in (".json", ".jsonl", ".ndjson"):
                        raw = up.payload(raw.decode("utf-8", "replace")).encode("utf-8")
                    val = up.add(raw, f.name)
                # a missing file is left as typed: the server says so the same way the CLI would
            if eq:
                args[i] = f"{flag}={val}"
            else:
                args[i + 1] = val
                i += 1
        i += 1
    body = {"args": args, "files": up.files, "names": up.names}
    if stdin is not None:
        body["stdin"] = stdin
    try:
        with request(remote, "POST", "/api/cli", body, timeout=900) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail")
        except Exception:
            detail = e.reason
        print(f"GrubTrack at {remote['url']} answered {e.code}: {detail}", file=sys.stderr)
        return 3
    except (urllib.error.URLError, OSError) as e:
        print(f"Can't reach GrubTrack at {remote['url']}: {getattr(e, 'reason', e)}", file=sys.stderr)
        return 3
    sys.stdout.write(out.get("stdout") or "")
    sys.stderr.write(out.get("stderr") or "")
    return int(out.get("code") or 0)


def snapshot(remote: dict, scope: str = "core", max_age: float = 60) -> str:
    """A read-only copy of the server's database for code that queries it with its own SQL, kept
    for max_age seconds. scope "core" leaves out the recipe library (most of the size)."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "grubtrack"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"food-{scope}.db"
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        return str(path)
    tmp = base / f".food-{scope}.{os.getpid()}.tmp"
    try:
        with request(remote, "GET", f"/api/export/db?scope={scope}", timeout=120) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        os.replace(tmp, path)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        if path.exists():
            print(f"warning: GrubTrack snapshot not refreshed ({e}); using the one from "
                  f"{time.ctime(path.stat().st_mtime)}", file=sys.stderr)
        else:
            raise
    return str(path)
