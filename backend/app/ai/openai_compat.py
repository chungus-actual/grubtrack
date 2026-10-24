"""Any OpenAI-compatible chat endpoint: OpenAI, Ollama (local or ollama.com), LM Studio, vLLM,
OpenRouter, llama.cpp. GT_MODEL_URL is the base (the part before /chat/completions)."""

from __future__ import annotations

import base64
import json
from typing import AsyncIterator

import httpx

from .. import config

DEFAULT_URL = "https://api.openai.com/v1"
transport: httpx.AsyncBaseTransport | None = None   # tests swap in an httpx.MockTransport


def _url() -> str:
    u = (config.MODEL_URL or DEFAULT_URL).rstrip("/")
    return u[: -len("/chat/completions")] if u.endswith("/chat/completions") else u


def _body(messages: list[dict], image: tuple[bytes, str] | None, stream: bool) -> dict:
    if image:
        data, media_type = image
        uri = f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"
        last = messages[-1]
        messages = messages[:-1] + [{"role": last["role"], "content": [
            {"type": "text", "text": last["content"]},
            {"type": "image_url", "image_url": {"url": uri}},
        ]}]
    body = {"model": config.MODEL, "messages": messages, "stream": stream}
    if config.MODEL_EFFORT:
        body["reasoning_effort"] = config.MODEL_EFFORT
    if config.MODEL_MAX_TOKENS:
        body["max_tokens"] = config.MODEL_MAX_TOKENS
    return body


def _client() -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {config.MODEL_KEY}"} if config.MODEL_KEY else {}
    return httpx.AsyncClient(timeout=httpx.Timeout(config.MODEL_TIMEOUT, connect=15), headers=headers,
                             transport=transport)


def _error(status: int, body: bytes) -> Exception:
    from . import AIError
    try:
        j = json.loads(body)
        msg = j.get("error", {}).get("message") if isinstance(j.get("error"), dict) else j.get("error")
    except ValueError:
        msg = None
    return AIError(f"{config.MODEL or 'model'} answered {status}: {msg or body[:200].decode('utf-8', 'replace')}")


async def text(messages: list[dict], image: tuple[bytes, str] | None = None) -> str:
    async with _client() as c:
        r = await c.post(f"{_url()}/chat/completions", json=_body(messages, image, False))
        if r.status_code >= 400:
            raise _error(r.status_code, r.content)
        choice = (r.json().get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or ""


async def stream(messages: list[dict]) -> AsyncIterator[str]:
    async with _client() as c:
        async with c.stream("POST", f"{_url()}/chat/completions", json=_body(messages, None, True)) as r:
            if r.status_code >= 400:
                raise _error(r.status_code, await r.aread())
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    choice = (json.loads(data).get("choices") or [{}])[0]
                except ValueError:
                    continue
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    yield piece
