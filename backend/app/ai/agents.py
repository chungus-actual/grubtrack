"""OpenClaw and Hermes, through their CLIs: one prompt in, the agent's final reply out.

  openclaw agent --agent <id> -m <prompt>   (a turn through the running Gateway)
  hermes -z <prompt>                        (scripted one-shot: same tools and skills, reply only)
"""

from __future__ import annotations

import asyncio
import codecs
import os
import shutil
from typing import AsyncIterator

from .. import config


def configured() -> bool:
    return config.AGENT in ("openclaw", "hermes")


def _argv(prompt: str) -> list[str]:
    if config.AGENT == "openclaw":
        return [*config.AGENT_CMD, "agent", "--agent", config.OPENCLAW_AGENT, "-m", prompt]
    if config.AGENT == "hermes":
        return [*config.AGENT_CMD, "-z", prompt]
    from . import AIError
    raise AIError(f"GT_AGENT must be openclaw or hermes, not {config.AGENT!r}")


def _env() -> dict:
    env = dict(os.environ)
    if config.AGENT_PATH:
        env["PATH"] = config.AGENT_PATH
    return env


def found() -> bool:
    """The agent's command resolves (no process is started)."""
    if not config.AGENT_CMD:
        return False
    exe = config.AGENT_CMD[0]
    path = _env().get("PATH")
    return bool(shutil.which(exe, path=path)) and all(
        os.path.exists(a) for a in config.AGENT_CMD[1:] if a.startswith(("/", "~", ".")))


async def _spawn(prompt: str):
    from . import AIError
    try:
        return await asyncio.create_subprocess_exec(*_argv(prompt), stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, env=_env())
    except FileNotFoundError as e:
        raise AIError(f"{config.AGENT_NAME} isn't installed here ({config.AGENT_CMD[0]} not found)") from e


def _failed(code: int, err: bytes):
    from . import AIError
    tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] if err else []
    return AIError(f"{config.AGENT_NAME} exited with code {code}" + (f": {tail[0][:200]}" if tail else ""))


async def text(prompt: str) -> str:
    from . import AIError
    proc = await _spawn(prompt)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=config.AGENT_TIMEOUT)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise AIError(f"{config.AGENT_NAME} didn't answer within {config.AGENT_TIMEOUT:g} s") from e
    if proc.returncode != 0 and not (config.AGENT == "hermes" and proc.returncode == 1):  # hermes 1 = no text
        raise _failed(proc.returncode, err)
    return out.decode("utf-8", errors="replace").strip()


async def stream(prompt: str) -> AsyncIterator[str]:
    proc = await _spawn(prompt)
    err_task = asyncio.create_task(proc.stderr.read())   # drained alongside, so a chatty stderr can't block stdout
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while True:
            chunk = await asyncio.wait_for(proc.stdout.read(512), timeout=config.AGENT_TIMEOUT)
            if not chunk:
                break
            piece = dec.decode(chunk)
            if piece:
                yield piece
        await proc.wait()
        err = await err_task
        if proc.returncode not in (0, 1 if config.AGENT == "hermes" else 0):
            raise _failed(proc.returncode, err)
    finally:
        if proc.returncode is None:
            proc.kill()
        err_task.cancel()
