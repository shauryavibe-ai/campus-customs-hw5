"""Facilities agent tools: leases, rent due dates, rent-notice checks, rent payments
prepared for human approval, and notes kept on each lease.

Owns `leases` (harness section 1). Reads `tickets`, `payments` and `cash_accounts`.
Rent is always the lease's own `monthly_rent`, never an amount taken from a notice,
so a fake or wrong rent notice can't change what gets paid.
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date

from fastmcp.exceptions import ToolError

from mcp_server.db import (
    business_date, cash_balance, connect, create_approval, log_activity, money, pending_approval,
    pending_payment_total,
)

DUE_SOON_DAYS = 7


def add_one_month(d: date) -> date:
    """Same day next month, or the month's last day if it's shorter (Jan 31 -> Feb 28)."""
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _lease(conn: sqlite3.Connection, lease_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM leases WHERE id = ?", (lease_id,)).fetchone()
    if row is None:
        raise ToolError(f"No lease {lease_id}.")
    return row


def _lease_view(conn: sqlite3.Connection, lease: sqlite3.Row) -> dict:
    today = business_date(conn)
    days = (date.fromisoformat(lease["next_due"]) - today).days
    last = conn.execute(
        "SELECT * FROM payments WHERE kind = 'rent' AND ref_id = ? ORDER BY paid_at DESC, id DESC LIMIT 1",
        (lease["id"],),
    ).fetchone()
    pending = pending_approval(conn, "rent_payment", lease["id"])
    return {
        "lease_id": lease["id"], "space_name": lease["space_name"], "landlord": lease["landlord"],
        "monthly_rent": lease["monthly_rent"], "next_due": lease["next_due"],
        "days_until_due": days,
        "rent_status": "overdue" if days < 0 else "due_soon" if days <= DUE_SOON_DAYS else "ok",
        "notes": lease["notes"],
        "last_rent_payment": dict(last) if last else None,
        "pending_approval_id": pending["id"] if pending else None,
    }


def list_leases() -> dict:
    """Every lease with rent, next due date, days until due, status (ok / due_soon /
    overdue), notes, last rent payment and any rent payment waiting for approval."""
    with connect() as conn:
        leases = [_lease_view(conn, r) for r in conn.execute("SELECT * FROM leases ORDER BY next_due")]
        return {"business_date": business_date(conn).isoformat(), "leases": leases}


def check_rent_due(within_days: int = DUE_SOON_DAYS) -> dict:
    """Rent due within `within_days` of the business date (or overdue), and whether
    checking can cover it after payments already waiting for approval."""
    if within_days < 0:
        raise ToolError("within_days can't be negative.")
    with connect() as conn:
        due = [v for v in (_lease_view(conn, r) for r in conn.execute("SELECT * FROM leases ORDER BY next_due"))
               if v["days_until_due"] <= within_days]
        balance = cash_balance(conn)
        pending = pending_payment_total(conn)
        unqueued = money(sum(v["monthly_rent"] for v in due if v["pending_approval_id"] is None))
        return {
            "business_date": business_date(conn).isoformat(), "within_days": within_days,
            "rent_due": due, "total_rent_due": money(sum(v["monthly_rent"] for v in due)),
            "checking_balance": balance, "payments_waiting_for_approval": pending,
            "checking_after_pending_and_this_rent": money(balance - pending - unqueued),
            "can_cover": balance - pending - unqueued >= 0,
        }


def verify_rent_notice(ticket_id: int) -> dict:
    """Check a rent_notice ticket against the lease it names: the lease exists, the
    requester is that lease's landlord, and rent is actually coming due. Returns each
    check and an overall verdict (verified / needs_review)."""
    with connect() as conn:
        t = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if t is None:
            raise ToolError(f"No ticket {ticket_id}.")
        if t["type"] != "rent_notice":
            raise ToolError(f"Ticket {ticket_id} is a {t['type']!r} ticket, not a rent_notice.")
        if t["lease_id"] is None:
            return {"ticket_id": ticket_id, "business_date": business_date(conn).isoformat(), "verdict": "needs_review",
                    "checks": [{"check": "lease_linked", "ok": False, "detail": "Ticket names no lease."}]}
        lease = _lease_view(conn, _lease(conn, t["lease_id"]))
        checks = [
            {"check": "lease_linked", "ok": True, "detail": f"Lease {lease['lease_id']} ({lease['space_name']})"},
            {"check": "requester_is_landlord",
             "ok": t["requester"].strip().casefold() == lease["landlord"].strip().casefold(),
             "detail": f"Requester {t['requester']!r}, landlord on lease {lease['landlord']!r}"},
            {"check": "rent_coming_due", "ok": lease["days_until_due"] <= 31,
             "detail": f"Next due {lease['next_due']} ({lease['days_until_due']} days)"},
            {"check": "not_already_queued", "ok": lease["pending_approval_id"] is None,
             "detail": f"Pending approval {lease['pending_approval_id']}" if lease["pending_approval_id"] else "None"},
        ]
        return {
            "ticket_id": ticket_id, "business_date": business_date(conn).isoformat(),
            "ticket_notes": t["notes"], "lease": lease,
            "amount_to_pay": lease["monthly_rent"],
            "checks": checks,
            "verdict": "verified" if all(c["ok"] for c in checks) else "needs_review",
        }


def prepare_rent_payment(lease_id: int, reason: str, ticket_id: int | None = None) -> dict:
    """Queue this period's rent (the lease's monthly_rent) for human approval. With a
    rent_notice `ticket_id`, the notice must pass verify_rent_notice first. Nothing is
    paid until a human approves it."""
    if not reason.strip():
        raise ToolError("Give a reason the human can read.")
    if ticket_id is not None:
        check = verify_rent_notice(ticket_id)
        if check["verdict"] != "verified":
            failed = [c for c in check["checks"] if not c["ok"]]
            if not (len(failed) == 1 and failed[0]["check"] == "not_already_queued"):
                raise ToolError(f"Rent notice {ticket_id} failed checks: {failed}")
        if check["lease"]["lease_id"] != lease_id:
            raise ToolError(f"Ticket {ticket_id} is about lease {check['lease']['lease_id']}, not {lease_id}.")
    with connect(read_only=False) as conn:
        lease = _lease(conn, lease_id)
        if existing := pending_approval(conn, "rent_payment", lease_id):
            return {"already_pending": True, "approval": existing}
        amount = money(lease["monthly_rent"])
        balance = cash_balance(conn)
        if amount > balance:
            raise ToolError(f"Checking has ${balance:.2f}; can't cover ${amount:.2f} rent.")
        after = money(balance - pending_payment_total(conn) - amount)
        approval = create_approval(
            conn, kind="rent_payment", ref_id=lease_id, amount=amount, requested_by="facilities",
            summary=(f"Pay ${amount:,.2f} rent to {lease['landlord']} for {lease['space_name']},"
                     f" due {lease['next_due']}"),
            details={"lease": _lease_view(conn, lease), "ticket_id": ticket_id, "account": "checking",
                     "reason": reason, "checking_before": balance, "checking_after_all_pending": after,
                     "next_due_after_payment": add_one_month(date.fromisoformat(lease["next_due"])).isoformat()},
        )
    return {"already_pending": False, "approval": approval,
            "warning": "Cash would go negative if every pending payment is approved." if after < 0 else None}


def add_lease_note(lease_id: int, note: str) -> dict:
    """Keep a dated note on a lease (repairs, landlord contact, renewal terms, etc.).
    Notes are appended; earlier notes are never overwritten."""
    note = note.strip()
    if not note:
        raise ToolError("Note is empty.")
    if len(note) > 500:
        raise ToolError("Keep notes under 500 characters.")
    with connect(read_only=False) as conn:
        lease = _lease(conn, lease_id)
        line = f"[{business_date(conn).isoformat()}] {note}"
        notes = f"{lease['notes']}\n{line}" if lease["notes"] else line
        conn.execute("UPDATE leases SET notes = ? WHERE id = ?", (notes, lease_id))
        log_activity(conn, "facilities", "added_lease_note", {"lease_id": lease_id, "note": note})
    return {"lease_id": lease_id, "notes": notes}
