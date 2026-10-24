"""The AI layer: which slot answers which job, and the exact requests each provider sends.
No network: OpenAI-compatible and Claude calls go to mock transports, agents are fake CLIs."""

import asyncio
import datetime as dt
import json
import os
import stat
import sys
import textwrap

import httpx
import pytest

from conftest import SCRIPTS

os.environ.setdefault("FOODDB_PATH", "/tmp/gt-test-ai.db")
os.environ.setdefault("GT_SKILL_SCRIPTS", str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS))

from app import ai, config, receipt_reader, recipe_import  # noqa: E402
from app.ai import agents, claude, openai_compat  # noqa: E402


def run(coro):
    return asyncio.run(coro)


async def collect(gen):
    return "".join([x async for x in gen])


@pytest.fixture()
def cfg(monkeypatch):
    """A clean slate: nothing configured."""
    for k, v in {"MODEL_PROVIDER": "", "MODEL": "", "MODEL_URL": "", "MODEL_KEY": "", "MODEL_EFFORT": "",
                 "MODEL_MAX_TOKENS": None, "MODEL_VISION": True, "AGENT": "", "AGENT_NAME": "", "AGENT_CMD": [],
                 "AGENT_PATH": "", "AGENT_TIMEOUT": 20, "LIST_SEND_TO": "", "LIST_SEND_LABEL": "chat"}.items():
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(openai_compat, "transport", None)
    monkeypatch.setattr(claude, "transport", None)
    return monkeypatch


# ------------------------------------------------------------------ routing

def test_routing_matrix(cfg):
    assert [ai.route(j) for j in ("chat", "extract", "vision", "act")] == [None, None, None, None]
    cfg.setattr(config, "MODEL_PROVIDER", "openai")
    assert [ai.route(j) for j in ("chat", "extract", "vision", "act")] == ["model", "model", "model", None]
    cfg.setattr(config, "AGENT", "hermes")
    assert [ai.route(j) for j in ("chat", "extract", "vision", "act")] == ["agent", "model", "model", None]
    cfg.setattr(config, "LIST_SEND_TO", "Telegram chat 1")
    assert ai.route("act") == "agent"
    cfg.setattr(config, "MODEL_PROVIDER", "")
    assert [ai.route(j) for j in ("chat", "extract", "vision")] == ["agent", "agent", None]
    cfg.setattr(config, "MODEL_PROVIDER", "openai")
    cfg.setattr(config, "MODEL_VISION", False)
    assert ai.route("vision") is None


def test_describe_names_who_answers(cfg):
    cfg.setattr(config, "MODEL_PROVIDER", "anthropic")
    d = ai.describe()
    assert d["model"] == {"provider": "anthropic", "model": "claude-opus-5-5"} and d["chat"] == "claude-opus-5-5"
    cfg.setattr(config, "AGENT", "openclaw")
    cfg.setattr(config, "AGENT_NAME", "Bob")
    cfg.setattr(config, "LIST_SEND_TO", "Signal +15550100")
    cfg.setattr(config, "LIST_SEND_LABEL", "Signal")
    d = ai.describe()
    assert d["chat"] == "Bob" and d["extract"] == "claude-opus-5-5" and d["vision"] and d["send"] == "Signal"


def test_nothing_configured_says_what_to_set(cfg):
    with pytest.raises(ai.AIUnavailable, match="GT_AGENT"):
        run(ai.text("hi", job="chat"))
    with pytest.raises(ai.AIUnavailable, match="vision model"):
        run(ai.read_image("x", b"img", "image/png"))
    with pytest.raises(ai.AIUnavailable, match="GT_LIST_SEND_TO"):
        run(ai.act("send"))


@pytest.mark.parametrize("raw,want", [
    ('{"a": 1}', {"a": 1}),
    ('Sure! ```json\n{"a": {"b": "}"}}\n``` hope that helps', {"a": {"b": "}"}}),
    ('<think>maybe {"no": 1}</think>{"yes": "say \\"hi\\" {"}', {"yes": 'say "hi" {'}),
])
def test_parse_json_tolerates_wrapping(raw, want):
    assert ai.parse_json(raw) == want


@pytest.mark.parametrize("raw", ["no json here", '{"a": 1', '{"a": tru}'])
def test_parse_json_errors_are_errors(raw):
    with pytest.raises(ai.AIError):
        ai.parse_json(raw)


# ------------------------------------------------------- OpenAI-compatible

def _openai(cfg, handler):
    seen = []

    def wrap(req):
        seen.append(req)
        return handler(req)
    cfg.setattr(config, "MODEL_PROVIDER", "openai")
    cfg.setattr(config, "MODEL", "glm-5.3-flash")
    cfg.setattr(config, "MODEL_URL", "https://llm.example/v1/")
    cfg.setattr(config, "MODEL_KEY", "k-123")
    cfg.setattr(openai_compat, "transport", httpx.MockTransport(wrap))
    return seen


def test_openai_text_with_image(cfg):
    seen = _openai(cfg, lambda r: httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}))
    cfg.setattr(config, "MODEL_EFFORT", "low")
    assert run(ai.read_image("read it", b"\x89PNG", "image/png")) == '{"ok": true}'
    req = seen[0]
    assert str(req.url) == "https://llm.example/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer k-123"
    body = json.loads(req.content)
    assert body["model"] == "glm-5.3-flash" and body["stream"] is False and body["reasoning_effort"] == "low"
    parts = body["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "read it"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,iVBORw")


def test_openai_stream_and_errors(cfg):
    sse = ('data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
           'data: {"choices":[{"delta":{"reasoning":"hmm"}}]}\n\n'
           'data: {"choices":[{"delta":{"content":"Tacos"}}]}\n\n'
           'data: {"choices":[{"delta":{"content":" tonight"}}]}\n\n'
           "data: [DONE]\n\n")
    seen = _openai(cfg, lambda r: httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"}))
    assert run(collect(ai.stream("dinner?", "chat"))) == "Tacos tonight"
    assert json.loads(seen[0].content)["stream"] is True
    _openai(cfg, lambda r: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(ai.AIError, match="401: bad key"):
        run(ai.text("x"))
    sse_out = run(collect(ai.sse(ai.stream("dinner?", "chat"))))
    assert '"error"' in sse_out and "bad key" in sse_out and sse_out.endswith("data: [DONE]\n\n")


# ------------------------------------------------------------------ Claude

def _anthropic_sse(text, stop="end_turn"):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 9, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        *[("content_block_delta", {"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "text_delta", "text": t}}) for t in text],
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                           "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


def _claude(cfg, model, sse):
    import httpx2
    seen = []

    def handler(req):
        seen.append(req)
        return httpx2.Response(200, text=sse, headers={"content-type": "text/event-stream"})
    cfg.setattr(config, "MODEL_PROVIDER", "anthropic")
    cfg.setattr(config, "MODEL", model)
    cfg.setattr(config, "MODEL_KEY", "sk-test")
    cfg.setattr(claude, "transport", httpx2.MockTransport(handler))
    return seen


def test_claude_request_shape_and_fallbacks(cfg):
    seen = _claude(cfg, "", _anthropic_sse(["Hel", "lo"]))
    cfg.setattr(config, "MODEL_EFFORT", "low")
    assert run(ai.read_image("read it", b"\xff\xd8\xff", "image/jpeg")) == "Hello"
    req = seen[0]
    body = json.loads(req.content)
    assert req.url.path == "/v1/messages" and req.headers["authorization"] == "Bearer sk-test"
    assert body["model"] == "claude-opus-5-5" and body["stream"] is True and body["max_tokens"] == 16000
    assert body["output_config"] == {"effort": "low"}
    assert body["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in req.headers["anthropic-beta"]
    img, txt = body["messages"][0]["content"]
    assert img["type"] == "image" and img["source"]["media_type"] == "image/jpeg" and txt == {"type": "text", "text": "read it"}

    seen = _claude(cfg, "claude-haiku-5-5", _anthropic_sse(["ok"]))
    assert run(ai.text("x")) == "ok"
    body = json.loads(seen[0].content)
    assert "fallbacks" not in body and "server-side-fallback" not in seen[0].headers.get("anthropic-beta", "")


def test_claude_refusal_is_an_error(cfg):
    _claude(cfg, "claude-sonnet-5-5", _anthropic_sse([], stop="refusal"))
    with pytest.raises(ai.AIError, match="declined"):
        run(ai.text("x"))


# ------------------------------------------------------------------ agents

def _fake_cli(tmp_path, reply="All set.", code=0, stderr=""):
    log = tmp_path / "argv.json"
    f = tmp_path / "fakeagent.py"
    f.write_text(textwrap.dedent(f"""\
        import json, os, sys
        json.dump({{"argv": sys.argv[1:], "path": os.environ.get("PATH")}}, open({str(log)!r}, "w"))
        sys.stdout.write({reply!r})
        sys.stderr.write({stderr!r})
        sys.exit({code})
        """))
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return [sys.executable, str(f)], log


def test_openclaw_and_hermes_argv(cfg, tmp_path):
    cmd, log = _fake_cli(tmp_path, reply="Sent ✓")
    cfg.setattr(config, "AGENT", "openclaw")
    cfg.setattr(config, "AGENT_NAME", "Bingo")
    cfg.setattr(config, "AGENT_CMD", cmd)
    cfg.setattr(config, "OPENCLAW_AGENT", "main")
    cfg.setattr(config, "LIST_SEND_TO", "Telegram chat 1")
    assert agents.found()
    assert run(ai.act("send the list")) == "Sent ✓"
    assert json.loads(log.read_text())["argv"] == ["agent", "--agent", "main", "-m", "send the list"]

    cfg.setattr(config, "AGENT", "hermes")
    cfg.setattr(config, "AGENT_PATH", "/opt/agent/bin:/usr/bin")
    assert run(collect(ai.stream("dinner?", "chat"))) == "Sent ✓"
    got = json.loads(log.read_text())
    assert got["argv"] == ["-z", "dinner?"] and got["path"] == "/opt/agent/bin:/usr/bin"


def test_agent_failures(cfg, tmp_path):
    cmd, _ = _fake_cli(tmp_path, reply="", code=2, stderr="boom\nprovider unreachable")
    cfg.setattr(config, "AGENT", "openclaw")
    cfg.setattr(config, "AGENT_NAME", "Bingo")
    cfg.setattr(config, "AGENT_CMD", cmd)
    with pytest.raises(ai.AIError, match="Bingo exited with code 2: provider unreachable"):
        run(ai.text("x", job="chat"))
    with pytest.raises(ai.AIError, match="code 2"):
        run(collect(ai.stream("x", "chat")))
    cmd, _ = _fake_cli(tmp_path, reply="", code=1)     # hermes: 1 means "finished, no text"
    cfg.setattr(config, "AGENT", "hermes")
    cfg.setattr(config, "AGENT_CMD", cmd)
    assert run(ai.text("x", job="chat")) == ""
    cfg.setattr(config, "AGENT_CMD", ["/nonexistent/hermes"])
    assert not agents.found()
    with pytest.raises(ai.AIError, match="isn't installed"):
        run(ai.text("x", job="chat"))


# ------------------------------------------------------------ receipt reader

def test_receipt_reader_cleans_and_reconciles(cfg):
    answer = {"store": "Corner Grocer, 12 Elm St", "date": "10/21/26", "tax": "0.50", "total": "$12.47",
              "lines": [{"raw": "OAT MILK", "name": "Oat milk", "price": "4.99", "regular": 5.49, "qty": 1, "taxed": False},
                        {"raw": "2 @ 3.49 SALSA", "name": "Salsa", "price": 6.98, "qty": 2, "brand": ""},
                        {"raw": "SUBTOTAL", "price": None}, "junk"]}
    _openai(cfg, lambda r: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]}))
    out = run(receipt_reader.read(b"\xff\xd8", "image/jpeg"))
    d = out["draft"]
    assert d["date"] == "2026-10-21" and d["total"] == 12.47 and d["tax"] == 0.5
    assert d["lines"] == [{"raw": "OAT MILK", "name": "Oat milk", "price": 4.99, "regular": 5.49},
                          {"raw": "2 @ 3.49 SALSA", "name": "Salsa", "price": 6.98, "qty": 2.0}]
    assert out["reconciliation"]["ok"] is True and out["reconciliation"]["diff"] == 0
    w = receipt_reader.warnings({**d, "store": None, "date": "2022-09-28", "partial": True}, today=dt.date(2026, 10, 24))
    assert [x.split(".")[0] for x in w] == ["No store name was read", "The date reads as 2022-09-28",
                                            "Part of the receipt couldn't be read"]
    assert receipt_reader.warnings(d, today=dt.date(2026, 10, 24)) == []


# ------------------------------------------------------------ recipe import

PAGE = """<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [{"@type": "WebPage", "name": "x"},
 {"@type": ["Recipe"], "name": "Chili &amp; Beans", "recipeYield": ["4", "4 bowls"], "prepTime": "PT15M",
  "cookTime": "PT1H30M", "recipeCategory": "Main course", "recipeCuisine": "American",
  "recipeIngredient": ["1 lb ground beef", "2 cans kidney beans", ""],
  "recipeInstructions": [{"@type": "HowToSection", "name": "Chili:", "itemListElement": [
     {"@type": "HowToStep", "text": "Brown the beef."}, {"@type": "HowToStep", "text": "Add <b>beans</b>."}]}]}]}
</script></head><body>ignored</body></html>"""


def test_recipe_from_page_data_needs_no_ai(cfg):
    cfg.setattr(recipe_import, "transport", httpx.MockTransport(lambda r: httpx.Response(200, text=PAGE)))
    out = run(recipe_import.parse(url="https://recipes.example/chili"))
    d = out["draft"]
    assert out["via"] == "page"
    assert (d["title"], d["servings"], d["prep_time"], d["cook_time"], d["category"], d["cuisine"]) == \
        ("Chili & Beans", "4", "15 min", "1h 30m", "dinner", "American")
    assert d["ingredients"] == ["1 lb ground beef", "2 cans kidney beans"]
    assert d["instructions"] == "Chili:\n1. Brown the beef.\n2. Add beans."
    assert d["source"] == "https://recipes.example/chili"


def test_recipe_page_without_data_goes_to_the_model(cfg):
    page = "<html><body><h1>Toast</h1><p>Bread. Toaster.</p><script>var x=1</script></body></html>"
    cfg.setattr(recipe_import, "transport", httpx.MockTransport(lambda r: httpx.Response(200, text=page)))
    with pytest.raises(ai.AIUnavailable):
        run(recipe_import.parse(url="https://recipes.example/toast"))
    seen = _openai(cfg, lambda r: httpx.Response(200, json={"choices": [{"message": {"content":
        '{"title": "Toast", "ingredients": "1 slice bread\\nbutter", "instructions": ["Toast it."], "category": "Snack"}'}}]}))
    out = run(recipe_import.parse(url="https://recipes.example/toast"))
    assert out["via"] == "ai" and out["draft"]["ingredients"] == ["1 slice bread", "butter"]
    assert out["draft"]["category"] == "dinner" and out["draft"]["instructions"] == "Toast it."
    prompt = json.loads(seen[0].content)["messages"][0]["content"]
    assert "Bread. Toaster." in prompt and "var x" not in prompt
    with pytest.raises(ValueError):
        run(recipe_import.fetch("file:///etc/passwd"))
    cfg.setattr(recipe_import, "transport", httpx.MockTransport(lambda r: httpx.Response(403, text="no bots")))
    with pytest.raises(ValueError, match="paste it here"):
        run(recipe_import.parse(url="https://recipes.example/locked"))


def test_no_em_dashes_in_drafts():
    assert recipe_import.plain("Toast \N{EM DASH} simple") == "Toast, simple"
    assert recipe_import.finish({"title": "Soup\N{EM DASH}fast", "ingredients": ["salt \N{EM DASH} to taste"]})["ingredients"] == ["salt, to taste"]
