"""Where things live and what GrubTrack may talk to. Everything comes from the environment (see the
README's settings table), so a throwaway copy on another port never touches the real database."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _path(name: str, default: Path) -> Path:
    v = _env(name)
    return Path(os.path.expanduser(v)) if v else default


def _flag(name: str, default: bool) -> bool:
    v = _env(name).lower()
    return default if not v else v in ("1", "true", "yes", "on")


_xdg = _path("XDG_DATA_HOME", Path.home() / ".local" / "share")
DB_PATH = _path("FOODDB_PATH", _path("GT_DATA", _xdg / "grubtrack") / "food.db")
DATA_DIR = DB_PATH.parent
IMAGES_DIR = DATA_DIR / "images"
MEDIA_ROOT = _path("GT_MEDIA_ROOT", DATA_DIR)   # relative photo paths an agent recorded resolve against this
SKILL_SCRIPTS = _path("GT_SKILL_SCRIPTS", REPO / "skill" / "food-inventory" / "scripts")
RECIPES_DIR = _path("GT_RECIPES_DIR", DATA_DIR / "recipes")   # markdown recipes (an Obsidian folder works)
RECIPES_WRITE = _flag("GT_RECIPES_WRITE", True)
STATIC_DIR = _path("GT_STATIC", REPO / "frontend" / "dist")
SWEEP_SECONDS = int(_env("GT_SWEEP_SECONDS", "30"))
API_TOKEN = _env("GT_API_TOKEN")                 # bearer token for /api/cli and /api/export/db
BACKUPS = int(_env("GT_BACKUPS", "14"))          # nightly copies of food.db kept in DATA_DIR/backups (0 = none)

# ---- the model: one direct LLM endpoint for reading receipts and recipes, and for chat when no
# agent is set. "openai" is any OpenAI-compatible server (OpenAI, Ollama, LM Studio, vLLM,
# OpenRouter, llama.cpp); "anthropic" is Claude.
MODEL_PROVIDER = _env("GT_MODEL_PROVIDER").lower()
MODEL = _env("GT_MODEL")
MODEL_URL = _env("GT_MODEL_URL")
MODEL_KEY = _env("GT_MODEL_KEY")
MODEL_EFFORT = _env("GT_MODEL_EFFORT")          # low | medium | high, where the model takes it
MODEL_MAX_TOKENS = int(_env("GT_MODEL_MAX_TOKENS", "0")) or None
MODEL_TIMEOUT = float(_env("GT_MODEL_TIMEOUT", "180"))
MODEL_VISION = _flag("GT_MODEL_VISION", True)   # 0 if the model can't read images

# ---- the agent: an OpenClaw or Hermes install with the food-inventory skill. It answers in its own
# voice with its own memory, and can act (send the list to a chat).
AGENT = _env("GT_AGENT").lower()
AGENT_NAME = _env("GT_AGENT_NAME") or {"openclaw": "OpenClaw", "hermes": "Hermes"}.get(AGENT, "")
AGENT_CMD = (shlex.split(_env("GT_AGENT_CMD")) or [AGENT]) if AGENT else []
OPENCLAW_AGENT = _env("GT_OPENCLAW_AGENT", "main")
AGENT_PATH = _env("GT_AGENT_PATH")              # PATH for the agent process, if the server's isn't right for it
AGENT_TIMEOUT = float(_env("GT_AGENT_TIMEOUT", "300"))

LIST_SEND_TO = _env("GT_LIST_SEND_TO")          # e.g. "Telegram chat 123456789"; the agent sends it there
LIST_SEND_LABEL = _env("GT_LIST_SEND_LABEL", "chat")
AI_STYLE = _env("GT_AI_STYLE", "Write plain text with simple markdown. Use commas, colons or periods, never em dashes. "
                                "Be specific and practical, no padding.")
MEAL_NOTES = _env("GT_MEAL_NOTES")              # household preferences added to every meal-ideas prompt

# fooddb_sqlite reads FOODDB_PATH at import; foodcore sits beside it
os.environ["FOODDB_PATH"] = str(DB_PATH)
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))
