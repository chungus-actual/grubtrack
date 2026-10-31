"""Deterministic shelf-life estimates for pantry rows without an explicit date.

Off until a household turns them on (`mode`), and turning them on only affects what is logged
from then on. Rules live in SQLite so a household can inspect and override them. Seed rows are
inserted only when their stable id is absent; a custom rule with a higher priority wins without
modifying application code.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from .taxonomy import classify


SETTING = "expirationEstimates"

DEFAULT_RULES = (
    # Exact produce rules first. Days sit at the top of the typical range across USDA FoodKeeper,
    # FDA and university extension charts, counting charts that copy each other once.
    ("banana-counter", "banana", None, "counter", 4),
    ("banana-fridge", "banana", None, "fridge", 3),
    ("banana-any", "banana", None, "*", 4),
    ("avocado-counter", "avocado", None, "counter", 4),
    ("avocado-fridge", "avocado", None, "fridge", 5),
    ("lettuce-fridge", "lettuce", None, "fridge", 7),
    ("spring-mix-fridge", "spring mix", None, "fridge", 4),
    ("spinach-fridge", "spinach", None, "fridge", 5),
    ("arugula-fridge", "arugula", None, "fridge", 4),
    ("kale-fridge", "kale", None, "fridge", 7),
    ("strawberry-fridge", "strawberry", None, "fridge", 3),
    ("blueberry-fridge", "blueberry", None, "fridge", 7),
    ("raspberry-fridge", "raspberry", None, "fridge", 3),
    ("blackberry-fridge", "blackberry", None, "fridge", 3),
    ("apple-fridge", "apple", None, "fridge", 30),
    ("apple-counter", "apple", None, "counter", 7),
    ("mushroom-fridge", "mushroom", None, "fridge", 5),
    ("asparagus-fridge", "asparagus", None, "fridge", 3),
    ("broccoli-fridge", "broccoli", None, "fridge", 5),
    ("cauliflower-fridge", "cauliflower", None, "fridge", 6),
    ("cucumber-fridge", "cucumber", None, "fridge", 7),
    ("tomato-counter", "tomato", None, "counter", 5),
    ("tomato-fridge", "tomato", None, "fridge", 3),
    ("bagel-any", "bagel", None, "*", 5),
    # Salsa sits between jarred (1 month) and fresh (3-7 days): the taxonomy can't tell them apart.
    ("olive-fridge", "olive", None, "fridge", 14),
    ("mayo-fridge", "mayo", None, "fridge", 60),
    ("potato-counter", "potato", None, "counter", 21),
    ("salsa-fridge", "salsa", None, "fridge", 14),
    ("butter-fridge", "butter", None, "fridge", 30),
    ("sour-cream-fridge", "sour cream", None, "fridge", 14),
    ("pasta-sauce-fridge", "pasta sauce", None, "fridge", 5),
    ("pesto-fridge", "pesto", None, "fridge", 5),
    ("pizza-sauce-fridge", "pizza sauce", None, "fridge", 5),
    ("tomato-paste-fridge", "tomato paste", None, "fridge", 7),
    ("tomato-sauce-fridge", "tomato sauce", None, "fridge", 5),
    ("garlic-counter", "garlic", None, "counter", 90),
    ("ginger-counter", "ginger", None, "counter", 7),
    ("ginger-pantry", "ginger", None, "pantry", 7),

    # Category rules cover the rest of the existing taxonomy.
    ("produce-greens-fridge", None, "produce.greens", "fridge", 5),
    ("produce-saladkits-fridge", None, "produce.saladkits", "fridge", 4),
    ("produce-herbs-fridge", None, "produce.herbs", "fridge", 7),
    ("produce-fruit-fridge", None, "produce.fruit", "fridge", 7),
    ("produce-fruit-counter", None, "produce.fruit", "counter", 5),
    ("produce-vegetables-fridge", None, "produce.vegetables", "fridge", 7),
    ("produce-vegetables-counter", None, "produce.vegetables", "counter", 7),
    ("produce-aromatics-fridge", None, "produce.aromatics", "fridge", 14),
    ("produce-aromatics-pantry", None, "produce.aromatics", "pantry", 30),
    ("produce-aromatics-counter", None, "produce.aromatics", "counter", 14),
    ("ground-beef-fridge", "ground beef", None, "fridge", 2),
    ("ground-chicken-fridge", "ground chicken", None, "fridge", 2),
    ("ground-turkey-fridge", "ground turkey", None, "fridge", 2),
    ("meat-poultry-fridge", None, "meat.poultry", "fridge", 2),
    ("meat-beef-fridge", None, "meat.beef", "fridge", 3),
    ("meat-pork-fridge", None, "meat.pork", "fridge", 3),
    ("meat-sausage-fridge", None, "meat.sausage", "fridge", 2),
    ("meat-seafood-fridge", None, "meat.seafood", "fridge", 2),
    ("meat-plant-fridge", None, "meat.plant", "fridge", 5),
    ("meat-freezer", None, "meat.*", "freezer", 180),
    ("deli-fridge", None, "deli.*", "fridge", 4),
    ("dairy-milk-fridge", None, "dairy.milk", "fridge", 7),
    ("dairy-plantmilk-fridge", None, "dairy.plantmilk", "fridge", 7),
    ("dairy-yogurt-fridge", None, "dairy.yogurt", "fridge", 10),
    ("cottage-cheese-fridge", "cottage cheese", None, "fridge", 7),
    ("ricotta-fridge", "ricotta", None, "fridge", 7),
    ("cream-cheese-fridge", "cream cheese", None, "fridge", 14),
    ("fresh-mozzarella-fridge", "mozzarella", None, "fridge", 7),
    ("dairy-cheese-fridge", None, "dairy.cheese", "fridge", 30),
    ("dairy-eggs-fridge", None, "dairy.eggs", "fridge", 28),
    ("dairy-creamer-fridge", None, "dairy.creamer", "fridge", 21),
    ("bakery-counter", None, "bakery.*", "counter", 5),
    ("bakery-fridge", None, "bakery.*", "fridge", 10),
    ("bakery-freezer", None, "bakery.*", "freezer", 90),
    ("frozen-freezer", None, "frozen.*", "freezer", 180),
    ("pantry-pantry", None, "pantry.*", "pantry", 365),
    ("pantry-cupboard", None, "pantry.*", "cupboard", 365),
    ("pantry-condiments-fridge", None, "pantry.condiments", "fridge", 90),
    ("pantry-spreads-fridge", None, "pantry.spreads", "fridge", 180),
    ("pantry-pickles-fridge", None, "pantry.pickles", "fridge", 30),
    ("pantry-sauces-fridge", None, "pantry.sauces", "fridge", 60),
    ("snacks-pantry", None, "snacks.*", "pantry", 90),
    ("drinks-fridge", None, "drinks.*", "fridge", 14),
    ("drinks-pantry", None, "drinks.*", "pantry", 180),

    # A date would be actively misleading for these categories.
    ("bar-no-estimate", None, "bar.*", "*", 0, 0, 1),
    ("household-no-estimate", None, "household.*", "*", 0, 0, 1),
    ("spices-no-estimate", None, "pantry.spices", "*", 0, 0, 1),

    # Total deterministic fallback: every new item receives an estimate.
    ("fallback-fridge", None, None, "fridge", 4),
    ("fallback-freezer", None, None, "freezer", 180),
    ("fallback-counter", None, None, "counter", 7),
    ("fallback-pantry", None, None, "pantry", 365),
    ("fallback-cupboard", None, None, "cupboard", 365),
    ("fallback-any", None, None, "*", 30),
)


REVISED = (
    # id, what v3/v4 seeded, what the sources support
    ("banana-counter", 5, 4),
    ("banana-fridge", 7, 3),
    ("banana-any", 5, 4),
    ("avocado-fridge", 7, 5),
    ("spring-mix-fridge", 5, 4),
    ("spinach-fridge", 7, 5),
    ("arugula-fridge", 5, 4),
    ("strawberry-fridge", 5, 3),
    ("raspberry-fridge", 4, 3),
    ("blackberry-fridge", 4, 3),
    ("apple-fridge", 21, 30),
    ("mushroom-fridge", 7, 5),
    ("asparagus-fridge", 5, 3),
    ("broccoli-fridge", 7, 5),
    ("cauliflower-fridge", 7, 6),
    ("tomato-counter", 7, 5),
    ("tomato-fridge", 7, 3),
    ("bagel-any", 7, 5),
    ("pasta-sauce-fridge", 4, 5),
    ("pesto-fridge", 3, 5),
    ("garlic-counter", 30, 90),
    ("ginger-counter", 2, 7),
    ("ginger-pantry", 2, 7),
    ("produce-greens-fridge", 7, 5),
    ("produce-saladkits-fridge", 5, 4),
    ("produce-fruit-fridge", 10, 7),
    ("produce-vegetables-fridge", 10, 7),
    ("dairy-yogurt-fridge", 14, 10),
    ("dairy-creamer-fridge", 14, 21),
    ("bakery-counter", 7, 5),
    ("bakery-fridge", 14, 10),
    ("pantry-condiments-fridge", 60, 90),
    ("pantry-spreads-fridge", 60, 180),
    ("pantry-pickles-fridge", 14, 30),
    ("drinks-fridge", 30, 14),
)


def revise(con) -> None:
    """Move seeded rules to new numbers. A rule someone edited no longer holds the old value and
    stays as it is. Only the rules change: item dates move only through `expiration-refresh`."""
    con.executemany(
        "UPDATE expiration_rules SET days = ? WHERE id = ? AND source = 'built-in' AND days = ?",
        [(new, rule, old) for rule, old, new in REVISED],
    )


def mode(con) -> str | None:
    """'on', 'off', or None while the household hasn't been asked. Only 'on' estimates."""
    try:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (SETTING,)).fetchone()
    except sqlite3.OperationalError:  # the migration was deferred: no settings table yet
        return None
    return row[0] if row else None


def set_mode(con, value: str) -> None:
    """Only the switch: rows already logged keep their dates, or their lack of one."""
    if value not in ("on", "off"):
        raise ValueError("expiration estimates are on or off")
    con.execute(
        """INSERT INTO settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (SETTING, value),
    )


def seed(con) -> None:
    """Install missing built-in rules without overwriting household edits."""
    dated = [rule for rule in DEFAULT_RULES if len(rule) == 5]
    undated = [rule for rule in DEFAULT_RULES if len(rule) == 7]
    con.executemany(
        """INSERT OR IGNORE INTO expiration_rules
           (id, generic, category, storage, days, priority, enabled, source, noEstimate)
           VALUES (?, ?, ?, ?, ?, 0, 1, 'built-in', 0)""",
        dated,
    )
    con.executemany(
        """INSERT OR IGNORE INTO expiration_rules
           (id, generic, category, storage, days, priority, enabled, source, noEstimate)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'built-in', 1)""",
        undated,
    )


def _category_matches(rule: str | None, category: str) -> bool:
    if not rule:
        return True
    if rule.endswith(".*"):
        return category.startswith(rule[:-1])
    return rule == category


def _base_date(value: str | None, today: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return today


def estimate(con, obj: dict, *, today: dt.date | None = None) -> dict:
    """Return an expiration estimate and its rule for one item.

    Selection is deterministic: enabled matching rules sort by explicit
    priority, then generic/category/storage specificity, then stable rule id.
    """
    placed = classify(
        name=obj.get("name"),
        brand=obj.get("brand"),
        category=obj.get("category"),
        subCategory=obj.get("subCategory"),
        spiritType=obj.get("spiritType"),
        storage=obj.get("storage"),
    )
    generic = placed.get("generic")
    category = placed.get("cat") or "other.other"
    storage = str(obj.get("storage") or "").strip().lower()
    candidates = []
    for row in con.execute(
        """SELECT id, generic, category, storage, days, priority, noEstimate
           FROM expiration_rules WHERE enabled = 1"""
    ).fetchall():
        rule = dict(row)
        if rule["generic"] and rule["generic"] != generic:
            continue
        if not _category_matches(rule["category"], category):
            continue
        if rule["storage"] not in ("*", storage):
            continue
        score = (
            int(rule["priority"] or 0),
            1 if rule["generic"] else 0,
            2 if rule["category"] and not rule["category"].endswith(".*") else
            (1 if rule["category"] else 0),
            1 if rule["storage"] != "*" else 0,
        )
        candidates.append((score, str(rule["id"]), rule))
    if not candidates:  # defensive: the built-in wildcard normally makes this impossible
        return {}
    _, _, selected = max(candidates, key=lambda value: (value[0], value[1]))
    if selected["noEstimate"]:
        return {
            "expirationDate": None,
            "expirationSource": "not_applicable",
            "expirationRuleId": selected["id"],
            "category": category,
            "generic": generic,
        }
    base = _base_date(obj.get("purchaseDate") or obj.get("createdAt"), today or dt.date.today())
    return {
        "expirationDate": (base + dt.timedelta(days=int(selected["days"]))).isoformat(),
        "expirationSource": "estimated",
        "expirationRuleId": selected["id"],
        "category": category,
        "generic": generic,
    }


def refresh_plan(con) -> list[dict]:
    """Preview rule changes for active estimates without writing anything."""
    changes = []
    rows = con.execute(
        """SELECT * FROM items
           WHERE deletedAt IS NULL AND expirationSource = 'estimated'"""
    ).fetchall()
    for row in rows:
        result = estimate(con, dict(row))
        if not result:
            continue
        if (result.get("expirationDate"), result.get("expirationSource"), result.get("expirationRuleId")) == (
            row["expirationDate"], row["expirationSource"], row["expirationRuleId"]
        ):
            continue
        changes.append({
            "id": row["id"],
            "name": row["name"],
            "oldDate": row["expirationDate"],
            "newDate": result.get("expirationDate"),
            "oldSource": row["expirationSource"],
            "newSource": result.get("expirationSource"),
            "oldRuleId": row["expirationRuleId"],
            "newRuleId": result.get("expirationRuleId"),
        })
    return changes


def refresh_estimates(con) -> list[dict]:
    """Apply a previously previewable refresh to active generated estimates."""
    changes = refresh_plan(con)
    for change in changes:
        con.execute(
            """UPDATE items
               SET expirationDate = ?, expirationSource = ?, expirationRuleId = ?
               WHERE id = ?""",
            (change["newDate"], change["newSource"], change["newRuleId"], change["id"]),
        )
    return changes
