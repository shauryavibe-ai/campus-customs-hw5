"""The human's approve / reject step for requests the agents prepared.

This is deliberately NOT registered as an MCP tool, so no agent can approve its own
request. The FastAPI backend calls `decide` when the human clicks Approve or Reject
on the dashboard. Approving is the only path that writes to `payments` or changes
`cash_accounts`, and it runs as one transaction: all of it happens or none of it does.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from mcp_server.accounting import still_owed
from mcp_server.db import (
    approval_row, business_date, cash_balance, connect, log_activity, money, now_utc,
)
from mcp_server.facilities import add_one_month
from mcp_server.safety import content_hash

AGENT_NAMES = {"boss", "inventory", "accounting", "facilities", "customer_service", "customer service"}


class ApprovalError(Exception):
    """The request can't be approved or rejected as asked."""


def decide(approval_id: int, approve: bool, decided_by: str, note: str | None = None) -> dict:
    decided_by = decided_by.strip()
    if not decided_by or decided_by.casefold() in AGENT_NAMES:
        raise ApprovalError("Approvals must be decided by a named human, not an agent.")
    with connect(read_only=False) as conn:
        approval = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if approval is None:
            raise ApprovalError(f"No approval {approval_id}.")
        if approval["status"] != "pending":
            raise ApprovalError(f"Approval {approval_id} was already {approval['status']}.")
        seal = content_hash(approval["kind"], approval["ref_id"], money(approval["amount"]),
                            approval["summary"], approval["details"])
        if approval["content_sha256"] and seal != approval["content_sha256"]:
            raise ApprovalError(f"Approval {approval_id} was changed after it was prepared; it can't be approved.")
        outcome = _apply(conn, approval, decided_by) if approve else _reject(conn, approval)
        status = "approved" if approve else "rejected"
        conn.execute(
            "UPDATE approvals SET status = ?, decided_by = ?, decision_note = ?, decided_at = ? WHERE id = ?",
            (status, decided_by, note, now_utc(), approval_id),
        )
        log_activity(conn, "human", f"{status}_{approval['kind']}",
                     {"approval_id": approval_id, "decided_by": decided_by, "note": note, **outcome})
        resolved = _resolve_finished_tickets(conn, approval, decided_by)
        return {"approval": approval_row(conn, approval_id), "outcome": outcome, "tickets_resolved": resolved}


def ticket_approval_refs(conn: sqlite3.Connection, ticket_id: int) -> list[tuple[str, int]]:
    """(kind, ref_id) of every approval that belongs to a ticket."""
    t = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    refs = [("discount", ticket_id)]
    refs += [("purchase_order", r["id"]) for r in conn.execute("SELECT id FROM purchase_orders WHERE ticket_id = ?",
                                                               (ticket_id,))]
    if t and t["invoice_id"]:
        refs.append(("invoice_payment", t["invoice_id"]))
    if t and t["lease_id"]:
        refs.append(("rent_payment", t["lease_id"]))
    return refs


def _linked_tickets(conn: sqlite3.Connection, approval: sqlite3.Row) -> list[int]:
    kind, ref = approval["kind"], approval["ref_id"]
    sql = {"discount": "SELECT id FROM tickets WHERE id = ?",
           "invoice_payment": "SELECT id FROM tickets WHERE invoice_id = ?",
           "rent_payment": "SELECT id FROM tickets WHERE lease_id = ? AND type = 'rent_notice'",
           "purchase_order": "SELECT ticket_id AS id FROM purchase_orders WHERE id = ? AND ticket_id IS NOT NULL"}[kind]
    return [r["id"] for r in conn.execute(sql, (ref,))]


def _resolve_finished_tickets(conn: sqlite3.Connection, approval: sqlite3.Row, decided_by: str) -> list[int]:
    """A ticket the Boss left 'waiting_on_human' is resolved once the human has decided
    every approval that belongs to it."""
    resolved = []
    for tid in _linked_tickets(conn, approval):
        if conn.execute("SELECT status FROM tickets WHERE id = ?", (tid,)).fetchone()["status"] != "waiting_on_human":
            continue
        decided, pending = [], 0
        for kind, ref in ticket_approval_refs(conn, tid):
            for a in conn.execute("SELECT id, status FROM approvals WHERE kind = ? AND ref_id = ?", (kind, ref)):
                if a["status"] == "pending":
                    pending += 1
                else:
                    decided.append(f"#{a['id']} {a['status']}")
        if pending:
            continue
        decision = f"All approvals for this ticket were decided by {decided_by}: {', '.join(decided)}."
        conn.execute("UPDATE tickets SET status = 'resolved' WHERE id = ?", (tid,))
        conn.execute("INSERT INTO ticket_updates (ticket_id, old_status, new_status, decision, agent, created_at)"
                     " VALUES (?, 'waiting_on_human', 'resolved', ?, 'human', ?)", (tid, decision, now_utc()))
        log_activity(conn, "human", "resolved_ticket", {"ticket_id": tid, "decision": decision})
        resolved.append(tid)
    return resolved


def _reject(conn: sqlite3.Connection, approval: sqlite3.Row) -> dict:
    if approval["kind"] == "purchase_order":
        conn.execute("UPDATE purchase_orders SET status = 'rejected' WHERE id = ?", (approval["ref_id"],))
    return {}


def _pay(conn: sqlite3.Connection, kind: str, ref_id: int, amount: float, approved_by: str) -> dict:
    balance = cash_balance(conn)
    if amount > balance:
        raise ApprovalError(f"Checking has ${balance:.2f}; can't pay ${amount:.2f}.")
    today = business_date(conn).isoformat()
    cur = conn.execute(
        "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by)"
        " VALUES (?, ?, ?, 'checking', ?, ?)",
        (kind, ref_id, amount, today, approved_by),
    )
    conn.execute("UPDATE cash_accounts SET balance = ROUND(balance - ?, 2), date = ? WHERE name = 'checking'",
                 (amount, today))
    return {"payment_id": cur.lastrowid, "amount": amount, "checking_after": money(balance - amount)}


def _apply(conn: sqlite3.Connection, approval: sqlite3.Row, approved_by: str) -> dict:
    kind, ref_id, amount = approval["kind"], approval["ref_id"], money(approval["amount"])

    if kind == "invoice_payment":
        inv = conn.execute("SELECT status FROM invoices WHERE id = ?", (ref_id,)).fetchone()
        if inv is None or inv["status"] != "open":
            raise ApprovalError(f"Invoice {ref_id} is no longer open.")
        if amount > still_owed(conn, ref_id):
            raise ApprovalError(f"${amount:.2f} is more than invoice {ref_id} still owes.")
        out = _pay(conn, "invoice", ref_id, amount, approved_by)
        if still_owed(conn, ref_id) <= 0:
            conn.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (ref_id,))
        out["invoice_status"] = conn.execute("SELECT status FROM invoices WHERE id = ?", (ref_id,)).fetchone()[0]
        return out

    if kind == "rent_payment":
        lease = conn.execute("SELECT next_due FROM leases WHERE id = ?", (ref_id,)).fetchone()
        if lease is None:
            raise ApprovalError(f"Lease {ref_id} no longer exists.")
        out = _pay(conn, "rent", ref_id, amount, approved_by)
        next_due = add_one_month(date.fromisoformat(lease["next_due"])).isoformat()
        conn.execute("UPDATE leases SET next_due = ? WHERE id = ?", (next_due, ref_id))
        out["lease_next_due"] = next_due
        return out

    if kind == "purchase_order":
        conn.execute("UPDATE purchase_orders SET status = 'approved' WHERE id = ? AND status = 'pending_approval'",
                     (ref_id,))
        return {"purchase_order_id": ref_id, "purchase_order_status": "approved"}

    if kind == "discount":
        details = json.loads(approval["details"])
        return {"ticket_id": ref_id, "approved_unit_price": details["margin"]["unit_price"]}

    raise ApprovalError(f"Unknown approval kind {kind!r}.")


def close_ticket(ticket_id: int, decided_by: str, note: str) -> dict:
    """A human closes a ticket that needs no approval (e.g. an internal ticket the Boss
    raised). Not an MCP tool. Refused while any approval for the ticket is still pending."""
    decided_by, note = decided_by.strip(), note.strip()
    if not decided_by or decided_by.casefold() in AGENT_NAMES:
        raise ApprovalError("Tickets must be closed by a named human, not an agent.")
    if not note:
        raise ApprovalError("Say why the ticket is being closed.")
    with connect(read_only=False) as conn:
        t = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if t is None:
            raise ApprovalError(f"No ticket {ticket_id}.")
        if t["status"] == "resolved":
            raise ApprovalError(f"Ticket {ticket_id} is already resolved.")
        pending = [f"#{a['id']}" for kind, ref in ticket_approval_refs(conn, ticket_id)
                   for a in conn.execute("SELECT id FROM approvals WHERE kind = ? AND ref_id = ? AND status = 'pending'",
                                         (kind, ref))]
        if pending:
            raise ApprovalError(f"Decide approval {', '.join(pending)} first.")
        decision = f"Closed by {decided_by}: {note}"
        conn.execute("UPDATE tickets SET status = 'resolved' WHERE id = ?", (ticket_id,))
        conn.execute("INSERT INTO ticket_updates (ticket_id, old_status, new_status, decision, agent, created_at)"
                     " VALUES (?, ?, 'resolved', ?, 'human', ?)", (ticket_id, t["status"], decision, now_utc()))
        log_activity(conn, "human", "closed_ticket", {"ticket_id": ticket_id, "decided_by": decided_by, "note": note})
    return {"ticket_id": ticket_id, "old_status": t["status"], "new_status": "resolved", "decision": decision}
