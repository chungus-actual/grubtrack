"""A recipe from a link or pasted text, as a draft to check before saving.

Most recipe sites embed schema.org Recipe data (JSON-LD), which is read directly with no AI. Pages
without it, and pasted text, go to the configured model (or agent)."""

from __future__ import annotations

import html as htmllib
import re

import httpx

from . import ai
from .db import recipes

CATEGORIES = ("dinner", "desserts", "breakfast", "lunch", "snacks", "drinks", "sides", "other")
MAX_PAGE = 3_000_000
transport: httpx.AsyncBaseTransport | None = None   # tests swap in an httpx.MockTransport

PROMPT = """Extract the recipe and answer with ONE JSON object and nothing else:
{"title": "...", "cuisine": "...", "servings": "...", "prep_time": "15 min", "cook_time": "30 min",
 "category": "dinner|desserts|breakfast|lunch|snacks|drinks|sides|other",
 "ingredients": ["2 cups flour", "..."], "instructions": "numbered steps, one per line"}
Leave a field "" if it isn't there. Copy ingredient amounts exactly. Never use em dashes.

"""


def _category(cats: list[str]) -> str:
    """schema.org recipeCategory words to the folders saved recipes go in."""
    t = " ".join(cats).lower()
    for key, words in (("desserts", ("dessert", "cake", "cookie", "pie", "sweet")), ("breakfast", ("breakfast", "brunch")),
                       ("drinks", ("drink", "cocktail", "beverage", "smoothie")), ("sides", ("side",)),
                       ("snacks", ("snack", "appetizer", "starter")), ("lunch", ("lunch", "sandwich", "salad")),
                       ("dinner", ("dinner", "main", "entree", "supper"))):
        if any(w in t for w in words):
            return key
    return "dinner"


def from_jsonld(page: str) -> dict | None:
    """The first schema.org Recipe in the page's JSON-LD (the library importer's reader), as a draft."""
    found = recipes.from_html(page)
    if not found:
        return None
    r = found[0]
    return {"title": r["title"], "cuisine": r["cuisine"], "servings": r["servings"], "prep_time": r["prep_time"],
            "cook_time": r["cook_time"], "category": _category(r["categories"]), "ingredients": r["ingredients"],
            "instructions": r["instructions"]}


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
