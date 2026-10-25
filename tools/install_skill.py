#!/usr/bin/env python3
"""Install the food-inventory skill into an agent harness (OpenClaw or Hermes).

  python3 tools/install_skill.py openclaw --db ~/grubtrack/data/food.db
  python3 tools/install_skill.py hermes   --db ~/grubtrack/data/food.db --local my-house.md
  python3 tools/install_skill.py openclaw --url https://nas.lan:8888 --token "$GT_API_TOKEN" --ca house-ca.pem

What it does:
  1. archives any copy already at the destination (a .tgz in --backup-dir)
  2. copies skill/food-inventory/ there
  3. writes the folder's absolute path in place of {baseDir}, so the agent runs the right script
     without having to work out where it lives
  4. pins the database in grubtrack.json, so the agent's CLI and GrubTrack open the same food.db; or,
     with --url, points the CLI at a GrubTrack server (another machine, a container), where every
     command then runs
  5. adds your household notes as LOCAL.md (names, where to confirm, house rules), if given
  6. runs `init` against the pinned database (creates or migrates it; safe on a live one)

Re-running keeps the pinned database and LOCAL.md from the previous install unless you pass new ones.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "skill" / "food-inventory"
DEST = {
    "openclaw": "~/.openclaw/workspace/skills/food-inventory",
    "hermes": "~/.hermes/skills/household/food-inventory",
}
NEXT = {
    "openclaw": "OpenClaw picks it up on the next session (`openclaw skills list` shows it; `/new` in a chat starts one).",
    "hermes": "Hermes picks it up on the next session (`hermes skills list` shows it).",
}
RENDERED = ("SKILL.md", "SESSION_RULES.md", "references/SCHEMA.md")
KEEP = ("grubtrack.json", "LOCAL.md")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("harness", choices=sorted(DEST))
    ap.add_argument("--db", help="food.db the agent should use (default: the CLI's own lookup)")
    ap.add_argument("--url", help="a GrubTrack server to send every command to, instead of a local food.db")
    ap.add_argument("--token", help="that server's GT_API_TOKEN")
    ap.add_argument("--ca", help="CA certificate that signed the server's HTTPS certificate, if it isn't public")
    ap.add_argument("--dest", help="skill folder to install into (default: %s)" % ", ".join(f"{k}: {v}" for k, v in DEST.items()))
    ap.add_argument("--local", help="household notes to install as LOCAL.md")
    ap.add_argument("--backup-dir", default="~/.local/share/grubtrack/skill-backups")
    ap.add_argument("--python", default=sys.executable, help="interpreter used for the init check")
    a = ap.parse_args()

    dest = Path(os.path.expanduser(a.dest or DEST[a.harness])).resolve()
    kept = {}
    if dest.exists():
        for name in KEEP:
            if (dest / name).is_file():
                kept[name] = (dest / name).read_bytes()
        backups = Path(os.path.expanduser(a.backup_dir))
        backups.mkdir(parents=True, exist_ok=True)
        archive = backups / f"{dest.name}-{time.strftime('%Y%m%d-%H%M%S')}.tgz"
        with tarfile.open(archive, "w:gz") as t:
            t.add(dest, arcname=dest.name)
        shutil.rmtree(dest)
        print(f"previous copy archived: {archive}")

    shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "grubtrack.json", "LOCAL.md"))
    base = dest.as_posix()
    for rel in RENDERED:
        f = dest / rel
        if f.is_file():
            f.write_text(f.read_text(encoding="utf-8").replace("{baseDir}", base), encoding="utf-8")

    for name, data in kept.items():
        (dest / name).write_bytes(data)
    if a.url:
        pin = {"url": a.url.rstrip("/")}
        if a.token:
            pin["token"] = a.token
        if a.ca:
            pin["ca"] = Path(os.path.expanduser(a.ca)).resolve().as_posix()
        (dest / "grubtrack.json").write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")
        os.chmod(dest / "grubtrack.json", 0o600)   # it can hold the API token
    elif a.db:
        db = Path(os.path.expanduser(a.db)).resolve()
        (dest / "grubtrack.json").write_text(json.dumps({"db": db.as_posix()}, indent=2) + "\n", encoding="utf-8")
    if a.local:
        shutil.copyfile(os.path.expanduser(a.local), dest / "LOCAL.md")

    cli = dest / "scripts" / "fooddb_sqlite.py"
    env = {k: v for k, v in os.environ.items() if k not in ("FOODDB_PATH", "GT_DATA")}  # test the pin, not this shell
    env["PYTHONDONTWRITEBYTECODE"] = "1"   # leave the skill folder as copied
    init = subprocess.run([a.python, str(cli), "init"], capture_output=True, text=True, env=env)
    where = subprocess.run([a.python, str(cli), "where"], capture_output=True, text=True, env=env)
    if init.returncode != 0:
        print(init.stderr.strip() or init.stdout.strip(), file=sys.stderr)
        print(f"installed to {dest}, but `init` failed", file=sys.stderr)
        return 1
    print(f"installed: {dest}")
    w = json.loads(where.stdout)
    print(f"server:    {w['server']}" if w.get("remote") else f"database:  {w['db']}")
    if (dest / "LOCAL.md").exists():
        print("household notes: LOCAL.md")
    print(NEXT[a.harness])
    return 0


if __name__ == "__main__":
    sys.exit(main())
