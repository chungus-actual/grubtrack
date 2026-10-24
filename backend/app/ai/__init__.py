"""What GrubTrack asks of an AI, and who answers.

Two optional slots, set in the environment:

  model  a direct LLM endpoint (OpenAI-compatible or Claude). Fast and predictable: reads receipt
         photos and recipes into JSON, and writes meal ideas when there is no agent.
  agent  an OpenClaw or Hermes install with the food-inventory skill. Answers in its own voice with
         its own memory, and can act (send the shopping list to a chat).

Each job goes to the slot that suits it, falling back to the other:

  chat     meal ideas                       agent, else model
  extract  recipe to JSON, recipe timings   model, else agent
  vision   receipt photo to JSON            model only
  act      send the list somewhere          agent only
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator

from .. import config
from . import agents, claude, openai_compat


class AIUnavailable(Exception):
    """Nothing is configured that can do this job."""


class AIError(Exception):
    """The model or agent was asked and failed."""


def _model():
    if not config.MODEL_PROVIDER:
        return None
    if config.MODEL_PROVIDER == "openai":
        return openai_compat
    if config.MODEL_PROVIDER in ("anthropic", "claude"):
        return claude
    raise AIError(f"GT_MODEL_PROVIDER must be openai or anthropic, not {config.MODEL_PROVIDER!r}")


def route(job: str) -> str | None:
    model, agent = bool(config.MODEL_PROVIDER), agents.configured()
    if job == "chat":
        return "agent" if agent else "model" if model else None
    if job == "extract":
        return "model" if model else "agent" if agent else None
    if job == "vision":
        return "model" if model and config.MODEL_VISION else None
    if job == "act":
        return "agent" if agent and config.LIST_SEND_TO else None
    raise ValueError(job)


def _need(job: str) -> str:
    who = route(job)
    if not who:
        raise AIUnavailable({
            "chat": "No AI is set up: set GT_AGENT (OpenClaw or Hermes) or GT_MODEL_PROVIDER.",
            "extract": "No AI is set up to read this: set GT_MODEL_PROVIDER (or GT_AGENT).",
            "vision": "Reading photos needs a vision model: set GT_MODEL_PROVIDER and GT_MODEL.",
            "act": "Sending needs an agent and a destination: set GT_AGENT and GT_LIST_SEND_TO.",
        }[job])
    return who


async def text(prompt: str, job: str = "extract") -> str:
    """One prompt, the whole answer."""
    try:
        if _need(job) == "agent":
            return await agents.text(prompt)
        return await _model().text([{"role": "user", "content": prompt}])
    except (AIUnavailable, AIError):
        raise
    except Exception as e:
        raise AIError(str(e) or type(e).__name__) from e


async def stream(prompt: str, job: str = "chat") -> AsyncIterator[str]:
    """One prompt, the answer as it arrives."""
    who = _need(job)
    try:
        if who == "agent":
            async for t in agents.stream(prompt):
                yield t
        else:
            async for t in _model().stream([{"role": "user", "content": prompt}]):
                yield t
    except (AIUnavailable, AIError):
        raise
    except Exception as e:
        raise AIError(str(e) or type(e).__name__) from e


async def read_image(prompt: str, data: bytes, media_type: str) -> str:
    _need("vision")
    try:
        return await _model().text([{"role": "user", "content": prompt}], image=(data, media_type))
    except AIError:
        raise
    except Exception as e:
        raise AIError(str(e) or type(e).__name__) from e


async def act(prompt: str) -> str:
    _need("act")
    try:
        return await agents.text(prompt)
    except AIError:
        raise
    except Exception as e:
        raise AIError(str(e) or type(e).__name__) from e


async def sse(gen: AsyncIterator[str]) -> AsyncIterator[str]:
    """Server-sent events for the browser: {"text": chunk}..., then [DONE]; a failure is {"error"}."""
    try:
        async for chunk in gen:
            yield f"data: {json.dumps({'text': chunk})}\n\n"
    except (AIUnavailable, AIError) as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
    yield "data: [DONE]\n\n"


def parse_json(raw: str) -> dict:
    """The JSON object in a model's answer: code fences, a preamble or reasoning tags around it are
    tolerated, a missing object is an error."""
    s = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    s = re.sub(r"```(?:json)?", "", s)
    start = s.find("{")
    if start < 0:
        raise AIError("The answer had no JSON in it")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except ValueError as e:
                    raise AIError(f"The answer's JSON didn't parse: {e}") from e
    raise AIError("The answer's JSON was cut off")


def describe() -> dict:
    """What the browser needs to know: who answers which job, by name."""
    model = None
    if config.MODEL_PROVIDER:
        claude_default = claude.DEFAULT_MODEL if config.MODEL_PROVIDER in ("anthropic", "claude") else ""
        model = {"provider": config.MODEL_PROVIDER, "model": config.MODEL or claude_default}
    agent = {"kind": config.AGENT, "name": config.AGENT_NAME} if agents.configured() else None
    name = {"agent": agent and agent["name"], "model": model and (model["model"] or "the model"), None: None}
    return {
        "model": model,
        "agent": agent,
        "chat": name[route("chat")],
        "extract": name[route("extract")],
        "vision": route("vision") is not None,
        "send": config.LIST_SEND_LABEL if route("act") else None,
    }


async def health() -> dict:
    out = {"model": None, "agent": None}
    if config.MODEL_PROVIDER:
        out["model"] = {"configured": True, "provider": config.MODEL_PROVIDER}
    if agents.configured():
        out["agent"] = {"configured": True, "found": agents.found(), "kind": config.AGENT}
    return out
