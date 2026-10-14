"""Confidentiality and integrity rules, in plain code (no AI, no tokens).

There is exactly one public channel: a customer message draft. Everything else
(decisions, hand-offs, tool results, the audit trail) stays inside the team and the
human's dashboard. Before a draft is saved, `check_customer_message` makes sure it
only says what a customer may know:

  PUBLIC:   product names, sizes, availability, list prices, the customer's own order
            (qty, total at list), their own discounted price/total (approved or pending,
            described as pending), expected dates, general status ("pending approval").
  INTERNAL: unit costs, margins, cash and bank balances, invoices, rent and leases,
            vendors, landlords, other customers, internal ids (approval #, PO #, invoice,
            other tickets), agent / tool / system names, secrets, and links.

Text written by requesters (ticket subject / notes) is untrusted data: `injection_flags`
spots attempts to give the agents orders. `content_hash` seals approvals and drafts so
the human approves or sends exactly what was reviewed.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from mcp_server.fact_check import MONEY, PERCENT

INTERNAL_TERMS = [
    "unit cost", "our cost", "cost price", "margin", "markup", "mark-up", "wholesale",
    "cash", "checking account", "checking balance", "bank", "balance", "overdue", "invoice", "rent", "lease", "landlord",
    "payroll", "profit", "purchase order", "vendor", "supplier",
    "boss agent", "inventory agent", "accounting agent", "facilities agent", "customer service agent",
    "ask_agent", "tool call", "mcp", "database", "sqlite", "system prompt", "system message", "audit trail",
    "api key", "portkey", "openai", "gpt-6", ".env",
]
INTERNAL_IDS = re.compile(r"\b(?:approval|purchase\s+order|PO|invoice|draft|lease)\s*#?\s*\d+", re.I)
TICKET_ID = re.compile(r"\bticket\s*#?\s*(\d+)", re.I)
SECRET = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{12,}|\b[A-Za-z0-9+/]{32,}={0,2}\b")
LINK = re.compile(r"https?://|www\.|\b[\w.+-]+@[\w-]+\.[\w.-]+\b|\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
INJECTION = re.compile(
    r"ignore (?:all |any |your |the )?(?:previous|prior|above)|disregard (?:your|the) (?:rules|instructions)|"
    r"system prompt|you are now|\bact as\b|developer mode|jailbreak|\breveal|api[ _-]?key|password|"
    r"other customers?|internal (?:data|info)|cash balance|bank|unit cost|margin", re.I)


def _public_money(conn: sqlite3.Connection, ticket_id: int) -> set[float]:
    """Dollar amounts a customer on this ticket may see."""
    public = {p["list_price"] for p in conn.execute("SELECT list_price FROM pricing")}
    t = conn.execute("SELECT t.qty, p.list_price FROM tickets t LEFT JOIN pricing p ON p.sku = t.sku"
                     " WHERE t.id = ?", (ticket_id,)).fetchone()
    if t and t["qty"] and t["list_price"]:
        public.add(t["qty"] * t["list_price"])
    for a in conn.execute("SELECT details FROM approvals WHERE kind = 'discount' AND ref_id = ?"
                          " AND status != 'rejected'", (ticket_id,)):
        m = json.loads(a["details"]).get("margin", {})
        price, listed, qty = m.get("unit_price", 0), m.get("list_price", 0), m.get("qty", 0)
        public |= {price, price * qty, listed - price, (listed - price) * qty, listed * qty}
    return {round(x, 2) for x in public}


def _public_percents(conn: sqlite3.Connection, ticket_id: int) -> set[float]:
    """Only this ticket's own discount percentage (never a margin)."""
    return {round(json.loads(a["details"]).get("margin", {}).get("discount_pct", -1), 2)
            for a in conn.execute("SELECT details FROM approvals WHERE kind = 'discount' AND ref_id = ?"
                                  " AND status != 'rejected'", (ticket_id,))}


def _names(conn: sqlite3.Connection, ticket_id: int) -> set[str]:
    """People and businesses a customer on this ticket must never see named."""
    me = conn.execute("SELECT requester FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    names = {r["requester"] for r in conn.execute("SELECT requester FROM tickets")}
    names |= {r["landlord"] for r in conn.execute("SELECT landlord FROM leases")}
    names |= {r["name"] for r in conn.execute("SELECT name FROM vendors")}
    names.discard(me["requester"] if me else None)
    return {n for n in names if n and not n.endswith(" agent")}


def check_customer_message(conn: sqlite3.Connection, ticket_id: int, text: str) -> list[str]:
    """Problems that would leak internal or other people's information (empty = safe to save)."""
    problems = []
    lower = text.lower()
    for name in _names(conn, ticket_id):
        if name.lower() in lower:
            problems.append(f"names '{name}', who is not this customer")
    for term in INTERNAL_TERMS:
        if re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", lower):
            problems.append(f"uses the internal term '{term}'")
    for m in INTERNAL_IDS.finditer(text):
        problems.append(f"mentions the internal record '{m.group(0)}'")
    for m in TICKET_ID.finditer(text):
        if int(m.group(1)) != ticket_id:
            problems.append(f"mentions another ticket ({m.group(0)})")
    public = _public_money(conn, ticket_id)
    for m in MONEY.finditer(text):
        raw = m.group(1).replace(",", "")
        tol = 0.5 if "." not in raw else 0.011
        if not any(abs(float(raw) - p) <= tol for p in public):
            problems.append(f"${m.group(1)} isn't a public price for this customer")
    allowed_pct = _public_percents(conn, ticket_id)
    for m in PERCENT.finditer(text):
        if not any(abs(float(m.group(1)) - p) <= 0.51 for p in allowed_pct):
            problems.append(f"{m.group(1)}% isn't this customer's own discount")
    if SECRET.search(text):
        problems.append("contains something that looks like a key or token")
    if LINK.search(text):
        problems.append("contains a link, email address or phone number")
    return sorted(set(problems))


def injection_flags(*texts: str) -> list[str]:
    """Phrases in untrusted requester text that try to steer the agents."""
    return sorted({m.group(0).lower() for t in texts if t for m in INJECTION.finditer(t)})


def content_hash(*parts) -> str:
    """sha256 seal over the exact content a human will review."""
    body = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()
