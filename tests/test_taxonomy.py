"""Names taken from the real inventory and receipts, placed where a person would put them."""

import pytest

from foodcore.taxonomy import CATEGORIES, DEPARTMENTS, classify
from foodcore.text import expand_receipt, tokens

CASES = [
    # name, kwargs, expected cat, expected generic (None = don't care)
    ("Bourbon chicken breasts", dict(category="meat", storage="fridge"), "meat.poultry", "chicken breast"),
    ("Celery ribs", dict(category="produce"), "produce.vegetables", "celery"),
    ("Root Beer Schnapps", dict(category="beverage", subCategory="liqueur", spiritType="liqueur"), "bar.liqueur", None),
    ("Plum Soju", dict(category="beverage", subCategory="rice", spiritType="wine"), "bar.wine", None),
    ("Kodiak Power Waffles chocolate chip", dict(category="frozen", storage="freezer"), "frozen.breakfast", "waffle"),
    ("Non-dairy oat creamer \u2014 brown sugar", dict(category="ingredient", subCategory="creamer"), "dairy.creamer", "creamer"),
    ("Chicken pesto sandwich (prepared)", dict(category="prepared", storage="fridge"), "deli.meals", "sandwich"),
    ("Uncrustables", dict(category="frozen", storage="freezer"), "frozen.meals", "sandwich"),
    ("Chef Boyardee mini ravioli", dict(category="ready-meal", storage="cupboard"), "pantry.meals", "canned pasta"),
    ("Organic hummus", dict(category="ready_meal", storage="fridge"), "deli.dips", "hummus"),
    ("Pine Nut Hummus", dict(category="snack"), "deli.dips", "hummus"),
    ("Avocado Ranch Salad Kit", dict(category="ready meal"), "produce.saladkits", "salad kit"),
    ("Lundberg microwave rice chili crisp (Fly By Jing)", dict(category="pantry", subCategory="microwave rice"),
     "pantry.grains", "microwave rice"),
    ("Uncle Ben's Jasmine rice microwave packs", dict(category="ingredient"), "pantry.grains", "microwave rice"),
    ("Jasmine rice (microwavable)", dict(category="grain"), "pantry.grains", "microwave rice"),
    ("Better Than Bouillon Roast Beef Base", dict(category="pantry", subCategory="broth base"), "pantry.broth", None),
    ("Old El Paso tinga-style chicken broth (2 lb)", dict(category="ingredient"), "pantry.broth", None),
    ("Sliced turkey breast", dict(category="deli"), "deli.meats", "deli turkey"),
    ("Organic turkey breast", dict(category="protein"), "meat.poultry", "turkey breast"),
    ("Siggi's skyr", dict(category="dairy"), "dairy.yogurt", "skyr"),
    ("Raspberry kefir", dict(category="dairy"), "dairy.yogurt", "kefir"),
    ("Fairlife 2% milk", dict(category="dairy"), "dairy.milk", "milk"),
    ("Oatly matcha oat milk", dict(category="dairy"), "dairy.plantmilk", "oat milk"),
    ("Eggs (dozen)", dict(category="dairy"), "dairy.eggs", "eggs"),
    ("Cage-free liquid egg whites", dict(category="dairy"), "dairy.eggs", "egg white"),
    ("Ginger paste (Gourmet Garden tube)", dict(category="ingredient"), "produce.aromatics", "ginger paste"),
    ("Glory's cherry snacking tomatoes (red)", dict(category="produce"), "produce.vegetables", "tomato"),
    ("Organic green onions (spring onions)", dict(category="produce"), "produce.aromatics", "green onion"),
    ("Hatch green chilies", dict(category="produce"), "produce.vegetables", "green chili"),
    ("Pickled beets", dict(category="produce"), "pantry.pickles", "pickled beet"),
    ("Dave's Killer Bread 21 Whole Grains & Seeds", dict(category="bakery"), "bakery.bread", "bread"),
    ("Spinach", dict(category="frozen-veg", storage="freezer"), "frozen.vegetables", "spinach"),
    ("Wild Pacific pink salmon (canned)", dict(category="ingredient", subCategory="canned fish"), "pantry.canned", None),
    ("Cigies (pork)", dict(category="meat", storage="freezer"), "meat.pork", "pork"),
    ("Lemony chicken orzo soup (premade)", dict(category="ready meal", subCategory="soup", storage="fridge"),
     "deli.meals", None),
    ("Tequila Reposado", dict(category="beverage", subCategory="spirit", spiritType="tequila"), "bar.agave", "tequila"),
    ("Mezcal Reposado", dict(category="beverage", subCategory="spirit", spiritType="mezcal"), "bar.agave", "mezcal"),
]


@pytest.mark.parametrize("name,kw,cat,generic", CASES)
def test_places_real_names(name, kw, cat, generic):
    got = classify(name, **kw)
    assert got["cat"] == cat, got
    if generic is not None:
        assert got["generic"] == generic, got


RECEIPT_LINES = [
    ("CKN BRST BNLS SKLS", "MEAT", "meat.poultry"),
    ("O ORG CHICKN BROTH", "GROCERY", "pantry.broth"),
    ("LUCERNE CHEESE STR", "REFRIG/FROZEN", "dairy.cheese"),
    ("ONIONS GREEN ORG", "PRODUCE", "produce.aromatics"),
    ("TWANG BEER SLT LMN", "LIQUOR", "bar.mixers"),
    ("BLACK BOX CAB SAUV", "LIQUOR", "bar.wine"),
    ("2@ SIG JASMINE RICE", "GROCERY", "pantry.grains"),
    ("CELSIUS FIZZ FREE", "GROCERY", "drinks.energy"),
    ("MYSTERY WIDGET", "PRODUCE", "produce.other"),
    ("MYSTERY WIDGET", None, "other.other"),
]


@pytest.mark.parametrize("raw,section,cat", RECEIPT_LINES)
def test_receipt_shorthand(raw, section, cat):
    assert classify(raw=raw, section=section)["cat"] == cat


def test_receipt_expansion():
    assert expand_receipt("2@ SIG JASMINE RICE") == "signature jasmine rice"
    assert expand_receipt("CKN BRST BNLS SKLS") == "chicken breast boneless skinless"


def test_tokens_singularize_consistently():
    assert tokens("Cookies") == tokens("cookie")
    assert tokens("Tomatoes") == ["tomato"]
    assert tokens("Siggi's") == ["siggis"]
    assert tokens("hummus") == ["hummus"]
    assert tokens("Jalapeños") == ["jalapeno"]


def test_every_category_belongs_to_a_department():
    depts = {d for d, _ in DEPARTMENTS}
    for k, v in CATEGORIES.items():
        assert v["dept"] in depts, k
    for d in depts:
        assert f"{d}.other" in CATEGORIES


def test_unknown_is_low_confidence_not_a_guess():
    got = classify("Zorblax")
    assert got["cat"] == "other.other" and got["confidence"] == "low" and got["generic"] is None
