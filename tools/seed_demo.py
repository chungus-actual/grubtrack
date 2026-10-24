#!/usr/bin/env python3
"""Fill an empty food.db with six months of a made-up household, for trying GrubTrack out.

  python3 tools/seed_demo.py --db /tmp/demo/food.db --recipes /tmp/demo/recipes

Three fictional stores, weekly trips, prices that drift (eggs jump, apples go up in the fall), sales,
a pantry with things past their date, a shopping list and six recipes. Everything goes in the way
real data does: items are logged as they're unpacked, then the receipt is recorded and linked.
Deterministic, and it refuses to touch a database that already has items in it (--force overrides).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skill" / "food-inventory" / "scripts"

STORES = {
    "value": "ValuMart #88, 400 Commerce Way, Maple Falls, OR 97031",
    "field": "Fieldstone Market #214, 1180 Harbor Rd, Maple Falls, OR 97031",
    "coop": "Corner Co-op, 52 Elm St, Maple Falls, OR 97031",
}
PRICE_LEVEL = {"value": 0.92, "field": 1.08, "coop": 1.16}
SALE_ODDS = {"value": 0.08, "field": 0.2, "coop": 0.1}
TAX = 0.08  # household and alcohol only

# key: (name, brand, raw, section, price, size, cadence days, storage, shelf days, stores, kind)
# kind: "ea" one per line, "n<k>" k per line, "lb<w>" weighed (price is per lb), "x" receipt-only (not pantry)
P = {
    "bananas": ("Bananas", None, "BANANAS", "PRODUCE", 0.62, None, 7, "counter", 6, "vfc", "lb2.4"),
    "apples": ("Honeycrisp apples", None, "APPLES HONEYCRISP", "PRODUCE", 2.29, None, 10, "fridge", 25, "vfc", "lb2.8"),
    "avocados": ("Hass avocados", None, "AVOCADOS HASS", "PRODUCE", 1.29, None, 9, "counter", 5, "vfc", "n3"),
    "spinach": ("Baby spinach", "Earthbound Farm", "EB ORG BABY SPINACH", "PRODUCE", 4.99, "5 oz", 9, "fridge", 6, "vfc", "ea"),
    "lemons": ("Lemons", None, "LEMONS", "PRODUCE", 0.79, None, 14, "fridge", 21, "vfc", "n3"),
    "onions": ("Yellow onions", None, "ONIONS YELLOW 3LB", "PRODUCE", 3.49, "3 lb", 21, "pantry", 45, "vf", "ea"),
    "garlic": ("Garlic", None, "GARLIC BULK", "PRODUCE", 0.69, None, 18, "pantry", 30, "vfc", "n2"),
    "tomatoes": ("Roma tomatoes", None, "TOMATOES ROMA", "PRODUCE", 1.79, None, 10, "counter", 6, "vf", "lb1.4"),
    "carrots": ("Carrots", None, "CARROTS 2LB", "PRODUCE", 2.29, "2 lb", 16, "fridge", 28, "vf", "ea"),
    "peppers": ("Red bell peppers", None, "PEPPERS RED BELL", "PRODUCE", 1.49, None, 12, "fridge", 10, "vfc", "n2"),
    "cilantro": ("Cilantro", None, "CILANTRO", "PRODUCE", 0.99, None, 14, "fridge", 7, "vfc", "ea"),
    "potatoes": ("Russet potatoes", None, "POTATOES RUSSET 5LB", "PRODUCE", 4.99, "5 lb", 26, "pantry", 40, "vf", "ea"),
    "milk": ("Fairlife 2% milk", "Fairlife", "FAIRLIFE 2% 52OZ", "DAIRY", 4.79, "52 fl oz", 7, "fridge", 14, "vf", "ea"),
    "eggs": ("Large eggs", "Vital Farms", "VITAL FARMS LG EGGS", "DAIRY", 6.49, "dozen", 9, "fridge", 28, "vfc", "ea"),
    "yogurt": ("Greek yogurt", "Fage", "FAGE TOTAL 2% 32OZ", "DAIRY", 6.49, "32 oz", 10, "fridge", 21, "vfc", "ea"),
    "butter": ("Unsalted butter", "Kerrygold", "KERRYGOLD UNSLTD", "DAIRY", 4.99, "8 oz", 21, "fridge", 60, "vfc", "ea"),
    "cheddar": ("Sharp cheddar", "Tillamook", "TLMK SHARP CHED 8OZ", "DAIRY", 4.49, "8 oz", 18, "fridge", 40, "vf", "ea"),
    "parm": ("Parmigiano Reggiano", None, "PARM REGG WEDGE", "DAIRY", 8.99, "6 oz", 35, "fridge", 60, "fc", "ea"),
    "oatmilk": ("Oat milk", "Oatly", "OATLY OATMILK 64OZ", "DAIRY", 5.49, "64 fl oz", 12, "fridge", 10, "vfc", "ea"),
    "sourcream": ("Sour cream", "Daisy", "DAISY SOUR CREAM 16OZ", "DAIRY", 2.99, "16 oz", 21, "fridge", 21, "vf", "ea"),
    "thighs": ("Boneless chicken thighs", None, "CHKN THIGH BNLS", "MEAT", 4.49, None, 9, "fridge", 3, "vf", "lb1.6"),
    "beef": ("Ground beef 85/15", None, "GRND BEEF 85/15 1LB", "MEAT", 6.99, "1 lb", 12, "fridge", 3, "vf", "ea"),
    "salmon": ("Atlantic salmon fillet", None, "SALMON ATL FILLET", "SEAFOOD", 11.99, None, 20, "fridge", 2, "fc", "lb1.1"),
    "bacon": ("Thick-cut bacon", "Wright", "WRIGHT THICK BACON", "MEAT", 8.49, "24 oz", 25, "fridge", 14, "vf", "ea"),
    "sourdough": ("Sourdough loaf", None, "SOURDOUGH ROUND", "BAKERY", 5.99, None, 8, "counter", 5, "fc", "ea"),
    "tortillas": ("Flour tortillas", "Mission", "MISSION FLOUR TORT", "BAKERY", 3.79, "10 ct", 14, "pantry", 21, "vf", "ea"),
    "coffee": ("Whole bean coffee", "Stumptown", "STUMPTOWN HOLLER MTN", "GROCERY", 17.99, "12 oz", 18, "pantry", 120, "fc", "ea"),
    "rice": ("Jasmine rice", "Mahatma", "MAHATMA JASMINE 5LB", "GROCERY", 7.49, "5 lb", 45, "pantry", 365, "vf", "ea"),
    "pasta": ("Spaghetti", "Barilla", "BARILLA SPAGHETTI", "GROCERY", 1.99, "16 oz", 14, "pantry", 600, "vf", "n2"),
    "oil": ("Extra virgin olive oil", "California Olive Ranch", "COR EVOO 500ML", "GROCERY", 11.99, "500 ml", 45, "pantry", 365, "vfc", "ea"),
    "beans": ("Black beans", "Bush's", "BUSHS BLACK BEANS", "GROCERY", 1.39, "15 oz", 21, "pantry", 700, "vf", "n3"),
    "crushed": ("Crushed tomatoes", "Cento", "CENTO CRUSHED TOM", "GROCERY", 3.49, "28 oz", 21, "pantry", 700, "vf", "ea"),
    "pb": ("Peanut butter", "Smucker's", "SMUCKERS NAT PB", "GROCERY", 4.29, "16 oz", 30, "pantry", 120, "vf", "ea"),
    "broth": ("Chicken broth", "Swanson", "SWANSON CHKN BROTH 32OZ", "GROCERY", 2.99, "32 fl oz", 21, "pantry", 300, "vf", "ea"),
    "soy": ("Soy sauce", "Kikkoman", "KIKKOMAN SOY SAUCE", "GROCERY", 3.99, "15 fl oz", 60, "pantry", 500, "vf", "ea"),
    "oats": ("Rolled oats", "Bob's Red Mill", "BRM ROLLED OATS", "GROCERY", 5.99, "32 oz", 30, "pantry", 300, "vfc", "ea"),
    "honey": ("Honey", "Local Hive", "LOCAL HIVE HONEY", "GROCERY", 8.49, "16 oz", 50, "pantry", 700, "fc", "ea"),
    "salsa": ("Salsa", "Frontera", "FRONTERA SALSA", "GROCERY", 4.29, "16 oz", 16, "fridge", 30, "vf", "ea"),
    "berries": ("Frozen mixed berries", None, "MIXED BERRIES 48OZ", "FROZEN", 9.99, "48 oz", 21, "freezer", 240, "v", "ea"),
    "waffles": ("Power waffles", "Kodiak", "KODIAK POWER WAFFLES", "FROZEN", 5.49, "10.7 oz", 18, "freezer", 200, "vf", "ea"),
    "icecream": ("Ice cream", "Tillamook", "TLMK ICE CREAM 48OZ", "FROZEN", 5.99, "48 oz", 25, "freezer", 120, "vf", "ea"),
    "peas": ("Frozen peas", None, "PEAS FROZEN 12OZ", "FROZEN", 1.99, "12 oz", 28, "freezer", 300, "vf", "ea"),
    "chips": ("Tortilla chips", "Late July", "LATE JULY CHIPS", "SNACKS", 4.49, "11 oz", 14, "pantry", 60, "vf", "ea"),
    "chocolate": ("Dark chocolate bar", "Lindt", "LINDT 85% BAR", "SNACKS", 3.49, "3.5 oz", 21, "pantry", 300, "vfc", "ea"),
    "seltzer": ("Sparkling water 12-pack", "LaCroix", "LACROIX 12PK", "BEVERAGES", 6.49, "12 ct", 10, "pantry", 300, "vf", "ea"),
    "oj": ("Orange juice", "Tropicana", "TROP OJ NO PULP 52OZ", "BEVERAGES", 4.99, "52 fl oz", 10, "fridge", 10, "vf", "ea"),
    "towels": ("Paper towels", "Bounty", "BOUNTY 6 DBL ROLL", "HOUSEHOLD", 11.99, "6 rolls", 30, None, 0, "v", "x"),
    "soap": ("Dish soap", "Dawn", "DAWN ULTRA 19OZ", "HOUSEHOLD", 3.99, "19 fl oz", 45, None, 0, "vf", "x"),
    "bags": ("Trash bags", "Glad", "GLAD 13GAL 40CT", "HOUSEHOLD", 9.49, "40 ct", 40, None, 0, "v", "x"),
    "wine": ("Pinot noir", "Meiomi", "MEIOMI PINOT NOIR", "WINE", 18.99, "750 ml", 21, "cupboard", 900, "vf", "ea"),
    "beer": ("IPA 6-pack", "Sierra Nevada", "SIERRA NEVADA 6PK", "BEER", 10.99, "6 ct", 14, "fridge", 120, "vf", "ea"),
}
STORE_CODE = {"v": "value", "f": "field", "c": "coop"}
TAXED_SECTIONS = {"HOUSEHOLD", "WINE", "BEER"}
QUICK_TRIP = {"PRODUCE", "DAIRY", "MEAT", "SEAFOOD", "BAKERY"}

RECIPES = {
    "Dinner/Chicken Thigh Tacos.md": ("Mexican", "15 min", "20 min", "4", 9, [
        "1.5 lb boneless chicken thighs", "8 flour tortillas", "2 avocados, sliced", "1 bunch cilantro, chopped",
        "1 lemon", "1 yellow onion, diced", "1 cup salsa", "sour cream"],
        "1. Season the chicken with salt, cumin and chili powder.\n2. Sear 6 minutes a side, rest, slice.\n"
        "3. Warm the tortillas and fill with chicken, avocado, onion and cilantro. Lemon, salsa and sour cream on top."),
    "Dinner/Weeknight Fried Rice.md": ("Chinese", "10 min", "15 min", "3", 8, [
        "3 cups cooked jasmine rice, a day old", "3 eggs", "1 cup frozen peas", "2 carrots, diced",
        "3 cloves garlic, minced", "3 tbsp soy sauce", "2 green onions, sliced"],
        "1. Scramble the eggs in a hot pan and set aside.\n2. Fry the carrots and garlic, add the rice and peas.\n"
        "3. Soy sauce, the eggs back in, green onions to finish."),
    "Dinner/Spaghetti Pomodoro.md": ("Italian", "5 min", "25 min", "4", 9, [
        "1 lb spaghetti", "28 oz crushed tomatoes", "4 cloves garlic, sliced", "1/4 cup extra virgin olive oil",
        "1 oz parmigiano reggiano, grated", "fresh basil"],
        "1. Warm the garlic in the oil, add the tomatoes and simmer 20 minutes.\n2. Cook the pasta, toss with the sauce.\n"
        "3. Parmesan and basil on top."),
    "Breakfast/Overnight Oats.md": ("American", "5 min", "", "2", 7, [
        "1 cup rolled oats", "1 cup oat milk", "1/2 cup greek yogurt", "1 cup frozen mixed berries", "1 tbsp honey"],
        "1. Stir everything together in a jar.\n2. Refrigerate overnight."),
    "Dinner/Salmon with Roasted Potatoes.md": ("American", "10 min", "35 min", "2", 8, [
        "1 lb salmon fillet", "1.5 lb russet potatoes, cubed", "1 lemon", "2 tbsp olive oil", "2 cloves garlic"],
        "1. Roast the potatoes with oil and garlic at 425°F for 20 minutes.\n2. Add the salmon, roast 12 minutes more.\n"
        "3. Lemon over everything."),
    "Lunch/Black Bean Quesadillas.md": ("Mexican", "10 min", "10 min", "2", 7, [
        "4 flour tortillas", "1 can black beans, drained", "1 cup sharp cheddar, shredded", "1/2 cup salsa",
        "1 red bell pepper, diced", "sour cream"],
        "1. Fill the tortillas with beans, cheese and pepper.\n2. Toast in a dry pan until the cheese melts.\n"
        "3. Salsa and sour cream on the side."),
}


def money(x: float) -> float:
    """Shelf prices end in 9: 4.83 -> 4.79, 1.31 -> 1.29."""
    x = max(0.39, x)
    return round(int(x * 10) / 10 + 0.09, 2)


class World:
    def __init__(self, today: dt.date, days: int, seed: int = 7):
        self.r = random.Random(seed)
        self.today = today
        self.start = today - dt.timedelta(days=days)
        self.days = days
        # each store prices each product a little differently, and keeps it
        self.skew = {(k, s): self.r.uniform(-0.05, 0.05) for k in P for s in STORES}

    def shelf(self, key: str, store: str, day: dt.date) -> float:
        name, brand, raw, sec, base, *_ = P[key]
        months = (day - self.start).days / 30.4
        price = base * PRICE_LEVEL[store] * (1 + self.skew[(key, store)]) * (1 + 0.004 * months)
        if key == "eggs" and (self.today - day).days < 75:
            price *= 1.16          # the egg jump
        if key == "coffee" and (self.today - day).days < 50:
            price *= 1.08
        if key == "apples" and (self.today - day).days < 40:
            price *= 1.15          # fall pricing
        if key == "avocados" and (self.today - day).days < 60:
            price *= 0.88
        return money(price)


def trips(w: World) -> list[tuple[dt.date, str]]:
    out = []
    d = w.start + dt.timedelta(days=(6 - w.start.weekday()) % 7)   # Sundays: the big ValuMart run
    while d < w.today:
        out.append((d, "value"))
        d += dt.timedelta(days=7)
    d = w.start + dt.timedelta(days=3)                               # Fieldstone, every ten days or so
    while d < w.today:
        out.append((d, "field"))
        d += dt.timedelta(days=w.r.choice((9, 10, 11)))
    d = w.start + dt.timedelta(days=12)                              # the co-op, now and then
    while d < w.today:
        out.append((d, "coop"))
        d += dt.timedelta(days=w.r.choice((24, 28, 31)))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", help="food.db to fill (default: the CLI's own lookup)")
    ap.add_argument("--recipes", help="folder for the sample markdown recipes (default: recipes/ beside food.db)")
    ap.add_argument("--today", help="pretend today is YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=182)
    ap.add_argument("--force", action="store_true", help="seed even if the database already has items")
    a = ap.parse_args()
    if a.db:
        os.environ["FOODDB_PATH"] = str(Path(a.db).expanduser().resolve())
    sys.path.insert(0, str(SCRIPTS))
    import fooddb_sqlite as fdb
    from foodcore import ledger

    con = fdb.connect()
    fdb.init_db(con)
    if con.execute("SELECT COUNT(*) FROM items").fetchone()[0] and not a.force:
        print(f"{fdb.DB_PATH} already has items; refusing to mix demo data in (--force to do it anyway)", file=sys.stderr)
        return 1

    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    w = World(today, a.days)
    due = {k: w.start + dt.timedelta(days=w.r.randint(0, P[k][6])) for k in P}
    live: dict[str, tuple[str, dt.date]] = {}     # product key -> (item id, the day it runs out)
    n_trips = n_lines = 0

    def stamp(day: dt.date, hour: int) -> str:
        return dt.datetime.combine(day, dt.time(hour, w.r.randint(0, 59))).astimezone().isoformat(timespec="seconds")

    def use_up(until: dt.date):
        for key, (iid, gone) in list(live.items()):
            if gone > until:
                continue
            tossed = w.r.random() < (0.22 if P[key][3] in ("PRODUCE", "BAKERY") else 0.06) or gone - shelf_end[iid] > dt.timedelta(days=3)
            fdb.delete_item(con, iid, note="tossed" if tossed else "eaten")
            at = stamp(gone, 20)
            con.execute("UPDATE items SET deletedAt = ?, updatedAt = ? WHERE id = ?", (at, at, iid))
            con.commit()
            del live[key]

    shelf_end: dict[str, dt.date] = {}
    for day, store in trips(w):
        use_up(day)
        code = next(c for c, s in STORE_CODE.items() if s == store)
        quick = store != "value"
        want = [k for k in P if due[k] <= day + dt.timedelta(days=2) and code in P[k][9]
                and (not quick or P[k][3] in QUICK_TRIP or w.r.random() < 0.15)]
        want += [k for k in w.r.sample(sorted(P), 2) if k not in want and code in P[k][9] and w.r.random() < 0.5]
        if not want:
            continue
        lines, unpacked = [], stamp(day, 18)
        for key in want:
            name, brand, raw, sec, _, size, cadence, storage, shelf, _, kind = P[key]
            per = w.shelf(key, store, day)
            line = {"raw": raw, "name": name, "section": sec, "taxed": sec in TAXED_SECTIONS}
            if brand:
                line["brand"] = brand
            if size:
                line["size"] = size
            if kind.startswith("lb"):
                wt = round(float(kind[2:]) * w.r.uniform(0.8, 1.25), 2)
                line.update(weightLb=wt, perLb=per, price=round(wt * per, 2))
            else:
                qty = int(kind[1:]) if kind.startswith("n") else 1
                line["price"] = round(per * qty, 2)
                if qty > 1:
                    line["qty"] = qty
                if w.r.random() < SALE_ODDS[store]:
                    line["regular"] = line["price"]
                    line["price"] = round(money(per * w.r.uniform(0.72, 0.85)) * qty, 2)
            line["code"] = f"{zlib.crc32(f'{raw}|{store}'.encode()) % 10**10:010d}"
            if kind != "x":
                iid = fdb.add_item(con, {"name": name, "brand": brand, "storage": storage, "size": size,
                                         "quantity": line.get("qty", 1), "purchaseDate": day.isoformat(),
                                         "location": "bar" if sec in ("WINE",) else None,
                                         "expirationDate": (day + dt.timedelta(days=shelf + w.r.randint(-1, 2))).isoformat(),
                                         "createdAt": unpacked})
                con.execute("UPDATE items SET updatedAt = ? WHERE id = ?", (unpacked, iid))
                con.commit()
                line["itemId"] = iid
                shelf_end[iid] = day + dt.timedelta(days=shelf)
                live[key] = (iid, day + dt.timedelta(days=round(cadence * w.r.uniform(0.75, 1.15))))
            lines.append(line)
            due[key] = day + dt.timedelta(days=round(cadence * w.r.uniform(0.85, 1.2)))
        tax = round(sum(l["price"] for l in lines if l["taxed"]) * TAX, 2)
        total = round(sum(l["price"] for l in lines) + tax, 2)
        savings = round(sum(l.get("regular", l["price"]) - l["price"] for l in lines), 2) or None
        ledger.import_receipt(con, {"store": STORES[store], "date": day.isoformat(), "tax": tax, "total": total,
                                    "savings": savings, "payment": "Visa 4242", "lines": lines}, autolink=False)
        n_trips += 1
        n_lines += len(lines)
    use_up(today - dt.timedelta(days=1))

    for name, qty, notes in (("Large eggs", None, None), ("Bananas", None, None), ("Greek yogurt", 2, None),
                             ("Cilantro", None, "for tacos"), ("Paper towels", None, None), ("Birthday candles", None, None)):
        fdb.shop_add(con, name, quantity=qty, notes=notes, by="demo")
    for name in ("Lemons", "Coffee"):
        row = fdb.shop_add(con, name, by="demo")
        fdb.shop_set_done(con, row["id"], True)
    ledger.sweep(con)

    recipes = Path(a.recipes).expanduser() if a.recipes else Path(fdb.DB_PATH).parent / "recipes"
    for rel, (cuisine, prep, cook, serves, rating, ings, steps) in RECIPES.items():
        f = recipes / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            f.write_text("\n".join(["---", "tags:", "  - recipe", f"cuisine: {cuisine}", f"prep_time: {prep}",
                                    f"cook_time: {cook}", f"servings: {serves}", f"rating: {rating}", "---", "",
                                    f"# {f.stem}", "", "## Ingredients", ""] + [f"- {i}" for i in ings]
                                   + ["", "## Instructions", "", steps, ""]), encoding="utf-8")

    stock = con.execute("SELECT COUNT(*) FROM items WHERE deletedAt IS NULL").fetchone()[0]
    print(f"{fdb.DB_PATH}: {n_trips} trips, {n_lines} receipt lines, {stock} items in the house, recipes in {recipes}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
