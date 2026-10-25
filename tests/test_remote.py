"""The CLI against a GrubTrack server: a real server on a spare port, and the CLI run the way an
agent on another machine runs it (no database of its own, a URL and a token)."""

import importlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from conftest import CLI, SCRIPTS

TOKEN = "t0ken-for-tests"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gtremote")
    static = tmp / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>GrubTrack</title>")
    db = tmp / "data" / "food.db"
    os.environ.update({"FOODDB_PATH": str(db), "GT_SKILL_SCRIPTS": str(SCRIPTS), "GT_STATIC": str(static),
                       "GT_API_TOKEN": TOKEN, "GT_AGENT": "", "GT_MODEL_PROVIDER": "", "GT_SWEEP_SECONDS": "3600",
                       "GT_RECIPES_DIR": str(tmp / "recipes")})
    for m in [m for m in sys.modules if m.startswith("app") or m == "fooddb_sqlite"]:
        del sys.modules[m]
    import uvicorn
    main = importlib.import_module("app.main")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}", "db": db, "data": db.parent, "main": main, "tmp": tmp}
    srv.should_exit = True
    t.join(5)
    os.environ.pop("GT_API_TOKEN", None)


@pytest.fixture()
def agent(server, tmp_path):
    """Run the CLI the way an agent elsewhere does: GRUBTRACK_URL, no FOODDB_PATH, its own cwd."""
    env = {k: v for k, v in os.environ.items() if k not in ("FOODDB_PATH", "GT_DATA", "GT_CLI_LOCAL")}
    env.update({"GRUBTRACK_URL": server["url"], "GRUBTRACK_TOKEN": TOKEN, "XDG_CACHE_HOME": str(tmp_path / "cache")})

    def run(*args, stdin=None, ok=True, token=TOKEN):
        e = {**env, "GRUBTRACK_TOKEN": token}
        r = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, input=stdin,
                           env=e, cwd=tmp_path)
        if ok:
            assert r.returncode == 0, f"{args}: {r.stderr}"
        return r
    return run


def photo(path, body=b"\xff\xd8\xff\xe0 a photo"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_logging_from_another_machine(server, agent, tmp_path):
    assert json.loads(agent("where").stdout) == {"server": server["url"], "remote": True}
    # the photo is where an OpenClaw agent keeps it, relative to its working folder
    photo(tmp_path / "data" / "food-inventory" / "images" / "yogurt.jpg")
    out = agent("add", "--json", json.dumps({"name": "Greek yogurt", "storage": "fridge", "price": 6.49,
                                             "images": [{"path": "data/food-inventory/images/yogurt.jpg"}]}))
    item_id = out.stdout.strip().strip('"').splitlines()[-1].strip().strip('"')
    con = sqlite3.connect(server["db"])
    row = con.execute("SELECT name, price FROM items WHERE deletedAt IS NULL").fetchone()
    assert row == ("Greek yogurt", 6.49)
    (img,) = [r[0] for r in con.execute("SELECT path FROM images")]
    assert img == "images/yogurt.jpg" and (server["data"] / img).read_bytes().startswith(b"\xff\xd8")
    inv = json.loads(agent("inventory").stdout)
    assert [i["name"] for i in inv] == ["Greek yogurt"] and item_id


def test_receipt_file_stdin_and_journal(server, agent, tmp_path):
    photo(tmp_path / "receipt.jpg")
    r = tmp_path / "r.json"
    r.write_text(json.dumps({"store": "Corner Grocer, 12 Elm St", "date": "2026-10-22", "tax": 0, "total": 3.98,
                             "image": "receipt.jpg", "lines": [{"raw": "MILK", "name": "Milk", "price": 3.98}]}))
    rep = json.loads(agent("receipt-add", "--file", r).stdout)
    assert rep["reconciliation"]["ok"] is True
    con = sqlite3.connect(server["db"])
    assert con.execute("SELECT imagePath FROM receipts").fetchone()[0] == "images/receipt.jpg"
    # stdin, the other way the skill hands over JSON
    agent("shop-add", "--name", "oat milk")
    out = agent("add", "--json", "-", stdin=json.dumps({"name": "Sourdough", "storage": "counter"}))
    assert out.returncode == 0
    assert con.execute("SELECT COUNT(*) FROM items WHERE name = 'Sourdough'").fetchone()[0] == 1
    j = json.loads(agent("journal-add", "--slug", "pad-thai", "--score", "8", "--tags", "takeout,quick",
                         "--review", "Better than last time").stdout)
    assert j["slug"] == "pad-thai"
    assert con.execute("SELECT score, tags FROM recipe_journal WHERE recipe_slug = 'pad-thai'").fetchone() == \
        (8, '["takeout", "quick"]')


def test_a_folder_of_recipes_travels_zipped(server, agent, tmp_path):
    d = tmp_path / "recipes"
    d.mkdir()
    (d / "a.jsonl").write_text(json.dumps({"title": "Toast", "ingredients": ["bread", "butter"]}) + "\n")
    rep = json.loads(agent("recipes-import", "--file", d).stdout)
    assert rep["added"] == 1 and rep["files"] == 1


def test_snapshot_for_readers(server, agent):
    snap = json.loads(agent("snapshot").stdout)
    assert snap["remote"] is True
    con = sqlite3.connect(f"file:{snap['path']}?mode=ro", uri=True)
    assert con.execute("SELECT COUNT(*) FROM items").fetchone()[0] >= 1
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "bulk_recipes" not in tables and "shopping" in tables   # core leaves out the library
    con.close()
    full = json.loads(agent("snapshot", "--scope", "full", "--max-age", "0").stdout)["path"]
    assert "bulk_recipes" in {r[0] for r in sqlite3.connect(full).execute("SELECT name FROM sqlite_master")}


def test_token_and_errors(server, agent):
    r = agent("inventory", ok=False, token="wrong")
    assert r.returncode == 3 and "answered 401" in r.stderr
    r = agent("receipt-add", "--file", "/nope/missing.json", ok=False)
    assert r.returncode != 0
    import httpx
    assert httpx.get(server["url"] + "/api/export/db").status_code == 401
    assert httpx.get(server["url"] + "/api/images/food.db").status_code == 404     # images only
    assert httpx.get(server["url"] + "/api/images/data/food-inventory/images/yogurt.jpg").status_code == 200


def test_unreachable_server_is_loud(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("FOODDB_PATH", "GT_DATA")}
    env["GRUBTRACK_URL"] = "http://127.0.0.1:9"
    r = subprocess.run([sys.executable, str(CLI), "inventory"], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert r.returncode == 3 and "Can't reach GrubTrack" in r.stderr


def test_nightly_backup_keeps_the_newest(server, monkeypatch):
    main = server["main"]
    monkeypatch.setattr(main.config, "BACKUPS", 2)
    for day in ("2026-10-20", "2026-10-21", "2026-10-22"):
        assert main.backup_once(today=day)
    assert main.backup_once(today="2026-10-22") is None
    kept = sorted(p.name for p in (server["data"] / "backups").glob("food-*.db"))
    assert len(kept) == 2 and "food-2026-10-22.db" in kept     # with the one the server made at startup
    assert "food-2026-10-20.db" not in kept and "food-2026-10-21.db" not in kept


def test_installer_points_a_skill_at_the_server(server, tmp_path):
    dest = tmp_path / "skills" / "food-inventory"
    env = {k: v for k, v in os.environ.items() if k not in ("FOODDB_PATH", "GT_DATA")}
    r = subprocess.run([sys.executable, str(SCRIPTS.parents[2] / "tools" / "install_skill.py"), "openclaw", "--dest", dest,
                        "--url", server["url"] + "/", "--token", TOKEN, "--backup-dir", tmp_path / "b"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert f"server:    {server['url']}" in r.stdout
    assert json.loads((dest / "grubtrack.json").read_text()) == {"url": server["url"], "token": TOKEN}
    shop = subprocess.run([sys.executable, str(dest / "scripts" / "fooddb_sqlite.py"), "shop"], capture_output=True,
                          text=True, env=env, cwd=tmp_path)
    assert shop.returncode == 0 and any(s["name"] == "oat milk" for s in json.loads(shop.stdout))
