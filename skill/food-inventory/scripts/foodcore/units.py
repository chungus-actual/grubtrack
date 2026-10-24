"""Package sizes and unit prices.

Everything is reduced to one of four dimensions with a base unit:
    mass   grams        ("32 oz" of yogurt, "2 lb" of chicken, "3.53 lb @ $2.29/lb")
    volume millilitres  ("1.5 L", "1 quart", "52 fl oz")
    count  each         ("dozen", "16 ct", "3pk")
    pkg    packages     (size unknown: still comparable against the same product)

`oz` is mass unless the thing is a liquid (drinks, milk, broth, oil...), in which case it is
fluid ounces: a 32 oz kefir is a volume, a 32 oz bag of rice is a weight.

Display bases follow US shelf tags: meat, produce and deli by the pound, most pantry goods
by the ounce, liquids by the fluid ounce, eggs per egg.
"""

from __future__ import annotations

import re

G_PER = {"g": 1.0, "gram": 1.0, "kg": 1000.0, "oz": 28.349523125, "ounce": 28.349523125,
         "lb": 453.59237, "pound": 453.59237}
ML_PER = {"ml": 1.0, "l": 1000.0, "liter": 1000.0, "litre": 1000.0, "floz": 29.5735295625,
          "qt": 946.352946, "quart": 946.352946, "pt": 473.176473, "pint": 473.176473,
          "gal": 3785.411784, "gallon": 3785.411784, "cup": 236.5882365}
EA_PER = {"ct": 1.0, "count": 1.0, "each": 1.0, "ea": 1.0, "pc": 1.0, "piece": 1.0, "dozen": 12.0,
          "doz": 12.0, "pk": None, "pack": None}

_ALIASES = {"lbs": "lb", "pounds": "lb", "ounces": "oz", "grams": "g", "kgs": "kg", "liters": "l",
            "litres": "l", "quarts": "qt", "pints": "pt", "gallons": "gal", "cups": "cup", "pcs": "pc",
            "pieces": "piece", "counts": "count", "packs": "pack", "pks": "pk", "dz": "dozen",
            "fl. oz": "floz", "fl oz": "floz", "fl.oz": "floz", "fluid ounce": "floz", "fluid ounces": "floz"}

LIQUID_CATS = {"drinks", "bar", "dairy.milk", "dairy.plantmilk", "dairy.creamer",
               "pantry.broth", "pantry.oils"}
# liquids that live in otherwise-solid categories
LIQUID_GENERICS = {"kefir", "drinkable yogurt", "egg white", "heavy cream", "half and half", "coconut milk",
                   "soy sauce", "hot sauce", "vinegar", "maple syrup", "simple syrup", "espresso", "kombucha", "chai"}
# drinks sold dry: beans, grounds, tea bags, matcha powder are weighed
DRY_CATS = {"drinks.coffee", "drinks.tea"}

_NUM = r"(\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+|\.\d+)"
_UNIT = (r"(fl\.?\s*oz|fluid\s+ounces?|lbs?|pounds?|ounces?|oz|kgs?|grams?|g|ml|liters?|litres?|l|quarts?|qt|"
         r"pints?|pt|gallons?|gal|cups?|ct|counts?|each|ea|pcs?|pieces?|dozen|doz|dz|packs?|pks?|pk)")
_PART = re.compile(_NUM + r"\s*-?\s*" + _UNIT + r"\b", re.I)
_MULTI = re.compile(r"(\d+)\s*[x×]\s*" + _NUM + r"\s*" + _UNIT + r"\b", re.I)


def _num(s: str) -> float:
    s = s.strip()
    if " " in s:
        whole, frac = s.split(None, 1)
        return float(whole) + _num(frac)
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b)
    return float(s)


def _unit(u: str) -> str:
    u = re.sub(r"\s+", " ", u.lower().strip()).rstrip(".")
    if u in G_PER or u in ML_PER or u in EA_PER:
        return u
    return _ALIASES.get(u, u)


def _is_liquid(cat: str | None, generic: str | None = None) -> bool:
    if generic and generic in LIQUID_GENERICS:
        return True
    if not cat or cat in DRY_CATS:
        return False
    return cat in LIQUID_CATS or cat.split(".")[0] in LIQUID_CATS


def _measure(value: float, unit: str, cat: str | None, generic: str | None = None):
    u = _unit(unit)
    if u == "oz" and _is_liquid(cat, generic):
        u = "floz"
    if u in G_PER:
        return value * G_PER[u], "mass"
    if u in ML_PER:
        return value * ML_PER[u], "volume"
    if u in EA_PER:
        per = EA_PER[u]
        return (value * per, "count") if per else (value, "count")
    return None


def parse_size(text: str | None, cat: str | None = None, generic: str | None = None):
    """'1 lb 8 oz' -> (680.4, 'mass'); '1.5 L' -> (1500, 'volume'); 'dozen' -> (12, 'count');
    '4 x 1.5 oz' -> (170.1, 'mass'). None when nothing measurable is said. A restatement in
    parentheses ('12 oz (340 g)') is ignored unless it is the only measure given."""
    if not text:
        return None
    outside = re.sub(r"\([^)]*\)", " ", str(text))
    if outside != str(text) and outside.strip():
        got = parse_size(outside, cat, generic)
        if got:
            return got
        text = re.sub(r"[()]", " ", str(text))
    t = str(text).lower().replace("×", "x")
    m = _MULTI.search(t)
    if m:
        got = _measure(_num(m.group(2)), m.group(3), cat, generic)
        if got:
            return got[0] * int(m.group(1)), got[1]
    if re.search(r"\bdozen\b", t) and not _PART.search(t):
        return 12.0 * (_num(re.search(_NUM, t).group(1)) if re.search(_NUM + r"\s*dozen", t) else 1), "count"
    total, dim = 0.0, None
    for m in _PART.finditer(t):
        got = _measure(_num(m.group(1)), m.group(2), cat, generic)
        if not got:
            continue
        if dim and got[1] != dim:
            break  # "12 oz (340 g)" style restatement: first measure wins
        if dim == got[1] and got[1] != "mass":
            break
        total += got[0]
        dim = got[1]
    return (round(total, 3), dim) if dim else None


def measure_of(qty: float | None, unit: str | None, cat: str | None = None, generic: str | None = None):
    """An inventory row's quantity + unit, when the unit is a measure ('2 lb', '8 oz')."""
    if qty is None or not unit:
        return None
    return _measure(float(qty), unit, cat, generic)


def line_amount(qty: float | None, size_label: str | None = None, weight_lb: float | None = None,
                cat: str | None = None, product_size: tuple | None = None, generic: str | None = None):
    """Total amount bought on one line: (amount, dim). Falls back to packages."""
    q = float(qty) if qty else 1.0
    if weight_lb:
        return round(weight_lb * G_PER["lb"], 3), "mass"
    size = parse_size(size_label, cat, generic) if size_label else None
    if not size and product_size and product_size[0]:
        size = product_size
    if size:
        return round(size[0] * q, 3), size[1]
    return q, "pkg"


# (label, base units per display unit) by department/category, then dimension.
_MASS_LB = {"meat", "produce", "deli"}
_MASS_LB_CATS = {"dairy.cheese", "frozen.vegetables", "frozen.fruit"}


def display_basis(cat: str | None, dim: str | None):
    """How a unit price is shown on a shelf tag: ('lb', 453.6) means '$x.xx / lb'."""
    dept = (cat or "").split(".")[0]
    if dim == "mass":
        if dept in _MASS_LB or cat in _MASS_LB_CATS:
            return "lb", G_PER["lb"]
        return "oz", G_PER["oz"]
    if dim == "volume":
        return "fl oz", ML_PER["floz"]
    if dim == "count":
        return ("egg", 1.0) if cat == "dairy.eggs" else ("ea", 1.0)
    if dim == "pkg":
        return "pkg", 1.0
    return None, None


def unit_price(price: float | None, amount: float | None):
    if price is None or not amount:
        return None
    return price / amount


def shelf(price_per_base: float | None, cat: str | None, dim: str | None):
    """(value, label) for display: 0.0141 $/g of cheese -> (6.40, 'lb')."""
    if price_per_base is None:
        return None, None
    label, factor = display_basis(cat, dim)
    if not label:
        return None, None
    return price_per_base * factor, label


def fmt_amount(amount: float | None, dim: str | None, cat: str | None = None) -> str:
    if not amount or not dim:
        return ""
    if dim == "mass":
        lb = amount / G_PER["lb"]
        if lb >= 1:
            return f"{lb:.2f}".rstrip("0").rstrip(".") + " lb"
        return f"{amount / G_PER['oz']:.1f}".rstrip("0").rstrip(".") + " oz"
    if dim == "volume":
        if amount >= 1000:
            return f"{amount / 1000:.2f}".rstrip("0").rstrip(".") + " L"
        return f"{amount / ML_PER['floz']:.1f}".rstrip("0").rstrip(".") + " fl oz"
    if dim == "count":
        return f"{amount:g} ct"
    return f"{amount:g} pkg"
