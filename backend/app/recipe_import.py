"""A recipe from a link or pasted text, as a draft to check before saving.

Most recipe sites embed schema.org Recipe data (JSON-LD), which is read directly with no AI. Pages
without it, and pasted text, go to the configured model (or agent)."""

from __future__ import annotations

import html as htmllib
import json
import re

import httpx

from . import ai

CATEGORIES = ("dinner", "desserts", "breakfast", "lunch", "snacks", "drinks", "sides", "other")
MAX_PAGE = 3_000_000
transport: httpx.AsyncBaseTransport | None = None   # tests swap in an httpx.MockTransport

PROMPT = """Extract the recipe and answer with ONE JSON object and nothing else:
{"title": "...", "cuisine": "...", "servings": "...", "prep_time": "15 min", "cook_time": "30 min",
 "category": "dinner|desserts|breakfast|lunch|snacks|drinks|sides|other",
 "ingredients": ["2 cups flour", "..."], "instructions": "numbered steps, one per line"}
Leave a field "" if it isn't there. Copy ingredient amounts exactly. Never use em dashes.

"""


def _iso_duration(v) -> str:
    """PT1H30M -> '1h 30m', PT15M -> '15 min'; anything else passes through."""
    s = str(v or "").strip()
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?", s)
    if not m or not (m.group(1) or m.group(2)):
        return s
    h, mins = int(m.group(1) or 0), int(m.group(2) or 0)
    if h:
        return f"{h}h {mins}m" if mins else f"{h}h"
    return f"{mins} min"


def _text(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = v.get("name") or v.get("text") or ""
    return htmllib.unescape(re.sub(r"<[^>]+>", "", str(v or ""))).strip()


def _steps(v) -> str:
    out = []

    def walk(x):
        if isinstance(x, str):
            out.append(_text(x))
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            if x.get("itemListElement"):
                if x.get("name"):
                    out.append(f"{_text(x['name']).rstrip(':')}:")
                walk(x["itemListElement"])
            else:
                out.append(_text(x.get("text") or x.get("name")))
    walk(v)
    lines, n = [], 0
    for s in (s for s in out if s):
        if s.endswith(":"):
            lines.append(s)   # a section heading ("For the sauce:")
        else:
            n += 1
            lines.append(f"{n}. {s}")
    return "\n".join(lines)


def _category(v) -> str:
    t = " ".join(_text(x) for x in (v if isinstance(v, list) else [v])).lower()
    for key, words in (("desserts", ("dessert", "cake", "cookie", "pie", "sweet")), ("breakfast", ("breakfast", "brunch")),
                       ("drinks", ("drink", "cocktail", "beverage", "smoothie")), ("sides", ("side",)),
                       ("snacks", ("snack", "appetizer", "starter")), ("lunch", ("lunch", "sandwich", "salad")),
                       ("dinner", ("dinner", "main", "entree", "supper"))):
        if any(w in t for w in words):
            return key
    return "dinner"


def from_jsonld(page: str) -> dict | None:
    """The first schema.org Recipe in the page's JSON-LD, as a draft."""
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            x = stack.pop(0)
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                types = x.get("@type")
                types = types if isinstance(types, list) else [types]
                if "Recipe" in types:
                    ings = [_text(i) for i in x.get("recipeIngredient") or x.get("ingredients") or []]
                    return {
                        "title": _text(x.get("name")), "cuisine": _text(x.get("recipeCuisine")),
                        "servings": _text(x.get("recipeYield")), "prep_time": _iso_duration(x.get("prepTime")),
                        "cook_time": _iso_duration(x.get("cookTime")), "category": _category(x.get("recipeCategory")),
                        "ingredients": [i for i in ings if i], "instructions": _steps(x.get("recipeInstructions")),
                    }
                stack.extend(v for k, v in x.items() if k == "@graph" or isinstance(v, (list, dict)))
    return None


def page_text(page: str) -> str:
    s = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</(p|li|h\d|div)>", "\n", s, flags=re.I)
    s = htmllib.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()[:24000]


async def fetch(url: str) -> str:
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("only http and https links can be read")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, transport=transport,
                                 headers={"User-Agent": "Mozilla/5.0 (GrubTrack recipe import)"}) as c:
        r = await c.get(url)
        if r.status_code in (401, 403, 429):
            raise ValueError(f"that site doesn't let apps read its pages ({r.status_code}). Copy the recipe and paste it here instead")
        if r.status_code >= 400:
            raise ValueError(f"the page answered {r.status_code}")
        if len(r.content) > MAX_PAGE:
            raise ValueError("the page is too large to read")
        return r.text


def plain(s) -> str:
    """House style: no em dashes in anything GrubTrack shows, whoever wrote it."""
    return re.sub(r"\s*\u2014\s*", ", ", str(s or "")).strip()


def finish(d: dict, source: str = "") -> dict:
    cat = str(d.get("category") or "").lower()
    ings = d.get("ingredients") or []
    if isinstance(ings, str):
        ings = [x for x in ings.splitlines() if x.strip()]
    instr = d.get("instructions") or ""
    if isinstance(instr, list):
        instr = "\n".join(map(str, instr))
    return {"title": plain(d.get("title")), "cuisine": plain(d.get("cuisine")), "servings": plain(d.get("servings")),
            "prep_time": plain(d.get("prep_time")), "cook_time": plain(d.get("cook_time")),
            "category": cat if cat in CATEGORIES else "dinner", "status": "to-try", "source": source,
            "ingredients": [plain(i) for i in ings if plain(i)], "instructions": plain(instr)}


async def parse(url: str = "", text: str = "") -> dict:
    """{draft, via}: via is 'page' (read from the page's recipe data) or 'ai'."""
    if url:
        page = await fetch(url)
        d = from_jsonld(page)
        if d and d["ingredients"]:
            return {"draft": finish(d, url), "via": "page"}
        text = page_text(page)
    raw = await ai.text(PROMPT + (f"From this page ({url}):\n\n" if url else "Recipe text:\n\n") + text, job="extract")
    return {"draft": finish(ai.parse_json(raw), url), "via": "ai"}
