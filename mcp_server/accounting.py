"""Accounting agent tools: cash, invoices, margins, and payments / purchase orders /
discounts prepared for human approval.

Owns `vendors`, `invoices`, `payments`, `cash_accounts`, `vendor_products` and
`purchase_orders` (harness section 1). The agent never writes to `payments` or
`cash_accounts` itself. It queues a request in `approvals`, and the money moves only
when a human approves it (see approvals.decide).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from fastmcp.exceptions import ToolError

from mcp_server.db import (
    business_date, cash_balance, connect, create_approval, money, now_utc, pending_approval,
    pending_payment_total,
)

MIN_MARGIN_PCT = 30.0   # discounts that leave less margin than this are flagged
MAX_PO_UNITS = 500      # sanity cap on a single purchase order


def date_of(text: str) -> date:
    return date.fromisoformat(text[:10])


def still_owed(conn: sqlite3.Connection, invoice_id: int) -> float:
    row = conn.execute(
        "SELECT i.amount - COALESCE((SELECT SUM(p.amount) FROM payments p"
        "  WHERE p.kind = 'invoice' AND p.ref_id = i.id), 0) AS owed FROM invoices i WHERE i.id = ?",
        (invoice_id,),
    ).fetchone()
    return money(row["owed"]) if row else 0.0


def _invoice_view(conn: sqlite3.Connection, inv: sqlite3.Row) -> dict:
    today = business_date(conn)
    days_left = (date_of(inv["due_date"]) - today).days
    pending = pending_approval(conn, "invoice_payment", inv["id"])
    return {
        "invoice_id": inv["id"], "vendor_id": inv["vendor_id"], "vendor": inv["vendor"],
        "amount": inv["amount"], "still_owed": still_owed(conn, inv["id"]),
        "due_date": inv["due_date"], "status": inv["status"], "description": inv["description"],
        "days_overdue": max(0, -days_left), "days_until_due": max(0, days_left),
        "linked_tickets": [r["id"] for r in conn.execute("SELECT id FROM tickets WHERE invoice_id = ?", (inv["id"],))],
        "pending_approval_id": pending["id"] if pending else None,
    }


def get_cash_position(within_days: int = 30) -> dict:
    """Cash in each account, what is owed (open invoices and rent due within `within_days`),
    what is already waiting for approval, and what would be left after paying it all."""
    with connect() as conn:
        today = business_date(conn)
        accounts = [dict(r) for r in conn.execute("SELECT name, balance, date FROM cash_accounts")]
        balance = cash_balance(conn)
        invoices = [_invoice_view(conn, r) for r in conn.execute(
            "SELECT i.*, v.name AS vendor FROM invoices i JOIN vendors v ON v.id = i.vendor_id"
            " WHERE i.status = 'open' ORDER BY i.due_date")]
        horizon = (today + timedelta(days=within_days)).isoformat()
        rent = [
            {"lease_id": r["id"], "space_name": r["space_name"], "landlord": r["landlord"],
             "amount": r["monthly_rent"], "due_date": r["next_due"],
             "days_until_due": (date_of(r["next_due"]) - today).days}
            for r in conn.execute("SELECT * FROM leases WHERE next_due <= ? ORDER BY next_due", (horizon,))
        ]
        owed_invoices = money(sum(i["still_owed"] for i in invoices))
        owed_rent = money(sum(r["amount"] for r in rent))
        committed_pos = money(conn.execute(
            "SELECT COALESCE(SUM(total), 0) FROM purchase_orders WHERE status IN ('pending_approval', 'approved')"
        ).fetchone()[0])
        pending = pending_payment_total(conn)
        total_out = money(owed_invoices + owed_rent + committed_pos)
        return {
            "business_date": today.isoformat(),
            "accounts": accounts,
            "checking_balance": balance,
            "open_invoices": invoices,
            "rent_due": rent,
            "totals": {
                "open_invoices": owed_invoices, "rent_due": owed_rent,
                "purchase_orders_committed": committed_pos,
                "payments_waiting_for_approval": pending,
                "all_obligations": total_out,
            },
            "balance_after_pending_payments": money(balance - pending),
            "balance_after_all_obligations": money(balance - total_out),
            "can_cover_all": balance >= total_out,
        }


def list_invoices(status: str | None = "open", vendor_id: int | None = None) -> dict:
    """List vendor invoices with vendor name, amount still owed, days overdue or until due,
    linked tickets and any payment already waiting for approval. status=None lists all."""
    sql = "SELECT i.*, v.name AS vendor FROM invoices i JOIN vendors v ON v.id = i.vendor_id WHERE 1 = 1"
    params: list = []
    if status:
        sql += " AND i.status = ?"
        params.append(status)
    if vendor_id is not None:
        sql += " AND i.vendor_id = ?"
        params.append(vendor_id)
    with connect() as conn:
        rows = [_invoice_view(conn, r) for r in conn.execute(sql + " ORDER BY i.due_date", params)]
        return {"business_date": business_date(conn).isoformat(), "invoices": rows}


def _margin(conn: sqlite3.Connection, sku: str, unit_price: float, qty: int) -> dict:
    p = conn.execute("SELECT * FROM pricing WHERE sku = ?", (sku,)).fetchone()
    if p is None:
        raise ToolError(f"No price for SKU {sku!r}.")
    if unit_price <= 0 or qty <= 0:
        raise ToolError("unit_price and qty must be positive.")
    cost, listed = p["unit_cost"], p["list_price"]
    margin_unit = unit_price - cost
    margin_pct = 100 * margin_unit / unit_price
    if unit_price < cost:
        verdict = "below_cost"
    elif unit_price >= listed:
        verdict = "list_price"
    elif margin_pct < MIN_MARGIN_PCT:
        verdict = "below_min_margin"
    else:
        verdict = "discount_ok"
    return {
        "sku": sku, "qty": qty, "unit_price": money(unit_price),
        "unit_cost": cost, "list_price": listed,
        "discount_pct": money(100 * (listed - unit_price) / listed),
        "margin_per_unit": money(margin_unit), "margin_pct": money(margin_pct),
        "revenue": money(unit_price * qty), "total_margin": money(margin_unit * qty),
        "revenue_at_list": money(listed * qty),
        "min_margin_pct": MIN_MARGIN_PCT,
        "lowest_price_at_min_margin": money(cost / (1 - MIN_MARGIN_PCT / 100)),
        "verdict": verdict,
        "needs_human_approval": unit_price < listed,
    }


def check_margin(sku: str, unit_price: float, qty: int = 1) -> dict:
    """Margin at a proposed selling price: discount from list, margin per unit and %,
    totals, and a verdict (list_price / discount_ok / below_min_margin / below_cost).
    Any price below list needs human approval."""
    with connect() as conn:
        return {"business_date": business_date(conn).isoformat(), **_margin(conn, sku.strip().upper(), unit_price, qty)}


def prepare_invoice_payment(invoice_id: int, reason: str, amount: float | None = None) -> dict:
    """Queue a vendor-invoice payment for human approval. Defaults to the full amount still
    owed. Nothing is paid until a human approves it on the dashboard."""
    if not reason.strip():
        raise ToolError("Give a reason the human can read.")
    with connect(read_only=False) as conn:
        inv = conn.execute("SELECT i.*, v.name AS vendor FROM invoices i JOIN vendors v ON v.id = i.vendor_id"
                           " WHERE i.id = ?", (invoice_id,)).fetchone()
        if inv is None:
            raise ToolError(f"No invoice {invoice_id}.")
        if inv["status"] != "open":
            raise ToolError(f"Invoice {invoice_id} is {inv['status']!r}, not open.")
        if existing := pending_approval(conn, "invoice_payment", invoice_id):
            return {"already_pending": True, "approval": existing}
        owed = still_owed(conn, invoice_id)
        amount = money(owed if amount is None else amount)
        if amount <= 0 or amount > owed:
            raise ToolError(f"Amount must be more than 0 and at most the ${owed:.2f} still owed.")
        balance = cash_balance(conn)
        if amount > balance:
            raise ToolError(f"Checking has ${balance:.2f}; can't cover ${amount:.2f}.")
        after = money(balance - pending_payment_total(conn) - amount)
        view = _invoice_view(conn, inv)
        approval = create_approval(
            conn, kind="invoice_payment", ref_id=invoice_id, amount=amount, requested_by="accounting",
            summary=f"Pay ${amount:,.2f} to {inv['vendor']} for invoice {invoice_id} ({inv['description']})",
            details={"invoice": view, "account": "checking", "reason": reason,
                     "checking_before": balance, "checking_after_all_pending": after},
        )
    return {"already_pending": False, "approval": approval,
            "warning": "Cash would go negative if every pending payment is approved." if after < 0 else None}


def prepare_purchase_order(vendor_id: int, sku: str, size: str, qty: int, reason: str,
                           ticket_id: int | None = None) -> dict:
    """Draft a purchase order to restock a product and queue it for human approval.
    Costed at unit cost, with an expected date from the vendor's lead time."""
    sku, size = sku.strip().upper(), size.strip().upper()
    if not reason.strip():
        raise ToolError("Give a reason the human can read.")
    if not 0 < qty <= MAX_PO_UNITS:
        raise ToolError(f"qty must be between 1 and {MAX_PO_UNITS}.")
    with connect(read_only=False) as conn:
        vendor = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if vendor is None:
            raise ToolError(f"No vendor {vendor_id}.")
        if not conn.execute("SELECT 1 FROM vendor_products WHERE vendor_id = ? AND sku = ?", (vendor_id, sku)).fetchone():
            makers = [r["name"] for r in conn.execute(
                "SELECT v.name FROM vendor_products vp JOIN vendors v ON v.id = vp.vendor_id WHERE vp.sku = ?", (sku,))]
            raise ToolError(f"{vendor['name']} doesn't supply {sku}. Suppliers: {', '.join(makers) or 'none'}.")
        if not conn.execute("SELECT 1 FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone():
            raise ToolError(f"{sku} has no size {size!r}.")
        if ticket_id is not None and not conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone():
            raise ToolError(f"No ticket {ticket_id}.")
        unit_cost = conn.execute("SELECT unit_cost FROM pricing WHERE sku = ?", (sku,)).fetchone()["unit_cost"]
        total = money(unit_cost * qty)
        expected = (business_date(conn) + timedelta(days=vendor["lead_days"])).isoformat()
        cur = conn.execute(
            "INSERT INTO purchase_orders (vendor_id, sku, size, qty, unit_cost, total, expected_date,"
            " ticket_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (vendor_id, sku, size, qty, unit_cost, total, expected, ticket_id, reason, now_utc()),
        )
        po = dict(conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (cur.lastrowid,)).fetchone())
        balance = cash_balance(conn)
        approval = create_approval(
            conn, kind="purchase_order", ref_id=po["id"], amount=total, requested_by="accounting",
            summary=f"Order {qty} x {sku} size {size} from {vendor['name']} for ${total:,.2f}, arriving {expected}",
            details={"purchase_order": po, "vendor": vendor["name"], "reason": reason,
                     "checking_balance": balance,
                     "checking_after_pending_payments": money(balance - pending_payment_total(conn))},
        )
    return {"purchase_order": po, "approval": approval}


def request_discount_approval(ticket_id: int, unit_price: float, reason: str) -> dict:
    """Queue a discount (price below list) on a price_override ticket for human approval.
    Prices below unit cost are refused outright."""
    if not reason.strip():
        raise ToolError("Give a reason the human can read.")
    with connect(read_only=False) as conn:
        t = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if t is None:
            raise ToolError(f"No ticket {ticket_id}.")
        if t["type"] != "price_override" or t["status"] != "open":
            raise ToolError(f"Ticket {ticket_id} is not an open price_override ticket.")
        margin = _margin(conn, t["sku"], unit_price, t["qty"])
        if margin["verdict"] == "below_cost":
            raise ToolError(f"${unit_price:.2f} is below the ${margin['unit_cost']:.2f} unit cost; not allowed.")
        if margin["verdict"] == "list_price":
            raise ToolError("That is list price or higher, so no discount approval is needed.")
        if existing := pending_approval(conn, "discount", ticket_id):
            return {"already_pending": True, "approval": existing}
        on_hand = conn.execute("SELECT qty FROM inventory WHERE sku = ? AND size = ?",
                               (t["sku"], t["size"])).fetchone()["qty"]
        approval = create_approval(
            conn, kind="discount", ref_id=ticket_id, amount=margin["revenue"], requested_by="accounting",
            summary=(f"Sell {t['qty']} x {t['sku']} size {t['size']} to {t['requester']} at ${unit_price:.2f}"
                     f" ({margin['discount_pct']:.1f}% off list, {margin['margin_pct']:.1f}% margin)"),
            details={"ticket_id": ticket_id, "requester": t["requester"], "margin": margin,
                     "on_hand": on_hand, "short_by": max(0, t["qty"] - on_hand), "reason": reason},
        )
    return {"already_pending": False, "approval": approval,
            "warning": "Margin is below the minimum." if margin["verdict"] == "below_min_margin" else None}


def list_approvals(status: str | None = "pending") -> dict:
    """List requests in the human approval queue (payments, purchase orders, discounts),
    newest first. status: pending / approved / rejected, or None for all."""
    with connect() as conn:
        sql, params = "SELECT * FROM approvals", []
        if status:
            sql, params = sql + " WHERE status = ?", [status]
        rows = []
        for r in conn.execute(sql + " ORDER BY id DESC", params):
            row = dict(r)
            row["details"] = json.loads(row["details"])
            rows.append(row)
        return {"business_date": business_date(conn).isoformat(), "approvals": rows,
                "pending_payment_total": pending_payment_total(conn)}
