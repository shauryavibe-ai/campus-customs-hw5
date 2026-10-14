"""What the Boss and each agent *should* do, in what order, and how that compares with
what they actually did (from the audit trail). Plain code, no AI.

The Boss is expected to:
  1. read and understand every open ticket and decide which one is the **priority**
     (`priority_order`);
  2. call **the agent who can solve it first** (`Expectation.first_agent`). E.g. for a
     restock, Inventory first: it checks stock by SKU and size, finds the shortfall and
     the vendor; then Accounting receives that from Inventory and queues the purchase
     order for approval;
  3. hand out the rest in order and make the final call.

`expected_steps(ticket)` builds the ideal ordered flow from the ticket's own data.
`compare(...)` marks each step done / missed, and done-but-out-of-order.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

from mcp_server import db

AGENTS = ("boss", "inventory", "accounting", "facilities", "customer_service")
INFO_TOOLS = {"get_ticket", "list_approvals", "list_customer_messages", "team_roster", "list_tickets",
              "recent_activity", "list_leases", "list_invoices", "check_rent_due", "get_cash_position",
              "check_stock", "check_margin", "verify_rent_notice"}
ALWAYS_ALLOWED = INFO_TOOLS | {"create_ticket"}   # raising a separate problem is always welcome

# Priority tiers the Boss should use across open tickets (lower = sooner).
TIERS = {
    "rent_notice": (1, "Keeps the shop open: rent has a hard due date and the landlord can act on it"),
    "facilities_issue": (1, "Keeps the shop running"),
    "cash_issue": (1, "Cash risk affects every other decision"),
    "customer_order": (2, "A customer is waiting on an order"),
    "restock": (2, "Stock is out; sales are being lost"),
    "vendor_followup": (2, "A vendor is holding up stock or money"),
    "price_override": (3, "A sales opportunity with no deadline: important, but it can wait for the others"),
    "other": (3, "No deadline stated"),
}


@dataclass
class Step:
    id: str
    agent: str
    action: str
    why: str
    tools: tuple[str, ...] = ()         # done if the agent ran any of these successfully
    handoff_to: str | None = None       # done if work was handed to this agent...
    handoff_from: str | None = None     # ...by this agent (any agent if None)
    first_handoff: bool = False         # ...and it was the Boss's FIRST hand-off on the ticket
    final_status: str | None = None     # done if the Boss's final status matches
    after: tuple[str, ...] = ()         # step ids that must happen before this one
    required: bool = True
    done: bool = False
    in_order: bool | None = None
    seq: int | None = None              # audit seq of the first matching event
    evidence: list[str] = field(default_factory=list)


@dataclass
class Expectation:
    facts: dict
    first_agent: str
    first_agent_why: str
    steps: list[Step]
    stay_out: dict[str, str]


@contextmanager
def _open(original: bool):
    """The working database, or the ORIGINAL data opened read-only (never written). Expected is
    judged against the data as it was before a run, so paying an invoice can't change it."""
    if not original:
        with db.connect() as conn:
            yield conn
        return
    conn = sqlite3.connect(f"{db.ORIGINAL_DB_PATH.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def in_original(ticket_id: int) -> bool:
    with _open(True) as conn:
        return conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone() is not None


def _facts(conn, ticket_id: int) -> dict:
    t = dict(conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone())
    on_hand = None
    if t["sku"] and t["size"]:
        row = conn.execute("SELECT qty FROM inventory WHERE sku = ? AND size = ?", (t["sku"], t["size"])).fetchone()
        on_hand = row["qty"] if row else 0
    invoice = dict(conn.execute("SELECT * FROM invoices WHERE id = ?", (t["invoice_id"],)).fetchone()) \
        if t["invoice_id"] else None
    lease = dict(conn.execute("SELECT * FROM leases WHERE id = ?", (t["lease_id"],)).fetchone()) \
        if t["lease_id"] else None
    short = max(0, (t["qty"] or 0) - (on_hand or 0)) if on_hand is not None else 0
    held = bool(invoice and invoice["status"] == "open" and t["sku"] and t["sku"] in (invoice["description"] or ""))
    return {"ticket": t, "on_hand": on_hand, "short_by": short, "invoice": invoice, "lease": lease,
            "restock_held_by_invoice": held, "business_date": db.business_date(conn).isoformat()}


def priority_order(ticket_ids: list[int], original: bool = False) -> list[dict]:
    """The order the Boss should take open tickets in: by tier, then nearest due date."""
    rows = []
    with _open(original) as conn:
        for tid in ticket_ids:
            f = _facts(conn, tid)
            t = f["ticket"]
            tier, reason = TIERS.get(t["type"], TIERS["other"])
            due = (f["lease"] or {}).get("next_due") or (f["invoice"] or {}).get("due_date") or "9999-12-31"
            detail = {"rent_notice": f"rent due {due}",
                      "customer_order": f"short {f['short_by']}, invoice {f['invoice']['id']} overdue since {due}"
                      if f["invoice"] else f"short {f['short_by']}",
                      "price_override": f"{t['qty']} wanted, {f['short_by']} short, no deadline"}.get(t["type"], "")
            rows.append({"ticket_id": tid, "subject": t["subject"], "type": t["type"], "tier": tier,
                         "reason": reason, "detail": detail, "due": due})
    rows.sort(key=lambda r: (r["tier"], r["due"], r["ticket_id"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def expected_steps(ticket_id: int, original: bool | None = None) -> Expectation:
    """Ideal flow for a ticket. By default judged on the ORIGINAL data when the ticket existed
    there (so a finished run can't rewrite it), else on the working data (tickets raised later)."""
    if original is None:
        original = in_original(ticket_id)
    with _open(original) as conn:
        f = _facts(conn, ticket_id)
    t, short, inv, lease = f["ticket"], f["short_by"], f["invoice"], f["lease"]
    item = f"{t['sku']} size {t['size']}" if t["sku"] else ""
    steps = [Step("read", "boss", "Read and understand the ticket; decide its priority",
                  "Every ticket starts with the Boss", tools=("get_ticket",))]
    stay_out: dict[str, str] = {}
    approvals = False

    def restock_chain(after: str) -> list[Step]:
        """Inventory finds the shortfall and the vendor, then hands the order to Accounting."""
        return [
            Step("vendor", "inventory", f"Find the vendor that can restock the {short} short",
                 "Inventory figures out which vendor can restock, when and at what cost",
                 tools=("find_restock_vendor",), after=(after,)),
            Step("to_acct", "inventory", "Send the purchase-order details to Accounting",
                 "Accounting receives the order from Inventory", handoff_to="accounting", handoff_from="inventory",
                 after=("vendor",)),
            Step("po", "accounting", f"Create a purchase order for {short} units for human approval",
                 "Only Accounting queues money; the human approves", tools=("prepare_purchase_order",),
                 after=("to_acct",)),
        ]

    if t["type"] == "customer_order":
        first, why = "inventory", "The customer wants stock: Inventory can say first whether we can fill it"
        steps += [
            Step("first", "boss", "Call Inventory first", why, handoff_to="inventory", handoff_from="boss",
                 first_handoff=True, after=("read",)),
            Step("stock", "inventory", f"Check {item} for {t['qty']} (stock by SKU and size)", "Exact count by size",
                 tools=("check_stock",), after=("first",)),
        ]
        last = "stock"
        if short and f["restock_held_by_invoice"]:
            steps += [Step("pay", "accounting", f"Queue payment of invoice {inv['id']} (${inv['amount']:,.0f}) for approval",
                           "The reprint for this order is held by that unpaid invoice, so no new order is needed",
                           tools=("prepare_invoice_payment",), after=("stock",))]
            last, approvals = "pay", True
        elif short:
            steps += restock_chain("stock")
            last, approvals = "po", True
        steps += [
            Step("ask_cs", "boss", "Ask Customer Service for a draft once the facts are in", "Customers get an update",
                 handoff_to="customer_service", after=(last,)),
            Step("draft", "customer_service", "Draft a message to the customer",
                 "Only public facts; anything pending is called pending", tools=("draft_customer_message",),
                 after=("ask_cs",)),
        ]
        last = "draft"
        stay_out["facilities"] = "A customer order has nothing to do with the shop space"

    elif t["type"] == "rent_notice":
        first, why = "facilities", "Leases and rent notices are Facilities' job"
        steps += [
            Step("first", "boss", "Call Facilities first", why, handoff_to="facilities", handoff_from="boss",
                 first_handoff=True, after=("read",)),
            Step("verify", "facilities", "Verify the notice against the lease", "Guards against fake or wrong notices",
                 tools=("verify_rent_notice",), after=("first",)),
            Step("rent", "facilities", f"Queue this month's rent (${lease['monthly_rent']:,.0f}) for approval" if lease
                 else "Queue the rent for approval", "Always the lease amount; only after verification",
                 tools=("prepare_rent_payment",), after=("verify",)),
            Step("cash", "accounting", "Confirm the cash picture", "Helper on rent: cash after everything pending",
                 tools=("get_cash_position",), required=False),
            Step("note", "facilities", "Note the notice on the lease", "Keeps the lease record complete",
                 tools=("add_lease_note",), after=("verify",), required=False),
        ]
        last, approvals = "rent", True
        stay_out["inventory"] = "Rent has nothing to do with stock"
        stay_out["customer_service"] = "A landlord's notice doesn't need a customer message"

    elif t["type"] == "price_override":
        first, why = "inventory", ("A discount only matters if we can supply the quantity: Inventory checks stock "
                                   "first and starts any restock")
        steps += [
            Step("first", "boss", "Call Inventory first", why, handoff_to="inventory", handoff_from="boss",
                 first_handoff=True, after=("read",)),
            Step("stock", "inventory", f"Check {item} for {t['qty']} (stock by SKU and size)", "Exact count by size",
                 tools=("check_stock",), after=("first",)),
        ]
        if short:
            steps += restock_chain("stock")
        steps += [
            Step("ask_acct", "boss", "Then ask Accounting about the discount", "Margins are Accounting's job",
                 handoff_to="accounting", handoff_from="boss", after=("stock",)),
            Step("margin", "accounting", "Check margins at candidate prices", "Never below cost; keep the 30% minimum",
                 tools=("check_margin",), after=("ask_acct",)),
            Step("discount", "accounting", "Queue a discount within the margin rules for approval",
                 "Discounts need human approval", tools=("request_discount_approval",), after=("margin",)),
            Step("ask_cs", "boss", "Ask Customer Service for a draft once stock and price are known",
                 "The club is waiting for an answer", handoff_to="customer_service", after=("stock", "margin")),
            Step("draft", "customer_service", "Draft a reply to the requester",
                 "Discount and restock described as pending", tools=("draft_customer_message",), after=("ask_cs",)),
        ]
        last, approvals = "draft", True
        stay_out["facilities"] = "A discount request has nothing to do with the shop space"

    else:
        first, why, last = "boss", "Internal ticket: the Boss decides who takes it", "read"

    status = "waiting_on_human" if approvals else "resolved"
    steps += [
        Step("decide", "boss", "Record the final call", "Only the Boss decides; it must be written down",
             tools=("update_ticket",), after=(last,)),
        Step("status", "boss", f"Final status: {status}", "Anything queued for approval waits on the human",
             final_status=status),
    ]
    return Expectation(f, first, why, steps, stay_out)


def run_events(entries: list[dict], ticket_id: int) -> tuple[str | None, list[dict]]:
    """The latest run for a ticket in the audit trail since the last reset, and its events
    in order. Runs from before a reset belong to old data, so they don't count."""
    last_reset = max((e["seq"] for e in entries if e["kind"] == "reset"), default=0)
    starts = [e for e in entries if e["kind"] == "start" and e.get("ticket_id") == ticket_id and e["seq"] > last_reset]
    if not starts:
        return None, []
    run_id = starts[-1]["run_id"]
    return run_id, [e for e in entries if e.get("run_id") == run_id]


def compare(exp: Expectation, events: list[dict]) -> dict:
    results = [e for e in events if e["kind"] == "tool_result"]
    handoffs = [e for e in events if e["kind"] == "handoff"]
    boss_first = next((e for e in handoffs if e["agent"] == "boss" and e.get("depth", 0) == 0), None)
    final = next((e for e in events if e["kind"] == "final"), None)

    for s in exp.steps:
        hits = []
        if s.tools:
            hits = [e for e in results if e["agent"] == s.agent and e["tool"] in s.tools]
            s.evidence = sorted({f"{s.agent} ran {e['tool']}" for e in hits})
        elif s.first_handoff:
            hits = [boss_first] if boss_first and boss_first["to"] == s.handoff_to else []
            s.evidence = [f"Boss's first hand-off went to {boss_first['to']}"] if boss_first else ["no hand-off"]
        elif s.handoff_to:
            hits = [e for e in handoffs if e["to"] == s.handoff_to and s.handoff_from in (None, e["agent"])]
            s.evidence = sorted({f"{e['agent']} -> {e['to']}" for e in hits})
        elif s.final_status and final:
            got = (final.get("output") or {}).get("final_status")
            hits = [final] if got == s.final_status else []
            s.evidence = [f"final status: {got}"]
        s.done = bool(hits)
        s.seq = min((e["seq"] for e in hits), default=None)

    by_id = {s.id: s for s in exp.steps}
    for s in exp.steps:
        if s.done:
            befores = [by_id[a] for a in s.after if a in by_id and by_id[a].done]
            s.in_order = all(b.seq is not None and b.seq < s.seq for b in befores)

    expected_tools = {(s.agent, t) for s in exp.steps for t in s.tools}
    per_agent = {}
    for a in AGENTS:
        acted = any(e["agent"] == a and e["kind"] in ("tool_call", "handoff") for e in events)
        extra = sorted({e["tool"] for e in results if e["agent"] == a and (a, e["tool"]) not in expected_tools
                        and e["tool"] not in ALWAYS_ALLOWED})
        mine = [s for s in exp.steps if s.agent == a]
        req = [s for s in mine if s.required]
        if a in exp.stay_out:
            verdict = "stayed out as expected" if not acted else "should have stayed out"
        elif not mine:
            verdict = "not needed" if not acted else "helped (not in the playbook)"
        elif sum(s.done for s in req) < len(req):
            verdict = f"missed {len(req) - sum(s.done for s in req)} of {len(req)}"
        elif any(s.in_order is False for s in mine):
            verdict = "done, but out of order"
        else:
            verdict = "on track"
        per_agent[a] = {"verdict": verdict, "extra_actions": extra, "stay_out_reason": exp.stay_out.get(a),
                        "steps": [asdict(s) for s in mine]}
    required = [s for s in exp.steps if s.required]
    return {
        "score": {"done": sum(s.done for s in required), "required": len(required),
                  "out_of_order": sum(1 for s in exp.steps if s.in_order is False)},
        "first_agent": {"expected": exp.first_agent, "why": exp.first_agent_why,
                        "actual": boss_first["to"] if boss_first else None},
        "flow": [asdict(s) for s in exp.steps],
        "per_agent": per_agent,
    }


def review_priority(entries: list[dict], ranking: list[dict]) -> dict:
    """Did the Boss's latest cross-ticket review put the expected #1 ticket first?"""
    reviews = [e for e in entries if e["kind"] == "final" and e.get("ticket_id") is None]
    if not reviews or not ranking:
        return {"recommendations": [], "matches": None}
    recs = (reviews[-1].get("output") or {}).get("recommendations") or []
    words = {"rent_notice": ("rent", "lease"), "customer_order": ("invoice", "tee", "reprint"),
             "price_override": ("hoodie", "discount", "purchase order", "po #")}
    top = ranking[0]
    first = (recs[0] if recs else "").lower()
    return {"recommendations": recs, "expected_first": top["ticket_id"],
            "matches": any(w in first for w in words.get(top["type"], ()))}
