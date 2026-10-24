"""The shopping list: the same `shopping` table the agent skill and any kiosks use, priced from the ledger.

Rows are crossed off, never deleted (doneAt is the history that answers "did we already buy
that"); only "clear crossed-off" removes rows, same as `fooddb_sqlite.py shop-clear`."""

from __future__ import annotations

import asyncio
import datetime as dt

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import ai, config
from ..db import fdb, get_con, intel
from foodcore.ledger import product_key

router = APIRouter(prefix="/api")


def _suggestions(con, open_names: set[str]) -> list[dict]:
    """Restock ideas: things on a buying rhythm that are due, and things recently eaten that
    aren't back in the house."""
    in_stock = {r[0] for r in con.execute("SELECT productId FROM items WHERE deletedAt IS NULL AND productId IS NOT NULL")}
    stock_names = {product_key(r[0]) for r in con.execute("SELECT name FROM items WHERE deletedAt IS NULL")}
    taken = {product_key(n) for n in open_names}
    out, seen = [], set()
    for c in intel.cadence(con):
        if c["status"] in ("due", "overdue") and c["productId"] not in in_stock:
            k = product_key(c["name"])
            if k not in taken and k not in seen:
                seen.add(k)
                out.append({"name": c["name"], "why": f"bought every ~{round(c['every'])} days, last {c['last']}",
                            "kind": "rhythm", "price": c["lastPrice"]})
    since = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    rows = con.execute(
        "SELECT name, COUNT(*) n, MAX(deletedAt) last, MAX(price) price FROM items WHERE deletedAt IS NOT NULL"
        " AND substr(deletedAt,1,10) >= ? AND lower(coalesce(notes,'')) LIKE '%eaten%' AND name IS NOT NULL"
        " GROUP BY lower(name) ORDER BY n DESC, last DESC LIMIT 40", (since,)).fetchall()
    for r in rows:
        k = product_key(r["name"])
        if k in taken or k in seen or k in stock_names:
            continue
        seen.add(k)
        out.append({"name": r["name"], "why": f"eaten {r['n']}× lately, none in stock", "kind": "eaten",
                    "price": r["price"]})
    return out[:16]


@router.get("/shopping")
def shopping(con=Depends(get_con)):
    rows = fdb.shop_rows(con, include_done=True, limit=300)
    open_rows = [r for r in rows if not r["doneAt"]]
    week = (dt.datetime.now().astimezone() - dt.timedelta(days=7)).isoformat()
    done = [r for r in rows if r["doneAt"] and r["doneAt"] >= week]
    est = intel.estimate(con, [r["name"] for r in open_rows])
    by_name = {}
    for ln in est["lines"]:
        by_name.setdefault(ln["name"], ln)
    for r in open_rows:
        r["estimate"] = by_name.get(r["name"])
    return {"open": open_rows, "done": done, "estimate": {k: v for k, v in est.items() if k != "lines"},
            "suggestions": _suggestions(con, {r["name"] for r in open_rows})}


@router.post("/shopping", status_code=201)
def add(data: dict = Body(...), con=Depends(get_con)):
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "A name is required")
    return fdb.shop_add(con, name, quantity=data.get("quantity"), unit=data.get("unit"), notes=data.get("notes"),
                        by=data.get("by") or "grubtrack")


@router.post("/shopping/{sid}/done")
def done(sid: str, con=Depends(get_con)):
    if not fdb.shop_set_done(con, sid, True):
        raise HTTPException(404, "Not on the list")
    return {"ok": True}


@router.post("/shopping/{sid}/undo")
def undo(sid: str, con=Depends(get_con)):
    if not fdb.shop_set_done(con, sid, False):
        raise HTTPException(404, "Not on the list")
    return {"ok": True}


@router.post("/shopping/clear")
def clear(con=Depends(get_con)):
    cur = con.execute("DELETE FROM shopping WHERE doneAt IS NOT NULL")
    con.commit()
    return {"removed": cur.rowcount}


@router.post("/shopping/send")
async def send(con=Depends(get_con)):
    """The agent sends the open list to GT_LIST_SEND_TO with its own messaging tools."""
    if not ai.route("act"):
        raise HTTPException(503, "Sending needs an agent and a destination: set GT_AGENT and GT_LIST_SEND_TO.")
    rows = [r for r in fdb.shop_rows(con) if not r["doneAt"]]
    if not rows:
        return {"ok": True, "message": "Nothing to send"}
    lines = "\n".join(f"- {r['name']}" + (f" ({r['quantity']:g} {r['unit'] or ''})".rstrip() if r["quantity"] else "")
                      for r in rows)
    prompt = (f"Send this shopping list to {config.LIST_SEND_TO}. Format it with a 🛒 header and "
              f"the items as a clean list. {config.AI_STYLE}\n\n{lines}")
    try:
        await asyncio.wait_for(ai.act(prompt), timeout=120)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"{config.AGENT_NAME} didn't answer in time")
    except ai.AIError as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


@router.get("/shopping-list")
def legacy_list(con=Depends(get_con)):
    """Read-only view in the old shape ({items: [{id, text, checked}]}), for anything that still
    asks here. The list lives in food.db's `shopping` table; write through fooddb_sqlite.py."""
    return {"items": [{"id": r["id"], "text": r["name"], "checked": bool(r["doneAt"])}
                      for r in fdb.shop_rows(con, include_done=True)]}


@router.put("/shopping-list")
def legacy_put():
    raise HTTPException(410, "The shopping list moved to food.db: use fooddb_sqlite.py shop-add / shop-done")
