"""The skill pack: where the CLI finds food.db, and the installer for OpenClaw and Hermes."""

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import CLI, ROOT

INSTALL = ROOT / "tools" / "install_skill.py"


def _where(env_extra, cli=CLI, home=None):
    env = {k: v for k, v in os.environ.items() if k not in ("FOODDB_PATH", "GT_DATA", "XDG_DATA_HOME")}
    env.update(env_extra)
    r = subprocess.run([sys.executable, str(cli), "where"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return Path(json.loads(r.stdout)["db"])


def test_db_lookup_order(tmp_path):
    assert _where({"XDG_DATA_HOME": str(tmp_path / "xdg")}) == tmp_path / "xdg" / "grubtrack" / "food.db"
    assert _where({"GT_DATA": str(tmp_path / "data"), "XDG_DATA_HOME": str(tmp_path)}) == tmp_path / "data" / "food.db"
    assert _where({"FOODDB_PATH": str(tmp_path / "x.db"), "GT_DATA": str(tmp_path / "data")}) == tmp_path / "x.db"


def _install(harness, dest, *extra):
    env = {k: v for k, v in os.environ.items() if k not in ("FOODDB_PATH", "GT_DATA")}
    r = subprocess.run([sys.executable, str(INSTALL), harness, "--dest", str(dest), "--backup-dir",
                        str(dest.parent / "backups"), *map(str, extra)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    return r.stdout


def test_install_pins_db_and_renders_paths(tmp_path):
    db = tmp_path / "data" / "food.db"
    local = tmp_path / "house.md"
    local.write_text("# Our house\nConfirm in the family chat.\n")
    for harness in ("openclaw", "hermes"):
        dest = tmp_path / harness / "food-inventory"
        out = _install(harness, dest, "--db", db, "--local", local)
        assert f"database:  {db.as_posix()}" in out and "LOCAL.md" in out
        skill = (dest / "SKILL.md").read_text()
        assert "{baseDir}" not in skill and f"python3 {dest.as_posix()}/scripts/fooddb_sqlite.py inventory" in skill
        assert "{baseDir}" not in (dest / "SESSION_RULES.md").read_text()
        assert (dest / "LOCAL.md").read_text() == local.read_text()
        assert json.loads((dest / "grubtrack.json").read_text()) == {"db": db.as_posix()}
        assert not list(dest.rglob("__pycache__"))
        # the installed CLI, with no env at all, opens the pinned db
        assert _where({}, cli=dest / "scripts" / "fooddb_sqlite.py") == db
    assert db.exists()


def test_reinstall_keeps_pin_and_notes_and_archives(tmp_path):
    db = tmp_path / "food.db"
    dest = tmp_path / "skills" / "food-inventory"
    local = tmp_path / "house.md"
    local.write_text("notes")
    _install("openclaw", dest, "--db", db, "--local", local)
    (dest / "stale.txt").write_text("from an old version")
    out = _install("openclaw", dest)
    assert "previous copy archived" in out
    assert not (dest / "stale.txt").exists()
    assert (dest / "LOCAL.md").read_text() == "notes"
    assert json.loads((dest / "grubtrack.json").read_text())["db"] == db.as_posix()
    assert len(list((tmp_path / "skills" / "backups").glob("food-inventory-*.tgz"))) == 1


def test_skill_frontmatter_serves_both_harnesses():
    text = (ROOT / "skill" / "food-inventory" / "SKILL.md").read_text()
    front = text.split("---")[1]
    assert "name: food-inventory" in front and "description: " in front
    assert "openclaw:" in front and "bins: [python3]" in front
    assert "hermes:" in front and "category: household" in front
    desc = next(ln for ln in front.splitlines() if ln.startswith("description: "))
    assert len(desc) < 400
