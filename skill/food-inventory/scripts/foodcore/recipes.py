"""Recipe collections into the library (`bulk_recipes`): read the formats people already have, give each
recipe the library's filters (protein, cuisine, dish, time), and skip what's already there.

Formats, detected by content rather than file name where possible:

  Meal-Master       plain text, "MMMMM----- Recipe via Meal-Master" blocks (many per file, one or two
                    ingredient columns)
  MasterCook        plain text export (.mxp), "* Exported from MasterCook *" blocks
  schema.org        Recipe JSON or JSON-LD, or an HTML page that embeds it
  Mealie            recipe JSON (camelCase: recipeIngredient objects, recipeInstructions steps)
  Paprika           .paprikarecipes (a zip of gzipped JSON) or a single .paprikarecipe
  GrubTrack         JSON or JSON Lines: {"title", "ingredients": [...], "instructions", ...}
  zip               any mix of the above
"""

from __future__ import annotations

import datetime as dt
import gzip
import html as htmllib
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Iterable, Iterator

from .taxonomy import classify

TEXT_EXT = {".txt", ".mmf", ".mm", ".mxp", ".mx2", ".rtf"}
JSON_EXT = {".json", ".jsonld", ".jsonl", ".ndjson"}
HTML_EXT = {".html", ".htm"}
MAX_ZIP_ENTRIES = 200_000

# ------------------------------------------------------------------ the table

TIME_BUCKET_SQL = """
UPDATE bulk_recipes SET time_bucket = CASE
  WHEN (instructions LIKE '%hours%' OR instructions LIKE '%overnight%' OR instructions LIKE '%slow cook%'
        OR instructions LIKE '%all day%' OR instructions LIKE '%marinate%' OR instructions LIKE '%refrigerat%'
        OR instructions LIKE '% rise%' OR instructions LIKE '%let stand%' OR instructions LIKE '%let rest%'
        OR lower(title) LIKE '%roast%' OR lower(title) LIKE '%braised%' OR lower(title) LIKE '%slow%'
        OR lower(title) LIKE '%overnight%') THEN 'long'
  WHEN (length(instructions) < 400 AND instructions NOT LIKE '%minutes%' AND instructions NOT LIKE '%simmer%'
        AND instructions NOT LIKE '%bake%') THEN 'quick'
  ELSE 'medium' END
WHERE time_bucket IS NULL
"""


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS bulk_recipes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, slug TEXT UNIQUE NOT NULL, category TEXT,
        cuisine TEXT, servings TEXT, ingredients TEXT, instructions TEXT, source_url TEXT,
        source_site TEXT DEFAULT 'recipesource', imported_at TEXT NOT NULL)""")
    cols = {r[1] for r in con.execute("PRAGMA table_info(bulk_recipes)")}
    for col in ("time_bucket", "prep_time", "cook_time", "total_time", "protein_type", "cuisine_group", "dish_group"):
        if col not in cols:
            con.execute(f"ALTER TABLE bulk_recipes ADD COLUMN {col} TEXT")
    for idx, col in (("bulk_recipes_slug", "slug"), ("bulk_recipes_category", "category"),
                     ("bulk_recipes_time_bucket", "time_bucket"), ("bulk_recipes_protein", "protein_type"),
                     ("bulk_recipes_cuisine", "cuisine_group"), ("bulk_recipes_dish", "dish_group")):
        con.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON bulk_recipes({col})")


# ------------------------------------------------------- ingredient lines

_QTY = re.compile(r"^[\s\d/.,½¼¾⅓⅔-]*(?:\([^)]*\))?\s*(?:c|cups?|tbsp?|tablespoons?|tsp|teaspoons?|oz|ounces?|lbs?|"
                  r"pounds?|g|grams?|kg|ml|l|liters?|pinch|dash|cloves?|cans?|jars?|pkgs?|packages?|large|medium|"
                  r"small|whole|slices?|sticks?|heads?|bunch(?:es)?|sprigs?|quarts?|pints?)?\.?\s+", re.I)


def ingredient_generic(line: str) -> str | None:
    """'2 cups jasmine rice, rinsed' -> 'jasmine rice' (the taxonomy's generic name), or None."""
    return ingredient_class(line)["generic"]


def ingredient_class(line: str) -> dict:
    text = _QTY.sub("", line or "").split(",")[0]
    text = re.sub(r"\b(chopped|minced|diced|sliced|fresh|large|small|ground|to taste|optional|divided|softened|melted)\b",
                  " ", text, flags=re.I)
    return classify(text)


# --------------------------------------------------------------- filters

_OTHER_MEAT = ("turkey", "duck", "goose", "quail", "lamb", "goat", "venison", "bison", "rabbit", "veal")
_PORK = ("ham", "prosciutto", "pancetta", "salami", "pepperoni", "chorizo", "bacon", "sausage", "pork")
_SEAFOOD = ("tuna", "salmon", "shrimp", "prawn", "crab", "lobster", "scallop", "clam", "mussel", "oyster", "cod",
            "tilapia", "halibut", "anchov", "sardine", "squid", "calamari", "fish", "trout", "catfish", "mahi")
_PROTEIN_ORDER = ("chicken", "beef", "pork", "seafood", "other_meat")


_DERIVED = re.compile(r"\b(broth|stock|bouillon|base|consomm[eé]|soup|fish sauce|oyster sauce|anchovy paste)\b")
# When the classifier can't place a line (two ingredients run together, or a word it doesn't know),
# the words themselves still say which animal it is.
_MEAT_WORDS = (
    ("chicken", re.compile(r"\b(chicken|hens?|poultry|fryers?|broilers?|capons?|cornish)\b")),
    ("other_meat", re.compile(r"\b(turkey|duck|goose|lamb|mutton|goat|venison|bison|buffalo|rabbit|veal|quail|pheasant)\b")),
    ("beef", re.compile(r"\b(beef|steaks?|hamburgers?|ground round|ground chuck|chuck roast|sirloin|brisket|meatballs?|"
                        r"ground meat|stew meat|minced meat|ox ?tails?)\b")),
    ("pork", re.compile(r"\b(pork|ham|bacon|sausages?|franks?|hot dogs?|prosciutto|pancetta|salami|pepperoni|chorizo|"
                        r"kielbasa)\b")),
    ("seafood", re.compile(r"\b(fish|shrimp|prawns?|crabs?|crab ?meat|crustaceans?|shellfish|seafood|lobsters?|scallops?|"
                           r"clams?|mussels?|oysters?|squid|calamari|octopus|"
                           r"cray ?fish|craw ?fish|anchov\w*|sardines?|tuna|salmon|cod|trout|catfish|tilapia|halibut|"
                           r"haddock|mackerel|herring|snapper|swordfish|eel|roe|caviar)\b")),
)
_NOT_MEAT = re.compile(r"\b(sauce|seasoning|flavou?r(?:ed|ing)?|spice|rub|marinade|substitute|imitation|vegetarian|vegan)\b")
# a title names the dish's meat unless it's about something else: "Dip for Chicken", "Chicken-Style
# Noodle Soup", "Mock Turtle", "Orange "Beef""
_TITLE_NOT_MEAT = re.compile(r'\b(for|style|mock|meatless|vegetarian|vegan|seasoning|marinade|rub|sauce|spice)\b|"')
_DERIVED_ANIMAL = (("chicken", ("chicken",)), ("other_meat", ("turkey", "duck", "veal", "lamb")), ("beef", ("beef",)),
                   ("pork", ("pork", "ham", "bacon", "lard")), ("seafood", ("fish", "clam", "shrimp", "anchov", "oyster",
                                                                           "lobster", "crab", "dashi", "bonito")))


def protein_of(title: str, ingredients: list[str], categories: Iterable[str] = ()) -> str:
    """The main protein: what the ingredient lines are, weighted toward the title. Broth, stock and
    fish sauce don't make a dish a chicken or fish dish, but they do keep it out of vegetarian: with no
    other meat, a dish made with chicken stock is filed under chicken. A recipe filed as vegetarian is."""
    if re.search(r"\b(vegetarian|vegan|meatless)\b", " ".join(categories).lower()):
        return "vegetarian"
    score: dict[str, float] = {}
    derived: dict[str, float] = {}
    for line in ingredients:
        low = line.lower()
        if _DERIVED.search(low):
            for kind, words in _DERIVED_ANIMAL:
                if any(w in low for w in words):
                    derived[kind] = derived.get(kind, 0) + 1
                    break
            continue
        c = ingredient_class(line)
        cat, g = c["cat"], (c["generic"] or "")
        kind = None
        if any(w in g for w in _OTHER_MEAT) and cat.startswith(("meat.", "deli.")):
            kind = "other_meat"
        elif cat == "meat.poultry":
            kind = "chicken"
        elif cat == "meat.beef":
            kind = "beef"
        elif cat in ("meat.pork", "meat.sausage") or (cat == "deli.meats" and any(w in g for w in _PORK)):
            kind = "pork"
        elif cat == "meat.seafood" or (any(w in g for w in _SEAFOOD) and "sauce" not in g):
            kind = "seafood"
        elif cat == "meat.other":
            kind = "other_meat"
        if not kind and not _NOT_MEAT.search(low):
            kind = next((k for k, rx in _MEAT_WORDS if rx.search(low)), None)
        if kind:
            score[kind] = score.get(kind, 0) + 1
    t = (title or "").lower()
    for kind, words in (("chicken", ("chicken",)), ("beef", ("beef", "steak", "brisket", "burger")),
                        ("pork", ("pork", "bacon", "ribs")), ("seafood", _SEAFOOD), ("other_meat", _OTHER_MEAT)):
        if any(re.search(rf"\b{w}", t) for w in words) and kind in score:
            score[kind] += 2
    score = score or derived
    if not score and not _TITLE_NOT_MEAT.search(t):
        hit = next((k for k, rx in _MEAT_WORDS if rx.search(t)), None)   # "Chicken Salad" with "chcken breasts"
        if hit:
            score = {hit: 1}
    if not score:
        return "vegetarian"
    return max(_PROTEIN_ORDER, key=lambda k: (score.get(k, 0), -_PROTEIN_ORDER.index(k)))


_CUISINES = {
    "asian": ("chinese", "japanese", "thai", "korean", "vietnamese", "indian", "indonesian", "filipino", "malaysian",
              "singapor", "asian", "sichuan", "szechuan", "cantonese", "hunan", "tibetan", "burmese", "nepal",
              "pakistan", "sri lank", "hawaiian"),
    "european": ("italian", "french", "spanish", "german", "greek", "british", "english", "irish", "scottish",
                 "swedish", "danish", "norwegian", "dutch", "belgian", "swiss", "austrian", "portuguese", "russian",
                 "polish", "hungarian", "croatian", "czech", "romanian", "rumanian", "serbian", "ukrainian", "welsh",
                 "basque", "icelandic", "finnish", "scandinavian", "european", "mediterranean"),
    "latin": ("mexican", "tex-mex", "caribbean", "cuban", "puerto ric", "jamaican", "colombian", "peruvian",
              "venezuelan", "argentin", "brazilian", "latin", "salvador", "chilean"),
    "african_me": ("africa", "egyptian", "ethiopian", "lebanese", "moroccan", "persian", "iranian", "turkish",
                   "armenian", "israeli", "middle east", "middle-east", "syrian", "tunisian", "nigerian"),
    "american": ("american", "southern", "cajun", "creole", "southwestern", "bbq", "barbecue", "new england",
                 "soul food", "californian", "midwest"),
    "other": ("jewish", "kosher"),
}


def cuisine_of(cuisine: str, categories: list[str]) -> str | None:
    text = " ".join([cuisine or ""] + list(categories)).lower()
    for group, words in _CUISINES.items():
        if any(w in text for w in words):
            return group
    return None


# whole words, plurals allowed: "pie" must not catch "pieces"
_DISHES = (
    ("soup", ("soup", "stew", "chili", "chowder", "bisque", "stock", "gumbo", "broth", "pho", "ramen")),
    ("breakfast", ("breakfast", "brunch", "pancake", "waffle", "omelet", "omelette", "granola", "french toast", "frittata")),
    ("baked", ("bread", "cookie", "cake", "pastry", "pastries", "pie", "brownie", "muffin", "roll", "biscuit", "bun",
               "scone", "bagel", "dessert", "tart", "cobbler", "cheesecake", "candy", "candies", "pudding", "frosting",
               "sorbet", "sherbet", "ice cream", "fudge", "crisp", "baking", "baked goods", "chocolate", "mousse",
               "trifle", "custard", "frozen-dessert", "frozen-yogurt")),
    ("sauce", ("sauce", "dressing", "condiment", "marinade", "rub", "salsa", "pesto", "jam", "jelly", "relish",
               "chutney", "preserve", "beverage", "drink", "cocktail", "smoothie", "dip", "gravy", "spice blend",
               "seasoning", "spice", "oil", "vinegar", "butter", "cheese", "canning", "pickle")),
    ("salad", ("salad", "side", "vegetable", "veg", "fgv", "fruit", "pilaf", "rice", "beans", "grain", "polenta",
               "stuffing", "slaw", "potato")),
)
_MAIN_FIRST = ("main", "entree", "dinner", "supper")   # "dinner-pies" is dinner before it's pie


_MAIN_CATS = ("main", "entree", "dinner", "supper", "poultry", "chicken", "beef", "pork", "lamb", "meat", "seafood",
              "fish", "shellfish", "casserole", "pasta", "pizza", "sandwich", "burger", "game", "stir-fry", "crockpot")

# A title only decides when the category says nothing, and only through words that can't mean a main:
# "Chicken in Mustard Sauce", "Chicken Pot Pie" and "Crab Cakes" are dinners.
_TITLE_DISHES = (
    ("soup", re.compile(r"\b(soups?|stews?|chowders?|bisques?|gumbos?|chil[il]i|pho|ramen|borscht|gazpacho)\b")),
    ("breakfast", re.compile(r"\b(pancakes?|waffles?|granola|omelett?e?s?|frittatas?|french toast|breakfast|brunch)\b")),
    ("salad", re.compile(r"\b(salads?|slaws?|coleslaw|pilafs?)\b")),
    ("sauce", re.compile(r"^(?!.*\b(?:with|in|on)\b)(?:.*\b(dressings?|vinaigrettes?|marinades?|salsas?|pestos?|chutneys?|"
                         r"relish(?:es)?|jams?|jell(?:y|ies)|gravy|gravies|rubs?|dips?|spreads?|syrups?|seasonings?|"
                         r"smoothies?|cocktails?|punch|lemonade|liqueurs?)\b|(?!.*\band\b).*\bsauces?$)")),
    ("baked", re.compile(r"\b(cookies?|brownies?|muffins?|cupcakes?|cheesecakes?|scones?|biscotti|fudge|cakes?|pies?|"
                         r"tarts?|cobblers?|crisps?|breads?|rolls?|buns?|biscuits?|bars|pastr(?:y|ies)|puddings?|"
                         r"strudels?|torte|candy|candies|truffles?)\b")),
)


def dish_of(title: str, categories: list[str], protein: str | None = None) -> str:
    """Soup, breakfast, salad (and sides), sauce (and drinks), baked (and desserts), or main."""
    cats = " ".join(categories).lower()
    if any(re.search(rf"\b{w}(?:e?s)?\b", cats) for w in _MAIN_FIRST):
        return "main"
    for group, words in _DISHES:
        if any(re.search(rf"\b{w}(?:e?s)?\b", cats) for w in words):
            return group
    if any(re.search(rf"\b{w}(?:e?s)?\b", cats) for w in _MAIN_CATS):
        return "main"
    t = (title or "").lower().strip()
    for group, pattern in _TITLE_DISHES:
        if pattern.search(t):
            if group == "baked" and protein not in (None, "vegetarian"):
                continue   # a pie, roll or cake with meat or fish in it is dinner
            return group
    return "main"


def minutes(text: str) -> int | None:
    t = (text or "").lower().strip()
    if not t:
        return None
    m = re.fullmatch(r"p(?:\d+d)?t?(?:(\d+)h)?(?:(\d+)m)?(?:\d+s)?", t)   # ISO 8601: PT1H30M
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    if "overnight" in t:
        return 480
    h = re.search(r"(\d+(?:\.\d+)?)\s*[-\s]?h(?:ours?|rs?)?\b", t)
    mins = int(float(h.group(1)) * 60) if h else 0
    mm = re.search(r"(\d+)\s*[-\s]?m(?:in(?:ute)?s?)?\b", t)
    mins += int(mm.group(1)) if mm else 0
    if not mins and re.fullmatch(r"\d+", t):
        mins = int(t)
    return mins or None


def duration_text(v) -> str:
    """'PT1H30M' -> '1h 30m', 'PT15M' -> '15 min'; anything else passes through."""
    s = str(v or "").strip()
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?", s, re.I)
    if not m or not (m.group(1) or m.group(2)):
        return s
    h, mins = int(m.group(1) or 0), int(m.group(2) or 0)
    if h:
        return f"{h}h {mins}m" if mins else f"{h}h"
    return f"{mins} min"


def time_bucket(r: dict) -> str | None:
    total = minutes(r.get("total_time", ""))
    if total is None:
        parts = [minutes(r.get(k, "")) for k in ("prep_time", "cook_time")]
        total = sum(p for p in parts if p) or None
    if total is None:
        return None   # left for the instruction-text heuristic (TIME_BUCKET_SQL)
    return "quick" if total < 30 else "medium" if total <= 75 else "long"


# ---------------------------------------------------- Meal-Master / MasterCook

_MM_START = re.compile(r"^(?:MMMMM|-----)-*\s*Recipe via Meal-Master", re.I | re.M)
_MM_END = re.compile(r"^(?:MMMMM|-----)\s*$", re.M)
_MM_UNITS = {"c": "cup", "ts": "tsp", "t": "tsp", "tb": "tbsp", "T": "tbsp", "pn": "pinch", "ds": "dash", "sm": "small",
             "md": "medium", "lg": "large", "cn": "can", "pk": "package", "pt": "pint", "qt": "quart", "ga": "gallon",
             "sl": "slice", "cl": "clove", "bn": "bunch", "dr": "drop", "ea": "", "x": "", "fl": "fl oz", "oz": "oz",
             "lb": "lb", "g": "g", "kg": "kg", "ml": "ml", "l": "l", "cb": "cubic cm", "dl": "dl", "cg": "cg",
             "mg": "mg", "sp": "sprig", "st": "stick", "pc": "piece", "bx": "box", "pg": "package"}


def _title(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s.title() if s.isupper() else s


def _paragraphs(lines: list[str]) -> str:
    out, cur = [], []
    for ln in lines:
        if ln.strip():
            cur.append(ln.strip())
        elif cur:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return "\n".join(re.sub(r"\s+", " ", p) for p in out)


def _mm_ingredient(col: str) -> str:
    """'  1 1/2 c  Flour, sifted' -> '1 1/2 cup Flour, sifted'"""
    m = re.match(r"^\s*([\d/ .-]*\d[\d/ .-]*|)\s{1,3}([A-Za-z]{1,2})?\s+(\S.*)$", col)
    if not m:
        return re.sub(r"\s+", " ", col).strip()
    qty, unit, rest = m.group(1).strip(), (m.group(2) or ""), m.group(3).strip()
    if unit and unit not in _MM_UNITS and unit.lower() not in _MM_UNITS:
        rest, unit = f"{unit} {rest}", ""     # a two-letter word, not a unit
    unit = _MM_UNITS.get(unit, _MM_UNITS.get(unit.lower(), unit))
    return " ".join(x for x in (qty, unit, rest) if x)


def _looks_like_step(s: str) -> bool:
    return not re.match(r"^[\d/]", s) and len(s.split()) >= 7


def parse_meal_master(block: str) -> dict | None:
    title = re.search(r"^\s*Title:\s*(.+)$", block, re.M)
    if not title:
        return None
    cats = re.search(r"^\s*Categories:\s*(.+)$", block, re.M)
    yld = re.search(r"^\s*(?:Yield|Servings):\s*(.+)$", block, re.M | re.I)
    body = re.split(r"^\s*(?:Yield|Servings):.*$", block, maxsplit=1, flags=re.M | re.I)[-1]
    ingredients, steps, in_steps = [], [], False
    for raw in body.splitlines():
        s = raw.strip()
        if in_steps:
            steps.append(raw)
            continue
        if not s or re.match(r"^(MMMMM|-----)", s):
            continue          # blank, or a section rule like MMMMM-----SAUCE-----
        if re.match(r"^-{1,2}(?!-)", s) and ingredients:
            ingredients[-1] += " " + s.lstrip("-").strip()   # wrapped ingredient
            continue
        if _looks_like_step(s):
            in_steps = True
            steps.append(raw)
            continue
        if len(raw) > 41 and not raw[39:41].strip() and raw[41:].strip():
            ingredients += [_mm_ingredient(raw[:41]), _mm_ingredient(raw[41:])]   # two columns
        else:
            ingredients.append(_mm_ingredient(raw))
    return {"title": _title(title.group(1)), "categories": _split_cats(cats.group(1) if cats else ""),
            "servings": (yld.group(1).strip() if yld else ""), "ingredients": [i for i in ingredients if i],
            "instructions": _paragraphs(steps)}


def parse_mastercook(block: str) -> dict | None:
    head = re.search(r"\*\s*Exported from\s+MasterCook.*?\*\s*\n+\s*(.+?)\s*\n", block, re.I)
    if not head:
        return None
    serves = re.search(r"Serving Size\s*:\s*(\S+)", block, re.I)
    cats = re.search(r"^Categories\s*:\s*(.+(?:\n {10,}\S.*)*)", block, re.I | re.M)
    prep = ""
    pt = re.search(r"Preparation Time\s*:\s*(\d+):(\d+)", block, re.I)
    if pt and (int(pt.group(1)) or int(pt.group(2))):
        h, m = int(pt.group(1)), int(pt.group(2))
        prep = f"{h}h {m}m" if h and m else f"{h}h" if h else f"{m} min"
    sep = re.search(r"^-{4,}[ \t]+-{4,}.*$", block, re.M)
    ingredients, steps, in_steps = [], [], False
    if sep:
        rest = block[sep.end():]
        foot = re.search(r"\n[ \t]*- - -", rest)
        for raw in (rest[:foot.start()] if foot else rest).splitlines():
            s = raw.strip()
            if in_steps:
                steps.append(raw)
                continue
            if not s:
                if ingredients:
                    in_steps = True     # the blank line after the ingredient table
                continue
            if re.match(r"^-{1,2}(?!-)", s) and ingredients:
                ingredients[-1] += " " + s.lstrip("-").strip()
                continue
            parts = re.split(r"\s{2,}", s, maxsplit=2)
            ingredients.append(re.sub(r"\s*--\s*", ", ", " ".join(parts)).strip())
    return {"title": _title(head.group(1)), "categories": _split_cats(re.sub(r"\s{2,}", ",", cats.group(1)) if cats else ""),
            "servings": serves.group(1) if serves else "", "prep_time": prep,
            "ingredients": [i for i in ingredients if i], "instructions": _paragraphs(steps)}


def from_text(text: str) -> list[dict]:
    """Every Meal-Master and MasterCook recipe in a text file."""
    out = []
    starts = [m.start() for m in _MM_START.finditer(text)]
    for i, s in enumerate(starts):
        chunk = text[s:starts[i + 1] if i + 1 < len(starts) else len(text)]
        end = _MM_END.search(chunk, 10)
        r = parse_meal_master(chunk[:end.start()] if end else chunk)
        if r:
            out.append({**r, "_format": "meal-master"})
    mc = [m.start() for m in re.finditer(r"\*\s*Exported from\s+MasterCook", text, re.I)]
    for i, s in enumerate(mc):
        r = parse_mastercook(text[s:mc[i + 1] if i + 1 < len(mc) else len(text)])
        if r:
            out.append({**r, "_format": "mastercook"})
    if not out and re.search(r"^\s*Title:", text, re.M):
        r = parse_meal_master(text)   # a bare Meal-Master recipe without the banner
        if r:
            out.append({**r, "_format": "meal-master"})
    return out


# ----------------------------------------------------------- JSON shapes

def _plain(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = v.get("name") or v.get("text") or ""
    return htmllib.unescape(re.sub(r"<[^>]+>", "", str(v or ""))).strip()


def _split_cats(v) -> list[str]:
    if isinstance(v, str):
        items = re.split(r"[,;]", v)
    elif isinstance(v, list):
        items = [x.get("name", "") if isinstance(x, dict) else str(x) for x in v]
    else:
        items = []
    return [x.strip() for x in items if x and x.strip()]


def steps_text(v) -> str:
    """schema.org HowToStep / HowToSection, Mealie steps, or plain text, as numbered lines."""
    if isinstance(v, str):
        text = htmllib.unescape(re.sub(r"<[^>]+>", "", v)).strip()
        return re.sub(r"\n{3,}", "\n\n", text)
    out = []

    def walk(x):
        if isinstance(x, str):
            out.append(_plain(x))
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            if x.get("itemListElement"):
                if x.get("name"):
                    out.append(f"{_plain(x['name']).rstrip(':')}:")
                walk(x["itemListElement"])
            else:
                if x.get("title") and x.get("text"):   # Mealie: a step that opens a section
                    out.append(f"{_plain(x['title']).rstrip(':')}:")
                out.append(_plain(x.get("text") or x.get("name")))
    walk(v)
    lines, n = [], 0
    for s in (s for s in out if s):
        if s.endswith(":") and len(s) < 60:
            lines.append(s)
        else:
            n += 1
            lines.append(f"{n}. {s}")
    return "\n".join(lines)


def _ingredient_list(v) -> list[str]:
    if isinstance(v, str):
        return [x.strip() for x in v.splitlines() if x.strip()]
    out = []
    for x in v or []:
        if isinstance(x, dict):   # Mealie: display / originalText / note, else rebuilt from its parts
            t = x.get("display") or x.get("originalText") or x.get("original_text")
            if not t:
                unit = x.get("unit") or {}
                food = x.get("food") or {}
                t = " ".join(str(p) for p in (x.get("quantity") or "", unit.get("name") if isinstance(unit, dict) else unit,
                                               food.get("name") if isinstance(food, dict) else food, x.get("note") or "") if p)
            if x.get("title"):
                out.append(f"{_plain(x['title']).rstrip(':')}:")
            if t:
                out.append(_plain(t))
        else:
            out.append(_plain(x))
    return [x for x in out if x]


def _get(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, "", []):
            return d[k]
    return None


def from_obj(d: dict) -> dict | None:
    """One recipe object in any of the JSON shapes, or None if it isn't one."""
    name = _get(d, "name", "title")
    ings = _get(d, "recipeIngredient", "recipe_ingredient", "ingredients")
    if not name or ings is None:
        return None
    fmt = ("paprika" if "directions" in d else "mealie" if any(k in d for k in ("recipeServings", "performTime", "orgURL", "recipe_ingredient"))
           else "schema.org" if "@type" in d or "recipeIngredient" in d else "grubtrack")
    cats = _split_cats(_get(d, "recipeCategory", "recipe_category", "categories", "category") or [])
    cats += [c for c in _split_cats(_get(d, "keywords", "tags") or []) if c not in cats][:6]
    yld = _get(d, "recipeYield", "recipe_yield", "servings", "yield")
    return {
        "title": _plain(name), "categories": cats, "cuisine": _plain(_get(d, "recipeCuisine", "cuisine") or ""),
        "servings": _plain(yld) if not isinstance(yld, (int, float)) else f"{yld:g}",
        "prep_time": duration_text(_get(d, "prepTime", "prep_time")),
        "cook_time": duration_text(_get(d, "cookTime", "performTime", "cook_time", "perform_time")),
        "total_time": duration_text(_get(d, "totalTime", "total_time")),
        "ingredients": _ingredient_list(ings),
        "instructions": steps_text(_get(d, "recipeInstructions", "recipe_instructions", "directions", "instructions") or ""),
        "source": str(_get(d, "url", "orgURL", "org_url", "source_url", "source") or ""),
        "_format": fmt,
    }


def from_json(data) -> list[dict]:
    """Every recipe in a parsed JSON document: a recipe, a list, an @graph, or nested."""
    out, stack = [], [data]
    while stack:
        x = stack.pop(0)
        if isinstance(x, list):
            stack.extend(x)
        elif isinstance(x, dict):
            types = x.get("@type")
            types = types if isinstance(types, list) else [types]
            r = from_obj(x) if ("Recipe" in types or "@type" not in x) else None
            if r:
                out.append(r)
            else:
                stack.extend(v for v in x.values() if isinstance(v, (list, dict)))
    return out


def from_html(page: str) -> list[dict]:
    out = []
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            out += from_json(json.loads(block.strip()))
        except ValueError:
            continue
    return out


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def from_bytes(name: str, raw: bytes, depth: int = 0) -> Iterator[dict]:
    """Recipes in one file's contents. `name` only hints at the format; the content decides."""
    low = name.lower()
    if low.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".pdf", ".mp4", ".db", ".sqlite")):
        return
    if raw[:2] == b"\x1f\x8b":                          # gzip: a single .paprikarecipe
        yield from from_bytes(low.removesuffix(".gz") + ".json", gzip.decompress(raw), depth)
        return
    if raw[:4] == b"PK\x03\x04" and depth < 3:          # zip: .paprikarecipes, a Mealie export, a folder
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for i, info in enumerate(z.infolist()):
                if i >= MAX_ZIP_ENTRIES:
                    break
                if info.is_dir() or "__MACOSX" in info.filename or Path(info.filename).name.startswith("."):
                    continue
                yield from from_bytes(info.filename, z.read(info), depth + 1)
        return
    text = _decode(raw)
    head = text.lstrip()[:1]
    if low.endswith((".jsonl", ".ndjson")):
        for line in text.splitlines():
            if line.strip():
                try:
                    yield from from_json(json.loads(line))
                except ValueError:
                    continue
        return
    if head in "[{":
        try:
            yield from from_json(json.loads(text))
            return
        except ValueError:
            pass
    if "<script" in text and "ld+json" in text:
        yield from from_html(text)
        return
    yield from from_text(text)


def from_path(path: Path) -> Iterator[tuple[str, dict]]:
    """(file, recipe) for a file or every file under a folder."""
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file() and not p.name.startswith("."))
    for f in files:
        for r in from_bytes(f.name, f.read_bytes()):
            yield str(f), r


# ---------------------------------------------------------------- import

def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:90]


def _dedupe_key(ings: list[str]) -> str:
    return "|".join(sorted(re.sub(r"[^a-z]", "", i.lower()) for i in ings))


def import_recipes(con: sqlite3.Connection, recipes: Iterable[dict], source: str | None = None,
                   dry_run: bool = False) -> dict:
    """Add recipes to the library. A recipe with the same title and the same ingredients as one already
    there is a duplicate and skipped; a different recipe with a taken title gets a numbered slug."""
    ensure_table(con)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    taken: dict[str, set[str]] = {}
    for slug, ings in con.execute("SELECT slug, ingredients FROM bulk_recipes"):
        base = re.sub(r"-\d+$", "", slug)
        try:
            taken.setdefault(base, set()).add(_dedupe_key(json.loads(ings or "[]")))
        except ValueError:
            pass
    used = {r[0] for r in con.execute("SELECT slug FROM bulk_recipes")}
    report = {"found": 0, "added": 0, "duplicates": 0, "empty": 0, "formats": {}, "groups": {}}
    rows = []
    for r in recipes:
        report["found"] += 1
        fmt = r.get("_format") or "unknown"
        report["formats"][fmt] = report["formats"].get(fmt, 0) + 1
        ings = [i for i in (r.get("ingredients") or []) if i.strip()]
        if not r.get("title") or not ings:
            report["empty"] += 1
            continue
        base = slugify(r["title"]) or "recipe"
        key = _dedupe_key(ings)
        if key in taken.get(base, set()):
            report["duplicates"] += 1
            continue
        taken.setdefault(base, set()).add(key)
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        cats = r.get("categories") or []
        protein = protein_of(r["title"], ings, cats)
        cuisine, dish = cuisine_of(r.get("cuisine", ""), cats), dish_of(r["title"], cats, protein)
        report["groups"][dish] = report["groups"].get(dish, 0) + 1
        rows.append((r["title"][:200], slug, (cats[0].lower() if cats else dish), r.get("cuisine") or "",
                     r.get("servings") or "", json.dumps(ings), r.get("instructions") or "", r.get("source") or "",
                     source or fmt, now, time_bucket(r), r.get("prep_time") or "", r.get("cook_time") or "",
                     r.get("total_time") or "", protein, cuisine, dish))
    report["added"] = len(rows)
    if not dry_run and rows:
        with con:
            con.executemany(
                "INSERT INTO bulk_recipes (title, slug, category, cuisine, servings, ingredients, instructions, source_url,"
                " source_site, imported_at, time_bucket, prep_time, cook_time, total_time, protein_type, cuisine_group,"
                " dish_group) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.execute(TIME_BUCKET_SQL)
    return report


LABELS = ("protein_type", "cuisine_group", "dish_group")


def relabel(con: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Re-derive every library recipe's protein, cuisine and dish from its ingredients and category with
    the current rules (recipes imported before the rules changed keep their old labels otherwise)."""
    ensure_table(con)
    rows = con.execute("SELECT id, title, category, cuisine, ingredients, protein_type, cuisine_group, dish_group"
                       " FROM bulk_recipes").fetchall()
    changes, moves = [], {k: {} for k in LABELS}
    after = {k: {} for k in LABELS}
    for rid, title, category, cuisine, ings, *old in rows:
        try:
            lines = json.loads(ings or "[]")
        except ValueError:
            lines = []
        cats = [category] if category else []
        protein = protein_of(title, lines, cats)
        new = (protein, cuisine_of(cuisine or "", cats), dish_of(title, cats, protein))
        for k, o, n in zip(LABELS, old, new):
            after[k][n or "none"] = after[k].get(n or "none", 0) + 1
            if o != n:
                move = f"{o or 'none'} -> {n or 'none'}"
                moves[k][move] = moves[k].get(move, 0) + 1
        if tuple(old) != new:
            changes.append((*new, rid))
    if not dry_run and changes:
        with con:
            con.executemany("UPDATE bulk_recipes SET protein_type = ?, cuisine_group = ?, dish_group = ? WHERE id = ?",
                            changes)
    top = {k: dict(sorted(v.items(), key=lambda kv: -kv[1])[:10]) for k, v in moves.items()}
    return {"recipes": len(rows), "changed": len(changes), "dryRun": dry_run, "moves": top,
            "after": {k: dict(sorted(v.items(), key=lambda kv: -kv[1])) for k, v in after.items()}}
