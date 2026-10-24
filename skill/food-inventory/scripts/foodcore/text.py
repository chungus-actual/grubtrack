"""Name normalisation shared by the classifier, product identity and store matching.

Everything here is deterministic and dependency-free: the same name must always land on the
same product, whether an agent wrote it from a chat at midnight or GrubTrack resolved it.
"""

from __future__ import annotations

import re
import unicodedata

# Words whose plural is not "drop the s" in a way the rules below get right.
_IRREGULAR = {
    "cookies": "cookie", "brownies": "brownie", "smoothies": "smoothie", "pies": "pie",
    "veggies": "veggie", "hoagies": "hoagie", "calories": "calorie", "goodies": "goodie",
    "leaves": "leaf", "loaves": "loaf", "halves": "half", "knives": "knife",
    "anchovies": "anchovy", "radishes": "radish", "dishes": "dish", "peaches": "peach",
    "sandwiches": "sandwich", "lunches": "lunch", "sauces": "sauce", "glasses": "glass",
    "molasses": "molasses", "hummus": "hummus", "asparagus": "asparagus", "couscous": "couscous",
    "swiss": "swiss", "brussels": "brussels", "oats": "oat", "grits": "grits", "chips": "chip",
    "jalapenos": "jalapeno", "tomatoes": "tomato", "potatoes": "potato", "mangoes": "mango",
    "avocados": "avocado", "whiskies": "whisky", "berries": "berry", "cherries": "cherry",
    "chilies": "chili", "chiles": "chile", "series": "series", "species": "species", "eggs": "egg", "chives": "chive", "olives": "olive",
}

_KEEP_S = ("ss", "us", "is", "ys")


def fold(s: str | None) -> str:
    """Lowercase, strip accents (café -> cafe), unify quotes."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.replace("’", "'").replace("‘", "'").lower()


def singular(t: str) -> str:
    if t in _IRREGULAR:
        return _IRREGULAR[t]
    if len(t) <= 3 or not t.endswith("s") or t.endswith(_KEEP_S):
        return t
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith(("ches", "shes", "sses", "xes", "zes")):
        return t[:-2]
    return t[:-1]


_PAREN = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_NONWORD = re.compile(r"[^a-z0-9%]+")


def strip_parens(s: str) -> str:
    return _PAREN.sub(" ", s)


def tokens(s: str | None, keep_parens: bool = False) -> list[str]:
    """Normalised, singularised tokens. `&` becomes `and`, apostrophes vanish
    (siggi's -> siggis), so receipt shouting and voice logs meet in the middle."""
    s = fold(s)
    if not keep_parens:
        s = strip_parens(s)
    s = s.replace("&", " and ").replace("'", "")
    return [singular(t) for t in _NONWORD.sub(" ", s).split() if t]


def norm(s: str | None) -> str:
    return " ".join(tokens(s))


def slug(s: str | None) -> str:
    return "-".join(_NONWORD.sub(" ", fold(s)).split())


def parens(s: str | None) -> list[str]:
    """The parenthetical qualifiers a voice log carries: '(prepared)', '(the usual)', '(2 lb)'."""
    return [p.strip("()[] ").lower() for p in _PAREN.findall(fold(s))]


# Receipt shorthand -> words. Only expansions that are unambiguous across the chains seen so
# far (Albertsons, Target, Sprouts). Brand prefixes that collide (GG = Good & Gather at Target
# but Gourmet Garden at Albertsons) are deliberately NOT expanded.
ABBREV = {
    "ckn": "chicken", "chkn": "chicken", "chickn": "chicken", "chicke": "chicken", "brst": "breast",
    "bnls": "boneless", "skls": "skinless", "grnd": "ground", "bf": "beef", "flort": "floret",
    "florets": "floret", "waff": "waffle", "waffl": "waffle", "orgnc": "organic", "orgn": "organic",
    "org": "organic", "lqd": "liquid", "str": "string", "mxed": "mixed", "sprk": "sparkling",
    "drnk": "drink", "blnd": "blend", "ital": "italian", "orgnl": "original", "kttl": "kettle",
    "matc": "matcha", "slt": "salt", "lmn": "lemon", "stic": "stick", "campbel": "campbell",
    "rstd": "roasted", "slcd": "sliced", "grlc": "garlic", "srln": "sirloin", "tstr": "toaster",
    "erthbnd": "earthbound", "sig": "signature", "luc": "lucerne", "pf": "pepperidge farm",
    "pep": "pepperidge", "brookl": "brooklyn", "oatmilk": "oat milk", "sauv": "sauvignon",
    "cab": "cabernet", "bx": "box", "dk": "dark", "wld": "wild", "chry": "cherry",
    "grn": "green", "veg": "vegetable", "frzn": "frozen", "yog": "yogurt", "yogrt": "yogurt",
    "chs": "cheese", "shrd": "shredded", "crm": "cream", "bttr": "butter", "pnut": "peanut",
    "choc": "chocolate", "van": "vanilla", "strwb": "strawberry", "blub": "blueberry",
}

_QTY_PREFIX = re.compile(r"^\s*(\d+)\s*@\s*")


def expand_receipt(raw: str | None) -> str:
    """'2@ SIG JASMINE RICE' -> 'signature jasmine rice'; 'CKN BRST BNLS SKLS' -> 'chicken breast
    boneless skinless'. The multi-buy prefix is dropped: quantity is its own field."""
    s = _QTY_PREFIX.sub("", fold(raw))
    out = []
    for w in _NONWORD.sub(" ", s.replace("&", " and ")).split():
        out.append(ABBREV.get(w, w))
    return " ".join(out)


def jaccard(a: list[str] | set[str], b: list[str] | set[str]) -> float:
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
