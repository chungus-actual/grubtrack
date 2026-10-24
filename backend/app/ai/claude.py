"""Claude, through the official Anthropic SDK. GT_MODEL_KEY, or the SDK's own credential lookup
(ANTHROPIC_API_KEY, an `ant auth login` profile) when it's unset."""

from __future__ import annotations

import base64
from typing import AsyncIterator

from .. import config

DEFAULT_MODEL = "claude-opus-5-5"
# Models that take server-side refusal fallbacks: a declined request is re-run on a fallback model
# inside the same call instead of coming back empty.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
transport = None   # tests swap in an httpx2.MockTransport (the SDK's HTTP library)


def _client():
    import anthropic  # imported here so the server starts without the SDK when Claude isn't used
    kw = {"timeout": config.MODEL_TIMEOUT}
    if config.MODEL_KEY:
        kw["api_key"] = config.MODEL_KEY
    if config.MODEL_URL:
        kw["base_url"] = config.MODEL_URL
    if transport is not None:
        import httpx2
        kw["http_client"] = httpx2.AsyncClient(transport=transport)
    return anthropic.AsyncAnthropic(**kw)


def _request(messages: list[dict], image: tuple[bytes, str] | None) -> dict:
    if image:
        data, media_type = image
        last = messages[-1]
        messages = messages[:-1] + [{"role": last["role"], "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.standard_b64encode(data).decode("ascii")}},
            {"type": "text", "text": last["content"]},
        ]}]
    model = config.MODEL or DEFAULT_MODEL
    kw = {"model": model, "max_tokens": config.MODEL_MAX_TOKENS or 16000, "messages": messages}
    if config.MODEL_EFFORT:
        kw["output_config"] = {"effort": config.MODEL_EFFORT}
    if model in FALLBACK_MODELS:
        kw["betas"] = ["server-side-fallback-2026-07-01"]
        kw["fallbacks"] = "default"
    return kw


def _check(final) -> None:
    from . import AIError
    if final.stop_reason == "refusal":
        raise AIError("Claude declined this request")


async def stream(messages: list[dict], image: tuple[bytes, str] | None = None) -> AsyncIterator[str]:
    async with _client() as client:
        async with client.beta.messages.stream(**_request(messages, image)) as s:
            async for piece in s.text_stream:
                yield piece
            final = await s.get_final_message()
    _check(final)


async def text(messages: list[dict], image: tuple[bytes, str] | None = None) -> str:
    out = []
    async for piece in stream(messages, image):
        out.append(piece)
    return "".join(out)
