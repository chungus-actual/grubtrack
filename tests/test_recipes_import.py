"""Recipe collections into the library: every supported format, the filters each recipe gets, and
duplicates. The sample recipes are written for these tests."""

import gzip
import io
import json
import sqlite3
import zipfile

from conftest import SCRIPTS  # noqa: F401  (puts foodcore on sys.path)
from foodcore import recipes as rc


def mm(qty, unit, ing):
    """One Meal-Master ingredient column: amount in 7, unit in 2, ingredient in 28."""
    return f"{qty:>7} {unit:<2} {ing:<28}"


CORNBREAD = "\n".join([
    "MMMMM----- Recipe via Meal-Master (tm) v8.05",
    " ",
    "      Title: Skillet Cornbread",
    " Categories: Breads, Southern",
    "      Yield: 8 servings",
    " ",
    mm("1", "c", "Yellow cornmeal") + "  " + mm("1", "c", "All-purpose flour"),
    mm("1", "tb", "Sugar") + "  " + mm("2", "ts", "Baking powder"),
    mm("1/2", "ts", "Salt") + "  " + mm("1", "c", "Buttermilk"),
    mm("2", "", "Eggs"),
    mm("4", "tb", "Butter, melted, plus more for"),
    "           -the pan",
    " ",
    "  Heat the oven to 425F with a cast iron skillet inside. Whisk the dry",
    "  ingredients together in a large bowl.",
    " ",
    "  Beat the eggs into the buttermilk, stir into the dry ingredients with",
    "  the butter, pour into the hot skillet and bake 20 minutes until golden.",
    " ",
    "MMMMM",
])

CHICKEN = "\n".join([
    "MMMMM----- Recipe via Meal-Master (tm) v8.05",
    " ",
    "      Title: CHICKEN THIGHS WITH LEMON",
    " Categories: Main dish, Poultry, Greek",
    "      Yield: 4 servings",
    " ",
    mm("8", "", "Bone-in chicken thighs"),
    mm("2", "", "Lemons, juiced"),
    mm("4", "cl", "Garlic, minced"),
    mm("2", "tb", "Olive oil"),
    " ",
    "MMMMM--------------------------SAUCE-------------------------------",
    mm("1/2", "c", "Greek yogurt"),
    mm("1", "tb", "Chopped dill"),
    " ",
    "  Toss the chicken with the lemon juice, garlic and oil and leave it for",
    "  30 minutes. Roast at 425F for 40 minutes until the skin is crisp.",
    " ",
    "  Stir the dill into the yogurt and serve it on the side.",
    " ",
    "MMMMM",
])

SOUP = "\n".join([
    "---------- Recipe via Meal-Master (tm) v8.02",
    " ",
    "      Title: Quick Tomato Soup",
    " Categories: Soups",
    "      Yield: 2 servings",
    " ",
    mm("1", "cn", "Crushed tomatoes (28 oz)"),
    mm("1", "c", "Chicken broth"),
    mm("1", "ts", "Dried basil"),
    "           Salt and pepper",
    " ",
    "  Simmer everything together for ten minutes, then blend it until smooth.",
    " ",
    "-----",
])

MASTERCOOK = """
                     * Exported from MasterCook *

                            Black Bean Tacos

Recipe By     :
Serving Size  : 4     Preparation Time :0:20
Categories    : Mexican                          Main Dish

  Amount  Measure       Ingredient -- Preparation Method
--------  ------------  --------------------------------
   2      cans          black beans -- drained
   8                    corn tortillas
   1      cup           salsa
   1                    avocado -- sliced

Warm the beans with the salsa. Fill the tortillas and top them with the avocado.

                   - - - - - - - - - - - - - - - - - - -
"""

PAGE = """<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [{"@type": "WebSite", "name": "x"},
 {"@type": "Recipe", "name": "Shrimp &amp; Grits", "recipeYield": 4, "totalTime": "PT40M", "recipeCuisine": "Southern",
  "recipeCategory": "Main Course", "recipeIngredient": ["1 lb shrimp, peeled", "1 cup stone-ground grits", "4 slices bacon"],
  "recipeInstructions": [{"@type": "HowToStep", "text": "Cook the grits."}, {"@type": "HowToStep", "text": "Fry the bacon, then the shrimp."}],
  "url": "https://recipes.example/shrimp-grits"}]}
</script></head><body></body></html>"""

MEALIE = {
    "id": "a1", "name": "Sheet Pan Gnocchi", "slug": "sheet-pan-gnocchi", "recipeServings": 4, "recipeYield": "4 servings",
    "totalTime": "35 minutes", "prepTime": "10 minutes", "performTime": "25 minutes", "orgURL": "https://example.com/gnocchi",
    "recipeCategory": [{"name": "Dinner", "slug": "dinner"}], "tags": [{"name": "Italian", "slug": "italian"}],
    "recipeIngredient": [
        {"quantity": 1, "unit": {"name": "pound"}, "food": {"name": "potato gnocchi"}, "note": "", "display": "1 pound potato gnocchi"},
        {"quantity": 0, "unit": None, "food": None, "note": "2 cups cherry tomatoes", "display": "", "originalText": None},
        {"quantity": 8, "unit": {"name": "ounce"}, "food": {"name": "Italian sausage"}, "note": "", "display": ""},
    ],
    "recipeInstructions": [{"id": "s1", "title": "", "summary": "", "text": "Heat the oven to 425F."},
                           {"id": "s2", "title": "Roast", "summary": "", "text": "Toss everything on a sheet pan and roast 25 minutes."}],
}

PAPRIKA = {"uid": "P1", "name": "Overnight Oats", "ingredients": "1 cup rolled oats\n1 cup milk\n1 tbsp honey",
           "directions": "Stir it all together.\n\nRefrigerate overnight.", "servings": "2", "prep_time": "5 min",
           "cook_time": "", "categories": ["Breakfast"], "source_url": "https://example.com/oats", "photo_data": None}


def paprika_archive(*recs) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for r in recs:
            z.writestr(f"{r['name']}.paprikarecipe", gzip.compress(json.dumps(r).encode()))
    return buf.getvalue()


def write_samples(d):
    d.mkdir()
    (d / "collection.mmf").write_text(CORNBREAD + "\n\n" + CHICKEN + "\n\n" + SOUP + "\n\n" + CORNBREAD + "\n", encoding="cp1252")
    (d / "tacos.mxp").write_text(MASTERCOOK)
    (d / "page.html").write_text(PAGE)
    (d / "gnocchi.json").write_text(json.dumps(MEALIE))
    (d / "export.paprikarecipes").write_bytes(paprika_archive(PAPRIKA))
    (d / "mine.jsonl").write_text(json.dumps({"title": "Toast", "ingredients": ["2 slices bread", "butter"],
                                              "instructions": "Toast the bread. Butter it."}) + "\n"
                                  + json.dumps({"title": "No ingredients", "ingredients": []}) + "\n")
    (d / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0 not a recipe")
    return d


def by_title(rs):
    return {r["title"]: r for r in rs}


def test_meal_master_columns_sections_and_wraps():
    rs = rc.from_text(CORNBREAD + "\n" + CHICKEN + "\n" + SOUP)
    assert [r["title"] for r in rs] == ["Skillet Cornbread", "Chicken Thighs With Lemon", "Quick Tomato Soup"]
    corn, chicken, soup = rs
    assert corn["ingredients"] == ["1 cup Yellow cornmeal", "1 cup All-purpose flour", "1 tbsp Sugar", "2 tsp Baking powder",
                                   "1/2 tsp Salt", "1 cup Buttermilk", "2 Eggs", "4 tbsp Butter, melted, plus more for the pan"]
    assert corn["servings"] == "8 servings" and corn["categories"] == ["Breads", "Southern"]
    assert corn["instructions"].count("\n") == 1 and corn["instructions"].startswith("Heat the oven to 425F with a cast iron")
    assert chicken["ingredients"][-2:] == ["1/2 cup Greek yogurt", "1 tbsp Chopped dill"]   # past the SAUCE rule
    assert "4 clove Garlic, minced" in chicken["ingredients"]
    assert soup["ingredients"][-1] == "Salt and pepper"


def test_mastercook():
    (r,) = rc.from_text(MASTERCOOK)
    assert r["title"] == "Black Bean Tacos" and r["servings"] == "4" and r["prep_time"] == "20 min"
    assert r["categories"] == ["Mexican", "Main Dish"]
    assert r["ingredients"] == ["2 cans black beans, drained", "8 corn tortillas", "1 cup salsa", "1 avocado, sliced"]
    assert r["instructions"].startswith("Warm the beans")


def test_json_shapes():
    (page,) = rc.from_html(PAGE)
    assert (page["title"], page["servings"], page["total_time"], page["source"]) == \
        ("Shrimp & Grits", "4", "40 min", "https://recipes.example/shrimp-grits")
    assert page["instructions"] == "1. Cook the grits.\n2. Fry the bacon, then the shrimp."
    (mealie,) = rc.from_json(MEALIE)
    assert mealie["_format"] == "mealie" and mealie["source"] == "https://example.com/gnocchi"
    assert mealie["ingredients"] == ["1 pound potato gnocchi", "2 cups cherry tomatoes", "8 ounce Italian sausage"]
    assert mealie["instructions"] == "1. Heat the oven to 425F.\nRoast:\n2. Toss everything on a sheet pan and roast 25 minutes."
    assert mealie["categories"] == ["Dinner", "Italian"] and mealie["servings"] == "4 servings"
    (pap,) = list(rc.from_bytes("export.paprikarecipes", paprika_archive(PAPRIKA)))
    assert pap["_format"] == "paprika" and pap["ingredients"] == ["1 cup rolled oats", "1 cup milk", "1 tbsp honey"]
    assert pap["instructions"] == "Stir it all together.\n\nRefrigerate overnight."


def test_filters():
    assert rc.protein_of("Chicken Thighs With Lemon", ["8 Bone-in chicken thighs", "1 tbsp chopped dill"]) == "chicken"
    # stock keeps a dish out of vegetarian, but only decides when there's no other meat
    assert rc.protein_of("Quick Tomato Soup", ["1 can crushed tomatoes", "1 cup chicken broth"]) == "chicken"
    assert rc.protein_of("Beef Stew", ["2 lb beef chuck", "2 cups chicken stock"]) == "beef"
    assert rc.protein_of("Vegetable Soup", ["4 carrots", "4 cups vegetable broth"]) == "vegetarian"
    # from the 41k-recipe library: words the classifier doesn't know, typos, titles, categories
    assert rc.protein_of("Chili", ["1 lb Ground meat", "1 lg Onion"]) == "beef"
    assert rc.protein_of("Ox Tail Stew", ["1 1/2 lb ox tails"]) == "beef"
    assert rc.protein_of("Crabapple Pickles", ["7 lb Crabapples", "7 c sugar"]) == "vegetarian"
    assert rc.protein_of("Hamburger Casserole", ["1 lb Hamburger                          12 oz Can corn"]) == "beef"
    assert rc.protein_of("Cobb Chicken Salad", ["4 barbecued chcken breasts", "2 avocados"]) == "chicken"
    assert rc.protein_of("Spicy Peanut Dip for Chicken", ["1 cup peanut butter"]) == "vegetarian"
    assert rc.protein_of('Orange "Beef" With Broccoli', ["1 lb seitan"], ["vegetarian"]) == "vegetarian"
    assert rc.protein_of("Grape Bread", ["4 c flour", "1/2 c Lard, shortening, or butter"]) == "vegetarian"
    assert rc.protein_of("Shrimp & Grits", ["1 lb shrimp, peeled", "4 slices bacon", "1 cup grits"]) == "seafood"
    assert rc.protein_of("Cheesecake", ["2 cups graham cracker crumbs", "16 oz cream cheese"]) == "vegetarian"
    assert rc.protein_of("Lamb Stew", ["2 lb lamb shoulder", "4 carrots"]) == "other_meat"
    assert rc.protein_of("Tuna Melt", ["1 can tuna", "2 slices bread"]) == "seafood"
    assert rc.cuisine_of("", ["Main dish", "Poultry", "Greek"]) == "european"
    assert rc.cuisine_of("Southern", []) == "american" and rc.cuisine_of("", ["Weeknight"]) is None
    assert rc.dish_of("Chicken Pieces", ["Main dish"]) == "main"          # "pieces" is not "pie"
    assert rc.dish_of("Apple Pie", []) == "baked" and rc.dish_of("x", ["Soups"]) == "soup"
    assert rc.dish_of("Chicken Pot Pie", [], "chicken") == "main" and rc.dish_of("Crab Cakes", [], "seafood") == "main"
    assert rc.dish_of("Chicken in Mustard Sauce", []) == "main" and rc.dish_of("Spaghetti Sauce", []) == "sauce"
    assert rc.dish_of("Pasta with Tomato Sauce", []) == "main" and rc.dish_of("Cabbage Rolls", ["main-dishes"]) == "main"
    assert rc.dish_of("Chicken Mushroom Pie", ["dinner-pies"], "chicken") == "main"
    assert rc.dish_of("Mushroom Caps With Pesto", ["appetizers"]) == "main" and rc.dish_of("Basil Pesto", []) == "sauce"
    assert rc.dish_of("Apple Butter", ["butter"]) == "sauce" and rc.dish_of("Chocolate Mousse", ["mousse"]) == "baked"
    assert rc.cuisine_of("", ["africa"]) == "african_me" and rc.cuisine_of("", ["jewish"]) == "other"
    assert rc.time_bucket({"total_time": "1h 30m"}) == "long" and rc.time_bucket({"prep_time": "10 min"}) == "quick"
    assert rc.time_bucket({}) is None


def test_import_a_folder_twice(tmp_path, cli, dbpath):
    d = write_samples(tmp_path / "recipes")
    out = json.loads(cli("recipes-import", "--file", d, "--dry-run").stdout)
    assert out["dryRun"] and out["added"] == 8
    out = json.loads(cli("recipes-import", "--file", d).stdout)
    assert (out["found"], out["added"], out["duplicates"]) == (9, 8, 1)   # an entry with no ingredients isn't a recipe
    assert out["formats"] == {"meal-master": 4, "mastercook": 1, "schema.org": 1, "mealie": 1, "paprika": 1, "grubtrack": 1}
    con = sqlite3.connect(dbpath)
    con.row_factory = sqlite3.Row
    rows = {r["title"]: dict(r) for r in con.execute("SELECT * FROM bulk_recipes")}
    assert len(rows) == 8
    assert (rows["Chicken Thighs With Lemon"]["protein_type"], rows["Chicken Thighs With Lemon"]["cuisine_group"],
            rows["Chicken Thighs With Lemon"]["dish_group"]) == ("chicken", "european", "main")
    assert rows["Black Bean Tacos"]["cuisine_group"] == "latin" and rows["Black Bean Tacos"]["time_bucket"] == "quick"
    assert rows["Sheet Pan Gnocchi"]["protein_type"] == "pork" and rows["Sheet Pan Gnocchi"]["time_bucket"] == "medium"
    assert rows["Overnight Oats"]["dish_group"] == "breakfast" and rows["Overnight Oats"]["source_site"] == "paprika"
    assert rows["Quick Tomato Soup"]["dish_group"] == "soup" and rows["Quick Tomato Soup"]["time_bucket"]  # heuristic filled it
    assert json.loads(rows["Skillet Cornbread"]["ingredients"])[0] == "1 cup Yellow cornmeal"
    again = json.loads(cli("recipes-import", "--file", d, "--source", "family").stdout)
    assert (again["added"], again["duplicates"]) == (0, 9)
    # a different recipe that happens to share a title gets its own slug
    (tmp_path / "other.jsonl").write_text(json.dumps({"title": "Toast", "ingredients": ["1 slice rye", "jam"]}))
    assert json.loads(cli("recipes-import", "--file", tmp_path / "other.jsonl").stdout)["added"] == 1
    assert {r[0] for r in con.execute("SELECT slug FROM bulk_recipes WHERE title = 'Toast'")} == {"toast", "toast-2"}
    con.close()


def test_import_needs_a_real_path(cli):
    r = cli("recipes-import", "--file", "/nope/nothing", ok=False)
    assert r.returncode != 0 and "no such file" in r.stderr


def test_relabel_rewrites_old_labels(tmp_path, cli, dbpath):
    (tmp_path / "r.jsonl").write_text(json.dumps({"title": "Basic Stock For Soups", "categories": ["Soups"],
                                                  "ingredients": ["3 lb chicken backs", "1 onion"]}) + "\n"
                                      + json.dumps({"title": "Graham Cracker Crust", "categories": ["Pies"],
                                                    "ingredients": ["1 1/2 cups graham cracker crumbs", "6 tbsp butter"]}) + "\n")
    cli("recipes-import", "--file", tmp_path / "r.jsonl")
    con = sqlite3.connect(dbpath)
    con.execute("UPDATE bulk_recipes SET protein_type = 'pork', cuisine_group = 'american', dish_group = 'main'")
    con.commit()
    dry = json.loads(cli("recipes-relabel", "--dry-run").stdout)
    assert dry["changed"] == 2 and dry["moves"]["protein_type"] == {"pork -> chicken": 1, "pork -> vegetarian": 1}
    assert con.execute("SELECT COUNT(*) FROM bulk_recipes WHERE protein_type = 'pork'").fetchone()[0] == 2
    out = json.loads(cli("recipes-relabel").stdout)
    assert out["changed"] == 2 and out["after"]["dish_group"] == {"soup": 1, "baked": 1}
    rows = dict(con.execute("SELECT title, protein_type || '/' || coalesce(cuisine_group, 'none') FROM bulk_recipes"))
    assert rows == {"Basic Stock For Soups": "chicken/none", "Graham Cracker Crust": "vegetarian/none"}
    assert json.loads(cli("recipes-relabel").stdout)["changed"] == 0
    con.close()
