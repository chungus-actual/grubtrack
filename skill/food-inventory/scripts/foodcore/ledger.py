"""The purchase ledger: stores, products, receipts and the sweep that keeps them resolved.

Writers (the agent through fooddb_sqlite.py, GrubTrack, the capture triggers) record facts.
`sweep()` turns facts into intelligence-ready rows: which store, which product, how much of
it, what that comes to per pound / fluid ounce / egg. It is idempotent, writes only what
changed, and is cheap enough (a few hundred rows) to run after every write.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import uuid

from . import schema
from .taxonomy import CATEGORIES, classify, dept_of
from .text import fold, jaccard, norm, slug, strip_parens, tokens
from .units import line_amount, measure_of, parse_size

TOLERANCE = 0.02  # dollars a receipt may be off by and still reconcile (store rounding)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ------------------------------------------------------------------------------ stores

CHAINS = [
    "Albertsons", "Vons", "Safeway", "Smith's", "Sprouts", "Target", "Walmart", "Costco", "Sam's Club",
    "Trader Joe's", "Whole Foods", "WinCo", "Aldi", "H Mart", "99 Ranch", "Seafood City", "Food 4 Less",
    "Cardenas", "El Super", "Lee's Discount Liquor", "Total Wine", "CVS", "Walgreens", "Amazon Fresh",
    "Smart & Final", "Natural Grocers", "Mariana's", "Glazier's", "7-Eleven", "Dollar Tree", "Kroger",
    "Ralphs", "Fresh Market", "Grocery Outlet", "Lidl", "Publix", "Wegmans", "HEB", "Meijer",
]
_CHAIN_KEYS = sorted(((norm(c), c) for c in CHAINS), key=lambda x: -len(x[0]))
_STREET = re.compile(
    r"(\d{2,6})\s+([NSEW]\.?\s+)?([A-Za-z0-9 .'-]+?)\s+(Blvd|Boulevard|St|Street|Ave|Avenue|Rd|Road|Dr|Drive|"
    r"Pkwy|Parkway|Hwy|Highway|Way|Ln|Lane|Ct|Court|Pl|Place|Cir|Circle|Trl|Trail)\b\.?", re.I)
_NUMBER = re.compile(r"(?:#|store\s*#?\s*|no\.?\s*)(\d{1,6})", re.I)
_POSTAL = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def parse_store(text: str | None) -> dict:
    """'Albertsons Store #12, 2200 W Oak Ave, Springfield, IL 62704' ->
    {chain, storeNumber, address, city, region, postal, branch}. Best-effort, never raises."""
    raw = (text or "").strip()
    n = norm(raw)
    chain = None
    for key, name in _CHAIN_KEYS:
        if n == key or n.startswith(key + " ") or f" {key} " in f" {n} ":
            chain = name
            break
    if not chain:
        chain = re.split(r"[(#,]|\s+store\b|\s+-\s+", raw, maxsplit=1, flags=re.I)[0].strip() or "Unknown store"
        if chain.isupper():  # receipt headers shout: "FIELDSTONE MARKET" -> "Fieldstone Market", "ABC" stays
            chain = " ".join(w.capitalize() if len(w) > 3 else w for w in chain.split())
    out = {"chain": chain, "storeNumber": None, "address": None, "city": None, "region": None,
           "postal": None, "branch": None}
    m = _NUMBER.search(raw)
    if m:
        out["storeNumber"] = m.group(1)
    m = _STREET.search(raw)
    if m:
        d = (m.group(2) or "").strip().rstrip(".").upper()
        street = " ".join(x for x in (m.group(1), d, m.group(3).strip().title(), m.group(4).title()) if x)
        out["address"] = street.replace(" Boulevard", " Blvd").replace(" Street", " St").replace(" Avenue", " Ave")
    m = _POSTAL.search(raw)
    if m:
        out["postal"] = m.group(1)
    m = re.search(r",\s*([A-Za-z .]+?),?\s+(NV|CA|AZ|UT|TX|NY|[A-Z]{2})\b", raw)
    if m:
        out["city"], out["region"] = m.group(1).strip(), m.group(2)
    paren = re.search(r"\(([^)]*)\)", raw)
    if paren:
        out["branch"] = paren.group(1).strip()
    elif " - " in raw:
        out["branch"] = raw.split(" - ", 1)[1].strip()
    return out


def _store_display(p: dict) -> str:
    where = p.get("branch") or (p.get("address") or "").split(",")[0]
    where = re.sub(r"^\d+\s+(?:[NSEW]\.?\s+)?", "", where or "")   # "2200 W Oak Ave" -> "Oak Ave"
    where = re.sub(r"\s+(?:Blvd|St)$", "", where).strip()
    return f"{p['chain']} {where}".strip() if where else p["chain"]


def resolve_store(con: sqlite3.Connection, text: str | None, create: bool = True) -> str | None:
    """Store id for a free-text store string. Exact alias first, then same chain + same street
    (or a branch naming that street), then a new store."""
    if not text or not text.strip():
        return None
    alias = norm(text)
    row = con.execute("SELECT storeId FROM store_aliases WHERE alias = ?", (alias,)).fetchone()
    if row:
        return row[0]
    p = parse_store(text)
    cands = [c for c in con.execute("SELECT * FROM stores").fetchall() if norm(c["chain"] or "") == norm(p["chain"])]
    hit = None
    street_words = set(tokens(re.sub(r"^\d+", "", p["address"] or ""))) - {"blvd", "st", "ave", "rd", "dr", "s", "n", "e", "w"}
    branch_words = set(tokens(p["branch"] or "")) - {"and"}
    for c in cands:
        caddr = set(tokens(c["address"] or "")) | set(tokens(c["branch"] or ""))
        if p["storeNumber"] and c["storeNumber"] == p["storeNumber"]:
            hit = c["id"]
            break
        if p["address"] and c["address"] and norm(p["address"]) == norm(c["address"]):
            hit = c["id"]
            break
        if (street_words or branch_words) and caddr & (street_words | branch_words):
            hit = c["id"]
    if not hit and len(cands) == 1 and not p["address"] and not p["branch"] and not p["storeNumber"]:
        hit = cands[0]["id"]  # "Target" alone, and we only know one Target
    if not hit and p["address"] and p["postal"]:
        # the header spelled differently, but it's the same street address and ZIP
        for c in con.execute("SELECT * FROM stores WHERE postal = ? AND address IS NOT NULL", (p["postal"],)).fetchall():
            if norm(c["address"]) == norm(p["address"]):
                hit = c["id"]
                break
    if not hit:
        if not create:
            return None
        sid = slug(" ".join(x for x in (p["chain"], (p["address"] or "").split(",")[0] or p["branch"] or p["storeNumber"]) if x))[:60]
        base, k = sid or "store", 2
        while con.execute("SELECT 1 FROM stores WHERE id = ?", (sid,)).fetchone():
            sid, k = f"{base}-{k}", k + 1
        ts = now_iso()
        con.execute(
            "INSERT INTO stores (id, chain, name, branch, address, city, region, postal, storeNumber, createdAt, updatedAt)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, p["chain"], _store_display(p), p["branch"], p["address"], p["city"], p["region"], p["postal"],
             p["storeNumber"], ts, ts))
        hit = sid
    else:
        # learn what we didn't know: a later receipt prints the address the first one lacked
        c = con.execute("SELECT * FROM stores WHERE id = ?", (hit,)).fetchone()
        patch = {k: p[k] for k in ("address", "city", "region", "postal", "storeNumber") if p[k] and not c[k]}
        if patch:
            sets = ", ".join(f"{k} = ?" for k in patch)
            con.execute(f"UPDATE stores SET {sets}, updatedAt = ? WHERE id = ?", (*patch.values(), now_iso(), hit))
    con.execute("INSERT OR IGNORE INTO store_aliases (alias, storeId) VALUES (?, ?)", (alias, hit))
    return hit


# ---------------------------------------------------------------------------- products

_PACKAGING = {"bag", "pack", "pk", "can", "jar", "bottle", "box", "tube", "bunch", "head", "carton", "container",
              "package", "pkg", "ct", "count", "each", "ea", "tub", "pouch", "packet", "case", "loaf", "the", "a",
              "of", "and", "with", "usual", "brand", "pc", "piece", "x"}
_SIZE_TOKEN = re.compile(r"^\d+(\.\d+)?(oz|lb|g|kg|ml|l|qt|pt|gal|ct|pk|floz)?$")
_UNIT_WORDS = {"oz", "lb", "g", "kg", "ml", "l", "qt", "pt", "gal", "floz", "fl", "ounce", "pound", "liter", "quart",
               "pint", "gallon", "dozen"}


def product_key(name: str | None, brand: str | None = None) -> str:
    """Order-insensitive identity: 'Fairlife 2% milk' == '2% Fairlife milk' == 'Fairlife 2% Milk
    (1.5 L)'. Sizes, packaging and filler words are not identity."""
    toks = [t for t in tokens(name) if t not in _PACKAGING and t not in _UNIT_WORDS and not _SIZE_TOKEN.match(t)
            or t.endswith("%")]
    if brand:
        b = [t for t in tokens(brand) if t not in _PACKAGING]
        if b and not set(b) <= set(toks):
            toks = b + toks
    return " ".join(sorted(set(toks)))


def _ctx_classify(name, brand=None, ctx: dict | None = None) -> dict:
    ctx = ctx or {}
    return classify(name, brand, ctx.get("category"), ctx.get("subCategory"), ctx.get("spiritType"),
                    ctx.get("storage"), ctx.get("section"), ctx.get("raw"))


def resolve_product(con: sqlite3.Connection, name: str | None, brand: str | None = None,
                    ctx: dict | None = None, size_label: str | None = None, create: bool = True) -> str | None:
    if not name or not name.strip():
        return None
    key = product_key(name, brand)
    if not key:
        return None
    row = con.execute("SELECT productId FROM product_aliases WHERE key = ?", (key,)).fetchone()
    if row:
        return row[0]
    row = con.execute("SELECT id FROM products WHERE key = ?", (key,)).fetchone()
    if row:
        return row[0]
    if not create:
        return None
    c = _ctx_classify(name, brand, ctx)
    size = parse_size(size_label, c["cat"], c["generic"]) if size_label else None
    pid = new_id("pr")
    ts = now_iso()
    display = re.sub(r"\s+", " ", strip_parens(name)).strip(" -,") or name.strip()
    con.execute(
        "INSERT INTO products (id, key, name, brand, generic, dept, cat, catSource, confidence, sizeAmount, sizeDim,"
        " sizeLabel, createdAt, updatedAt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, key, display, brand, c["generic"], c["dept"], c["cat"], "auto", c["confidence"],
         size[0] if size else None, size[1] if size else None, size_label if size else None, ts, ts))
    return pid


def merge_products(con: sqlite3.Connection, keep: str, drop: str) -> None:
    """A person said two products are the same thing: repoint everything at `keep` and remember
    `drop`'s key so the name never splits off again."""
    if keep == drop:
        return
    row = con.execute("SELECT key FROM products WHERE id = ?", (drop,)).fetchone()
    if not row:
        return
    if not con.execute("SELECT 1 FROM products WHERE id = ?", (keep,)).fetchone():
        raise ValueError(f"can't merge into {keep}: no such product")
    con.execute("INSERT OR REPLACE INTO product_aliases (key, productId) VALUES (?, ?)", (row[0], keep))
    con.execute("UPDATE product_aliases SET productId = ? WHERE productId = ?", (keep, drop))
    con.execute("UPDATE purchases SET productId = ? WHERE productId = ?", (keep, drop))
    con.execute("UPDATE items SET productId = ? WHERE productId = ?", (keep, drop))
    con.execute("DELETE FROM products WHERE id = ?", (drop,))


# ---------------------------------------------------------------------------- receipts

def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace("$", "").replace(",", "").strip())


def reconcile(lines: list[dict], tax, total) -> dict:
    s = round(sum(_num(l.get("price")) or 0 for l in lines), 2)
    t = _num(tax)
    tot = _num(total)
    if tot is None:
        return {"lines": s, "tax": t, "total": None, "diff": None, "ok": False}
    diff = round(tot - (s + (t or 0)), 2)
    return {"lines": s, "tax": t, "total": tot, "diff": diff, "ok": t is not None and abs(diff) <= TOLERANCE}


def _receipt_id(store_id: str | None, date: str) -> str:
    return f"rc_{date.replace('-', '')}_{(store_id or 'store')[:24]}_{uuid.uuid4().hex[:4]}"


def _norm_date(s) -> str | None:
    if not s:
        return None
    s = str(s).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        return f"{y:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


def _days(a: str, b: str) -> int:
    return (dt.date.fromisoformat(a[:10]) - dt.date.fromisoformat(b[:10])).days


def link_candidates(con: sqlite3.Connection, date: str, exclude: set[str] | None = None) -> list[dict]:
    """Inventory rows a receipt from `date` could have stocked: written from the day before to
    three days after (people log the haul when they unpack it, not at the register)."""
    rows = con.execute(
        "SELECT * FROM items WHERE (substr(createdAt,1,10) BETWEEN date(?, '-1 day') AND date(?, '+3 days'))"
        " OR (substr(updatedAt,1,10) BETWEEN date(?, '-1 day') AND date(?, '+3 days'))",
        (date, date, date, date)).fetchall()
    out = [dict(r) for r in rows if r["id"] not in (exclude or set())]
    # a row deleted within ten minutes of being added was a correction, not a purchase
    return [r for r in out if not (r.get("deletedAt") and _minutes(r["createdAt"], r["deletedAt"]) < 10)]


def _minutes(a: str, b: str) -> float:
    try:
        return (dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds() / 60
    except Exception:
        return 1e9


def _score(line: dict, item: dict) -> float:
    lc = _ctx_classify(line.get("name") or line.get("raw"), line.get("brand"), {"raw": line.get("raw"),
                                                                                 "section": line.get("section")})
    ic = _ctx_classify(item.get("name"), item.get("brand"), item)
    if product_key(line.get("name"), line.get("brand")) == product_key(item.get("name"), item.get("brand")):
        return 1.0
    s = jaccard(tokens(line.get("name") or line.get("raw")), tokens(item.get("name")))
    if lc["generic"] and lc["generic"] == ic["generic"]:
        s = max(s, 0.8)
    elif lc["cat"] == ic["cat"] and not lc["cat"].endswith(".other"):
        s = max(s, min(0.6, s + 0.2))
    return s


def import_receipt(con: sqlite3.Connection, data: dict, replace: bool = False, autolink: bool = True) -> dict:
    """Record a receipt and every line on it. Returns a report: reconciliation, links made, lines
    left unlinked, and inventory rows from that day still without a price.

    Lines are the ledger whether or not they stock the inventory: "receipt-only extras are not
    inventory" (a house rule) is about the pantry, and the spend is real either way."""
    date = _norm_date(data.get("date"))
    if not date:
        raise ValueError("receipt needs a date (YYYY-MM-DD)")
    lines = data.get("lines") or []
    if not lines:
        raise ValueError("receipt needs at least one line")
    for i, l in enumerate(lines):
        if _num(l.get("price")) is None:
            raise ValueError(f"line {i + 1} ({l.get('raw') or l.get('name')}) has no price")
    store_id = data.get("storeId") or resolve_store(con, data.get("store"))
    rec = reconcile(lines, data.get("tax"), data.get("total"))
    rid = data.get("id")
    if not rid and rec["total"] is not None:
        # the same trip sent twice (or re-sent to fix a line) must not become two receipts
        same = con.execute("SELECT id FROM receipts WHERE storeId IS ? AND date = ? AND ABS(COALESCE(total, -1) - ?) < 0.01",
                           (store_id, date, rec["total"])).fetchone()
        if same:
            if not replace:
                raise ValueError(f"receipt {same[0]} (same store, date and total) is already recorded; to rewrite it, "
                                 f"re-run with replace and \"id\": \"{same[0]}\"")
            rid = same[0]
    if rid and con.execute("SELECT 1 FROM receipts WHERE id = ?", (rid,)).fetchone():
        if not replace:
            raise ValueError(f"receipt {rid} already exists (pass replace to rewrite its lines)")
    rid = rid or _receipt_id(store_id, date)
    partial = bool(data.get("partial"))
    ts = now_iso()

    with con:
        exists = con.execute("SELECT * FROM receipts WHERE id = ?", (rid,)).fetchone()
        fields = {
            "date": date, "store": data.get("store") or (exists["store"] if exists else None),
            "total": rec["total"], "savings": _num(data.get("savings")), "paymentNote": data.get("payment"),
            "imagePath": data.get("image"), "notes": data.get("notes"), "storeId": store_id,
            "subtotal": _num(data.get("subtotal")) or rec["lines"], "tax": rec["tax"], "lineCount": len(lines),
            "reconciled": 1 if rec["ok"] and not partial else 0, "diff": rec["diff"], "updatedAt": ts,
        }
        if exists:
            keep = {k: v for k, v in fields.items() if v is not None or k in ("reconciled", "diff")}
            sets = ", ".join(f"{k} = ?" for k in keep)
            con.execute(f"UPDATE receipts SET {sets} WHERE id = ?", (*keep.values(), rid))
            # links made before (by hand or by an earlier run) carry over to the same line
            old_links = {}
            for o in con.execute("SELECT raw, name, price, itemId FROM purchases WHERE receiptId = ? AND source = 'receipt'"
                                 " AND itemId IS NOT NULL", (rid,)).fetchall():
                old_links.setdefault((o["raw"] or o["name"], round(o["price"], 2)), o["itemId"])
            for l in lines:
                if not l.get("itemId") and not l.get("noLink"):
                    hit = old_links.get((l.get("raw") or l.get("name"), round(_num(l.get("price")), 2)))
                    if hit:
                        l["itemId"] = hit
            con.execute("DELETE FROM purchases WHERE receiptId = ? AND source = 'receipt'", (rid,))
        else:
            cols = ["id", "createdAt", "currency"] + list(fields)
            con.execute(f"INSERT INTO receipts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        (rid, ts, data.get("currency") or "USD", *fields.values()))

        # explicit links first, then (optionally) confident automatic ones
        taken: dict[str, list[int]] = {}
        for i, l in enumerate(lines):
            if l.get("itemId"):
                taken.setdefault(l["itemId"], []).append(i)
        auto = []
        if autolink:
            cands = [c for c in link_candidates(con, date) if c["id"] not in taken]
            for i, l in enumerate(lines):
                if l.get("itemId") or l.get("noLink"):
                    continue
                scored = sorted(((_score(l, c), c) for c in cands), key=lambda x: -x[0])
                if not scored or scored[0][0] < 0.8:
                    continue
                if len(scored) > 1 and scored[1][0] > scored[0][0] - 0.15:
                    continue  # two plausible rows: leave it for a person
                c = scored[0][1]
                l["itemId"] = c["id"]
                taken.setdefault(c["id"], []).append(i)
                cands = [x for x in cands if x["id"] != c["id"]]
                auto.append({"line": i + 1, "raw": l.get("raw"), "name": l.get("name"), "itemId": c["id"],
                             "item": c["name"], "score": round(scored[0][0], 2)})

        for i, l in enumerate(lines):
            qty = _num(l.get("qty")) or 1.0
            pid = _line_product(con, store_id, l)
            if pid and l.get("cat") in CATEGORIES:
                con.execute("UPDATE products SET cat = ?, dept = ?, catSource = 'receipt', updatedAt = ?"
                            " WHERE id = ? AND catSource = 'auto'", (l["cat"], dept_of(l["cat"]), ts, pid))
            con.execute(
                "INSERT INTO purchases (id, source, receiptId, line, storeId, date, itemId, productId, raw, name, brand,"
                " code, section, qty, sizeLabel, weightLb, perLb, price, regular, taxed, notes, createdAt, updatedAt)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id("pu"), "receipt", rid, i + 1, store_id, date, l.get("itemId"), pid, l.get("raw"),
                 l.get("name") or l.get("raw"), l.get("brand"), l.get("code"), l.get("section"), qty,
                 l.get("size"), _num(l.get("weightLb")), _num(l.get("perLb")), round(_num(l.get("price")), 2),
                 _num(l.get("regular")), 1 if l.get("taxed") else 0, l.get("notes"), ts, ts))

        if rec["ok"] and not partial:
            # fully itemized: these lines are the whole truth, item-level guesses are superseded
            con.execute("UPDATE purchases SET supersededBy = ? WHERE receiptId = ? AND source = 'item'"
                        " AND supersededBy IS NULL", (rid, rid))

        # the inventory rows these lines stocked carry the receipt's price
        corrected = []
        for item_id, idx in taken.items():
            item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not item:
                continue
            paid = round(sum(_num(lines[i]["price"]) for i in idx), 2)
            latest = _latest_purchase(con, item_id, exclude_receipt=rid, around=date, recorded=_recorded(con, rid))
            if latest and latest > date:
                continue  # the row already reflects a later purchase; this line is history
            _supersede(con, item_id, rid, date)
            if item["price"] != paid or item["receiptId"] != rid:
                con.execute("INSERT INTO item_price_audit (itemId, oldPrice, newPrice, oldReceiptId, newReceiptId,"
                            " reason, at) VALUES (?,?,?,?,?,?,?)",
                            (item_id, item["price"], paid, item["receiptId"], rid, "receipt line", ts))
                con.execute("UPDATE items SET price = ?, receiptId = ? WHERE id = ?", (paid, rid, item_id))
                if item["price"] is not None and abs((item["price"] or 0) - paid) > 0.005:
                    corrected.append({"itemId": item_id, "item": item["name"], "was": item["price"], "now": paid})

        loose = [c for c in link_candidates(con, date) if c["id"] not in taken and c.get("price") is None
                 and not c.get("deletedAt")]
    sweep_error = None
    try:
        sweep(con)
    except Exception as e:  # the receipt is committed; resolution can catch up later
        sweep_error = str(e)
    return {
        "receiptId": rid, "storeId": store_id, "date": date, "reconciliation": rec, "partial": partial,
        "lines": len(lines), "linked": sum(len(v) for v in taken.values()), "autoLinked": auto,
        "corrected": corrected,
        "unlinkedLines": [{"line": i + 1, "raw": l.get("raw"), "name": l.get("name"), "price": _num(l.get("price"))}
                          for i, l in enumerate(lines) if not l.get("itemId")],
        "unpricedItems": [{"id": c["id"], "name": c["name"]} for c in loose],
        **({"sweepError": sweep_error} if sweep_error else {}),
    }


def _iso(d) -> str | None:
    """A YYYY-MM-DD date or None: anything else ("Oct 20") never becomes a key or a series point."""
    if not d:
        return None
    d = str(d)[:10]
    try:
        dt.date.fromisoformat(d)
        return d
    except ValueError:
        return None


def _price(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def capture_item_price(con: sqlite3.Connection, item_id: str) -> bool:
    """Record an inventory row's current price as a purchase, by the same rules as the triggers.
    The CLI calls it after a priced restock, which the triggers can't see when the price is the
    same as last time."""
    it = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not it or _price(it["price"]) is None:
        return False
    r = con.execute("SELECT date, storeId, reconciled FROM receipts WHERE id = ?", (it["receiptId"],)).fetchone() \
        if it["receiptId"] else None
    if r and r["reconciled"] == 1:
        return False
    if con.execute("SELECT 1 FROM purchases WHERE itemId = ? AND source != 'item' AND COALESCE(receiptId, '') = ?",
                   (item_id, it["receiptId"] or "")).fetchone():
        return False
    day = _iso(r["date"] if r else None) or _iso(it["purchaseDate"]) or _iso(it["createdAt"]) or dt.date.today().isoformat()
    key = f"item|{item_id}|{it['receiptId'] or day}"
    ts = now_iso()
    cur = con.execute("UPDATE purchases SET price = ?, name = ?, brand = ?, sizeLabel = ?, productId = NULL,"
                      " unitPrice = NULL, updatedAt = ? WHERE captureKey = ?",
                      (it["price"], it["name"], it["brand"], it["size"], ts, key))
    if cur.rowcount:
        return True
    con.execute(
        "INSERT OR IGNORE INTO purchases (id, source, receiptId, storeId, date, itemId, name, brand, qty, sizeLabel,"
        " price, captureKey, createdAt, updatedAt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (new_id("pu"), "item", it["receiptId"], r["storeId"] if r else None, day, item_id, it["name"], it["brand"], 1,
         it["size"], it["price"], key, ts, ts))
    return True


def backfill_item_prices(con: sqlite3.Connection) -> int:
    """Rows priced before the capture triggers existed get their observation now."""
    n = 0
    with con:
        for it in con.execute("SELECT id FROM items WHERE price IS NOT NULL").fetchall():
            n += 1 if capture_item_price(con, it["id"]) else 0
    return n


# A receipt-less price is a guess at a receipt's purchase when it was logged in the receipt's
# window (the day before to three days after) AND before the receipt was recorded. Logged
# after the receipt was already in, it is a new purchase (a restock), never the receipt's.
_GUESS = ("source = 'item' AND receiptId IS NULL AND date BETWEEN date(?, '-1 day') AND date(?, '+3 days')"
          " AND replace(substr(createdAt, 1, 23), ' ', 'T') < replace(substr(?, 1, 23), ' ', 'T')")


def _recorded(con: sqlite3.Connection, rid: str) -> str:
    r = con.execute("SELECT createdAt FROM receipts WHERE id = ?", (rid,)).fetchone()
    return (r[0] if r and r[0] else None) or now_iso()


def _latest_purchase(con: sqlite3.Connection, item_id: str, exclude_receipt: str | None = None,
                     around: str | None = None, recorded: str | None = None) -> str | None:
    """The most recent purchase on record for an inventory row (any source, not superseded),
    leaving out the receipt being linked and the guesses at that receipt's purchase."""
    sql = ("SELECT MAX(date) FROM purchases WHERE itemId = ? AND supersededBy IS NULL"
           " AND COALESCE(receiptId, '') != COALESCE(?, '')")
    args = [item_id, exclude_receipt]
    if around:
        sql += f" AND NOT ({_GUESS})"
        args += [around, around, recorded or now_iso()]
    r = con.execute(sql, args).fetchone()
    return _iso(r[0]) if r else None


def _chain(con: sqlite3.Connection, store_id: str | None) -> str | None:
    if not store_id:
        return None
    r = con.execute("SELECT chain FROM stores WHERE id = ?", (store_id,)).fetchone()
    return r[0] if r else None


def _line_product(con: sqlite3.Connection, store_id: str | None, l: dict) -> str | None:
    """A receipt line's product. The item code is the strongest identity there is: the same code
    at the same chain is the same product, whatever the name was typed as."""
    code = (l.get("code") or "").strip()
    if code:
        chain = _chain(con, store_id)
        row = con.execute(
            "SELECT p.productId FROM purchases p JOIN stores s ON s.id = p.storeId"
            " WHERE p.code = ? AND s.chain = ? AND p.productId IS NOT NULL ORDER BY p.date LIMIT 1",
            (code, chain)).fetchone()
        if row:
            return row[0]
    return resolve_product(con, l.get("name") or l.get("raw"), l.get("brand"),
                           {"raw": l.get("raw"), "section": l.get("section")}, l.get("size"))


def link_line(con: sqlite3.Connection, purchase_id: str, item_id: str | None) -> None:
    """Point a receipt line at an inventory row (or unlink it with None) and re-price the row."""
    p = con.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
    if not p:
        raise ValueError("no such purchase")
    if item_id and not con.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
        raise ValueError(f"no inventory row {item_id}")
    with con:
        old = p["itemId"]
        con.execute("UPDATE purchases SET itemId = ?, productId = NULL, updatedAt = ? WHERE id = ?",
                    (item_id, now_iso(), purchase_id))
        for iid in {x for x in (old, item_id) if x}:
            _reprice_item(con, iid)
    try:
        sweep(con)
    except Exception:
        pass


def _supersede(con: sqlite3.Connection, item_id: str, rid: str, date: str) -> None:
    """A receipt line now prices this row: the price logged for the same purchase (on the same
    receipt, or a guess at it, see _GUESS) is superseded. Marked, not deleted, so unlinking can
    bring it back."""
    con.execute(f"UPDATE purchases SET supersededBy = ? WHERE itemId = ? AND source = 'item' AND supersededBy IS NULL"
                f" AND (receiptId = ? OR ({_GUESS}))", (rid, item_id, rid, date, date, _recorded(con, rid)))


def _reprice_item(con: sqlite3.Connection, item_id: str) -> None:
    """items.price = what the latest receipt linked to this row says it cost, unless the row has
    a newer purchase of its own. With no receipt lines left, undo what linking did."""
    item = con.execute("SELECT price, receiptId FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        return
    row = con.execute(
        "SELECT receiptId, MAX(date) AS date, SUM(price) AS paid FROM purchases WHERE itemId = ? AND source = 'receipt'"
        " GROUP BY receiptId ORDER BY date DESC LIMIT 1", (item_id,)).fetchone()
    ts = now_iso()
    if not row:
        # unlinked from every receipt: bring back the logged prices it had superseded, and the
        # price the row carried before a receipt set it
        con.execute("UPDATE purchases SET supersededBy = NULL WHERE itemId = ? AND source = 'item'"
                    " AND supersededBy IS NOT NULL AND supersededBy NOT IN (SELECT DISTINCT receiptId FROM purchases"
                    " WHERE itemId = ? AND source = 'receipt' AND receiptId IS NOT NULL)", (item_id, item_id))
        a = con.execute("SELECT oldPrice, oldReceiptId FROM item_price_audit WHERE itemId = ? AND newReceiptId IS ?"
                        " AND reason IN ('receipt line', 'relinked') ORDER BY id DESC LIMIT 1",
                        (item_id, item["receiptId"])).fetchone()
        if a and item["receiptId"]:
            con.execute("INSERT INTO item_price_audit (itemId, oldPrice, newPrice, oldReceiptId, newReceiptId, reason, at)"
                        " VALUES (?,?,?,?,?,?,?)", (item_id, item["price"], a["oldPrice"], item["receiptId"],
                                                    a["oldReceiptId"], "unlinked", ts))
            con.execute("UPDATE items SET price = ?, receiptId = ? WHERE id = ?", (a["oldPrice"], a["oldReceiptId"], item_id))
        return
    latest = _latest_purchase(con, item_id, exclude_receipt=row["receiptId"], around=row["date"],
                              recorded=_recorded(con, row["receiptId"]))
    if latest and row["date"] and latest > row["date"]:
        return  # a newer purchase (a priced restock) owns the row's price
    paid = round(row["paid"], 2)
    if item["price"] != paid or item["receiptId"] != row["receiptId"]:
        con.execute("INSERT INTO item_price_audit (itemId, oldPrice, newPrice, oldReceiptId, newReceiptId, reason, at)"
                    " VALUES (?,?,?,?,?,?,?)", (item_id, item["price"], paid, item["receiptId"], row["receiptId"],
                                                "relinked", ts))
        _supersede(con, item_id, row["receiptId"], row["date"])
        con.execute("UPDATE items SET price = ?, receiptId = ? WHERE id = ?", (paid, row["receiptId"], item_id))


# ------------------------------------------------------------------------------- sweep

def _item_ctx(it) -> dict:
    return {"category": it["category"], "subCategory": it["subCategory"], "spiritType": it["spiritType"],
            "storage": it["storage"]}


_PER_EACH_UNITS = ("", "each", "ea", "ct", "count", "pack", "packs", "pk")


def _f(v) -> float | None:
    """A number from a column agents fill freely ("1/2", "2 bags", "1.5")."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip()
    try:
        return float(t)
    except ValueError:
        pass
    m = re.match(r"^(\d+)\s*/\s*(\d+)", t)
    if m and int(m.group(2)):
        return int(m.group(1)) / int(m.group(2))
    m = re.match(r"^(\d+(?:\.\d+)?)", t)
    return float(m.group(1)) if m else None


def sweep(con: sqlite3.Connection) -> dict:
    """Resolve everything that can be resolved. Returns counts of what changed.

    Order matters: purchases get products (receipt lines carry the best identity: brand + code),
    same-code products merge, inventory rows follow the product of their latest receipt line,
    products learn taxonomy and size, and only then are amounts and unit prices computed."""
    stats = {"stores": 0, "items": 0, "purchases": 0, "products": 0, "merged": 0, "skipped": 0}
    with con:
        for r in con.execute("SELECT id, store FROM receipts WHERE storeId IS NULL AND store IS NOT NULL").fetchall():
            con.execute("UPDATE receipts SET storeId = ? WHERE id = ?", (resolve_store(con, r["store"]), r["id"]))
            stats["stores"] += 1

        # 1. purchases: store/date from the receipt, a product for every line
        for p in con.execute("SELECT p.*, r.date AS rdate, r.storeId AS rstore FROM purchases p"
                             " LEFT JOIN receipts r ON r.id = p.receiptId").fetchall():
            patch = {}
            if p["receiptId"] and p["rstore"] and p["storeId"] != p["rstore"]:
                patch["storeId"] = p["rstore"]
            if p["receiptId"] and _iso(p["rdate"]) and p["date"] != _iso(p["rdate"]):
                patch["date"] = _iso(p["rdate"])
            if not p["productId"] or not con.execute("SELECT 1 FROM products WHERE id = ?", (p["productId"],)).fetchone():
                if p["source"] == "item" and p["itemId"]:
                    it = con.execute("SELECT * FROM items WHERE id = ?", (p["itemId"],)).fetchone()
                    pid = resolve_product(con, it["name"], it["brand"], _item_ctx(it), it["size"]) if it else \
                        resolve_product(con, p["name"], p["brand"])
                else:
                    pid = _line_product(con, patch.get("storeId") or p["storeId"], dict(p))
                patch["productId"] = pid
            if patch:
                sets = ", ".join(f"{k} = ?" for k in patch)
                con.execute(f"UPDATE purchases SET {sets} WHERE id = ?", (*patch.values(), p["id"]))
                stats["purchases"] += 1

        # 2. one code at one chain is one product
        groups: dict[tuple, set] = {}
        for r in con.execute("SELECT s.chain, p.code, p.productId, COUNT(*) n FROM purchases p JOIN stores s ON"
                             " s.id = p.storeId WHERE p.code IS NOT NULL AND p.code != '' AND p.productId IS NOT NULL"
                             " GROUP BY 1, 2, 3").fetchall():
            groups.setdefault((r[0], r[1]), set()).add((r[3], r[2]))
        merged: dict[str, str] = {}

        def live(pid):
            while pid in merged:
                pid = merged[pid]
            return pid
        for members in groups.values():
            ids = {}
            for n, pid in members:
                pid = live(pid)
                ids[pid] = ids.get(pid, 0) + n
            if len(ids) > 1:
                ranked = sorted(ids.items(), key=lambda m: -m[1])
                keep = ranked[0][0]
                for drop, _ in ranked[1:]:
                    merge_products(con, keep, drop)
                    merged[drop] = keep
                    stats["merged"] += 1

        # 3. inventory rows: the product of their latest receipt line, else their own name
        latest = {r[0]: r[1] for r in con.execute(
            "SELECT itemId, productId FROM purchases WHERE itemId IS NOT NULL AND source = 'receipt'"
            " AND productId IS NOT NULL ORDER BY date, line").fetchall()}
        for it in con.execute("SELECT * FROM items").fetchall():
            pid = latest.get(it["id"]) or resolve_product(con, it["name"], it["brand"], _item_ctx(it), it["size"])
            if not pid:
                continue
            pr = con.execute("SELECT dept, cat, catSource FROM products WHERE id = ?", (pid,)).fetchone()
            if not pr:
                stats["skipped"] += 1
                continue
            if pr["catSource"] != "auto":
                dept, cat = pr["dept"], pr["cat"]
            else:
                c = _ctx_classify(it["name"], it["brand"], _item_ctx(it))
                dept, cat = c["dept"], c["cat"]
            if (it["productId"], it["dept"], it["cat"]) != (pid, dept, cat):
                con.execute("UPDATE items SET productId = ?, dept = ?, cat = ? WHERE id = ?", (pid, dept, cat, it["id"]))
                stats["items"] += 1

        # 4. products: taxonomy from the richest context (a linked inventory row knows its storage
        #    and the agent's hints; a receipt line knows its section), size once anything states it
        for pr in con.execute("SELECT * FROM products").fetchall():
            patch = {}
            cat, generic = pr["cat"], pr["generic"]
            if pr["catSource"] == "auto":
                it = con.execute("SELECT * FROM items WHERE productId = ? ORDER BY updatedAt DESC LIMIT 1",
                                 (pr["id"],)).fetchone()
                if it:
                    c = _ctx_classify(it["name"], it["brand"], _item_ctx(it))
                    if not c["generic"]:
                        g = _ctx_classify(pr["name"], pr["brand"])
                        if g["cat"] == c["cat"]:
                            c["generic"] = g["generic"]
                else:
                    pu = con.execute("SELECT raw, section FROM purchases WHERE productId = ? ORDER BY date DESC LIMIT 1",
                                     (pr["id"],)).fetchone()
                    c = _ctx_classify(pr["name"], pr["brand"], dict(pu) if pu else None)
                cat, generic = c["cat"], c["generic"]
                if (pr["dept"], pr["cat"], pr["generic"]) != (c["dept"], c["cat"], c["generic"]):
                    patch.update({"dept": c["dept"], "cat": c["cat"], "generic": c["generic"],
                                  "confidence": c["confidence"]})
            elif not pr["generic"]:
                g = _ctx_classify(pr["name"], pr["brand"])
                if g["generic"] and g["cat"] == pr["cat"]:
                    patch["generic"] = generic = g["generic"]
            if not pr["sizeAmount"]:
                labels = [r[0] for r in con.execute(
                    "SELECT sizeLabel FROM purchases WHERE productId = ? AND sizeLabel IS NOT NULL UNION ALL "
                    "SELECT size FROM items WHERE productId = ? AND size IS NOT NULL", (pr["id"], pr["id"])).fetchall()]
                for lab in labels:
                    got = parse_size(lab, cat, generic)
                    if got:
                        patch.update({"sizeAmount": got[0], "sizeDim": got[1], "sizeLabel": lab})
                        break
            if patch:
                patch["updatedAt"] = now_iso()
                sets = ", ".join(f"{k} = ?" for k in patch)
                con.execute(f"UPDATE products SET {sets} WHERE id = ?", (*patch.values(), pr["id"]))
                stats["products"] += 1

        # 5. amounts and unit prices
        linked_lines = {(r[0], r[1]): r[2] for r in con.execute(
            "SELECT itemId, receiptId, COUNT(*) FROM purchases WHERE itemId IS NOT NULL AND source = 'receipt'"
            " GROUP BY 1, 2").fetchall()}
        for p in con.execute("SELECT p.*, pr.cat AS pcat, pr.generic AS pgen, pr.sizeAmount AS psize,"
                             " pr.sizeDim AS pdim FROM purchases p LEFT JOIN products pr ON pr.id = p.productId").fetchall():
            item = con.execute("SELECT * FROM items WHERE id = ?", (p["itemId"],)).fetchone() if p["itemId"] else None
            prod_size = (p["psize"], p["pdim"]) if p["psize"] else None
            amount = dim = None
            qty = _f(p["qty"]) or 1.0
            if _f(p["weightLb"]) or p["sizeLabel"]:
                amount, dim = line_amount(qty, p["sizeLabel"], _f(p["weightLb"]), p["pcat"], prod_size, p["pgen"])
            elif item and (p["source"] == "item" or linked_lines.get((item["id"], p["receiptId"])) == 1):
                # the inventory row says how much: "0.5 lb" of deli turkey, "3" microwave pouches
                m = measure_of(_f(item["quantity"]), item["unit"], p["pcat"], p["pgen"])
                if m and m[1] != "count":
                    amount, dim = m
                elif item["size"] and parse_size(item["size"], p["pcat"], p["pgen"]):
                    amount, dim = line_amount(qty, item["size"], None, p["pcat"], None, p["pgen"])
            if amount is None:
                amount, dim = line_amount(qty, None, None, p["pcat"], prod_size, p["pgen"])
                q = _f(item["quantity"]) if item else None
                if dim == "pkg" and p["source"] == "item" and q and (item["unit"] or "").lower() in _PER_EACH_UNITS \
                        and q > 1:
                    amount, dim = q, "count"
            price = _price(p["price"])
            up = round(price / amount, 6) if amount and price else None
            if (p["amount"], p["dim"], p["unitPrice"]) != (amount, dim, up):
                con.execute("UPDATE purchases SET amount = ?, dim = ?, unitPrice = ? WHERE id = ?",
                            (amount, dim, up, p["id"]))
                stats["purchases"] += 1
    return stats


def set_product_category(con: sqlite3.Connection, product_id: str, cat: str) -> None:
    """A person placed it. catSource='user' means sweep() never moves it again."""
    with con:
        con.execute("UPDATE products SET cat = ?, dept = ?, catSource = 'user', confidence = 'user', updatedAt = ?"
                    " WHERE id = ?", (cat, dept_of(cat), now_iso(), product_id))
        con.execute("UPDATE items SET cat = ?, dept = ? WHERE productId = ?", (cat, dept_of(cat), product_id))


def receipt_json(con: sqlite3.Connection, rid: str) -> dict | None:
    r = con.execute("SELECT * FROM receipts WHERE id = ?", (rid,)).fetchone()
    if not r:
        return None
    out = dict(r)
    out["lines"] = [dict(x) for x in con.execute(
        "SELECT * FROM purchases WHERE receiptId = ? ORDER BY source = 'item', line, createdAt", (rid,)).fetchall()]
    return out


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, default=str)
