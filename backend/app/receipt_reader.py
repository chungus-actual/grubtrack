"""A receipt photo to a draft the person checks before it's saved: the same line-by-line shape the
food-inventory skill writes, read by the configured vision model."""

from __future__ import annotations

import datetime as dt
import re

from . import ai
from .db import ledger

PROMPT = """Transcribe this shop receipt into JSON. Answer with ONE JSON object and nothing else:
{"store": "store name, with the address if printed", "date": "YYYY-MM-DD",
 "tax": number, "total": number, "subtotal": number or null, "savings": number or null,
 "payment": "card type and last 4 digits, or cash" or null, "partial": false,
 "lines": [
  {"raw": "the line exactly as printed", "name": "what it is in plain words", "brand": string or null,
   "code": "item number printed on the line" or null, "section": "department header above it" or null,
   "price": number, "regular": number or null, "qty": number, "weightLb": number or null,
   "perLb": number or null, "taxed": true or false, "size": string or null}
 ]}

Rules:
- One entry per purchased line, top to bottom, every line (household items too, not only food).
- price is what was paid for the line after any sale. When a sale or "Regular Price" note sits under
  an item, fold it into that item: price = the price paid, regular = the price before the sale.
- A discount that applies to the whole order (not under one item) is its own line with a negative price.
- Bag fees and bottle deposits are lines. Subtotal, tax, total, change and savings summaries are not.
- qty is the count for multi-buys like "2 @ 1.99" (price is the line total); otherwise 1.
- Weighed items: weightLb and perLb as printed, price is the line total.
- Numbers without $ or commas. Dates as YYYY-MM-DD.
- Lines plus tax should equal the total. If they don't, look again. Never invent or adjust a line to
  make it add up; if part of the receipt can't be read, set "partial": true.
- Never include full card numbers, auth codes or reference numbers."""


def _num(v):
    if v is None or v == "":
        return None
    try:
        return round(float(str(v).replace("$", "").replace(",", "").strip()), 2)
    except ValueError:
        return None


def _date(v) -> str | None:
    s = str(v or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        mo, d, y = (int(x) for x in m.groups())
        y = y + 2000 if y < 100 else y
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def clean(draft: dict) -> dict:
    """Shape whatever the model returned into the receipt-add format, dropping lines with no price."""
    lines = []
    for ln in draft.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        price = _num(ln.get("price"))
        if price is None:
            continue
        out = {"raw": str(ln.get("raw") or ln.get("name") or "").strip() or None,
               "name": str(ln.get("name") or ln.get("raw") or "").strip() or None, "price": price}
        for k in ("brand", "code", "section", "size"):
            if ln.get(k) not in (None, ""):
                out[k] = str(ln[k]).strip()
        for k in ("regular", "weightLb", "perLb"):
            v = _num(ln.get(k))
            if v is not None:
                out[k] = v
        qty = _num(ln.get("qty"))
        if qty and qty != 1:
            out["qty"] = qty
        if ln.get("taxed") is True:
            out["taxed"] = True
        lines.append(out)
    return {
        "store": str(draft.get("store") or "").strip() or None,
        "date": _date(draft.get("date")),
        "tax": _num(draft.get("tax")),
        "total": _num(draft.get("total")),
        "subtotal": _num(draft.get("subtotal")),
        "savings": _num(draft.get("savings")),
        "payment": str(draft.get("payment") or "").strip() or None,
        "partial": bool(draft.get("partial")),
        "lines": lines,
    }


def warnings(draft: dict, today: dt.date | None = None) -> list[str]:
    """What to look at before saving. A read that adds up can still be wrong: a model can misread a
    digit in a line and in the total together, or "fix" the tax to make the sum work."""
    out = []
    today = today or dt.date.today()
    if not draft["store"]:
        out.append("No store name was read. Type it in as printed.")
    if not draft["date"]:
        out.append("No date was read.")
    elif abs((dt.date.fromisoformat(draft["date"]) - today).days) > 60:
        out.append(f"The date reads as {draft['date']}. Check it against the photo.")
    if draft["total"] is None:
        out.append("No total was read.")
    if draft["partial"]:
        out.append("Part of the receipt couldn't be read.")
    return out


async def read(data: bytes, media_type: str) -> dict:
    raw = await ai.read_image(PROMPT, data, media_type)
    draft = clean(ai.parse_json(raw))
    return {"draft": draft, "reconciliation": ledger.reconcile(draft["lines"], draft["tax"], draft["total"]),
            "warnings": warnings(draft)}
