import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skill" / "food-inventory" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "backend"))

CLI = SCRIPTS / "fooddb_sqlite.py"


@pytest.fixture()
def dbpath(tmp_path, monkeypatch):
    p = tmp_path / "food.db"
    monkeypatch.setenv("FOODDB_PATH", str(p))
    r = subprocess.run([sys.executable, str(CLI), "init"], capture_output=True, text=True,
                       env={**os.environ, "FOODDB_PATH": str(p)})
    assert r.returncode == 0, r.stderr
    return p


@pytest.fixture()
def con(dbpath):
    c = sqlite3.connect(str(dbpath))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture()
def cli(dbpath):
    def run(*args, stdin=None, ok=True):
        r = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, input=stdin,
                           env={**os.environ, "FOODDB_PATH": str(dbpath)})
        if ok:
            assert r.returncode == 0, f"{args}: {r.stderr}"
        return r
    return run
