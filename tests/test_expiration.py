"""Deterministic expiration estimates shared by the CLI and web application."""

import datetime as dt
import json

import pytest


def _in(days):
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def estimates_on(cli):
    assert cli("expiration-estimates", "--set", "on").stdout.strip() == "on"


def _add(cli, **item):
    result = cli("add", "--json", json.dumps(item))
    return result.stdout.strip()


def _item(con, item_id):
    return dict(con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())


def test_migration_seeds_reviewable_rule_table(con):
    assert con.execute("PRAGMA user_version").fetchone()[0] == 4
    count = con.execute("SELECT count(*) FROM expiration_rules WHERE source = 'built-in'").fetchone()[0]
    assert count >= 50


def test_migration_leaves_active_legacy_rows_undated(con):
    from foodcore import schema

    con.execute(
        """INSERT INTO items (id, createdAt, updatedAt, name, storage)
           VALUES ('legacy-banana', '2026-10-30T18:00:00-06:00',
                   '2026-10-30T18:00:00-06:00', 'Bananas', 'counter')"""
    )
    con.execute("PRAGMA user_version = 2")

    assert schema.migrate(con) == schema.VERSION
    item = _item(con, "legacy-banana")
    assert item["expirationDate"] is None
    assert item["expirationSource"] is None
    assert item["expirationRuleId"] is None


def test_banana_and_lettuce_use_specific_rules(cli, con):
    banana = _item(con, _add(cli, name="Bananas", storage="counter", purchaseDate=_in(0)))
    lettuce = _item(con, _add(cli, name="Green lettuce", storage="fridge", purchaseDate=_in(0)))

    assert banana["expirationDate"] == _in(5)
    assert banana["expirationSource"] == "estimated"
    assert banana["expirationRuleId"] == "banana-counter"
    assert lettuce["expirationDate"] == _in(7)
    assert lettuce["expirationRuleId"] == "lettuce-fridge"


def test_banana_in_pantry_uses_product_rule_not_yearlong_fallback(cli, con):
    banana = _item(con, _add(
        cli, name="Bananas", storage="pantry", purchaseDate=_in(0),
    ))
    assert banana["expirationDate"] == _in(5)
    assert banana["expirationRuleId"] == "banana-any"


def test_migration_does_not_refresh_existing_estimates(con):
    from foodcore import schema

    con.execute(
        """INSERT INTO items
           (id, createdAt, updatedAt, name, storage, expirationDate,
            expirationSource, expirationRuleId)
           VALUES ('v3-banana', '2026-10-30T18:00:00-06:00',
                   '2026-10-30T18:00:00-06:00', 'Bananas', 'pantry',
                   '2027-10-30', 'estimated', 'fallback-pantry')"""
    )
    con.execute("DELETE FROM expiration_rules WHERE id = 'banana-any'")
    con.execute("PRAGMA user_version = 3")

    assert schema.migrate(con) == schema.VERSION
    item = _item(con, "v3-banana")
    assert item["expirationDate"] == "2027-10-30"
    assert item["expirationRuleId"] == "fallback-pantry"


def test_explicit_date_always_wins(cli, con):
    item = _item(con, _add(
        cli, name="Bananas", storage="counter", purchaseDate=_in(0),
        expirationDate=_in(40),
    ))
    assert item["expirationDate"] == _in(40)
    assert item["expirationSource"] == "explicit"
    assert item["expirationRuleId"] is None


def test_higher_priority_household_rule_overrides_seed(cli, con):
    con.execute(
        """INSERT INTO expiration_rules
           (id, generic, category, storage, days, priority, enabled, source)
           VALUES ('household-banana', 'banana', NULL, 'counter', 2, 100, 1, 'custom')"""
    )
    con.commit()
    item = _item(con, _add(
        cli, name="Organic bananas", storage="counter", purchaseDate=_in(0),
    ))
    assert item["expirationDate"] == _in(2)
    assert item["expirationRuleId"] == "household-banana"


def test_unknown_product_uses_storage_fallback(cli, con):
    item = _item(con, _add(
        cli, name="Unknown test product", storage="cupboard", purchaseDate=_in(-10),
    ))
    assert item["expirationDate"] == _in(355)
    assert item["expirationRuleId"] == "fallback-cupboard"


def test_high_risk_refrigerated_food_uses_conservative_rule(cli, con):
    item = _item(con, _add(
        cli, name="Ground beef", storage="fridge", purchaseDate=_in(0),
    ))
    assert item["expirationDate"] == _in(2)
    assert item["expirationRuleId"] == "ground-beef-fridge"


def test_missing_rule_logs_an_undated_item(cli, con):
    con.execute("UPDATE expiration_rules SET enabled = 0")
    con.commit()

    result = cli(
        "add", "--json",
        json.dumps({"name": "Unknown test product", "storage": "cupboard"}),
    )
    item = _item(con, result.stdout.strip())
    assert item["expirationDate"] is None
    assert item["expirationSource"] is None
    assert "logging without a date" in result.stderr


def test_undated_restock_never_replaces_an_explicit_date(cli, con):
    item_id = _add(
        cli, name="Milk", storage="fridge", purchaseDate=_in(0),
        expirationDate=_in(21),
    )
    assert _add(cli, name="Milk", storage="fridge", purchaseDate=_in(0)) == item_id
    item = _item(con, item_id)
    assert item["expirationDate"] == _in(21)
    assert item["expirationSource"] == "explicit"
    assert item["expirationRuleId"] is None


def test_explicit_restock_replaces_an_estimate(cli, con):
    item_id = _add(cli, name="Milk", storage="fridge", purchaseDate=_in(0))
    assert _add(
        cli, name="Milk", storage="fridge", purchaseDate=_in(0),
        expirationDate=_in(21),
    ) == item_id
    item = _item(con, item_id)
    assert item["expirationDate"] == _in(21)
    assert item["expirationSource"] == "explicit"
    assert item["expirationRuleId"] is None


def test_nonexpiring_categories_are_labeled_without_a_fake_date(cli, con):
    spirit = _item(con, _add(cli, name="Bourbon", storage="pantry", category="bar.whiskey"))
    spice = _item(con, _add(cli, name="Black pepper", storage="pantry", category="pantry.spices"))
    for item, rule in ((spirit, "bar-no-estimate"), (spice, "spices-no-estimate")):
        assert item["expirationDate"] is None
        assert item["expirationSource"] == "not_applicable"
        assert item["expirationRuleId"] == rule


def test_rule_refresh_is_preview_only_until_explicitly_applied(cli, con):
    item_id = _add(cli, name="Blueberry bagel", storage="pantry", purchaseDate=_in(0))
    con.execute(
        "UPDATE items SET expirationDate='2027-10-30', expirationRuleId='fallback-pantry' WHERE id=?",
        (item_id,),
    )
    con.commit()

    preview = json.loads(cli("expiration-refresh").stdout)
    assert preview["applied"] is False
    assert preview["changes"] == [{
        "id": item_id, "name": "Blueberry bagel",
        "oldDate": "2027-10-30", "newDate": _in(7),
        "oldSource": "estimated", "newSource": "estimated",
        "oldRuleId": "fallback-pantry", "newRuleId": "bagel-any",
    }]
    assert _item(con, item_id)["expirationDate"] == "2027-10-30"

    applied = json.loads(cli("expiration-refresh", "--apply").stdout)
    assert applied["applied"] is True
    assert applied["count"] == 1
    assert _item(con, item_id)["expirationDate"] == _in(7)


def _unasked(con):
    con.execute("DELETE FROM settings")
    con.commit()


def test_estimates_stay_off_until_a_household_turns_them_on(cli, con):
    _unasked(con)
    assert cli("expiration-estimates").stdout.strip() == "undecided"
    item = _item(con, _add(cli, name="Bananas", storage="counter"))
    assert item["expirationDate"] is None
    assert item["expirationSource"] is None

    assert cli("expiration-estimates", "--set", "off").stdout.strip() == "off"
    assert _item(con, _add(cli, name="Lettuce", storage="fridge"))["expirationDate"] is None
    assert cli("expiration-estimates", "--set", "maybe", ok=False).returncode != 0


def test_turning_estimates_on_never_reaches_back(cli, con):
    _unasked(con)
    old = _add(cli, name="Bananas", storage="counter")
    cli("expiration-estimates", "--set", "on")

    assert _item(con, old)["expirationDate"] is None
    assert json.loads(cli("expiration-refresh").stdout)["count"] == 0
    assert _item(con, _add(cli, name="Lettuce", storage="fridge"))["expirationSource"] == "estimated"


def test_turning_estimates_off_keeps_the_ones_already_made(cli, con):
    item_id = _add(cli, name="Lettuce", storage="fridge")
    cli("expiration-estimates", "--set", "off")
    assert _item(con, item_id)["expirationDate"] == _in(7)
    assert _item(con, _add(cli, name="Bananas", storage="counter"))["expirationDate"] is None


def test_an_estimate_already_over_when_logged_is_not_applied(cli, con):
    item = _item(con, _add(cli, name="Lettuce", storage="fridge", purchaseDate=_in(-30)))
    assert item["expirationDate"] is None
    assert item["expirationSource"] is None
    assert item["expirationRuleId"] is None
    # still ahead, counted from the purchase
    assert _item(con, _add(cli, name="Bananas", storage="counter", purchaseDate=_in(-2)))["expirationDate"] == _in(3)


def test_long_keeping_fridge_items_do_not_get_the_four_day_fallback(cli, con):
    for name, storage, rule, days in (
        ("Yellow mustard", "fridge", "pantry-condiments-fridge", 60),
        ("Mayonnaise", "fridge", "pantry-condiments-fridge", 60),
        ("Dill pickles", "fridge", "pantry-pickles-fridge", 14),
        ("Olives", "fridge", "pantry-pickles-fridge", 14),
        ("Teriyaki sauce", "fridge", "pantry-sauces-fridge", 60),
        ("Marinara sauce", "fridge", "pasta-sauce-fridge", 4),
        ("Pesto", "fridge", "pesto-fridge", 3),
        ("Strawberry jam", "fridge", "pantry-spreads-fridge", 60),
        ("Salted butter", "fridge", "butter-fridge", 30),
        ("Sour cream", "fridge", "sour-cream-fridge", 14),
        ("Oat creamer", "fridge", "dairy-creamer-fridge", 14),
        ("Garlic", "counter", "garlic-counter", 30),
        ("Yellow onion", "counter", "produce-aromatics-counter", 14),
        ("Ginger", "counter", "ginger-counter", 2),
    ):
        item = _item(con, _add(cli, name=name, storage=storage))
        assert (item["expirationRuleId"], item["expirationDate"]) == (rule, _in(days)), name


def test_half_and_half_keeps_the_short_fridge_window(cli, con):
    item = _item(con, _add(cli, name="Half and half", storage="fridge"))
    assert (item["expirationRuleId"], item["expirationDate"]) == ("fallback-fridge", _in(4))


def test_a_household_rule_beats_a_no_estimate_rule(cli, con):
    con.execute(
        """INSERT INTO expiration_rules (id, generic, category, storage, days, priority, enabled, source)
           VALUES ('open-wine', NULL, 'bar.wine', 'fridge', 5, 1, 1, 'custom')"""
    )
    con.commit()
    item = _item(con, _add(cli, name="Pinot noir", storage="fridge", category="bar.wine"))
    assert (item["expirationRuleId"], item["expirationDate"]) == ("open-wine", _in(5))


def test_v3_database_retires_the_rules_the_opt_outs_replaced(con):
    from foodcore import schema

    con.execute(
        """INSERT INTO expiration_rules (id, generic, category, storage, days, priority, enabled, source)
           VALUES ('bar-any', NULL, 'bar.*', '*', 365, 0, 1, 'built-in'),
                  ('my-rule', NULL, 'bar.*', '*', 9, 100, 1, 'custom')"""
    )
    con.execute("UPDATE expiration_rules SET priority = 100 WHERE id = 'bar-no-estimate'")
    con.execute("DROP TABLE settings")
    con.execute("PRAGMA user_version = 3")

    assert schema.migrate(con) == schema.VERSION
    rules = {r["id"]: r["priority"] for r in con.execute("SELECT id, priority FROM expiration_rules")}
    assert "bar-any" not in rules
    assert rules["bar-no-estimate"] == 0
    assert rules["my-rule"] == 100
    assert con.execute("SELECT count(*) FROM settings").fetchone()[0] == 0
