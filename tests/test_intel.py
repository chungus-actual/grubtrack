"""Comparisons that mean something: like with like, shelf prices for trends, evidence counts."""

import json

import pytest

from foodcore import intel


def receipt(cli, tmp_path, store, date, lines, n=[0]):
    n[0] += 1
    f = tmp_path / f"r{n[0]}.json"
    total = round(sum(l["price"] for l in lines), 2)
    f.write_text(json.dumps({"store": store, "date": date, "total": total, "tax": 0, "lines": lines}))
    return json.loads(cli("receipt-add", "--file", f).stdout)


ALB = "Albertsons Store #12, 2200 W Oak Ave, Springfield, IL 62704"
TGT = "Target Springfield Westgate, 3200 Westgate Dr, Springfield, IL 62711"


def book(con):
    return {p["name"]: p for p in intel.price_book(con)}


def test_a_sale_is_not_a_price_change(cli, con, tmp_path):
    receipt(cli, tmp_path, ALB, "2026-09-28", [{"raw": "APPLES", "name": "Cosmic Crisp apples", "price": 3.22,
                                                "regular": 6.47, "weightLb": 3.25}])
    receipt(cli, tmp_path, ALB, "2026-10-21", [{"raw": "APPLES", "name": "Cosmic Crisp apples", "price": 8.08,
                                                "weightLb": 3.53}])
    p = book(con)["Cosmic Crisp apples"]
    assert p["basis"]["label"] == "lb"
    assert p["change"]["pct"] == pytest.approx(15.0, abs=0.2)  # shelf $1.99 -> $2.29, not paid $0.99 -> $2.29
    assert p["deal"] is None  # the last one wasn't on sale


def test_a_store_gap_is_not_a_price_change(cli, con, tmp_path):
    receipt(cli, tmp_path, TGT, "2026-10-03", [{"raw": "KODIAK", "name": "Kodiak waffles", "price": 5.69}])
    receipt(cli, tmp_path, ALB, "2026-10-21", [{"raw": "KODIAK", "name": "Kodiak waffles", "price": 7.99}])
    p = book(con)["Kodiak waffles"]
    assert p["change"] is None
    assert p["storeGap"]["cheap"].startswith("Target") and p["storeGap"]["pct"] == pytest.approx(40.4, abs=0.1)
    idx = intel.store_index(con)["stores"]
    assert len(idx) == 2 and min(idx.values(), key=lambda v: v["index"])["shared"] == 1


def test_by_weight_packages_are_not_compared(cli, con, tmp_path):
    receipt(cli, tmp_path, ALB, "2026-09-28", [{"raw": "SLCD RSTD TURKEY", "name": "Sliced roasted turkey",
                                                "price": 6.09}])
    receipt(cli, tmp_path, ALB, "2026-10-21", [{"raw": "SLCD RSTD TURKEY", "name": "Sliced roasted turkey",
                                                "price": 5.29}])
    p = book(con)["Sliced roasted turkey"]
    assert p["basis"] is None and p["change"] is None  # two packs of unknown weight say nothing
    assert intel.inflation(con)["pairs"] == []


def test_multibuy_compares_per_each(cli, con, tmp_path):
    receipt(cli, tmp_path, TGT, "2026-10-03", [{"raw": "CELSIUS", "name": "Celsius energy drink", "qty": 3,
                                                "price": 7.20, "regular": 8.07}])
    receipt(cli, tmp_path, ALB, "2026-10-21", [{"raw": "CELSIUS", "name": "Celsius energy drink", "price": 3.29}])
    p = book(con)["Celsius energy drink"]
    assert p["basis"]["label"] == "ea"
    assert {s["store"].split()[0]: s["last"] for s in p["stores"]} == {"Target": 2.4, "Albertsons": 3.29}


def test_cadence_needs_three_purchases(cli, con, tmp_path):
    for d in ("2026-09-28", "2026-10-03"):
        receipt(cli, tmp_path, ALB, d, [{"raw": "FAIRLIFE", "name": "Fairlife 2% milk", "price": 6.49}])
    c = intel.cadence(con)[0]
    assert c["status"] == "learning" and c["next"] is None
    receipt(cli, tmp_path, ALB, "2026-10-21", [{"raw": "FAIRLIFE", "name": "Fairlife 2% milk", "price": 6.49}])
    c = intel.cadence(con)[0]
    assert c["purchases"] == 3 and c["every"] == 11.5 and c["next"] == "2026-11-02"


def test_spend_splits_receipts_by_department(cli, con, tmp_path):
    receipt(cli, tmp_path, ALB, "2026-10-21", [
        {"raw": "ORGANIC CILANTRO", "name": "Organic cilantro", "price": 1.69},
        {"raw": "BLACK BOX", "name": "Black Box Cabernet Sauvignon", "price": 4.50}])
    sp = intel.spend(con)
    assert sp["months"][-1]["total"] == 6.19
    assert sp["months"][-1]["depts"] == {"produce": 1.69, "bar": 4.5}
    assert sp["trips"][0]["reconciled"] is True and sp["trips"][0]["unitemized"] == 0


def test_estimate_compares_like_for_like(cli, con, tmp_path):
    receipt(cli, tmp_path, TGT, "2026-10-03", [{"raw": "FAIRLIFE", "name": "Fairlife 2% milk", "price": 4.79},
                                               {"raw": "LIFEWAY", "name": "Lifeway kefir", "price": 4.59}])
    receipt(cli, tmp_path, ALB, "2026-10-21", [{"raw": "FAIRLIFE", "name": "Fairlife 2% milk", "price": 6.49},
                                               {"raw": "CILANTRO", "name": "Organic cilantro", "price": 1.69}])
    est = intel.estimate(con, ["fairlife 2% milk", "kefir", "cilantro", "dish soap"])
    assert est["priced"] == 3 and est["count"] == 4
    cmp = {s["store"].split()[0]: s["total"] for s in est["compare"]["stores"]}
    assert est["compare"]["lines"] == 1 and cmp == {"Albertsons": 6.49, "Target": 4.79}


def test_waste_ignores_corrections(cli, con):
    iid = cli("add", "--json", json.dumps({"name": "Hummus", "storage": "fridge", "price": 3.89})).stdout.strip()
    cli("delete", "--id", iid, "--notes", "tossed")  # deleted seconds after adding: a typo fixed
    w = intel.waste(con, 30)
    assert w["tossed"] == 0


def test_overview_counts_stock_and_its_value(cli, con):
    """The stock line is numbers: rows in the house, how many have a price, and what those come to. A row
    that's gone doesn't count, and a price someone wrote as text isn't a price."""
    cli("add", "--name", "Greek yogurt", "--storage", "fridge", "--price", "6.49")
    cli("add", "--name", "Sourdough loaf", "--storage", "counter", "--price", "5.99")
    cli("add", "--name", "Tofu", "--storage", "fridge")
    cli("add", "--name", "Oat milk", "--storage", "fridge", "--price", "4.29")
    gone = con.execute("SELECT id FROM items WHERE name = 'Oat milk'").fetchone()[0]
    cli("delete", "--id", gone, "--notes", "eaten")
    con.execute("UPDATE items SET price = 'about 3' WHERE name = 'Tofu'")
    con.commit()
    s = intel.overview(con)["stock"]
    assert (s["items"], s["priced"]) == (3, 2)
    assert s["value"] == pytest.approx(12.48)
