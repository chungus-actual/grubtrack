"""For agents and scripts on other machines: the CLI over HTTP, and a copy of the database to read.

POST /api/cli runs any fooddb_sqlite.py command here, against this server's database, exactly as
it runs on the command line (the skill's CLI forwards to it when grubtrack.json has a URL). Files
come with the request: "@file:<key>" in an argument is the uploaded file's path, and inside a
JSON payload a photo becomes images/<its name> for good.

GET /api/export/db is a consistent snapshot for code that reads with its own SQL. Both need
GT_API_TOKEN as a bearer token when it is set.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse

from .. import config

router = APIRouter(prefix="/api")
CLI = config.SKILL_SCRIPTS / "fooddb_sqlite.py"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}
_snap_lock = asyncio.Lock()


def require_token(request: Request) -> None:
    if config.API_TOKEN and request.headers.get("authorization") != f"Bearer {config.API_TOKEN}":
        raise HTTPException(401, "This needs GrubTrack's API token (GT_API_TOKEN) as a bearer token")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "upload").name)[:100] or "upload"


def _keep_photo(src: Path, name: str) -> str:
    """An uploaded photo into images/, under its own name (numbered if taken); its stored path."""
    config.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    stem, ext = Path(_safe(name)).stem, Path(name).suffix.lower()
    dest, n = config.IMAGES_DIR / f"{stem}{ext}", 2
    while dest.exists():
        if dest.read_bytes() == src.read_bytes():
            break   # the same photo sent again
        dest, n = config.IMAGES_DIR / f"{stem}-{n}{ext}", n + 1
    if not dest.exists():
        dest.write_bytes(src.read_bytes())
    return f"images/{dest.name}"


def _resolve_json(text: str, saved: dict, names: dict) -> str:
    """Inside a JSON payload, "@file:<key>" photos become their kept images/ path."""
    def walk(x):
        if isinstance(x, str) and x.startswith("@file:"):
            key = x[6:]
            if key in saved and Path(names.get(key, "")).suffix.lower() in IMAGE_EXT:
                return _keep_photo(saved[key], names[key])
            return str(saved[key]) if key in saved else x
        if isinstance(x, list):
            return [walk(y) for y in x]
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        return x
    try:
        return json.dumps(walk(json.loads(text)))
    except ValueError:
        return text


@router.post("/cli")
async def cli(request: Request, body: dict = Body(...)):
    require_token(request)
    args = [str(a) for a in body.get("args") or []]
    if not args:
        raise HTTPException(400, "args: a command is required, e.g. [\"inventory\"]")
    names = {k: str(v) for k, v in (body.get("names") or {}).items()}
    with tempfile.TemporaryDirectory(prefix="gt-cli-") as tmp:
        saved = {}
        for key, b64 in (body.get("files") or {}).items():
            path = Path(tmp) / f"{_safe(key)}-{_safe(names.get(key, key))}"
            path.write_bytes(base64.b64decode(b64))
            saved[key] = path
        out_args = []
        for i, a in enumerate(args):
            flag, eq, val = a.partition("=")
            prev = args[i - 1] if i else ""
            if a.startswith("@file:") and a[6:] in saved:
                p = saved[a[6:]]
                if prev == "--file" and p.suffix.lower() in (".json", ".jsonl", ".ndjson"):
                    p.write_text(_resolve_json(p.read_text(encoding="utf-8"), saved, names), encoding="utf-8")
                a = str(p)
            elif eq and flag == "--file" and val.startswith("@file:") and val[6:] in saved:
                a = f"--file={saved[val[6:]]}"
            elif eq and flag == "--json" and val != "-":
                a = f"--json={_resolve_json(val, saved, names)}"
            elif prev == "--json" and a != "-":
                a = _resolve_json(a, saved, names)
            out_args.append(a)
        stdin = body.get("stdin")
        if stdin is not None:
            stdin = _resolve_json(str(stdin), saved, names)
        env = {**os.environ, "FOODDB_PATH": str(config.DB_PATH), "GT_CLI_LOCAL": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(CLI), *out_args, cwd=tmp, env=env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        timeout = 900 if args[0] == "recipes-import" else 120
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf-8") if stdin is not None else None),
                                              timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(504, f"{args[0]} took longer than {timeout} s")
    return {"code": proc.returncode, "stdout": out.decode("utf-8", "replace"), "stderr": err.decode("utf-8", "replace")}


def _snapshot(scope: str, dest: Path) -> None:
    src = sqlite3.connect(str(config.DB_PATH), timeout=15)
    try:
        out = sqlite3.connect(str(dest))
        src.backup(out)
        if scope == "core":
            out.execute("DROP TABLE IF EXISTS bulk_recipes")
            out.commit()
            out.execute("VACUUM")
        out.close()
    finally:
        src.close()


@router.get("/export/db")
async def export_db(request: Request, scope: str = "core"):
    """A consistent copy of the database. scope=core leaves out the recipe library; scope=full is
    everything (what the nightly backup keeps). Shared for a few seconds between callers."""
    require_token(request)
    if scope not in ("core", "full"):
        raise HTTPException(400, "scope is core or full")
    cache = config.DATA_DIR / ".export" / f"food-{scope}.db"
    async with _snap_lock:
        if not cache.exists() or time.time() - cache.stat().st_mtime > 10:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.unlink(missing_ok=True)
            await asyncio.to_thread(_snapshot, scope, tmp)
            os.replace(tmp, cache)
    return FileResponse(str(cache), media_type="application/vnd.sqlite3", filename=f"food-{scope}.db",
                        headers={"Cache-Control": "no-store"})
