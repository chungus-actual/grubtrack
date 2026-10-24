"""Money out: trips (receipts), stores, and what was thrown away."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

from .. import ai, config, receipt_reader
from ..db import get_con, intel, ledger

router = APIRouter(prefix="/api")


@router.get("/spend")
def spend(con=Depends(get_con)):
    return {
        "spend": intel.spend(con, months=12),
        "stores": intel.stores(con),
        "waste": intel.waste(con, 90),
        "overview": intel.overview(con),
    }


@router.get("/stores")
def store_list(con=Depends(get_con)):
    """Stores in the order they were first seen: that order is each store's chart colour, so a
    store keeps its colour on every page and through every filter."""
    rows = con.execute("SELECT id, name, chain, branch, address FROM stores ORDER BY createdAt, id").fetchall()
    return [{**dict(r), "slot": i + 1} for i, r in enumerate(rows)]


PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
MAX_PHOTO = 15 * 1024 * 1024


@router.post("/receipts", status_code=201)
def add_receipt(data: dict = Body(...), con=Depends(get_con)):
    """Record a receipt: the same JSON as the CLI's `receipt-add`. `"replace": true` with its `id`
    rewrites one already recorded."""
    replace = bool(data.pop("replace", False))
    try:
        return ledger.import_receipt(con, data, replace=replace)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/receipts/read")
async def read_receipt(photo: UploadFile = File(...)):
    """A receipt photo to a draft for a person to check before saving. The photo is kept under
    images/receipts/ so the saved receipt can show it."""
    if not ai.route("vision"):
        raise HTTPException(503, "Reading photos needs a vision model: set GT_MODEL_PROVIDER and GT_MODEL.")
    ext = PHOTO_TYPES.get((photo.content_type or "").lower())
    if not ext:
        raise HTTPException(400, "Send a JPEG, PNG, WebP or GIF photo")
    data = await photo.read()
    if not data or len(data) > MAX_PHOTO:
        raise HTTPException(400, "The photo is empty or over 15 MB")
    rel = f"images/receipts/{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}.{ext}"
    path = config.DATA_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        out = await receipt_reader.read(data, photo.content_type.lower())
    except (ai.AIError, ai.AIUnavailable) as e:
        path.unlink(missing_ok=True)
        raise HTTPException(502, str(e))
    out["draft"]["image"] = rel
    return out


@router.get("/receipts/{rid}")
def receipt(rid: str, con=Depends(get_con)):
    r = ledger.receipt_json(con, rid)
    if not r:
        raise HTTPException(404, "Receipt not found")
    store = con.execute("SELECT * FROM stores WHERE id = ?", (r["storeId"],)).fetchone() if r["storeId"] else None
    r["storeInfo"] = dict(store) if store else None
    prods = {p["id"]: dict(p) for p in con.execute("SELECT * FROM products").fetchall()}
    items = {i["id"]: i for i in con.execute("SELECT id, name, deletedAt, storage FROM items").fetchall()}
    for ln in r["lines"]:
        pr = prods.get(ln["productId"])
        ln["product"] = {"id": pr["id"], "name": pr["name"], "cat": pr["cat"], "catLabel": intel.cat_label(pr["cat"]),
                         "generic": pr["generic"]} if pr else None
        it = items.get(ln["itemId"])
        ln["item"] = {"id": it["id"], "name": it["name"], "gone": bool(it["deletedAt"]), "storage": it["storage"]} \
            if it else None
        ln["dept"] = pr["dept"] if pr else None
    covered = sum(ln["price"] for ln in r["lines"])
    r["unitemized"] = round(max(0.0, (r["total"] or 0) - covered - (r["tax"] or 0)), 2) if r["total"] else None
    return r


@router.post("/receipts/{rid}/lines/{pid}/link")
def link(rid: str, pid: str, data: dict = Body(default={}), con=Depends(get_con)):
    if not con.execute("SELECT 1 FROM purchases WHERE id = ? AND receiptId = ?", (pid, rid)).fetchone():
        raise HTTPException(404, "Line not found on that receipt")
    try:
        ledger.link_line(con, pid, data.get("itemId") or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/receipts/{rid}/candidates")
def candidates(rid: str, con=Depends(get_con)):
    """Inventory rows this receipt could have stocked, for the link picker."""
    r = con.execute("SELECT date FROM receipts WHERE id = ?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "Receipt not found")
    return [{"id": c["id"], "name": c["name"], "storage": c["storage"], "price": c["price"],
             "gone": bool(c["deletedAt"])} for c in ledger.link_candidates(con, r["date"])]


@router.get("/stores/{sid}")
def store(sid: str, con=Depends(get_con)):
    s = next((x for x in intel.stores(con) if x["id"] == sid), None)
    if not s:
        raise HTTPException(404, "Store not found")
    book = intel.price_book(con)
    here = []
    for p in book:
        mine = next((x for x in p["stores"] if x["storeId"] == sid), None)
        if not mine:
            continue
        others = [x for x in p["stores"] if x["storeId"] != sid and x["avg"] is not None]
        best_other = min(others, key=lambda x: x["avg"]) if others else None
        here.append({"id": p["id"], "name": p["name"], "cat": p["cat"], "catLabel": p["catLabel"], "dept": p["dept"],
                     "basis": p["basis"], "n": mine["n"], "last": mine["last"], "avg": mine["avg"],
                     "lastDate": mine["lastDate"], "lastPrice": p["last"]["price"] if p["last"]["storeId"] == sid else None,
                     "other": best_other})
    s["products"] = here
    s["trips"] = [t for t in intel.spend(con)["trips"] if t["storeId"] == sid]
    return s


@router.patch("/stores/{sid}")
def edit_store(sid: str, data: dict = Body(...), con=Depends(get_con)):
    if not con.execute("SELECT 1 FROM stores WHERE id = ?", (sid,)).fetchone():
        raise HTTPException(404, "Store not found")
    sets = {k: (data[k] or None) for k in ("name", "branch", "address", "city", "region", "postal", "notes")
            if k in data}
    if sets:
        with con:
            con.execute(f"UPDATE stores SET {', '.join(f'{k} = ?' for k in sets)}, updatedAt = ? WHERE id = ?",
                        (*sets.values(), ledger.now_iso(), sid))
    return store(sid, con)
