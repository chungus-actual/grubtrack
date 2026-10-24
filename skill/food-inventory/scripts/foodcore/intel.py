"""Price intelligence over the purchase ledger.

Rules every number here follows:
  * Compare like with like. A product's prices are compared on one basis: unit price (per lb,
    fl oz, egg...) when sizes are known, else per package. A package of something sold by
    weight (deli turkey, meat-case chicken) is never compared per package: two packs of
    "sliced turkey" at $6.09 and $5.29 say nothing about the price of turkey.
  * Shelf price beats paid price for trends: a sale is not deflation. `regular` (the price
    before the receipt's discount) drives the shelf index; paid price drives spend and deals.
  * Show the evidence: every summary carries how many observations it rests on and whether
    they came off a receipt (verified) or from a voice log.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
import sqlite3
from collections import defaultdict

from .taxonomy import CATEGORIES, DEPARTMENTS, cat_label, classify, dept_of
from .units import display_basis, fmt_amount, shelf
from .ledger import product_key

_DEPT_ORDER = {d: i for i, (d, _) in enumerate(DEPARTMENTS)}
# Sold by weight: a package price says nothing unless the weight is known.
_BY_WEIGHT_CATS = {"meat.poultry", "meat.beef", "meat.pork", "meat.seafood", "meat.sausage", "meat.other",
                   "deli.meats", "deli.salads", "produce.fruit", "produce.vegetables", "produce.aromatics"}
# ...except the produce people buy by the piece or the bunch
_PER_EACH = {"lemon", "lime", "orange", "mandarin", "avocado", "garlic", "shallot", "green onion", "cilantro",
             "parsley", "basil", "mint", "dill", "celery", "lettuce", "mango", "kiwi", "bell pepper", "cucumber",
             "banana", "corn", "artichoke", "pineapple", "melon", "watermelon", "cantaloupe", "cabbage", "cauliflower",
             "broccoli", "bean sprout", "tomato", "carrot", "spinach", "salad kit", "spring mix", "kale", "snap pea",
             "green bean", "mushroom", "grape", "berry", "strawberry", "blueberry", "raspberry", "blackberry"}


def _today() -> str:
    return dt.date.today().isoformat()


def _r(x, n=2):
    return None if x is None else round(x, n)


def _rv(x):
    """Values on a unit basis keep 4 decimals: 9.2¢ per fl oz must not become 9¢."""
    return _r(x, 4)


def _n(v) -> float:
    """A money figure from a column anyone can write: text or NULL counts as 0."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _sold_by_weight(cat: str | None, generic: str | None = None) -> bool:
    return cat in _BY_WEIGHT_CATS and generic not in _PER_EACH


# ---------------------------------------------------------------------------- loading

def load(con: sqlite3.Connection, product_id: str | None = None, positive: bool = True) -> list[dict]:
    """Ledger rows ready for analysis: superseded guesses, non-numeric prices and rows without a
    real date are left out. `positive` also leaves out zero and negative lines (coupons, returns),
    which mean nothing as a price but do count as money for spend."""
    sql = """
      SELECT p.*, pr.name AS pname, pr.brand AS pbrand, pr.generic, pr.dept, pr.cat, pr.sizeLabel AS psize,
             pr.sizeAmount AS psizeAmount, s.name AS storeName, s.chain, r.reconciled, r.total AS rtotal
        FROM purchases p
        LEFT JOIN products pr ON pr.id = p.productId
        LEFT JOIN stores s ON s.id = p.storeId
        LEFT JOIN receipts r ON r.id = p.receiptId
    """
    args: tuple = ()
    sql += (" WHERE p.supersededBy IS NULL AND typeof(p.price) IN ('integer', 'real')"
            " AND p.date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]*' AND date(p.date) IS NOT NULL")
    if positive:
        sql += " AND p.price > 0"
    if product_id:
        sql += " AND p.productId = ?"
        args = (product_id,)
    sql += " ORDER BY p.date, p.receiptId, p.line"
    out = []
    for row in con.execute(sql, args).fetchall():
        o = dict(row)
        o["verified"] = o["source"] == "receipt"
        o["unitShelf"], o["unitLabel"] = shelf(o["unitPrice"], o["cat"], o["dim"])
        if o["dim"] == "pkg" and (o["qty"] or 1) > 1:
            o["unitLabel"] = "ea"  # "5@ GARLIC": the price per piece is the unit price
        reg = o["regular"] if o["regular"] and o["regular"] > o["price"] else None
        o["saved"] = _r(reg - o["price"]) if reg else 0.0
        o["shelfPrice"] = reg or o["price"]
        # amount guessed from the inventory row ("0.5 lb" said aloud), not printed or weighed
        o["amountEst"] = bool(o["dim"] in ("mass", "volume") and not o["weightLb"] and not o["sizeLabel"]
                              and not o["psizeAmount"])
        out.append(o)
    return out


def _obs_public(o: dict) -> dict:
    return {
        "id": o["id"], "date": o["date"], "price": o["price"], "regular": o["regular"], "saved": o["saved"],
        "qty": o["qty"], "amount": fmt_amount(o["amount"], o["dim"]) if o["dim"] != "pkg" else None,
        "unit": _rv(o["unitShelf"]), "unitLabel": o["unitLabel"], "storeId": o["storeId"],
        "store": o["storeName"], "chain": o["chain"], "receiptId": o["receiptId"], "verified": o["verified"],
        "raw": o["raw"], "name": o["name"], "itemId": o["itemId"], "weightLb": o["weightLb"],
    }


# ----------------------------------------------------------------------- product series

def _comparable(o: dict) -> bool:
    """A by-weight thing whose weight was only guessed can't be priced per pound or per pack."""
    return not (o["amountEst"] and _sold_by_weight(o["cat"], o["generic"]))


def _basis(obs: list[dict]) -> tuple[str, str] | None:
    """The dimension a product's prices are compared on, or None if they can't be."""
    obs = [o for o in obs if _comparable(o)]
    dims = [o["dim"] for o in obs if o["dim"]]
    if not dims:
        return None
    measured = [d for d in dims if d in ("mass", "volume", "count")]
    if measured:
        d = max(set(measured), key=measured.count)
        return d, display_basis(obs[0]["cat"], d)[0]
    if _sold_by_weight(obs[0]["cat"], obs[0]["generic"]):
        return None
    return "pkg", "ea" if any((o["qty"] or 1) > 1 for o in obs) else "pkg"


def _value(o: dict, dim: str, shelf_price: bool = False) -> float | None:
    """One observation on the product's basis: unit price for measured dims, per-package
    price for pkg/count. `shelf_price` uses the pre-discount price."""
    if o["dim"] != dim or not _comparable(o):
        return None
    price = o["shelfPrice"] if shelf_price else o["price"]
    if dim == "pkg":
        return price / (o["qty"] or 1)
    if not o["amount"]:
        return None
    v, _ = shelf(price / o["amount"], o["cat"], dim)
    return v


def _move(a: float | None, b: float | None):
    return _r((b - a) / a * 100, 1) if a and b is not None else None


def summarize(obs: list[dict]) -> dict:
    """Everything the price book says about one product, from its observations (date order).

    change     the shelf price (pre-discount) at one store, between its last two visits that
               saw it on different days: an actual price change, not a sale and not a store gap
    storeGap   cheapest vs dearest store on the latest shelf price at each: where to buy it
    deal       how far under shelf price the last purchase was (a sale you caught)
    vsMedian   last paid vs the product's usual paid price on the same basis"""
    first, last = obs[0], obs[-1]
    basis = _basis(obs)
    dim = basis[0] if basis else None
    vals = [(o, _value(o, dim)) for o in obs] if basis else []
    vals = [(o, v) for o, v in vals if v is not None]
    stats = change = gap = vs_median = None
    if vals:
        vs = [v for _, v in vals]
        stats = {"min": _rv(min(vs)), "max": _rv(max(vs)), "median": _rv(statistics.median(vs)),
                 "mean": _rv(statistics.fmean(vs)), "n": len(vs)}
        if len(vs) >= 2 and statistics.median(vs):
            vs_median = _move(statistics.median(vs), vs[-1])
        # one store, distinct days, shelf price
        per_store = defaultdict(list)
        for o in obs:
            v = _value(o, dim, True)
            if v is not None and o["storeId"]:
                seq = per_store[o["storeId"]]
                if not seq or seq[-1][0]["date"] != o["date"]:
                    seq.append((o, v))
                else:
                    seq[-1] = (o, v)
        pairs = [(seq[-2], seq[-1]) for seq in per_store.values() if len(seq) >= 2]
        if pairs:
            (a, va), (b, vb) = max(pairs, key=lambda p: p[1][0]["date"])
            change = {"pct": _move(va, vb), "from": _rv(va), "to": _rv(vb), "fromDate": a["date"],
                      "toDate": b["date"], "store": b["storeName"]}
        latest = {sid: seq[-1] for sid, seq in per_store.items()}
        if len(latest) >= 2:
            lo = min(latest.values(), key=lambda x: x[1])
            hi = max(latest.values(), key=lambda x: x[1])
            gap = {"pct": _move(lo[1], hi[1]), "cheap": lo[0]["storeName"], "cheapV": _rv(lo[1]),
                   "dear": hi[0]["storeName"], "dearV": _rv(hi[1])}
    stores = defaultdict(list)
    for o in obs:
        stores[(o["storeId"], o["storeName"], o["chain"])].append(o)
    per_store_out = []
    for (sid, sname, chain), so in stores.items():
        sv = [_value(o, dim) for o in so] if basis else []
        sv = [v for v in sv if v is not None]
        per_store_out.append({"storeId": sid, "store": sname or "Unknown store", "chain": chain, "n": len(so),
                              "last": _rv(sv[-1]) if sv else None, "avg": _rv(statistics.fmean(sv)) if sv else None,
                              "min": _rv(min(sv)) if sv else None, "lastDate": so[-1]["date"]})
    per_store_out.sort(key=lambda s: (s["avg"] is None, s["avg"] or 0))
    deal = _r(last["saved"] / last["shelfPrice"] * 100, 0) if last["saved"] and last["shelfPrice"] else None
    return {
        "n": len(obs), "trips": len({o["date"] for o in obs}),
        "verified": sum(1 for o in obs if o["verified"]),
        "basis": {"dim": dim, "label": basis[1]} if basis else None,
        "last": _obs_public(last), "first": _obs_public(first),
        "stats": stats, "change": change, "storeGap": gap, "deal": deal, "vsMedianPct": vs_median,
        "stores": per_store_out,
        "spend": _r(sum(o["price"] for o in obs)), "saved": _r(sum(o["saved"] or 0 for o in obs)),
        "spark": [{"date": o["date"], "v": _r(v, 4), "shelf": _r(_value(o, dim, True), 4), "store": o["storeName"],
                   "storeId": o["storeId"], "deal": bool(o["saved"])} for o, v in vals],
    }


def _product_head(pr: dict) -> dict:
    return {"id": pr["id"], "name": pr["name"], "brand": pr["brand"], "generic": pr["generic"],
            "dept": pr["dept"], "cat": pr["cat"], "catLabel": cat_label(pr["cat"]), "catSource": pr["catSource"],
            "confidence": pr["confidence"], "size": pr["sizeLabel"]}


def price_book(con: sqlite3.Connection, dept: str | None = None, q: str | None = None) -> list[dict]:
    obs = load(con)
    by = defaultdict(list)
    for o in obs:
        if o["productId"]:
            by[o["productId"]].append(o)
    prods = {r["id"]: dict(r) for r in con.execute("SELECT * FROM products").fetchall()}
    stock = {r[0]: r[1] for r in con.execute(
        "SELECT productId, COUNT(*) FROM items WHERE deletedAt IS NULL AND productId IS NOT NULL GROUP BY 1").fetchall()}
    qn = (q or "").strip().lower()
    out = []
    for pid, po in by.items():
        pr = prods.get(pid)
        if not pr:
            continue
        if dept and pr["dept"] != dept:
            continue
        if qn and qn not in f"{pr['name']} {pr['brand'] or ''} {pr['generic'] or ''} {cat_label(pr['cat'])}".lower():
            continue
        out.append({**_product_head(pr), **summarize(po), "inStock": stock.get(pid, 0)})
    out.sort(key=lambda p: (_DEPT_ORDER.get(p["dept"], 99), p["cat"] or "", p["name"].lower()))
    return out


def product_detail(con: sqlite3.Connection, pid: str) -> dict | None:
    pr = con.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not pr:
        return None
    pr = dict(pr)
    obs = load(con, pid)
    out = {**_product_head(pr), "observations": [_obs_public(o) for o in reversed(obs)]}
    if obs:
        out.update(summarize(obs))
    # siblings: same generic, compared on unit price where both have one
    sib = []
    if pr["generic"]:
        for r in con.execute("SELECT id FROM products WHERE generic = ? AND id != ?", (pr["generic"], pid)).fetchall():
            so = load(con, r[0])
            if not so:
                continue
            p2 = dict(con.execute("SELECT * FROM products WHERE id = ?", (r[0],)).fetchone())
            sib.append({**_product_head(p2), **{k: v for k, v in summarize(so).items()
                                                 if k in ("n", "last", "stats", "basis")}})
    out["siblings"] = sib
    out["cadence"] = _cadence_one(obs)
    out["stock"] = [dict(r) for r in con.execute(
        "SELECT id, name, storage, location, expirationDate, quantity, unit, price, deletedAt, notes FROM items"
        " WHERE productId = ? ORDER BY deletedAt IS NOT NULL, updatedAt DESC LIMIT 20", (pid,)).fetchall()]
    outcomes = _outcomes(con, pid)
    out["outcomes"] = outcomes
    return out


# -------------------------------------------------------------------------------- stores

def stores(con: sqlite3.Connection) -> list[dict]:
    rows = [dict(r) for r in con.execute("SELECT * FROM stores ORDER BY chain, name").fetchall()]
    trips = defaultdict(list)
    for r in con.execute("SELECT * FROM receipts ORDER BY date").fetchall():
        trips[r["storeId"]].append(dict(r))
    obs = load(con)
    by_store_dept = defaultdict(lambda: defaultdict(float))
    for o in obs:
        by_store_dept[o["storeId"]][o["dept"] or "other"] += o["price"]
    index = store_index(con, obs)
    for s in rows:
        t = trips.get(s["id"], [])
        totals = [_n(x["total"]) for x in t if _n(x["total"])]
        s["trips"] = len(t)
        s["spend"] = _r(sum(totals))
        s["avgBasket"] = _r(statistics.fmean(totals)) if totals else None
        s["lastVisit"] = t[-1]["date"] if t else None
        s["firstVisit"] = t[0]["date"] if t else None
        s["saved"] = _r(sum(_n(x["savings"]) for x in t))
        s["depts"] = sorted(({"dept": d, "spend": _r(v)} for d, v in by_store_dept[s["id"]].items()),
                            key=lambda x: -x["spend"])
        s["index"] = index["stores"].get(s["id"])
    return rows


def store_index(con: sqlite3.Connection, obs: list[dict] | None = None) -> dict:
    """How each store prices the products you buy at more than one store.

    For every product seen at 2+ stores, each store's average comparable price is divided by the
    product's cross-store average; a store's index is the geometric mean of those ratios (1.00 =
    average, 0.90 = 10% cheaper) over the products it shares. Shelf prices are used so a single
    sale doesn't make a store look cheap."""
    obs = obs if obs is not None else load(con)
    by = defaultdict(list)
    for o in obs:
        if o["productId"] and o["storeId"]:
            by[o["productId"]].append(o)
    ratios = defaultdict(list)
    pairs = []
    for pid, po in by.items():
        basis = _basis(po)
        if not basis:
            continue
        per = defaultdict(list)
        for o in po:
            v = _value(o, basis[0], True)
            if v:
                per[(o["storeId"], o["storeName"])].append(v)
        if len(per) < 2:
            continue
        means = {k: statistics.fmean(v) for k, v in per.items()}
        overall = statistics.fmean(means.values())
        for (sid, _), m in means.items():
            ratios[sid].append(m / overall)
        cheapest = min(means.items(), key=lambda kv: kv[1])
        dearest = max(means.items(), key=lambda kv: kv[1])
        pairs.append({"productId": pid, "name": po[0]["pname"], "generic": po[0]["generic"], "cat": po[0]["cat"],
                      "basis": basis[1], "prices": [{"storeId": k[0], "store": k[1], "avg": _rv(v), "n": len(per[k])}
                                                    for k, v in sorted(means.items(), key=lambda kv: kv[1])],
                      "cheapest": cheapest[0][1], "spreadPct": _r((dearest[1] - cheapest[1]) / cheapest[1] * 100, 1)})
    out = {}
    for sid, rs in ratios.items():
        rs = [r for r in rs if r > 0]
        if not rs:
            continue
        g = math.exp(statistics.fmean(math.log(r) for r in rs))
        out[sid] = {"index": _r(g, 3), "shared": len(rs)}
    pairs.sort(key=lambda p: -(p["spreadPct"] or 0))
    return {"stores": out, "products": pairs}


# --------------------------------------------------------------------------------- spend

def spend(con: sqlite3.Connection, months: int = 12) -> dict:
    """Money out, by month, store and department. Receipt totals are the truth; lines split
    them by department and whatever the lines don't cover is `unitemized`."""
    receipts = [dict(r) for r in con.execute(
        "SELECT r.*, s.name AS storeName, s.chain FROM receipts r LEFT JOIN stores s ON s.id = r.storeId"
        " WHERE date(r.date) IS NOT NULL ORDER BY r.date DESC").fetchall()]
    obs = load(con, positive=False)
    lines_by_receipt = defaultdict(list)
    loose = []
    for o in obs:
        if o["receiptId"]:
            lines_by_receipt[o["receiptId"]].append(o)
        else:
            loose.append(o)
    month = defaultdict(lambda: {"total": 0.0, "tax": 0.0, "unitemized": 0.0, "saved": 0.0, "trips": 0,
                                 "depts": defaultdict(float)})
    by_dept = defaultdict(float)
    by_store = defaultdict(lambda: {"spend": 0.0, "trips": 0})
    trips = []
    for r in receipts:
        ls = lines_by_receipt.get(r["id"], [])
        total = _n(r["total"]) if isinstance(r["total"], (int, float)) else sum(o["price"] for o in ls)
        covered = sum(o["price"] for o in ls)
        unitem = max(0.0, total - covered - _n(r["tax"]))
        m = month[r["date"][:7]]
        m["total"] += total
        m["tax"] += _n(r["tax"])
        m["unitemized"] += unitem
        m["saved"] += _n(r["savings"])
        m["trips"] += 1
        depts = defaultdict(float)
        for o in ls:
            d = o["dept"] or "other"
            m["depts"][d] += o["price"]
            by_dept[d] += o["price"]
            depts[d] += o["price"]
        by_store[(r["storeId"], r["storeName"] or r["store"])]["spend"] += total
        by_store[(r["storeId"], r["storeName"] or r["store"])]["trips"] += 1
        trips.append({"id": r["id"], "date": r["date"], "storeId": r["storeId"], "store": r["storeName"] or r["store"],
                      "chain": r["chain"], "total": _r(total), "tax": _n(r["tax"]) or None, "saved": _n(r["savings"]) or None,
                      "lines": len([o for o in ls if o["source"] == "receipt"]),
                      "voiced": len([o for o in ls if o["source"] == "item"]),
                      "reconciled": bool(r["reconciled"]), "unitemized": _r(unitem), "diff": r["diff"],
                      "image": r["imagePath"], "payment": r["paymentNote"],
                      "depts": sorted(({"dept": d, "spend": _r(v)} for d, v in depts.items()), key=lambda x: -x["spend"])})
    for o in loose:
        m = month[o["date"][:7]]
        m["total"] += o["price"]
        d = o["dept"] or "other"
        m["depts"][d] += o["price"]
        by_dept[d] += o["price"]
        by_store[(None, "No receipt")]["spend"] += o["price"]
    months_out = []
    for k in sorted(month)[-months:]:
        m = month[k]
        months_out.append({"month": k, "total": _r(m["total"]), "tax": _r(m["tax"]), "unitemized": _r(m["unitemized"]),
                           "saved": _r(m["saved"]), "trips": m["trips"],
                           "depts": {d: _r(v) for d, v in m["depts"].items()}})
    return {
        "months": months_out,
        "byDept": sorted(({"dept": d, "spend": _r(v)} for d, v in by_dept.items()), key=lambda x: -x["spend"]),
        "byStore": sorted(({"storeId": k[0], "store": k[1], "spend": _r(v["spend"]), "trips": v["trips"]}
                           for k, v in by_store.items()), key=lambda x: -x["spend"]),
        "trips": trips,
        "loose": [_obs_public(o) | {"dept": o["dept"]} for o in reversed(loose)],
    }


# ----------------------------------------------------------------------------- movement

def movers(con: sqlite3.Connection, limit: int = 12) -> dict:
    """Shelf-price changes at one store, and the widest gaps between stores."""
    book = price_book(con)
    moved = [p for p in book if p.get("change") and p["change"]["pct"] not in (None, 0)]
    moved.sort(key=lambda p: -abs(p["change"]["pct"]))
    gaps = [p for p in book if p.get("storeGap") and p["storeGap"]["pct"]]
    gaps.sort(key=lambda p: -p["storeGap"]["pct"])
    steady = [p for p in book if p.get("change") and p["change"]["pct"] == 0]
    return {"moved": moved[:limit], "gaps": gaps[:limit], "steady": len(steady)}


def inflation(con: sqlite3.Connection) -> dict:
    """A personal shelf-price index: every product bought twice at the same store contributes the
    change in its pre-discount price between the two visits. Months chain together."""
    obs = load(con)
    by = defaultdict(list)
    for o in obs:
        if o["productId"] and o["storeId"]:
            by[(o["productId"], o["storeId"])].append(o)
    pairs = []
    for (pid, sid), po in by.items():
        basis = _basis(po)
        if not basis:
            continue
        seq = []
        for o in po:
            v = _value(o, basis[0], True)
            if v and (not seq or seq[-1][0]["date"] != o["date"]):
                seq.append((o, v))
            elif v and seq and seq[-1][0]["date"] == o["date"]:
                seq[-1] = (o, v)
        for (a, va), (b, vb) in zip(seq, seq[1:]):
            if va <= 0 or vb <= 0:
                continue
            pairs.append({"productId": pid, "name": a["pname"], "store": a["storeName"], "basis": basis[1],
                          "from": {"date": a["date"], "v": _rv(va)}, "to": {"date": b["date"], "v": _rv(vb)},
                          "pct": _r((vb - va) / va * 100, 1), "log": math.log(vb / va)})
    by_month = defaultdict(list)
    for p in pairs:
        by_month[p["to"]["date"][:7]].append(p["log"])
    index, level = [], 100.0
    for mth in sorted(by_month):
        level *= math.exp(statistics.fmean(by_month[mth]))
        index.append({"month": mth, "value": _r(level, 1), "pairs": len(by_month[mth])})
    for p in pairs:
        p.pop("log")
    pairs.sort(key=lambda p: -abs(p["pct"]))
    overall = _r((math.exp(statistics.fmean(math.log(1 + p["pct"] / 100) for p in pairs)) - 1) * 100, 1) if pairs else None
    return {"index": index, "pairs": pairs, "basketPct": overall}


# ------------------------------------------------------------------------------- cadence

def _cadence_one(obs: list[dict]) -> dict | None:
    """How often a thing gets bought. Two purchases are a gap, not a rhythm: a due date is only
    predicted from three or more purchase days."""
    days = sorted({o["date"] for o in obs})
    if len(days) < 2:
        return None
    ds = [dt.date.fromisoformat(d) for d in days]
    gaps = [(b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days > 0]
    if not gaps:
        return None
    med = statistics.median(gaps)
    out = {"every": _r(med, 1), "gaps": gaps, "last": days[-1], "purchases": len(days)}
    if len(days) >= 3:
        nxt = ds[-1] + dt.timedelta(days=round(med))
        left = (nxt - dt.date.today()).days
        out.update({"next": nxt.isoformat(), "daysLeft": left,
                    "status": "overdue" if left < 0 else "due" if left <= 3 else "ok"})
    else:
        out.update({"next": None, "daysLeft": None, "status": "learning"})
    return out


def cadence(con: sqlite3.Connection) -> list[dict]:
    obs = load(con)
    by = defaultdict(list)
    for o in obs:
        if o["productId"]:
            by[o["productId"]].append(o)
    out = []
    for pid, po in by.items():
        c = _cadence_one(po)
        if c:
            out.append({"productId": pid, "name": po[0]["pname"], "cat": po[0]["cat"], **c,
                        "lastPrice": po[-1]["price"], "store": po[-1]["storeName"]})
    out.sort(key=lambda c: (c["daysLeft"] is None, c["daysLeft"] or 0))
    return out


# ------------------------------------------------------------------------- consumption

def _is_correction(created: str | None, deleted: str | None) -> bool:
    try:
        a = dt.datetime.fromisoformat(created)
        b = dt.datetime.fromisoformat(deleted)
        return (b - a).total_seconds() < 600
    except Exception:
        return False


def _fate(notes: str | None) -> str | None:
    n = (notes or "").lower()
    if "toss" in n or "spoil" in n or "threw" in n:
        return "tossed"
    if "eaten" in n or "ate it" in n or " ate" in n or "used" in n:
        return "eaten"
    return None


def _outcomes(con: sqlite3.Connection, pid: str) -> dict:
    out = {"eaten": 0, "tossed": 0, "unknown": 0}
    for r in con.execute("SELECT createdAt, deletedAt, notes FROM items WHERE productId = ? AND deletedAt IS NOT NULL",
                         (pid,)).fetchall():
        if _is_correction(r["createdAt"], r["deletedAt"]):
            continue
        out[_fate(r["notes"]) or "unknown"] += 1
    return out


def waste(con: sqlite3.Connection, days: int = 90) -> dict:
    """What left the house, and how: eaten or tossed (the FOOD pane's two buttons write those
    words into notes since 2026-08-25), with what the tossed food cost where a price is known."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = con.execute(
        "SELECT i.id, i.name, i.cat, i.dept, i.price, i.createdAt, i.deletedAt, i.notes, i.productId FROM items i"
        " WHERE i.deletedAt IS NOT NULL AND substr(i.deletedAt, 1, 10) >= ? ORDER BY i.deletedAt DESC",
        (since,)).fetchall()
    tossed, eaten, unknown = [], 0, 0
    by_cat = defaultdict(lambda: {"eaten": 0, "tossed": 0, "usd": 0.0})
    by_month = defaultdict(lambda: {"tossed": 0, "usd": 0.0, "priced": 0})
    for r in rows:
        if _is_correction(r["createdAt"], r["deletedAt"]):
            continue
        f = _fate(r["notes"])
        c = by_cat[r["cat"] or "other.other"]
        if f == "tossed":
            tossed.append({"id": r["id"], "name": r["name"], "cat": r["cat"], "price": r["price"],
                           "date": r["deletedAt"][:10]})
            c["tossed"] += 1
            m = by_month[r["deletedAt"][:7]]
            m["tossed"] += 1
            if r["price"] is not None:
                c["usd"] += r["price"]
                m["usd"] += r["price"]
                m["priced"] += 1
        elif f == "eaten":
            eaten += 1
            c["eaten"] += 1
        else:
            unknown += 1
    cats = []
    for k, v in by_cat.items():
        n = v["eaten"] + v["tossed"]
        if n:
            cats.append({"cat": k, "catLabel": cat_label(k), "eaten": v["eaten"], "tossed": v["tossed"],
                         "usd": _r(v["usd"]), "tossRate": _r(v["tossed"] / n * 100, 0)})
    cats.sort(key=lambda c: (-c["tossed"], -c["usd"]))
    priced = [t for t in tossed if t["price"] is not None]
    return {
        "days": days, "eaten": eaten, "tossed": len(tossed), "unknown": unknown,
        "tossedUsd": _r(sum(t["price"] for t in priced)), "tossedPriced": len(priced),
        "byCat": cats, "items": tossed[:50],
        "months": [{"month": k, **{kk: _r(vv) if isinstance(vv, float) else vv for kk, vv in v.items()}}
                   for k, v in sorted(by_month.items())],
    }


# --------------------------------------------------------------------- shopping estimate

def estimate(con: sqlite3.Connection, names: list[str]) -> dict:
    """What a shopping list will cost, and where. Each line is matched to a product you've
    bought (same name), else to the cheapest recent thing of the same generic kind."""
    book = {p["id"]: p for p in price_book(con)}
    by_key = {}
    for r in con.execute("SELECT id, key FROM products").fetchall():
        by_key[r["key"]] = r["id"]
    for r in con.execute("SELECT key, productId FROM product_aliases").fetchall():
        by_key[r["key"]] = r["productId"]
    by_generic = defaultdict(list)
    for p in book.values():
        if p["generic"]:
            by_generic[p["generic"]].append(p)
    lines = []
    store_tot = defaultdict(lambda: {"total": 0.0, "lines": 0})
    for name in names:
        c = classify(name)
        pid = by_key.get(product_key(name))
        p = book.get(pid) if pid else None
        match = "exact" if p else None
        if not p and c["generic"] and by_generic.get(c["generic"]):
            p = max(by_generic[c["generic"]], key=lambda x: x["last"]["date"])
            match = "kind"
        if not p:
            lines.append({"name": name, "cat": c["cat"], "catLabel": cat_label(c["cat"]), "match": None})
            continue
        last_pkg = p["last"]["price"] / (p["last"]["qty"] or 1)
        at = {}
        for s in p["stores"]:
            obs = [o for o in load(con, p["id"]) if o["storeId"] == s["storeId"]]
            if obs:
                at[s["storeId"]] = {"store": s["store"], "price": _r(obs[-1]["price"] / (obs[-1]["qty"] or 1))}
        for sid, v in at.items():
            store_tot[(sid, v["store"])]["total"] += v["price"]
            store_tot[(sid, v["store"])]["lines"] += 1
        best = min(at.values(), key=lambda v: v["price"]) if at else None
        kinds = by_generic.get(p["generic"] or "", []) if match == "kind" else []
        lo = min((k["last"]["price"] / (k["last"]["qty"] or 1) for k in kinds), default=None)
        hi = max((k["last"]["price"] / (k["last"]["qty"] or 1) for k in kinds), default=None)
        lines.append({"name": name, "cat": p["cat"], "catLabel": p["catLabel"], "match": match,
                      "productId": p["id"], "product": p["name"], "estimate": _r(last_pkg),
                      "range": [_r(lo), _r(hi)] if kinds and len(kinds) > 1 else None, "kinds": len(kinds),
                      "best": best, "stores": at})
    priced = [l for l in lines if l.get("estimate") is not None]
    stores_out = sorted(({"storeId": k[0], "store": k[1], "total": _r(v["total"]), "lines": v["lines"]}
                         for k, v in store_tot.items()), key=lambda s: (-s["lines"], s["total"]))
    compare = None
    if len(stores_out) >= 2:
        a_, b_ = stores_out[0]["storeId"], stores_out[1]["storeId"]
        both = [l for l in priced if a_ in l["stores"] and b_ in l["stores"]]
        if both:
            ta = sum(l["stores"][a_]["price"] for l in both)
            tb = sum(l["stores"][b_]["price"] for l in both)
            compare = {"lines": len(both), "stores": [{"storeId": a_, "store": stores_out[0]["store"], "total": _r(ta)},
                                                      {"storeId": b_, "store": stores_out[1]["store"], "total": _r(tb)}]}
    return {"lines": lines, "total": _r(sum(l["estimate"] for l in priced)), "priced": len(priced),
            "count": len(lines), "stores": stores_out, "compare": compare}


# ------------------------------------------------------------------------------ overview

def overview(con: sqlite3.Connection) -> dict:
    today = _today()
    live = con.execute("SELECT COUNT(*), SUM(price IS NOT NULL), ROUND(SUM(price), 2) FROM items WHERE deletedAt IS NULL"
                       ).fetchone()
    exp = con.execute(
        "SELECT SUM(expirationDate < ?), SUM(expirationDate BETWEEN ? AND date(?, '+3 days')),"
        " SUM(expirationDate BETWEEN ? AND date(?, '+7 days')) FROM items WHERE deletedAt IS NULL AND expirationDate IS NOT NULL",
        (today, today, today, today, today)).fetchone()
    sp = spend(con, months=3)
    month = sp["months"][-1] if sp["months"] else None
    last30 = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    spend30 = sum(t["total"] or 0 for t in sp["trips"] if t["date"] >= last30) + \
        sum(o["price"] for o in sp["loose"] if o["date"] >= last30)
    infl = inflation(con)
    live = "supersededBy IS NULL AND typeof(price) IN ('integer', 'real') AND price > 0"
    n_products = con.execute(f"SELECT COUNT(DISTINCT productId) FROM purchases WHERE {live}").fetchone()[0]
    n_obs = con.execute(f"SELECT COUNT(*), SUM(source = 'receipt') FROM purchases WHERE {live}").fetchone()
    review = con.execute("SELECT COUNT(*) FROM products WHERE (cat LIKE '%.other' OR confidence = 'low')"
                         " AND catSource = 'auto' AND id IN (SELECT productId FROM items WHERE deletedAt IS NULL"
                         " UNION SELECT productId FROM purchases WHERE supersededBy IS NULL)").fetchone()[0]
    return {
        "stock": {"items": live[0], "priced": live[1] or 0, "value": live[2] or 0,
                  "expired": exp[0] or 0, "soon3": exp[1] or 0, "soon7": exp[2] or 0},
        "spend": {"last30": _r(spend30), "month": month, "trips": len(sp["trips"]),
                  "saved": _r(sum(_n(t["saved"]) for t in sp["trips"]))},
        "prices": {"products": n_products, "observations": n_obs[0], "verified": n_obs[1] or 0,
                   "basketPct": infl["basketPct"], "pairs": len(infl["pairs"])},
        "review": review,
    }


def taxonomy() -> dict:
    depts = []
    for d, label in DEPARTMENTS:
        cats = [{"key": k, "label": v["label"]} for k, v in CATEGORIES.items() if v["dept"] == d]
        cats.sort(key=lambda c: (c["key"].endswith(".other"), c["label"]))
        depts.append({"key": d, "label": label, "cats": cats})
    return {"departments": depts}
