"""Cooking: meal ideas from what's on hand, your recipes (a folder of markdown files) and the
imported library, and whether you can make a recipe with what's in the house."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from .. import ai, config, recipe_import
from ..db import get_con
from foodcore import recipes
from foodcore.taxonomy import classify

router = APIRouter(prefix="/api")

# ------------------------------------------------------------------ recipe migrations

def migrate_recipes(con) -> None:
    """Once at startup: the library table (shared with the CLI's recipes-import), its derived columns,
    and the journal table."""
    recipes.ensure_table(con)
    con.execute(recipes.TIME_BUCKET_SQL)
    con.execute("""CREATE TABLE IF NOT EXISTS recipe_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recipe_slug TEXT NOT NULL, recipe_type TEXT NOT NULL DEFAULT 'personal',
        score INTEGER, tags TEXT, review TEXT, mods TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS rj_slug ON recipe_journal(recipe_slug)")
    con.commit()


# ------------------------------------------------------------------- can I make it?

_ALWAYS = {"water", "salt", "black pepper", "ice", "spice"}
ingredient_generic = recipes.ingredient_generic


def stock_generics(con) -> set[str]:
    out = set()
    for r in con.execute("SELECT i.name, p.generic FROM items i LEFT JOIN products p ON p.id = i.productId"
                         " WHERE i.deletedAt IS NULL").fetchall():
        if r["generic"]:
            out.add(r["generic"])
        g = classify(r["name"])["generic"]
        if g:
            out.add(g)
    return out


def line_status(ingredients: list[str], stock: set[str]) -> list[dict]:
    """Per ingredient line: what it is and whether the house has it (None = can't tell)."""
    out = []
    for line in ingredients or []:
        g = ingredient_generic(line)
        out.append({"text": line, "generic": g, "have": None if not g else (g in stock or g in _ALWAYS)})
    return out


def makeable(ingredients: list[str], stock: set[str]) -> dict:
    need, have, unknown = [], 0, 0
    for line in ingredients or []:
        g = ingredient_generic(line)
        if not g:
            unknown += 1
            continue
        if g in stock or g in _ALWAYS:
            have += 1
        else:
            need.append(g)
    known = have + len(need)
    return {"have": have, "known": known, "need": sorted(set(need)), "unknown": unknown,
            "pct": round(have / known * 100) if known else None}


# ----------------------------------------------------------- your recipes (markdown)

def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _frontmatter(text: str) -> tuple[dict, str]:
    fm, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            kv = re.match(r"^(\w[\w_]*):\s*(.+)$", line.strip())
            if kv:
                fm[kv.group(1)] = kv.group(2).strip().strip("\"'")
    return fm, body


def _md_ingredients(body: str) -> list[str]:
    block = re.search(r"##\s*Ingredients\s*\n(.*?)(?=\n##|\Z)", body, re.DOTALL | re.IGNORECASE)
    if not block:
        return []
    out = []
    for line in block.group(1).splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ")):
            ing = re.sub(r"^\[[ xX]\] ", "", line[2:]).strip()
            ing = re.sub(r"\*+", "", ing)
            ing = re.sub(r"!\[.*?\]\(.*?\)", "", ing).strip()
            if ing:
                out.append(ing)
    return out


def _md_recipe(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = _frontmatter(text)
    return {"slug": _slugify(path.stem), "title": path.stem, "cuisine": fm.get("cuisine", ""),
            "rating": fm.get("rating", ""), "prep_time": fm.get("prep_time", ""), "cook_time": fm.get("cook_time", ""),
            "servings": fm.get("servings", ""), "status": fm.get("status", ""), "source": fm.get("source", ""),
            "created": fm.get("created", ""), "category": path.parent.name.lower(),
            "ingredients": _md_ingredients(body), "has_image_ingredients": bool(re.search(r"!\[", body)),
            "type": "personal"}


def my_recipes() -> list[dict]:
    if not config.RECIPES_DIR.exists():
        return []
    out = []
    for md in sorted(config.RECIPES_DIR.rglob("*.md")):
        if not md.name.startswith("."):
            try:
                out.append(_md_recipe(md))
            except Exception:
                pass
    return out


def _bulk_out(row: dict) -> dict:
    return {"slug": row["slug"], "title": row["title"], "cuisine": row["cuisine"] or "", "servings": row["servings"] or "",
            "category": row["category"] or "", "time_bucket": row.get("time_bucket") or "",
            "prep_time": row.get("prep_time") or "", "cook_time": row.get("cook_time") or "",
            "total_time": row.get("total_time") or "", "protein_type": row.get("protein_type") or "",
            "cuisine_group": row.get("cuisine_group") or "", "dish_group": row.get("dish_group") or "",
            "ingredients": json.loads(row["ingredients"] or "[]"), "instructions": row["instructions"] or "",
            "source": row["source_url"] or "", "type": "imported"}


# ------------------------------------------------------------------------ routes

@router.get("/recipes")
def list_recipes(con=Depends(get_con)):
    stock = stock_generics(con)
    mine = my_recipes()
    for r in mine:
        r["make"] = makeable(r["ingredients"], stock)
    total = con.execute("SELECT COUNT(*) FROM bulk_recipes").fetchone()[0]
    journal = {r["recipe_slug"]: {"score": r["score"], "tags": json.loads(r["tags"] or "[]")}
               for r in con.execute("SELECT recipe_slug, score, tags FROM recipe_journal").fetchall()}
    for r in mine:
        r["journal"] = journal.get(r["slug"])
    return {"personal": mine, "libraryTotal": total, "journal": journal}


@router.get("/recipes/bulk/search")
def search_library(q: Optional[str] = None, complexity: Optional[str] = None, protein: Optional[str] = None,
                   cuisine: Optional[str] = None, dish: Optional[str] = None, category: Optional[str] = None,
                   cookable: bool = False, offset: int = Query(0, ge=0), con=Depends(get_con)):
    clauses, params = [], []
    if q and len(q.strip()) >= 2:
        for word in q.split()[:6]:
            clauses.append("(title LIKE ? OR ingredients LIKE ?)")
            params += [f"%{word}%", f"%{word}%"]
    for col, val in (("time_bucket", complexity), ("protein_type", protein), ("cuisine_group", cuisine),
                     ("dish_group", dish)):
        if val:
            clauses.append(f"{col} = ?")
            params.append(val)
    if category:
        clauses.append("lower(category) = ?")
        params.append(category.lower())
    where = " AND ".join(clauses) or "1"
    total = con.execute(f"SELECT COUNT(*) FROM bulk_recipes WHERE {where}", params).fetchone()[0]
    stock = stock_generics(con)
    limit = 50
    if cookable:
        # rank a wider pool by how much of it is in the house
        rows = con.execute(f"SELECT * FROM bulk_recipes WHERE {where} ORDER BY title LIMIT 1500", params).fetchall()
        scored = []
        for r in rows:
            b = _bulk_out(dict(r))
            b["make"] = makeable(b["ingredients"], stock)
            if b["make"]["known"] >= 3:
                scored.append(b)
        scored.sort(key=lambda b: (-(b["make"]["pct"] or 0), len(b["make"]["need"]), b["title"]))
        return {"recipes": scored[offset:offset + limit], "total": len(scored), "offset": offset, "limit": limit}
    rows = con.execute(f"SELECT * FROM bulk_recipes WHERE {where} ORDER BY title LIMIT ? OFFSET ?",
                       params + [limit, offset]).fetchall()
    out = []
    for r in rows:
        b = _bulk_out(dict(r))
        b["make"] = makeable(b["ingredients"], stock)
        out.append(b)
    return {"recipes": out, "total": total, "offset": offset, "limit": limit}


@router.get("/recipes/stats")
def recipe_stats(con=Depends(get_con)):
    personal = my_recipes()
    cats = {}
    for r in personal:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    bulk = con.execute("SELECT category, COUNT(*) n FROM bulk_recipes GROUP BY category ORDER BY n DESC LIMIT 24"
                       ).fetchall()
    return {"personal": {"count": len(personal), "categories": cats},
            "bulk": {"count": con.execute("SELECT COUNT(*) FROM bulk_recipes").fetchone()[0],
                     "categories": {r[0]: r[1] for r in bulk if r[0]}}}


@router.get("/recipes/{slug}/journal")
def get_journal(slug: str, con=Depends(get_con)):
    row = con.execute("SELECT * FROM recipe_journal WHERE recipe_slug = ?", (slug,)).fetchone()
    if not row:
        return {"entry": None}
    row = dict(row)
    return {"entry": {**row, "tags": json.loads(row["tags"] or "[]")}}


@router.put("/recipes/{slug}/journal")
def put_journal(slug: str, body: dict = Body(...), con=Depends(get_con)):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    score = body.get("score")
    score = max(1, min(10, int(score))) if score not in (None, "") else None
    tags = json.dumps(body.get("tags") or [])
    rtype = body.get("recipe_type", "personal")
    if con.execute("SELECT 1 FROM recipe_journal WHERE recipe_slug = ?", (slug,)).fetchone():
        con.execute("UPDATE recipe_journal SET score=?, tags=?, review=?, mods=?, updated_at=?, recipe_type=?"
                    " WHERE recipe_slug=?", (score, tags, body.get("review", ""), body.get("mods", ""), now, rtype, slug))
    else:
        con.execute("INSERT INTO recipe_journal (recipe_slug, recipe_type, score, tags, review, mods, created_at,"
                    " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (slug, rtype, score, tags, body.get("review", ""), body.get("mods", ""), now, now))
    con.commit()
    return {"ok": True}


@router.delete("/recipes/{slug}/journal")
def delete_journal(slug: str, con=Depends(get_con)):
    con.execute("DELETE FROM recipe_journal WHERE recipe_slug = ?", (slug,))
    con.commit()
    return {"ok": True}


@router.get("/recipes/{slug}")
def get_recipe(slug: str, con=Depends(get_con)):
    stock = stock_generics(con)
    if config.RECIPES_DIR.exists():
        for md in config.RECIPES_DIR.rglob("*.md"):
            if _slugify(md.stem) == slug:
                r = _md_recipe(md)
                r["markdown"] = md.read_text(encoding="utf-8", errors="replace")
                r["make"] = makeable(r["ingredients"], stock)
                r["lines"] = line_status(r["ingredients"], stock)
                return r
    row = con.execute("SELECT * FROM bulk_recipes WHERE slug = ?", (slug,)).fetchone()
    if row:
        b = _bulk_out(dict(row))
        b["make"] = makeable(b["ingredients"], stock)
        b["lines"] = line_status(b["ingredients"], stock)
        return b
    raise HTTPException(404, "Recipe not found")


@router.post("/recipes")
def create_recipe(body: dict = Body(...)):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "Title required")
    if not config.RECIPES_WRITE:
        raise HTTPException(403, "Saving recipes is off on this instance (GT_RECIPES_WRITE=0)")
    category = (body.get("category") or "dinner").strip().lower()
    cat_dir = config.RECIPES_DIR / category.capitalize()
    cat_dir.mkdir(parents=True, exist_ok=True)
    fm = ["---", "tags:", "  - recipe", f"  - {category}"]
    for k in ("cuisine", "prep_time", "cook_time", "servings", "status", "source"):
        v = (body.get(k) or ("to-try" if k == "status" else "")).strip()
        if v:
            fm.append(f"{k}: {v}")
    fm += [f"created: {dt.date.today().isoformat()}", "---", "", f"# {title}", ""]
    ings = body.get("ingredients") or []
    if ings:
        fm += ["## Ingredients", ""] + [f"- {i}" for i in ings] + [""]
    instr = (body.get("instructions") or "").strip()
    if instr:
        fm += ["## Instructions", "", instr, ""]
    safe = re.sub(r'[<>:"/\\|?*]', "-", title)
    path = cat_dir / f"{safe}.md"
    if path.exists():
        raise HTTPException(409, f"Recipe '{title}' already exists")
    path.write_text("\n".join(fm), encoding="utf-8")
    return {"slug": _slugify(title), "title": title, "category": category}


def _minutes(text: str) -> int | None:
    t = (text or "").lower()
    if "overnight" in t:
        return 480
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-\s]?h(?:ours?|rs?)?\b", t)
    mins = int(float(m.group(1)) * 60) if m else 0
    m2 = re.search(r"(\d+)\s*[-\s]?m(?:in(?:utes?)?)?\b", t)
    mins += int(m2.group(1)) if m2 else 0
    return mins or None


@router.post("/recipes/bulk/enrich")
async def enrich(body: dict = Body(...), con=Depends(get_con)):
    slug = (body.get("slug") or "").strip()
    row = con.execute("SELECT * FROM bulk_recipes WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise HTTPException(404, "Recipe not found")
    ings = json.loads(row["ingredients"] or "[]")
    prompt = f"""You are a culinary assistant. Return ONLY a JSON object, no markdown, no explanation:
{{"prep_time": "hands-on time e.g. '15 min'", "cook_time": "heat time e.g. '30 min'",
  "total_time": "TOTAL wall-clock time including rise, rest, marinate, bake e.g. '2h 30m'",
  "cuisine": "specific cuisine e.g. 'Italian'"}}
total_time must include all passive time. Leave a field "" only if unknowable.
Recipe: {row['title']}
Ingredients: {', '.join(ings[:15])}
Instructions (excerpt): {(row['instructions'] or '')[:700]}"""
    result = {"prep_time": "", "cook_time": "", "total_time": "", "cuisine": ""}
    try:
        got = ai.parse_json(await ai.text(prompt, job="extract"))
    except ai.AIUnavailable as e:
        raise HTTPException(503, str(e))
    except ai.AIError as e:
        raise HTTPException(502, str(e))
    result.update({k: str(v) for k, v in got.items() if k in result})
    bucket = None
    mins = _minutes(result["total_time"])
    if mins:
        bucket = "quick" if mins < 30 else "medium" if mins <= 75 else "long"
    con.execute("UPDATE bulk_recipes SET prep_time=?, cook_time=?, total_time=?, cuisine=?,"
                " time_bucket=COALESCE(?, time_bucket) WHERE slug=?",
                (result["prep_time"], result["cook_time"], result["total_time"], result["cuisine"] or row["cuisine"],
                 bucket, slug))
    con.commit()
    return {**result, "slug": slug}


MAX_COLLECTION = 200 * 1024 * 1024


@router.post("/recipes/import")
async def import_collection(file: UploadFile = File(...), con=Depends(get_con)):
    """A recipe collection into the library: the same reader as the CLI's recipes-import."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "The file is empty")
    if len(data) > MAX_COLLECTION:
        raise HTTPException(413, "That file is over 200 MB; import it with the CLI (recipes-import --file)")
    name = file.filename or "upload"
    rep = await asyncio.to_thread(lambda: recipes.import_recipes(con, recipes.from_bytes(name, data)))
    if not rep["found"]:
        raise HTTPException(400, "No recipes found in that file. GrubTrack reads Meal-Master, MasterCook, Paprika "
                                 "and Mealie exports, schema.org recipe JSON or web pages, and zips of them.")
    return rep


@router.post("/recipes/bulk/revert")
def revert(body: dict = Body(...), con=Depends(get_con)):
    slug = (body.get("slug") or "").strip()
    if not con.execute("SELECT 1 FROM bulk_recipes WHERE slug=?", (slug,)).fetchone():
        raise HTTPException(404, "Recipe not found")
    con.execute("UPDATE bulk_recipes SET prep_time='', cook_time='', total_time='' WHERE slug=?", (slug,))
    con.commit()
    return {"slug": slug, "reverted": True}


@router.patch("/recipes/bulk/{slug}")
def rename(slug: str, body: dict = Body(...), con=Depends(get_con)):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    if not con.execute("SELECT 1 FROM bulk_recipes WHERE slug=?", (slug,)).fetchone():
        raise HTTPException(404, "Recipe not found")
    con.execute("UPDATE bulk_recipes SET title=? WHERE slug=?", (title, slug))
    con.commit()
    return {"slug": slug, "title": title}


@router.post("/recipes/parse")
async def parse(body: dict = Body(...)):
    """A link or pasted text to a recipe draft. Links with recipe data on the page need no AI."""
    text, url = (body.get("text") or "").strip(), (body.get("url") or "").strip()
    if not text and not url:
        raise HTTPException(400, "Provide text or url")
    try:
        return await recipe_import.parse(url=url, text=text)
    except ai.AIUnavailable:
        raise HTTPException(503, "That page has no recipe data GrubTrack can read on its own, and no model is set up"
                                 " to read it (GT_MODEL_PROVIDER). Paste the recipe text instead." if url else
                                 "Reading pasted text needs a model: set GT_MODEL_PROVIDER.")
    except ai.AIError as e:
        raise HTTPException(502, str(e))
    except (ValueError, httpx.HTTPError) as e:
        raise HTTPException(400, f"Couldn't read that link: {e}")


# ------------------------------------------------------------------------- meals

def _meal_prompt(con, focus: str | None, extra: str) -> str:
    rows = con.execute(
        "SELECT name, brand, storage, cat, quantity, unit, expirationDate FROM items WHERE deletedAt IS NULL"
        " AND (expirationDate IS NULL OR expirationDate >= date('now','localtime'))"
        " AND storage IN ('fridge','pantry','cupboard','freezer','counter') AND coalesce(dept,'') NOT IN ('bar','household')"
        + (" AND storage = ?" if focus else "") + " ORDER BY expirationDate IS NULL, expirationDate, storage",
        ((focus,) if focus else ())).fetchall()
    soon, rest = [], {}
    horizon = (dt.date.today() + dt.timedelta(days=4)).isoformat()
    for r in rows:
        name = f"{r['brand']} {r['name']}" if r["brand"] and r["brand"].lower() not in (r["name"] or "").lower() else r["name"]
        qty = f" ({r['quantity']:g} {r['unit'] or ''}".rstrip() + ")" if r["quantity"] else ""
        line = f"  - {name}{qty}" + (f" [use by {r['expirationDate']}]" if r["expirationDate"] else "")
        if r["expirationDate"] and r["expirationDate"] <= horizon:
            soon.append(line)
        else:
            rest.setdefault((r["storage"] or "other").upper(), []).append(line)
    inv = "".join(f"\n{k}:\n" + "\n".join(v) for k, v in sorted(rest.items()))
    recipes = my_recipes()
    stock = stock_generics(con)
    rl = []
    for r in recipes:
        mk = makeable(r["ingredients"], stock)
        rl.append(f"  - {r['title']}" + (f" (have {mk['have']}/{mk['known']}; missing: {', '.join(mk['need'][:4])})"
                                         if mk["known"] else ""))
    focus_note = f" (focusing on the {focus})" if focus else ""
    return f"""I want meal ideas from what we have{focus_note}.

USE FIRST (expires within 4 days):
{chr(10).join(soon) or '  (nothing urgent)'}

ON HAND:{inv}

MY SAVED RECIPES (with what we have of each):
{chr(10).join(rl) or '  (none)'}
{('Extra context: ' + extra) if extra.strip() else ''}

Give me:
1. Which saved recipes I can make now (or nearly: name the one or two missing things)
2. 4 to 6 meal ideas built around what's on hand, especially the USE FIRST items
3. Anything expiring I should deal with even if it isn't dinner

{(config.MEAL_NOTES + chr(10)) if config.MEAL_NOTES else ''}{config.AI_STYLE}"""


@router.post("/meals/suggest")
async def meals(data: dict = Body(default={}), con=Depends(get_con)):
    focus = data.get("storage")
    if focus not in (None, "", "fridge", "freezer", "pantry", "cupboard", "counter"):
        raise HTTPException(400, "Unknown storage")
    if not ai.route("chat"):
        raise HTTPException(503, "No AI is set up: set GT_AGENT (OpenClaw or Hermes) or GT_MODEL_PROVIDER.")
    prompt = _meal_prompt(con, focus or None, data.get("context") or "")
    return StreamingResponse(ai.sse(ai.stream(prompt, "chat")), media_type="text/event-stream")
