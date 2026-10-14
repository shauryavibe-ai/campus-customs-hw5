"""Plain-code check of the figures in customer drafts and Boss decisions.

No AI: every dollar amount, percentage and record id in the text must be backed by
the database, either a stored value or a simple calculation from stored values
(cash left after any combination of what is owed, price x quantity, discount per
unit and in total, ...). Anything else is reported, so a made-up or mistyped number
never reaches a customer or the human's decision log.

Limits: it checks numbers and ids, not the words around them. "$1,000 discount"
passes if $1,000 is a real figure (e.g. the order total), even though it is mislabelled.
"""

from __future__ import annotations

import json
import re
import sqlite3
from itertools import combinations

from mcp_server.db import cash_balance

MONEY = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)")
PERCENT = re.compile(r"(?<![\w.])([0-9]+(?:\.[0-9]+)?)\s?%")
ID_PATTERNS = {
    "approval": (re.compile(r"\bapprovals?\s*(?:request\s*)?#\s*(\d+)", re.I), "approvals"),
    "purchase order": (re.compile(r"\b(?:purchase\s+order|PO)\s*#\s*(\d+)", re.I), "purchase_orders"),
    "invoice": (re.compile(r"\binvoice\s*#?\s*(\d{3,})", re.I), "invoices"),
    "draft": (re.compile(r"\bdraft(?:\s+message)?\s*#\s*(\d+)", re.I), "customer_messages"),
    "ticket": (re.compile(r"\bticket\s*#?\s*(\d{3,})", re.I), "tickets"),
    "lease": (re.compile(r"\blease\s*#?\s*(\d+)", re.I), "leases"),
}
MAX_OBLIGATIONS = 10  # keeps the "cash left after any combination" set small (2^10)


NOT_MONEY = ("id", "qty", "days", "short_by", "on_hand", "pct", "count")


def _numbers(value, key: str = "") -> list[float]:
    """Every dollar amount inside a JSON-like value (approval details). Ids, quantities,
    day counts and percentages are skipped so they can't pass as dollar amounts."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [] if any(key == k or key.endswith("_" + k) for k in NOT_MONEY) else [float(value)]
    if isinstance(value, dict):
        return [n for k, v in value.items() for n in _numbers(v, k)]
    if isinstance(value, list):
        return [n for v in value for n in _numbers(v, key)]
    return []


def known_figures(conn: sqlite3.Connection, ticket_id: int | None = None) -> tuple[set[float], set[float]]:
    """(money, percentages) the database supports, for the whole shop and the ticket."""
    money: set[float] = set()
    pct: set[float] = set()

    for p in conn.execute("SELECT * FROM pricing"):
        money |= {p["unit_cost"], p["list_price"], p["list_price"] - p["unit_cost"]}
        pct.add(100 * (p["list_price"] - p["unit_cost"]) / p["list_price"])

    balance = cash_balance(conn)
    money |= {r["balance"] for r in conn.execute("SELECT balance FROM cash_accounts")}

    # Everything the shop owes or has queued, without double counting.
    obligations: dict[str, float] = {}
    for i in conn.execute("SELECT i.id, i.amount, i.amount - COALESCE((SELECT SUM(amount) FROM payments p"
                          " WHERE p.kind = 'invoice' AND p.ref_id = i.id), 0) AS owed FROM invoices i"):
        money |= {i["amount"], i["owed"]}
        if i["owed"] > 0:
            obligations[f"invoice:{i['id']}"] = i["owed"]
    for lease in conn.execute("SELECT id, monthly_rent FROM leases"):
        money.add(lease["monthly_rent"])
        obligations[f"rent:{lease['id']}"] = lease["monthly_rent"]
    for po in conn.execute("SELECT * FROM purchase_orders WHERE status IN ('pending_approval', 'approved')"):
        money |= {po["unit_cost"], po["total"]}
        obligations[f"po:{po['id']}"] = po["total"]
    for a in conn.execute("SELECT * FROM approvals WHERE status != 'rejected'"):
        details = json.loads(a["details"])
        money.add(a["amount"])
        money |= {abs(n) for n in _numbers(details)}
        if a["kind"] == "discount":
            m = details.get("margin", {})
            off = m.get("list_price", 0) - m.get("unit_price", 0)
            money |= {off, off * m.get("qty", 0)}
            pct |= {m.get("margin_pct", 0), m.get("discount_pct", 0), m.get("min_margin_pct", 0)}
    # Cash after paying any combination of obligations, and the totals of those combinations.
    amounts = list(obligations.values())[:MAX_OBLIGATIONS]
    for k in range(1, len(amounts) + 1):
        for combo in combinations(amounts, k):
            money |= {sum(combo), abs(balance - sum(combo))}

    if ticket_id is not None:
        t = conn.execute("SELECT t.sku, t.size, t.qty, p.unit_cost, p.list_price, COALESCE(i.qty, 0) AS on_hand"
                         " FROM tickets t LEFT JOIN pricing p ON p.sku = t.sku"
                         " LEFT JOIN inventory i ON i.sku = t.sku AND i.size = t.size WHERE t.id = ?",
                         (ticket_id,)).fetchone()
        if t and t["qty"] and t["unit_cost"] is not None:
            short = max(0, t["qty"] - t["on_hand"])
            for n in {t["qty"], short, t["on_hand"]}:
                money |= {n * t["unit_cost"], n * t["list_price"]}

    from mcp_server.accounting import MIN_MARGIN_PCT
    pct.add(MIN_MARGIN_PCT)
    return {round(abs(m), 2) for m in money}, {round(p, 2) for p in pct}


def _money_ok(value: float, raw: str, known: set[float]) -> bool:
    tolerance = 0.5 if "." not in raw else 0.011  # "$52" may round $52.20; "$52.20" must match
    return any(abs(value - k) <= tolerance for k in known)


def _pct_ok(value: float, raw: str, known: set[float]) -> bool:
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    tolerance = 0.5 / (10 ** decimals) + 0.001  # "55%" and "55.4%" both match 55.38
    return any(abs(value - k) <= tolerance for k in known)


def check_figures(conn: sqlite3.Connection, text: str, ticket_id: int | None = None) -> list[str]:
    """Return a list of problems (empty if every figure and id is backed by the database)."""
    money, pct = known_figures(conn, ticket_id)
    problems = []
    for m in MONEY.finditer(text):
        raw = m.group(1).replace(",", "")
        if not _money_ok(float(raw), raw, money):
            problems.append(f"${m.group(1)} doesn't match any amount in the database")
    for m in PERCENT.finditer(text):
        if not _pct_ok(float(m.group(1)), m.group(1), pct):
            problems.append(f"{m.group(1)}% doesn't match any margin or discount in the database")
    for label, (pattern, table) in ID_PATTERNS.items():
        for m in pattern.finditer(text):
            if not conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (int(m.group(1)),)).fetchone():
                problems.append(f"{label} #{m.group(1)} doesn't exist")
    return sorted(set(problems))
