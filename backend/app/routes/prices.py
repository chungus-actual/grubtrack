"""Price intelligence: the price book, one product's story, what moved, where it's cheaper."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from ..db import get_con, intel, ledger
from foodcore import CATEGORIES

router = APIRouter(prefix="/api")


@router.get("/prices")
def prices(con=Depends(get_con)):
    """The whole Prices page in one call."""
    book = intel.price_book(con)
    return {
        "overview": intel.overview(con),
        "book": book,
        "movers": intel.movers(con),
        "inflation": intel.inflation(con),
        "storeIndex": intel.store_index(con),
        "cadence": intel.cadence(con),
        "taxonomy": intel.taxonomy(),
    }


@router.get("/products/review")
def review(con=Depends(get_con)):
    """Products the classifier couldn't place with confidence, that matter (in stock or bought)."""
    rows = con.execute(
        "SELECT * FROM products WHERE catSource = 'auto' AND (cat LIKE '%.other' OR confidence = 'low')"
        " AND id IN (SELECT productId FROM items WHERE deletedAt IS NULL UNION SELECT productId FROM purchases"
        " WHERE supersededBy IS NULL)"
        " ORDER BY name").fetchall()
    out = []
    for r in rows:
        raw = con.execute("SELECT raw, section FROM purchases WHERE productId = ? LIMIT 1", (r["id"],)).fetchone()
        out.append({**dict(r), "catLabel": intel.cat_label(r["cat"]), "raw": raw["raw"] if raw else None,
                    "section": raw["section"] if raw else None})
    return {"products": out, "taxonomy": intel.taxonomy()}


@router.get("/products/{pid}")
def product(pid: str, con=Depends(get_con)):
    p = intel.product_detail(con, pid)
    if not p:
        raise HTTPException(404, "Product not found")
    return p


@router.patch("/products/{pid}")
def edit_product(pid: str, data: dict = Body(...), con=Depends(get_con)):
    pr = con.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not pr:
        raise HTTPException(404, "Product not found")
    if "cat" in data:
        if data["cat"] not in CATEGORIES:
            raise HTTPException(400, "Unknown category")
        ledger.set_product_category(con, pid, data["cat"])
    sets = {}
    if (data.get("name") or "").strip():
        sets["name"] = data["name"].strip()
    if "generic" in data:
        sets["generic"] = (data["generic"] or "").strip().lower() or None
    if "size" in data:
        from foodcore.units import parse_size
        lab = (data["size"] or "").strip()
        got = parse_size(lab, pr["cat"], pr["generic"]) if lab else None
        if lab and not got:
            raise HTTPException(400, f"Can't read a size from {lab!r} (try '32 oz', '1.5 L', 'dozen')")
        sets.update({"sizeLabel": lab or None, "sizeAmount": got[0] if got else None, "sizeDim": got[1] if got else None})
        con.execute("UPDATE purchases SET amount = NULL, unitPrice = NULL WHERE productId = ?", (pid,))
    if sets:
        with con:
            con.execute(f"UPDATE products SET {', '.join(f'{k} = ?' for k in sets)}, updatedAt = ? WHERE id = ?",
                        (*sets.values(), ledger.now_iso(), pid))
    ledger.sweep(con)
    return intel.product_detail(con, pid)


@router.post("/products/{pid}/merge")
def merge(pid: str, data: dict = Body(...), con=Depends(get_con)):
    """This product is the same thing as `into`: fold it in and remember the name."""
    into = data.get("into")
    if not into or not con.execute("SELECT 1 FROM products WHERE id = ?", (into,)).fetchone():
        raise HTTPException(400, "into: a product id is required")
    with con:
        ledger.merge_products(con, into, pid)
    ledger.sweep(con)
    return intel.product_detail(con, into)


@router.get("/products")
def search_products(q: str = "", limit: int = 12, con=Depends(get_con)):
    """For pickers (merge into..., link a line to...)."""
    qn = f"%{q.strip().lower()}%"
    rows = con.execute("SELECT id, name, brand, cat, generic FROM products WHERE lower(name) LIKE ? OR"
                       " lower(coalesce(brand,'')) LIKE ? OR lower(coalesce(generic,'')) LIKE ? ORDER BY"
                       " (SELECT COUNT(*) FROM purchases WHERE productId = products.id) DESC, name LIMIT ?",
                       (qn, qn, qn, min(limit, 50))).fetchall()
    return [{**dict(r), "catLabel": intel.cat_label(r["cat"])} for r in rows]


@router.get("/taxonomy")
def taxonomy():
    return intel.taxonomy()
