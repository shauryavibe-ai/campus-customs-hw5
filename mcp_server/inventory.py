"""Inventory agent tools: stock by SKU and size, shortfalls, and who can restock.

Owns `inventory` and `pricing` (harness section 1). Reads `tickets`, `vendors`,
`vendor_products`, `invoices` and `purchase_orders` to see demand and incoming stock.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from fastmcp.exceptions import ToolError

from mcp_server.db import business_date, connect, log_activity, money

LOW_STOCK_THRESHOLD = 3  # a size at or below this many units counts as "low"


def _stock_status(qty: int, threshold: int = LOW_STOCK_THRESHOLD) -> str:
    if qty <= 0:
        return "out_of_stock"
    return "low_stock" if qty <= threshold else "in_stock"


def _product(conn: sqlite3.Connection, sku: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT i.sku, i.name, i.location, p.unit_cost, p.list_price"
        " FROM inventory i LEFT JOIN pricing p ON p.sku = i.sku WHERE i.sku = ? LIMIT 1",
        (sku,),
    ).fetchone()
    if row is None:
        known = [r["sku"] for r in conn.execute("SELECT DISTINCT sku FROM inventory ORDER BY sku")]
        raise ToolError(f"Unknown SKU {sku!r}. Known SKUs: {', '.join(known)}")
    return row


def incoming_stock(conn: sqlite3.Connection, sku: str, size: str | None = None) -> list[dict]:
    """Stock already on its way: open purchase orders, and open vendor invoices whose
    description names this SKU (e.g. invoice 501 "Rush reprint CC-TEE-WHITE S")."""
    incoming = []
    po_sql = ("SELECT po.*, v.name AS vendor FROM purchase_orders po JOIN vendors v ON v.id = po.vendor_id"
              " WHERE po.sku = ? AND po.status IN ('pending_approval', 'approved')")
    params: list = [sku]
    if size:
        po_sql += " AND po.size = ?"
        params.append(size)
    for po in conn.execute(po_sql, params):
        incoming.append({
            "source": "purchase_order", "purchase_order_id": po["id"], "vendor": po["vendor"],
            "size": po["size"], "qty": po["qty"], "status": po["status"],
            "expected_date": po["expected_date"],
        })
    for inv in conn.execute(
        "SELECT i.*, v.name AS vendor, v.lead_days FROM invoices i JOIN vendors v ON v.id = i.vendor_id"
        " WHERE i.status = 'open' AND i.description LIKE ?", (f"%{sku}%",)
    ):
        words = (inv["description"] or "").split()
        if size and size not in words:
            continue
        incoming.append({
            "source": "vendor_invoice", "invoice_id": inv["id"], "vendor": inv["vendor"],
            "description": inv["description"], "invoice_status": inv["status"],
            "invoice_due_date": inv["due_date"],
            "note": f"Qty not recorded. Vendor lead time is {inv['lead_days']} days; "
                    "the reprint may be held until this invoice is paid.",
        })
    return incoming


def check_stock(sku: str, size: str | None = None, qty_needed: int | None = None) -> dict:
    """Check stock for a product, optionally one size. With `size` and `qty_needed`, also
    say whether the shop can fill that quantity and how many units it is short."""
    sku = sku.strip().upper()
    size = size.strip().upper() if size else None
    with connect() as conn:
        product = _product(conn, sku)
        rows = conn.execute("SELECT size, qty FROM inventory WHERE sku = ?", (sku,)).fetchall()
        sizes = {r["size"]: r["qty"] for r in rows}
        if size and size not in sizes:
            raise ToolError(f"{sku} has no size {size!r}. Sizes: {', '.join(sizes)}")
        shown = {size: sizes[size]} if size else sizes
        result = {
            "sku": sku, "name": product["name"], "location": product["location"],
            "list_price": product["list_price"],
            "business_date": business_date(conn).isoformat(),
            "sizes": [{"size": s, "qty": q, "status": _stock_status(q)} for s, q in shown.items()],
            "total_units": sum(shown.values()),
            "incoming": incoming_stock(conn, sku, size),
        }
    if qty_needed is not None:
        if not size:
            raise ToolError("Give a size together with qty_needed.")
        if qty_needed <= 0:
            raise ToolError("qty_needed must be at least 1.")
        on_hand = sizes[size]
        result["order_check"] = {
            "qty_needed": qty_needed, "on_hand": on_hand,
            "can_fill": on_hand >= qty_needed, "short_by": max(0, qty_needed - on_hand),
            "other_sizes_in_stock": {s: q for s, q in sizes.items() if s != size and q > 0},
        }
    return result


def find_shortfalls(low_stock_threshold: int = LOW_STOCK_THRESHOLD) -> dict:
    """List every size that is out of stock or low, and every open ticket asking for
    more units than the shop has. Each item shows any stock already on its way."""
    if low_stock_threshold < 0:
        raise ToolError("low_stock_threshold can't be negative.")
    with connect() as conn:
        stock_issues = [
            {"sku": r["sku"], "name": r["name"], "size": r["size"], "qty": r["qty"],
             "status": _stock_status(r["qty"], low_stock_threshold),
             "incoming": incoming_stock(conn, r["sku"], r["size"])}
            for r in conn.execute("SELECT * FROM inventory WHERE qty <= ? ORDER BY qty, sku, size",
                                  (low_stock_threshold,))
        ]
        ticket_shortfalls = []
        for t in conn.execute(
            "SELECT t.id, t.type, t.requester, t.sku, t.size, t.qty, COALESCE(i.qty, 0) AS on_hand"
            " FROM tickets t LEFT JOIN inventory i ON i.sku = t.sku AND i.size = t.size"
            " WHERE t.status = 'open' AND t.sku IS NOT NULL AND t.qty IS NOT NULL ORDER BY t.id"
        ):
            if t["qty"] > t["on_hand"]:
                ticket_shortfalls.append({
                    "ticket_id": t["id"], "type": t["type"], "requester": t["requester"],
                    "sku": t["sku"], "size": t["size"], "qty_wanted": t["qty"],
                    "on_hand": t["on_hand"], "short_by": t["qty"] - t["on_hand"],
                    "incoming": incoming_stock(conn, t["sku"], t["size"]),
                })
        return {
            "business_date": business_date(conn).isoformat(),
            "low_stock_threshold": low_stock_threshold,
            "stock_issues": stock_issues,
            "ticket_shortfalls": ticket_shortfalls,
        }


def find_restock_vendor(sku: str, size: str | None = None, qty: int | None = None) -> dict:
    """Find which vendor can restock a product, when it would arrive (business date +
    vendor lead time) and, with `qty`, what it would cost at unit cost. The fastest
    vendor is recommended. Ordering is done by Accounting with prepare_purchase_order."""
    sku = sku.strip().upper()
    size = size.strip().upper() if size else None
    if qty is not None and qty <= 0:
        raise ToolError("qty must be at least 1.")
    with connect() as conn:
        product = _product(conn, sku)
        if size and not conn.execute("SELECT 1 FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone():
            raise ToolError(f"{sku} has no size {size!r}.")
        today = business_date(conn)
        vendors = []
        for v in conn.execute(
            "SELECT v.* FROM vendor_products vp JOIN vendors v ON v.id = vp.vendor_id"
            " WHERE vp.sku = ? ORDER BY v.lead_days, v.id", (sku,)
        ):
            option = {
                "vendor_id": v["id"], "vendor": v["name"], "specialty": v["specialty"],
                "lead_days": v["lead_days"],
                "expected_date": (today + timedelta(days=v["lead_days"])).isoformat(),
                "unit_cost": product["unit_cost"],
            }
            if qty:
                option["estimated_total"] = money(qty * product["unit_cost"])
            vendors.append(option)
        return {
            "sku": sku, "name": product["name"], "size": size, "qty": qty,
            "business_date": today.isoformat(),
            "vendors": vendors,
            "recommended_vendor_id": vendors[0]["vendor_id"] if vendors else None,
            "incoming": incoming_stock(conn, sku, size),
            "note": None if vendors else "No vendor is mapped to this SKU in vendor_products.",
        }


def receive_purchase_order(purchase_order_id: int) -> dict:
    """Add the units from an approved purchase order to stock, once the goods have
    physically arrived. Marks the order `received`. Only approved orders can be received."""
    with connect(read_only=False) as conn:
        po = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (purchase_order_id,)).fetchone()
        if po is None:
            raise ToolError(f"No purchase order {purchase_order_id}.")
        if po["status"] != "approved":
            raise ToolError(f"Purchase order {purchase_order_id} is {po['status']!r}; only approved orders can be received.")
        conn.execute("UPDATE inventory SET qty = qty + ? WHERE sku = ? AND size = ?",
                     (po["qty"], po["sku"], po["size"]))
        conn.execute("UPDATE purchase_orders SET status = 'received' WHERE id = ?", (purchase_order_id,))
        new_qty = conn.execute("SELECT qty FROM inventory WHERE sku = ? AND size = ?",
                               (po["sku"], po["size"])).fetchone()["qty"]
        log_activity(conn, "inventory", "received_purchase_order", {
            "purchase_order_id": purchase_order_id, "sku": po["sku"], "size": po["size"],
            "qty_added": po["qty"], "new_qty": new_qty,
        })
    return {"purchase_order_id": purchase_order_id, "sku": po["sku"], "size": po["size"],
            "qty_added": po["qty"], "new_qty": new_qty, "status": "received"}
