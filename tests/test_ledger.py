"""The ledger's promises: prices survive restocks, corrections don't duplicate, receipts reconcile
or say so, receipt lines outrank voice-logged prices, and one store is one store."""

import json

import pytest

from foodcore import intel, ledger, schema


def add(cli, **kw):
    return cli("add", "--json", json.dumps(kw)).stdout.strip()


def purchases(con, item_id=None):
    sql = "SELECT * FROM purchases" + (" WHERE itemId = ?" if item_id else "") + " ORDER BY date, createdAt"
    return [dict(r) for r in con.execute(sql, (item_id,) if item_id else ())]


def test_migration_is_idempotent_and_versioned(con):
    assert con.execute("PRAGMA user_version").fetchone()[0] == schema.VERSION
    assert schema.migrate(con) == schema.VERSION
    cols = {r[1] for r in con.execute("PRAGMA table_info(items)")}
    assert {"price", "receiptId", "productId", "dept", "cat"} <= cols


def test_cli_add_classifies_and_records_price(cli, con):
    iid = add(cli, name="Fairlife 2% milk", storage="fridge", category="dairy", size="1.5 L", price=6.49)
    it = con.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone()
    assert (it["dept"], it["cat"]) == ("dairy", "dairy.milk") and it["productId"]
    ps = purchases(con, iid)
    assert len(ps) == 1 and ps[0]["price"] == 6.49 and ps[0]["source"] == "item"
    assert ps[0]["dim"] == "volume" and ps[0]["unitPrice"] == pytest.approx(6.49 / 1500, rel=1e-3)


def test_restock_merge_keeps_price_history(cli, con):
    """The bug this ledger exists for: `add` merges a restock into the old row (same matchKey),
    overwriting items.price. The old price must survive in the ledger."""
    con.execute("INSERT INTO receipts (id, date, store, total) VALUES ('r1', '2026-09-28', 'Albertsons #46', 10)")
    con.execute("INSERT INTO receipts (id, date, store, total) VALUES ('r2', '2026-10-21', 'Albertsons #46', 10)")
    con.commit()
    a = add(cli, name="Siggi's skyr", storage="fridge", price=7.49, receiptId="r1")
    b = add(cli, name="Siggi's skyr", storage="fridge", price=7.99, receiptId="r2")
    assert a == b  # merged into one inventory row...
    ps = purchases(con, a)
    assert [p["price"] for p in ps] == [7.49, 7.99]  # ...but both prices are on record
    assert [p["date"] for p in ps] == ["2026-09-28", "2026-10-21"]
    assert ps[0]["storeId"] == ps[1]["storeId"] is not None


def test_correction_on_same_receipt_updates_not_duplicates(cli, con):
    con.execute("INSERT INTO receipts (id, date, store, total) VALUES ('r1', '2026-09-28', 'Albertsons', 10)")
    con.commit()
    iid = add(cli, name="Lemon chicken", storage="fridge", price=8.16, receiptId="r1")
    cli("update", "--id", iid, "--set", json.dumps({"price": 6.02}))
    ps = purchases(con, iid)
    assert len(ps) == 1 and ps[0]["price"] == 6.02


def test_dollar_signs_survive_file_input(cli, con, tmp_path):
    f = tmp_path / "item.json"
    f.write_text(json.dumps({"name": "Lemons", "storage": "fridge", "notes": "$1.78 for two (Albertsons)"}))
    iid = cli("add", "--file", f).stdout.strip()
    assert con.execute("SELECT notes FROM items WHERE id = ?", (iid,)).fetchone()[0].startswith("$1.78")


def _receipt(lines, total, tax=0.0, **kw):
    return {"store": "Albertsons Store #12, 2200 W Oak Ave, Springfield, IL 62704", "date": "2026-10-21",
            "total": total, "tax": tax, "lines": lines, **kw}


def test_receipt_reconciles_and_links(cli, con, tmp_path):
    iid = add(cli, name="Eggs (dozen)", storage="fridge", category="dairy", price=4.49)  # voice guess
    f = tmp_path / "r.json"
    f.write_text(json.dumps(_receipt([
        {"raw": "PETE & GERRY EGGS", "name": "Pete & Gerry's eggs", "brand": "Pete & Gerry's", "price": 6.99,
         "regular": 7.99, "size": "dozen", "itemId": iid, "code": "81565200214"},
        {"raw": "TACO SEASONING MIX", "name": "Taco seasoning mix", "price": 1.67, "regular": 1.99},
        {"raw": "BLACK BOX CAB SAUV", "name": "Black Box Cabernet Sauvignon", "price": 4.50, "taxed": True},
    ], total=13.54, tax=0.38)))
    rep = json.loads(cli("receipt-add", "--file", f).stdout)
    assert rep["reconciliation"]["ok"] and rep["reconciliation"]["diff"] == 0
    assert rep["corrected"] == [{"itemId": iid, "item": "Eggs (dozen)", "was": 4.49, "now": 6.99}]
    it = con.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone()
    assert it["price"] == 6.99 and it["receiptId"] == rep["receiptId"]
    # the voice guess is superseded, not kept as a second "observation"
    ps = [p for p in purchases(con, iid) if not p["supersededBy"]]
    assert [(p["source"], p["price"]) for p in ps] == [("receipt", 6.99)]
    audit = con.execute("SELECT oldPrice, newPrice FROM item_price_audit WHERE itemId = ?", (iid,)).fetchall()
    assert [tuple(a) for a in audit] == [(4.49, 6.99)]
    # receipt-only lines are in the ledger but NOT in the inventory (a house rule)
    assert con.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM purchases WHERE receiptId = ?", (rep["receiptId"],)).fetchone()[0] == 3


def test_receipt_that_does_not_add_up_is_flagged(cli, con, tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(_receipt([{"raw": "X", "name": "Hummus", "price": 3.89}], total=20.00, tax=0)))
    r = cli("receipt-add", "--file", f)
    rep = json.loads(r.stdout)
    assert not rep["reconciliation"]["ok"] and rep["reconciliation"]["diff"] == 16.11
    assert "does not reconcile" in r.stderr
    assert con.execute("SELECT reconciled FROM receipts").fetchone()[0] == 0


def test_receipt_needs_prices_and_a_date(cli, tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"store": "Target", "total": 1, "lines": [{"raw": "X", "price": 1}]}))
    assert "needs a date" in cli("receipt-add", "--file", f, ok=False).stderr
    f.write_text(json.dumps(_receipt([{"raw": "X"}], total=1)))
    assert "has no price" in cli("receipt-add", "--file", f, ok=False).stderr


def test_reconciled_receipt_blocks_item_level_double_count(cli, con, tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(_receipt([{"raw": "HUMMUS", "name": "Hummus", "price": 3.89}], total=3.89, id="rx")))
    cli("receipt-add", "--file", f)
    iid = add(cli, name="Garlic hummus", storage="fridge", price=1.98, receiptId="rx")
    assert purchases(con, iid) == []  # the receipt's lines are the whole truth


def test_autolink_only_when_unambiguous(cli, con, tmp_path):
    a = add(cli, name="Organic cilantro", storage="fridge", category="produce")
    add(cli, name="Kefir (the usual)", storage="fridge", category="dairy")
    add(cli, name="Raspberry kefir", storage="fridge", category="dairy")
    today = con.execute("SELECT substr(createdAt,1,10) FROM items LIMIT 1").fetchone()[0]
    f = tmp_path / "r.json"
    f.write_text(json.dumps(_receipt([
        {"raw": "ORGANIC CILANTRO", "name": "Organic cilantro", "price": 1.69},
        {"raw": "LIFEWAY KEFIR", "name": "Lifeway kefir", "price": 6.99},  # two kefirs: leave it
    ], total=8.68, date=today)))
    rep = json.loads(cli("receipt-add", "--file", f).stdout)
    assert [x["itemId"] for x in rep["autoLinked"]] == [a]
    assert [u["name"] for u in rep["unlinkedLines"]] == ["Lifeway kefir"]


def test_store_spellings_fold_together(con):
    a = ledger.resolve_store(con, "Albertsons Store #12, 2200 W Oak Ave, Springfield, IL 62704")
    b = ledger.resolve_store(con, "Albertsons (Oak & Elm)")
    c = ledger.resolve_store(con, "Target Springfield Westgate, 3200 Westgate Dr, Springfield, IL 62711")
    d = ledger.resolve_store(con, "Sprouts (771 W Oak Ave)")
    assert a == b and len({a, c, d}) == 3
    s = con.execute("SELECT * FROM stores WHERE id = ?", (a,)).fetchone()
    assert (s["chain"], s["storeNumber"], s["postal"]) == ("Albertsons", "12", "62704")


def test_same_code_same_chain_is_one_product(cli, con, tmp_path):
    for i, (date, name) in enumerate([("2026-09-28", "Siggis yogurt"), ("2026-10-21", "Siggi's skyr 1.5 lb tub")]):
        f = tmp_path / f"r{i}.json"
        f.write_text(json.dumps(_receipt([{"raw": "SIGGIS YOGURT", "name": name, "code": "69824800141",
                                           "price": 7.49}], total=7.49, date=date)))
        cli("receipt-add", "--file", f)
    assert con.execute("SELECT COUNT(DISTINCT productId) FROM purchases").fetchone()[0] == 1


def test_brand_split_products_share_a_generic(cli, con, tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(_receipt([
        {"raw": "GG EGGS", "name": "Good & Gather eggs", "brand": "Good & Gather", "size": "dozen", "price": 2.69},
        {"raw": "PETE & GERRY EGGS", "name": "Pete & Gerry's eggs", "brand": "Pete & Gerry's", "size": "dozen",
         "price": 6.99}], total=9.68)))
    cli("receipt-add", "--file", f)
    rows = con.execute("SELECT name, generic FROM products WHERE generic = 'eggs'").fetchall()
    assert len(rows) == 2
    book = {p["name"]: p for p in intel.price_book(con)}
    assert book["Pete & Gerry's eggs"]["last"]["unitLabel"] == "egg"
    assert book["Pete & Gerry's eggs"]["last"]["unit"] == pytest.approx(0.58, abs=0.01)


def test_user_category_is_sticky(cli, con):
    iid = add(cli, name="Origin X", storage="pantry")
    pid = con.execute("SELECT productId FROM items WHERE id = ?", (iid,)).fetchone()[0]
    ledger.set_product_category(con, pid, "drinks.coffee")
    cli("sweep")
    assert con.execute("SELECT cat FROM items WHERE id = ?", (iid,)).fetchone()[0] == "drinks.coffee"
    assert con.execute("SELECT catSource FROM products WHERE id = ?", (pid,)).fetchone()[0] == "user"


def test_merge_products_remembers(cli, con):
    a = add(cli, name="Stumptown Holler", storage="cupboard")
    b = add(cli, name="Holler Mountain coffee", storage="cupboard")
    pa, pb = (con.execute("SELECT productId FROM items WHERE id = ?", (x,)).fetchone()[0] for x in (a, b))
    assert pa != pb
    ledger.merge_products(con, pa, pb)
    con.commit()
    cli("sweep")
    assert con.execute("SELECT productId FROM items WHERE id = ?", (b,)).fetchone()[0] == pa


def test_legacy_commands_unchanged(cli, con):
    iid = add(cli, name="Greek yogurt", brand="Fage", storage="fridge", category="dairy", expirationDate="2099-01-02")
    inv = json.loads(cli("inventory").stdout)
    assert [r["id"] for r in inv] == [iid]
    assert json.loads(cli("expiring", "--on", "2099-01-02").stdout)[0]["id"] == iid
    assert json.loads(cli("find", "--q", "fage").stdout)[0]["id"] == iid
    cli("delete", "--id", iid, "--notes", "eaten")
    assert json.loads(cli("inventory").stdout) == []
    assert con.execute("SELECT notes FROM items WHERE id = ?", (iid,)).fetchone()[0] == "eaten"
    res = json.loads(cli("shop-add", "--name", "oat milk").stdout)
    assert res["added"] is True


def test_store_display_names_keep_the_street():
    shown = {t: ledger._store_display(ledger.parse_store(t)) for t in (
        "Corner Co-op, 52 Elm St, Maple Falls, OR 97031", "Albertsons Store #12, 2200 W Oak Ave, Springfield, IL 62704",
        "Vons, 19 Sunset Blvd, Springfield, IL 62704", "Target Westgate (Westgate)", "Aldi, 77 N. Main St, Springfield, IL 62704")}
    assert list(shown.values()) == ["Corner Co-op Elm", "Albertsons Oak Ave", "Vons Sunset", "Target Westgate", "Aldi Main"]


def test_shouting_headers_find_the_same_store(con):
    a = ledger.resolve_store(con, "Fieldstone Market #214, 1180 Harbor Rd, Maple Falls, OR 97031")
    assert ledger.resolve_store(con, "FIELDSTONE MARKET Store #214, 1180 Harbor Rd, Maple Falls, OR 97031") == a
    assert ledger.resolve_store(con, "FIELDSTONE MKT, 1180 HARBOR RD, MAPLE FALLS OR 97031") == a   # same street + ZIP
    b = ledger.resolve_store(con, "NORTHGATE GROCERY STORE #9, 77 Pine St, Maple Falls, OR 97031")
    assert b != a
    row = con.execute("SELECT chain, name FROM stores WHERE id = ?", (b,)).fetchone()
    assert tuple(row) == ("Northgate Grocery", "Northgate Grocery Pine")
