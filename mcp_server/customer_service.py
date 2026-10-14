"""Customer Service agent tools: draft messages to customers and read back drafts.

Owns `customer_messages`. Drafts are never sent by an agent: the human reviews and
sends them from the dashboard. Facts in a draft (stock, prices, dates, discounts) must
come from the other agents' tools, not from the model's guesses.
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError

from mcp_server.db import business_date, connect, log_activity, now_utc
from mcp_server.fact_check import check_figures
from mcp_server.safety import check_customer_message, content_hash


def draft_customer_message(ticket_id: int, subject: str, body: str) -> dict:
    """Save a draft message to the person who raised a ticket. It is addressed to the
    ticket's requester and stays a draft until a human sends it. A ticket has at most
    one unsent draft: calling this again updates that draft instead of adding another.
    Every dollar amount, percentage and record id is checked against the database
    automatically; the draft is refused if any figure isn't backed by the data."""
    subject, body = subject.strip(), body.strip()
    if not subject or not body:
        raise ToolError("Give both a subject and a body.")
    if len(subject) > 150 or len(body) > 2000:
        raise ToolError("Keep the subject under 150 characters and the body under 2,000.")
    with connect(read_only=False) as conn:
        t = conn.execute("SELECT id, requester, type FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if t is None:
            raise ToolError(f"No ticket {ticket_id}.")
        if leaks := check_customer_message(conn, ticket_id, f"{subject}\n{body}"):
            raise ToolError("Draft not saved. The confidentiality check refused it: " + "; ".join(leaks)
                            + ". Customers may only see product names, sizes, availability, list prices, their "
                            "own order and their own price (say 'pending approval' if not yet approved).")
        if problems := check_figures(conn, f"{subject}\n{body}", ticket_id):
            raise ToolError("Draft not saved. The automatic figure check found: " + "; ".join(problems)
                            + ". A customer must only see amounts, percentages and ids that are in the data.")
        seal = content_hash(ticket_id, t["requester"], subject, body)
        existing = conn.execute("SELECT id FROM customer_messages WHERE ticket_id = ? AND status = 'draft'",
                                (ticket_id,)).fetchone()
        if existing:
            message_id = existing["id"]
            conn.execute("UPDATE customer_messages SET subject = ?, body = ?, created_at = ?, content_sha256 = ?"
                         " WHERE id = ?", (subject, body, now_utc(), seal, message_id))
        else:
            message_id = conn.execute(
                "INSERT INTO customer_messages (ticket_id, to_name, subject, body, drafted_by, created_at,"
                " content_sha256) VALUES (?, ?, ?, ?, 'customer_service', ?, ?)",
                (ticket_id, t["requester"], subject, body, now_utc(), seal)).lastrowid
        log_activity(conn, "customer_service", "updated_customer_message" if existing else "drafted_customer_message",
                     {"message_id": message_id, "ticket_id": ticket_id, "to": t["requester"], "subject": subject})
        row = dict(conn.execute("SELECT * FROM customer_messages WHERE id = ?", (message_id,)).fetchone())
    return {"message": row, "updated_existing_draft": existing is not None,
            "note": "Saved as a draft. A human reviews and sends it."}


def list_customer_messages(ticket_id: int | None = None, status: str | None = None) -> dict:
    """Customer message drafts (and sent messages), optionally for one ticket or status."""
    sql, params = "SELECT * FROM customer_messages WHERE 1 = 1", []
    if ticket_id is not None:
        sql, params = sql + " AND ticket_id = ?", params + [ticket_id]
    if status:
        sql, params = sql + " AND status = ?", params + [status]
    with connect() as conn:
        return {"business_date": business_date(conn).isoformat(),
                "messages": [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]}


def verify_customer_message(message_id: int) -> bool:
    """True if a draft is exactly what was checked and sealed (who it's to, subject, body).
    Not an MCP tool: the backend calls it right before a human sends the message."""
    with connect() as conn:
        m = conn.execute("SELECT * FROM customer_messages WHERE id = ?", (message_id,)).fetchone()
    return bool(m and m["content_sha256"]
                and m["content_sha256"] == content_hash(m["ticket_id"], m["to_name"], m["subject"], m["body"]))
