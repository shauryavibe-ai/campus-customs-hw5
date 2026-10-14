"""Build outputs/desk_tickets.html (Problem 6's page): a page you can double-click (no
server needed) with one tab per ticket on the board, showing the playbook's expected
steps, what actually happened in the run since the last reset (which agents worked,
what they delegated, which tools they used, what the human decided), plus empty Cash
and Reflection tabs for later.

Expected is always computed from the ORIGINAL data (data/campus_customs.db, on a
temporary copy), so it stays exactly as filled in Problem 6 even after a run has
paid invoices or moved stock. Tickets the agents raised during a run use the
current data.

    .venv/bin/python scripts/build_agent_monitor.py

Actual behaviour comes from the audit trail (latest run per ticket). Expected
behaviour comes from agent_team/playbook.py. All data is embedded in the page as
JSON and rendered as text (never as HTML), because ticket notes are written by
customers and are untrusted.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HW5 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HW5))

from agent_team import audit  # noqa: E402
from agent_team.playbook import (AGENTS, compare, expected_steps, priority_order, review_priority,  # noqa: E402
                                  run_events)
from mcp_server import approvals as human  # noqa: E402
from mcp_server import db  # noqa: E402

OUT = HW5 / "outputs" / "desk_tickets.html"
LABEL = {"boss": "Boss", "inventory": "Inventory", "accounting": "Accounting", "facilities": "Facilities",
         "customer_service": "Customer Service"}


def _short(value, n=160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= n else text[:n] + "…"


def describe(e: dict) -> str | None:
    k = e["kind"]
    if k == "tool_call":
        return f"{e['tool']}({_short(e.get('args', {}), 120)})"
    if k == "tool_error":
        return f"{e['tool']} refused: {_short(e.get('error', ''), 220)}"
    if k == "handoff":
        return f"asks {LABEL.get(e['to'], e['to'])}: {_short(e.get('task', ''), 260)}"
    if k == "handoff_refused":
        return f"hand-off to {LABEL.get(e['to'], e['to'])} refused: {_short(e.get('reason', ''), 160)}"
    if k == "handoff_reply":
        return f"replies to {LABEL.get(e['to'], e['to'])}: {_short(e.get('answer', ''), 400)}"
    if k == "final":
        return "final call recorded"
    if k == "start":
        return e.get("task", "start")
    return None


def runs_since_reset(entries: list[dict], ticket_id: int) -> tuple[list[str], list[dict]]:
    """Every run of this ticket since the last reset (this board run), and all their events in order."""
    last_reset = max((e["seq"] for e in entries if e["kind"] == "reset"), default=0)
    run_ids = [e["run_id"] for e in entries if e["kind"] == "start" and e.get("ticket_id") == ticket_id
               and e["seq"] > last_reset]
    return run_ids, [e for e in entries if e.get("run_id") in set(run_ids)]


def actual_for(ticket_id: int, run_ids: list[str], events: list[dict]) -> dict:
    """What really happened: which agents worked, what they delegated, which tools, what the human decided."""
    runs = []
    for rid in run_ids:
        mine = [e for e in events if e.get("run_id") == rid]
        end = next((e for e in mine if e["kind"] == "run_end"), {})
        final = next((e.get("output") or {} for e in mine if e["kind"] == "final"), {})
        runs.append({"run_id": rid, "started": next((e["time"] for e in mine if e["kind"] == "start"), None),
                     "seconds": end.get("elapsed_s"), "model_calls": (end.get("usage") or {}).get("requests"),
                     "handoffs": end.get("handoffs"), "boss_status": final.get("final_status"),
                     "decision": final.get("decision"), "error": end.get("error")})
    replies = {e.get("message_id"): e for e in events if e["kind"] == "handoff_reply"}
    delegations = []
    for e in events:
        if e["kind"] == "handoff":
            r = replies.get(e.get("message_id"), {})
            delegations.append({"from": e["agent"], "to": e["to"], "depth": e.get("depth", 0), "task": _short(e.get("task", ""), 300),
                                "status": r.get("status", "no reply"), "reply": _short(r.get("answer", ""), 300)})
        elif e["kind"] == "handoff_refused":
            delegations.append({"from": e["agent"], "to": e["to"], "depth": e.get("depth", 0), "task": _short(e.get("task", ""), 200),
                                "status": "refused", "reply": e.get("reason", "")})
    agents = []
    for a in AGENTS:
        mine = [e for e in events if e["agent"] == a]
        tools = Counter(e["tool"] for e in mine if e["kind"] == "tool_call")
        asked_by = sorted({LABEL.get(e["agent"], e["agent"]) for e in events if e["kind"] == "handoff" and e["to"] == a})
        delegated = sorted({LABEL.get(e["to"], e["to"]) for e in mine if e["kind"] == "handoff"})
        errors = [f"{e['tool']}: {_short(e.get('error', ''), 140)}" for e in mine if e["kind"] == "tool_error"]
        if tools or delegated or asked_by:
            agents.append({"name": a, "label": LABEL[a], "asked_by": asked_by or (["(started the ticket)"] if a == "boss" else []),
                           "delegated_to": delegated, "tools": tools.most_common(), "tool_calls": sum(tools.values()),
                           "refused": errors})
    with db.connect() as conn:
        decisions = []
        for kind, ref in human.ticket_approval_refs(conn, ticket_id):
            for a in conn.execute("SELECT * FROM approvals WHERE kind = ? AND ref_id = ?", (kind, ref)):
                decisions.append({"what": f"Approval #{a['id']} · {a['kind'].replace('_', ' ')} · ${a['amount']:,.2f}",
                                  "summary": a["summary"], "status": a["status"], "by": a["decided_by"],
                                  "note": a["decision_note"]})
        for u in conn.execute("SELECT * FROM ticket_updates WHERE ticket_id = ? AND agent = 'human'", (ticket_id,)):
            decisions.append({"what": f"Ticket {u['old_status']} → {u['new_status']}", "summary": u["decision"],
                              "status": u["new_status"], "by": "human", "note": None})
        ticket_now = dict(conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone())
        status_now = ticket_now["status"]
    return {"ticket": ticket_now, "runs": runs, "agents": agents, "delegations": delegations, "human": decisions, "status_now": status_now,
            "model_calls": sum(r["model_calls"] or 0 for r in runs), "handoffs": sum(r["handoffs"] or 0 for r in runs),
            "seconds": round(sum(r["seconds"] or 0 for r in runs), 1)}


REASON = {  # what kind of cash movement each approval is
    "rent_payment": ("Pay", "Rent paid to the landlord"),
    "invoice_payment": ("Pay", "Vendor invoice paid (stock the shop bought)"),
    "purchase_order": ("Purchase", "Restock ordered from a vendor; no cash until the vendor bills"),
    "discount": ("Something else", "Discounted sale agreed with the customer; no cash until they pay"),
}


# Reflection (Problem 10): the user's assessment, with evidence pulled from the run itself.
REFLECTION = {
    "verdict": [
        "The agents performed really well. The expected plan was followed clearly: on every ticket the Boss read "
        "the ticket, called the right agent first and made the final call, every required step was done (27 of 27), "
        "and the tools we designed for each agent were the ones they actually used. The one exception to the order "
        "is described below.",
        "This was partly because we kept conversing and adding things on the fly across the problems. Each time "
        "a run showed a gap, we closed it in the next prompt instead of designing everything up front.",
        "One step ran out of order. On ticket 103 (the hoodie discount) the Boss sent Accounting its discount "
        "task at the same moment as it asked Inventory to check stock (audit entries 589 and 590), instead of "
        "waiting for the stock answer. Inventory's answer (8 of 20 in stock) went only to the Boss, so Accounting "
        "priced the discount without it. The Boss also asked for \"the lowest permissible unit price / largest "
        "discount\", so Accounting queued the margin-floor price of $31.43 a hoodie (about 46% off list). The "
        "ticket was still resolved, but this is the clearest thing to improve: the Boss should wait for "
        "Inventory's stock answer before asking Accounting about a discount, pass that answer along, and ask for "
        "a modest discount rather than the largest one allowed.",
    ],
    "on_the_fly": [
        ["Problem 3–4", "Per-agent MCP servers and tools, tested one by one through mcp.json before any agent used them"],
        ["Problem 5", "The Boss became the sole decision maker with a live team roster (capability and capacity) — "
                      "and Inventory now hands purchase orders to Accounting instead of stopping at a vendor name"],
        ["Problem 5", "Guard rails added after live runs: no ping-pong hand-offs, one customer draft per ticket, "
                      "restock sized to the shortfall, an automatic figure check, create_ticket, an append-only audit trail"],
        ["Problem 5", "Safety: confidentiality check on customer drafts, untrusted ticket text, API key isolation, "
                      "sealed approvals — proven with a live prompt-injection test"],
        ["Problem 6–9", "The playbook (expected vs actual), the backend, the warehouse dashboard, auto-resolve after "
                        "the last approval, a human close-ticket step, and the cash reconciliation"],
    ],
    "next": [
        {"title": "1. Receiving inventory — and paying only for what arrived",
         "problem": "Goods come in against a purchase order: they must be counted into stock, and the payment must "
                    "match what was actually received (short, damaged or extra units change what is owed).",
         "flow": ["Inventory counts the delivery against the purchase order and adds the received units to stock.",
                  "Inventory hands Accounting the received quantities (ask_agent).",
                  "Accounting works out the amount payable = received units × agreed unit cost, and queues only that "
                  "payment for approval; any shortfall is flagged.",
                  "The Boss makes the call on disputes; the human approves the payment."],
         "have": ["receive_purchase_order", "check_stock", "prepare_invoice_payment (partial amounts)", "ask_agent", "approvals"],
         "add": ["record_goods_receipt (qty received, damaged, date)", "payable_for_receipt (received × unit cost vs invoice)"]},
        {"title": "2. Reconciling PO, GRN and invoice (three-way match)",
         "problem": "Before any vendor is paid, the purchase order (what we ordered), the goods received note (what "
                    "arrived) and the vendor invoice (what they bill) must agree on items, quantities and prices.",
         "flow": ["Accounting runs the match line by line when a vendor invoice arrives.",
                  "Inventory confirms the goods received note for the same order.",
                  "If all three agree, Accounting queues the payment; if not, it holds the payment and raises a "
                  "vendor_followup ticket with the exact differences.",
                  "The Boss decides what to accept; the human approves."],
         "have": ["purchase_orders", "receive_purchase_order", "list_invoices", "prepare_invoice_payment",
                  "create_ticket (vendor_followup)", "the automatic figure check"],
         "add": ["a goods-received table", "a purchase-order link on invoices", "three_way_match (per-line differences)"]},
        {"title": "3. Back-in-stock messages for waiting customers",
         "problem": "When stock arrives for an item customers were waiting on (like Tauhid's size-S tee), each of them "
                    "should get a message that it's back — without anyone checking by hand.",
         "flow": ["When goods are received, Inventory finds open customer orders that were short for that SKU and size.",
                  "Inventory tells Customer Service who is waiting and how many units are now in stock (ask_agent).",
                  "Customer Service drafts a message to each waiting customer with only confirmed, public facts.",
                  "The confidentiality and figure checks run on every draft; a person sends them."],
         "have": ["find_shortfalls (ticket shortfalls)", "check_stock", "receive_purchase_order", "ask_agent",
                  "draft_customer_message", "confidentiality and figure checks"],
         "add": ["customers_waiting_for (open orders for a SKU and size)", "a trigger after receive_purchase_order"]},
    ],
}


def reflection_data(tickets: list[dict], cash: dict) -> dict:
    """The reflection plus evidence from this run (scores, first calls, tools, cash)."""
    tools: dict[str, set] = {}
    for t in tickets:
        for a in t["actual"]["agents"]:
            tools.setdefault(a["label"], set()).update(name for name, _ in a["tools"])
    evidence = {
        "tickets": [{"id": t["id"], "subject": t["subject"], "status": t["actual"]["status_now"],
                     "done": t["score"]["done"], "required": t["score"]["required"],
                     "out_of_order": t["score"]["out_of_order"],
                     "first_ok": t["first_agent"]["actual"] == t["first_agent"]["expected"],
                     "first": t["first_agent"]["expected"]} for t in tickets],
        "tools": {k: sorted(v) for k, v in tools.items()},
        "cash_matches": cash["matches"], "cash_end": cash["end"],
    }
    return {**REFLECTION, "evidence": evidence}


def cash_data(entries: list[dict]) -> dict:
    """Starting checking balance → every approval decided since the reset, in order (which ticket, why,
    cash out, running balance) → ending balance, reconciled against cash_accounts and the audit trail."""
    from agent_team.playbook import _open
    with _open(True) as conn:      # the original data, read-only
        row = conn.execute("SELECT balance, date FROM cash_accounts WHERE name = 'checking'").fetchone()
        start, start_date = round(row["balance"], 2), row["date"]
    last_reset = max((e["seq"] for e in entries if e["kind"] == "reset"), default=0)
    audit_after = {e["approval_id"]: (e.get("outcome") or {}).get("checking_after")
                   for e in entries if e["kind"] == "human_decision" and e["seq"] > last_reset}
    flow, balance = [], start
    with db.connect() as conn:
        def tickets_for(a) -> list[int]:
            sql = {"discount": "SELECT id FROM tickets WHERE id = ?",
                   "invoice_payment": "SELECT id FROM tickets WHERE invoice_id = ?",
                   "rent_payment": "SELECT id FROM tickets WHERE lease_id = ? AND type = 'rent_notice'",
                   "purchase_order": "SELECT ticket_id FROM purchase_orders WHERE id = ?"}[a["kind"]]
            return [r[0] for r in conn.execute(sql, (a["ref_id"],)) if r[0] is not None]
        for a in conn.execute("SELECT * FROM approvals WHERE status != 'pending' ORDER BY decided_at, id"):
            pay = None
            if a["status"] == "approved" and a["kind"] in ("rent_payment", "invoice_payment"):
                pay = conn.execute("SELECT * FROM payments WHERE kind = ? AND ref_id = ? AND amount = ?",
                                   ("rent" if a["kind"] == "rent_payment" else "invoice", a["ref_id"], a["amount"])).fetchone()
            out = pay["amount"] if pay else 0.0
            balance = round(balance - out, 2)
            category, why = REASON[a["kind"]]
            tickets = tickets_for(a)
            subjects = [conn.execute("SELECT subject FROM tickets WHERE id = ?", (t,)).fetchone()[0] for t in tickets]
            flow.append({"approval_id": a["id"], "when": a["decided_at"], "tickets": tickets, "subjects": subjects,
                         "category": category if a["status"] == "approved" else "Rejected", "why": why,
                         "summary": a["summary"], "amount": a["amount"], "status": a["status"], "by": a["decided_by"],
                         "cash_out": out, "payment_id": pay["id"] if pay else None,
                         "paid_to": None if not pay else ("landlord" if pay["kind"] == "rent" else "vendor"),
                         "balance_after": balance, "audit_balance": audit_after.get(a["id"]),
                         "audit_matches": audit_after.get(a["id"]) is None or abs(audit_after[a["id"]] - balance) < .005})
        db_balance = db.cash_balance(conn)
        payments_total = round(conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payments").fetchone()[0], 2)
        unlinked = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] - sum(1 for f in flow if f["payment_id"])
        committed = round(conn.execute("SELECT COALESCE(SUM(total), 0) FROM purchase_orders WHERE status = 'approved'")
                          .fetchone()[0], 2)
        sales = round(sum(f["amount"] for f in flow if f["category"] == "Something else"), 2)
        lease = conn.execute("SELECT monthly_rent, next_due FROM leases WHERE id = 1").fetchone()
        account_date = conn.execute("SELECT date FROM cash_accounts WHERE name = 'checking'").fetchone()[0]
    end = round(start - sum(f["cash_out"] for f in flow), 2)
    return {"start": start, "start_date": start_date, "flow": flow, "end": end, "db_balance": db_balance,
            "account_date": account_date, "matches": abs(end - db_balance) < .005, "difference": round(db_balance - end, 2),
            "payments_total": payments_total, "ledger_matches": abs((start - payments_total) - db_balance) < .005,
            "unlinked_payments": unlinked, "audit_ok": all(f["audit_matches"] for f in flow),
            "outlook": {"committed_purchase_orders": committed, "after_vendor_bills": round(db_balance - committed, 2),
                        "sales_not_yet_paid": sales, "next_rent": lease["monthly_rent"], "next_rent_due": lease["next_due"]}}


def ticket_data(entries: list[dict], ticket_id: int, ranking: list[dict], exp) -> dict:
    facts = exp.facts
    run_ids, events = runs_since_reset(entries, ticket_id)
    run_id = run_ids[-1] if run_ids else None
    actual = actual_for(ticket_id, run_ids, events)
    result = compare(exp, events)
    end = next((e for e in events if e["kind"] == "run_end"), {})
    start = next((e for e in events if e["kind"] == "start"), {})
    session = next((e for e in entries if e["kind"] == "session_start" and e.get("session_id") == start.get("session_id")), {})
    final = next((e.get("output") or {} for e in events if e["kind"] == "final"), {})
    timeline = [{"seq": e["seq"], "time": e["time"][11:19], "agent": e["agent"], "depth": e.get("depth", 0),
                 "kind": e["kind"], "text": describe(e)} for e in events if describe(e)]
    t = actual.pop("ticket")          # the ticket as it is on the board now (its text never changes in a run)
    return {
        "id": ticket_id, "type": t["type"], "requester": t["requester"], "subject": t["subject"],
        "notes": t["notes"], "status_now": actual["status_now"], "actual": actual,
        "facts": {"sku": t["sku"], "size": t["size"], "qty": t["qty"], "on_hand": facts["on_hand"],
                  "short_by": facts["short_by"],
                  "invoice": f"#{facts['invoice']['id']} ${facts['invoice']['amount']:,.0f} {facts['invoice']['status']}"
                  if facts["invoice"] else None,
                  "lease": f"#{facts['lease']['id']} {facts['lease']['space_name']}, ${facts['lease']['monthly_rent']:,.0f}"
                  f" due {facts['lease']['next_due']}" if facts["lease"] else None},
        "run": {"run_id": run_id, "started": start.get("time"), "database": session.get("database", "").split("hw 5/")[-1],
                "model": (session.get("models") or {}).get("boss"), "model_calls": (end.get("usage") or {}).get("requests"),
                "handoffs": end.get("handoffs"), "seconds": end.get("elapsed_s"), "error": end.get("error")},
        "boss": {k: final.get(k) for k in ("understanding", "decision", "final_status", "pending_approvals",
                                           "assignments", "customer_message_ids")},
        "score": result["score"],
        "priority": next(r for r in ranking if r["ticket_id"] == ticket_id),
        "first_agent": result["first_agent"],
        "flow": result["flow"],
        "agents": [{"name": a, "label": LABEL[a], **result["per_agent"][a],
                    "actual": [x for x in timeline if x["agent"] == a and x["kind"] != "start"]} for a in AGENTS],
        "timeline": timeline,
    }


def reflection_markdown(r: dict) -> str:
    e = r["evidence"]
    lines = ["# Reflection: Campus Customs agent team", ""] + [p + "\n" for p in r["verdict"]]
    lines += ["## Evidence from the run", "", "| Ticket | Playbook steps done | Right agent called first | Status |", "|---|---|---|---|"]
    lines += [f"| {t['id']} {t['subject']} | {t['done']}/{t['required']}{' (' + str(t['out_of_order']) + ' out of order)' if t['out_of_order'] else ''} "
              f"| {'✓' if t['first_ok'] else '✗'} {t['first']} | {t['status']} |" for t in e["tickets"]]
    lines += ["", "Tools each agent used: " + "; ".join(f"**{k}**: {', '.join(v)}" for k, v in e["tools"].items()) + ".",
              "", f"Cash: ending checking ${e['cash_end']:,.2f} {'matches' if e['cash_matches'] else 'does NOT match'} cash_accounts.",
              "", "## Why it worked: building it in conversation", ""]
    lines += [f"- **{w}:** {x}" for w, x in r["on_the_fly"]]
    lines += ["", "## Next: three Campus Customs problems these agents can take on", ""]
    for n in r["next"]:
        lines += [f"### {n['title']}", "", n["problem"], ""] + [f"{i}. {f}" for i, f in enumerate(n["flow"], 1)]
        lines += ["", f"- Tools we already have: {', '.join(n['have'])}", f"- To add: {', '.join(n['add'])}", ""]
    return "\n".join(lines)


def build() -> Path:
    path = audit.audit_path()
    entries = json.loads(path.read_text())["entries"] if path.exists() else []
    with db.connect() as conn:
        board = [r["id"] for r in conn.execute("SELECT id FROM tickets ORDER BY id")]
        today = db.business_date(conn).isoformat()
    # Expected exactly as filled in Problem 6: from the original data.
    from agent_team.playbook import _open
    with _open(True) as conn:
        original_ids = [r["id"] for r in conn.execute("SELECT id FROM tickets WHERE status != 'resolved' ORDER BY id")]
    ranking = priority_order(original_ids, original=True)
    expectations = {t: expected_steps(t, original=True) for t in original_ids}
    raised = [t for t in board if t not in expectations]          # tickets the agents raised during the run
    for r in priority_order(raised):
        r["rank"] += len(ranking)
        r["reason"] = "Raised by an agent during the run. " + r["reason"]
        ranking.append(r)
    expectations.update({t: expected_steps(t, original=False) for t in raised})
    check = audit.verify()
    data = {
        "generated": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        "business_date": today,
        "audit": {"entries": check["entries"], "ok": check["ok"], "problem": check["problem"]},
        "ranking": ranking,
        "review": review_priority(entries, ranking),
        "tickets": [ticket_data(entries, t, ranking, expectations[t]) for t in board if t in expectations],
        "cash": cash_data(entries),
    }
    data["reflection"] = reflection_data(data["tickets"], data["cash"])
    (HW5 / "outputs" / "reflection.md").write_text(reflection_markdown(data["reflection"]), encoding="utf-8")
    # Every "<" becomes \u003c (still valid JSON), so text from tickets can never form a tag.
    blob = json.dumps(data, default=str).replace("<", "\\u003c")
    OUT.write_text(TEMPLATE.replace("__DATA__", blob), encoding="utf-8")
    return OUT


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Campus Customs · Desk Tickets</title>
<style>
:root{--bg:#0a1220;--panel:#121c2e;--panel2:#1a263b;--line:#26344d;--text:#f4f6fb;--muted:#9aa6bb;
--yale:#00356b;--brand:#286dc0;--accent:#6fa8ff;--glow:rgba(111,168,255,.14);--ok:#5ee0a0;--warn:#ffc35a;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{padding:20px 28px 0;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#0c2245,var(--bg))}
h1{margin:0;font-size:20px;letter-spacing:.2px;font-family:Georgia,serif}h1 span{color:var(--brand)}
.sub{color:var(--muted);font-size:12.5px;margin:4px 0 14px}
nav{display:flex;gap:4px;flex-wrap:wrap}
nav button{background:none;border:1px solid transparent;border-bottom:none;color:var(--muted);padding:10px 16px;
border-radius:10px 10px 0 0;cursor:pointer;font:inherit;font-weight:600}
nav button:hover{color:var(--text)}nav button[aria-selected=true]{color:var(--text);background:var(--panel);border-color:var(--line);
box-shadow:inset 0 2px 0 var(--brand)}
nav .later{opacity:.75}
main{padding:22px 28px 60px;max-width:1400px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:16px}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:1px;color:var(--accent);margin:0 0 10px}
.row{display:flex;gap:16px;flex-wrap:wrap}.row>.card{flex:1;min-width:320px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.chip{border:1px solid var(--line);background:var(--panel2);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--muted)}
.chip{display:inline-block;max-width:100%;overflow-wrap:anywhere;margin:2px 2px 2px 0}.chip b{color:var(--text)}.chip.brand{border-color:var(--brand);color:var(--accent);background:var(--glow)}
.chip.ok{border-color:#1f5a40;color:var(--ok)}.chip.warn{border-color:#5a4720;color:var(--warn)}.chip.bad{border-color:#5a2030;color:var(--bad)}
.quote{border-left:3px solid var(--brand);padding:6px 12px;background:var(--panel2);border-radius:0 8px 8px 0;margin:8px 0;white-space:pre-wrap}
.label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}
.agent .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.agent .name{font-weight:700;font-size:15px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.cols h3{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);margin:0 0 6px}
ul.steps,ul.acts{list-style:none;margin:0;padding:0}
ul.steps li{display:flex;gap:8px;padding:5px 0;border-bottom:1px dashed var(--line)}
ul.steps li:last-child,ul.acts li:last-child{border:none}
.mark{width:18px;flex:none;text-align:center;font-weight:700}.mark.ok{color:var(--ok)}.mark.bad{color:var(--bad)}.mark.opt{color:var(--muted)}
.why{display:block;color:var(--muted);font-size:12px}
ul.acts li{padding:5px 0;border-bottom:1px dashed var(--line);font-size:12.5px;word-break:break-word}
ul.acts .k{color:var(--accent);font-weight:600;margin-right:4px}
.empty{color:var(--muted);font-style:italic}
.extra{margin-top:8px;font-size:12px;color:var(--warn)}
details summary{cursor:pointer;color:var(--accent);font-weight:600}
.tl{margin-top:10px}.tl div{padding:4px 0;border-bottom:1px solid var(--line);font-size:12.5px;display:flex;gap:10px}
.tl .t{color:var(--muted);flex:none;width:64px}.tl .a{flex:none;width:130px;font-weight:600}
.tl .x{word-break:break-word}
.k-handoff .x,.k-handoff_reply .x{color:var(--accent)}.k-tool_error .x,.k-handoff_refused .x{color:var(--warn)}
.placeholder{text-align:center;padding:70px 20px;border:1px dashed var(--brand);border-radius:16px;background:var(--glow)}
.placeholder .big{font-size:40px;color:var(--brand);font-weight:800}
.flow{list-style:none;margin:8px 0 0;padding:0;counter-reset:f}
.flow li{display:grid;grid-template-columns:26px 22px 120px 1fr;gap:8px;align-items:start;padding:7px 0;border-bottom:1px dashed var(--line)}
.flow li:last-child{border:none}.flow .n{color:var(--brand);font-weight:800}
.flow .who{font-weight:700}.flow .arrow{color:var(--accent)}
.mark.ooo{color:var(--warn)}
.prio{list-style:none;margin:6px 0 0;padding:0}.prio li{padding:6px 10px;border-radius:8px;margin-bottom:4px;background:var(--panel2);display:flex;gap:10px}
.prio li.me{outline:1px solid var(--brand);background:var(--glow)}.prio .r{color:var(--brand);font-weight:800;width:22px}
.job{margin:0;padding-left:18px}.job li{margin:3px 0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:900px){.two{grid-template-columns:1fr}}
table.act{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}
table.act th{text-align:left;color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.6px;border-bottom:1px solid var(--line);padding:6px 8px}
table.act td{border-bottom:1px dashed var(--line);padding:7px 8px;vertical-align:top}
ol.dlg{margin:6px 0 0;padding-left:20px}ol.dlg li{margin:0 0 8px}
ol.dlg .rep{display:block;color:var(--muted);font-size:12px;margin-top:2px}
.st-done{color:var(--ok)}.st-refused,.st-error{color:var(--warn)}
.score{font-size:28px;font-weight:800;color:var(--brand)}
.bar{height:8px;background:var(--panel2);border-radius:99px;overflow:hidden;margin-top:6px}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--brand),var(--accent))}
</style></head>
<body>
<header>
  <h1>Campus Customs <span>Desk Tickets</span></h1>
  <div class="sub" id="sub"></div>
  <nav id="tabs" role="tablist"></nav>
</header>
<main id="panel" role="tabpanel"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const add = (p, ...kids) => { kids.forEach(k => k && p.appendChild(k)); return p; };
const chip = (label, value, cls) => { const c = el('span', 'chip ' + (cls || '')); c.append(label ? label + ' ' : ''); add(c, el('b', null, value)); return c; };
const verdictCls = v => v.startsWith('on track') || v.startsWith('stayed out') || v === 'not needed' ? 'ok' : v.startsWith('missed') || v.startsWith('should') ? 'bad' : 'warn';
const typeName = t => ({customer_order: 'Customer order', rent_notice: 'Rent notice', price_override: 'Discount request'}[t] || t);

document.getElementById('sub').textContent =
  `Shop date ${D.business_date} · built ${D.generated} from the audit trail (${D.audit.entries} entries, chain ${D.audit.ok ? 'OK' : 'BROKEN: ' + D.audit.problem}) · rebuild: .venv/bin/python scripts/build_agent_monitor.py`;

const tabs = D.tickets.map(t => ({id: 't' + t.id, label: `${t.status_now === 'resolved' ? '✓ ' : ''}Ticket ${t.id} · ${t.subject}`, render: () => ticketView(t)}))
  .concat([{id: 'cash', label: 'Cash', render: () => cashView(D.cash)},
           {id: 'reflection', label: 'Reflection', render: () => reflectionView(D.reflection)}]);

const $ = n => (n < 0 ? '−$' : '$') + Math.abs(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
function cashView(C) {
  const wrap = el('div');
  // 1. Verdict first
  const top = el('div', 'card');
  add(top, el('h2', null, 'Checking: start → end, and does it match the database?'));
  const big = el('div', 'row');
  const box = (label, value, sub, cls) => add(el('div', 'card'), el('div', 'label', label), el('div', 'score ' + (cls || ''), value), el('div', 'empty', sub));
  add(big, box('Starting balance', $(C.start), `cash_accounts in the original data · ${C.start_date}`),
      box('Cash out', $(C.start - C.end), `${C.flow.filter(f => f.cash_out).length} payment(s) made`),
      box('Ending balance (computed)', $(C.end), 'start − every payment below'),
      box('cash_accounts in the working database', $(C.db_balance), `checking · dated ${C.account_date}`));
  top.append(big);
  const v = el('p'); v.style.fontSize = '16px'; v.style.fontWeight = '700';
  v.className = C.matches && C.ledger_matches && C.audit_ok ? 'ok-text' : 'bad-text';
  v.textContent = C.matches && C.ledger_matches && C.audit_ok
    ? `✓ They match. Computed ending balance ${$(C.end)} = cash_accounts ${$(C.db_balance)}. The payments table (${$(C.payments_total)} paid) and the audit trail's balance after each decision agree too.`
    : `✗ They don't match: computed ${$(C.end)} vs cash_accounts ${$(C.db_balance)} (difference ${$(C.difference)}).`
      + (C.unlinked_payments ? ` ${C.unlinked_payments} payment(s) have no approval.` : '') + (C.audit_ok ? '' : ' The audit trail disagrees on at least one step.');
  top.append(v);
  wrap.append(top);

  // 2. The flow
  const fl = el('div', 'card');
  add(fl, el('h2', null, 'How cash changed hands, in the order approvals were decided'));
  const tb = el('table', 'act'), hr = el('tr');
  ['#', 'Ticket', 'What happened', 'Reason', 'Cash out', 'Checking after', 'Audit trail says'].forEach(h => hr.append(el('th', null, h)));
  tb.append(hr);
  const startRow = el('tr');
  add(startRow, el('td', null, '—'), el('td', null, '—'), el('td', null, 'Starting balance (clean reset to the original data)'), el('td', null, '—'),
      el('td', null, '—'), add(el('td'), el('b', null, $(C.start))), el('td', null, '—'));
  tb.append(startRow);
  C.flow.forEach((f, i) => {
    const tr = el('tr');
    const tk = el('td'); tk.textContent = f.tickets.map((t, j) => `${t} · ${f.subjects[j]}`).join(', ') || '—';
    const what = el('td'); add(what, el('div', null, f.summary), el('div', 'why', `Approval #${f.approval_id} ${f.status} by ${f.by}${f.payment_id ? ` · payment #${f.payment_id} to the ${f.paid_to}` : ''}`));
    const reason = el('td'); add(reason, chip('', f.category, f.category === 'Pay' ? 'warn' : f.category === 'Rejected' ? 'bad' : 'brand'), el('div', 'why', f.why));
    add(tr, el('td', null, String(i + 1)), tk, what, reason,
        el('td', null, f.cash_out ? '−' + $(f.cash_out) : 'no cash moved'), add(el('td'), el('b', null, $(f.balance_after))),
        el('td', null, f.audit_balance == null ? '—' : `${$(f.audit_balance)} ${f.audit_matches ? '✓' : '✗'}`));
    tb.append(tr);
  });
  const endRow = el('tr');
  add(endRow, el('td', null, '—'), el('td', null, '—'), el('td', null, 'Ending balance'), el('td', null, '—'),
      el('td', null, '−' + $(C.start - C.end)), add(el('td'), el('b', null, $(C.end))), el('td', null, `cash_accounts: ${$(C.db_balance)} ${C.matches ? '✓' : '✗'}`));
  tb.append(endRow);
  fl.append(tb);
  wrap.append(fl);

  // 3. What comes next (not cash yet)
  const o = C.outlook, nx = el('div', 'card');
  add(nx, el('h2', null, 'Not cash yet: what the approvals commit the shop to'));
  const ul = el('ul');
  [`Purchase orders approved, not yet billed: ${$(o.committed_purchase_orders)} → checking would be ${$(o.after_vendor_bills)} once the vendor bills${o.after_vendor_bills < 0 ? ' (overdrawn)' : ''}.`,
   `Discounted sales agreed, not yet paid by the customer: ${$(o.sales_not_yet_paid)} (cash comes in on delivery).`,
   `Next rent: ${$(o.next_rent)} due ${o.next_rent_due}.`].forEach(x => ul.append(el('li', null, x)));
  nx.append(ul);
  wrap.append(nx);
  return wrap;
}

function reflectionView(R) {
  const wrap = el('div');
  const v = el('div', 'card'); v.append(el('h2', null, 'Reflection — how the team did'));
  R.verdict.forEach(p => v.append(el('p', null, p)));
  wrap.append(v);

  const E = R.evidence, ev = el('div', 'card');
  ev.append(el('h2', null, 'Evidence from this run'));
  const tb = el('table', 'act'), hr = el('tr');
  ['Ticket', 'Playbook steps done', 'Right agent called first', 'Status'].forEach(h => hr.append(el('th', null, h)));
  tb.append(hr);
  E.tickets.forEach(t => {
    const tr = el('tr');
    add(tr, el('td', null, `${t.id} · ${t.subject}`),
        add(el('td'), el('b', null, `${t.done}/${t.required}`), document.createTextNode(t.out_of_order ? ` · ${t.out_of_order} out of order` : '')),
        el('td', null, `${t.first_ok ? '✓' : '✗'} ${AGL[t.first] || t.first}`),
        add(el('td'), chip('', t.status, t.status === 'resolved' ? 'ok' : 'warn')));
    tb.append(tr);
  });
  ev.append(tb);
  ev.append(el('div', 'label', 'Tools each agent actually used'));
  const tl = el('table', 'act');
  Object.entries(E.tools).forEach(([agent, tools]) => {
    const tr = el('tr'), td = el('td'); tools.forEach(x => td.append(chip('', x)));
    add(tr, add(el('td'), el('b', null, agent)), td); tl.append(tr);
  });
  ev.append(tl);
  ev.append(el('p', E.cash_matches ? 'ok-text' : 'bad-text',
    `Cash: ending checking ${$(E.cash_end)} ${E.cash_matches ? 'matches' : 'does not match'} cash_accounts in the working database.`));
  wrap.append(ev);

  const w = el('div', 'card'); w.append(el('h2', null, 'Why it worked: building it in conversation'));
  const ul = el('ul'); R.on_the_fly.forEach(([when, what]) => ul.append(add(el('li'), el('b', null, when + ': '), document.createTextNode(what))));
  w.append(ul); wrap.append(w);

  const n = el('div', 'card'); n.append(el('h2', null, 'Next: three Campus Customs problems these agents can take on'));
  const grid = el('div', 'grid');
  R.next.forEach(x => {
    const c = el('div', 'card');
    add(c, el('h3', null, x.title), el('p', null, x.problem));
    const ol = el('ol'); x.flow.forEach(f => ol.append(el('li', null, f))); c.append(ol);
    const have = el('div'); have.append(el('div', 'label', 'Tools we already have')); x.have.forEach(h => have.append(chip('', h, 'ok')));
    const need = el('div'); need.append(el('div', 'label', 'To add')); x.add.forEach(h => need.append(chip('', h, 'warn')));
    add(c, have, need); grid.append(c);
  });
  n.append(grid); wrap.append(n);
  return wrap;
}

function placeholder(title, what) {
  const p = el('div', 'placeholder');
  return add(p, el('div', 'big', title), el('p', null, 'This tab is intentionally empty for now. It will be filled in a later problem.'),
             el('p', 'empty', 'Planned: ' + what));
}

const AGL = {boss:'Boss',inventory:'Inventory',accounting:'Accounting',facilities:'Facilities',customer_service:'Customer Service'};
const AG = {boss:'Boss',inventory:'Inventory',accounting:'Accounting',facilities:'Facilities',customer_service:'Customer Service'};
function markFor(s) {
  if (s.done && s.in_order === false) return el('span', 'mark ooo', '⚠');
  if (s.done) return el('span', 'mark ok', '✓');
  return s.required ? el('span', 'mark bad', '✗') : el('span', 'mark opt', '○');
}
function expectedCard(t) {
  const c = el('div', 'card');
  c.append(el('h2', null, 'Expected — what the Boss and the team should do'));
  const job = el('ol', 'job');
  ['Read and understand every open ticket, and decide which one is the priority.',
   'Call the agent who can solve this ticket first.',
   'Hand out the rest in the right order (facts → requests for approval → customer draft), then make the final call alone.']
    .forEach(x => job.append(el('li', null, x)));
  add(c, el('div', 'label', "The Boss's job"), job);

  const two = el('div', 'two');
  // Priority across open tickets
  const pr = el('div'); pr.append(el('div', 'label', 'Priority across open tickets (expected order)'));
  const ul = el('ul', 'prio');
  D.ranking.forEach(r => {
    const li = el('li', r.ticket_id === t.id ? 'me' : '');
    add(li, el('span', 'r', '#' + r.rank), add(el('span'), el('b', null, `Ticket ${r.ticket_id} · ${r.subject}`),
        el('span', 'why', `${r.reason}${r.detail ? ' — ' + r.detail : ''}`)));
    ul.append(li);
  });
  pr.append(ul);
  const rv = D.review;
  if (rv.recommendations && rv.recommendations.length) {
    const ok = rv.matches;
    add(pr, el('div', 'label', "Actual: the Boss's cross-ticket review put first"),
        add(el('p'), el('span', 'mark ' + (ok ? 'ok' : 'bad'), ok ? '✓ ' : '✗ '), document.createTextNode(rv.recommendations[0])));
  } else pr.append(el('p', 'empty', 'No cross-ticket review yet.'));
  // First agent
  const fa = el('div'); const f = t.first_agent || {};
  fa.append(el('div', 'label', 'Who should be called first on this ticket'));
  add(fa, add(el('p'), el('b', null, AG[f.expected] || f.expected || '—'), el('span', 'why', f.why || '')));
  if (t.run.run_id) {
    const ok = f.actual === f.expected;
    add(fa, el('div', 'label', 'Actual first hand-off'),
        add(el('p'), el('span', 'mark ' + (ok ? 'ok' : 'bad'), ok ? '✓ ' : '✗ '),
            document.createTextNode(f.actual ? `Boss called ${AG[f.actual] || f.actual} first` : 'The Boss handed nothing off')));
  }
  add(two, pr, fa); c.append(two);

  // Ordered flow
  c.append(el('div', 'label', 'Expected flow, in order (✓ done · ⚠ done out of order · ✗ missed · ○ optional)'));
  const fl = el('ol', 'flow');
  t.flow.forEach((s, i) => {
    const li = el('li');
    const body = el('span', null, s.action + (s.required ? '' : ' (optional)'));
    const why = s.why + (s.after.length ? ` · after: ${s.after.join(', ')}` : '') + (s.evidence.length ? ' · ' + s.evidence.join(', ') : '');
    body.append(el('span', 'why', why));
    add(li, el('span', 'n', String(i + 1)), markFor(s), el('span', 'who', AG[s.agent] || s.agent), body);
    fl.append(li);
  });
  c.append(fl);
  return c;
}

function actualCard(t) {
  const A = t.actual, c = el('div', 'card');
  c.append(el('h2', null, 'Actual — what happened in this run'));
  if (!A.runs.length) { c.append(el('p', 'empty', 'This ticket has not been run since the last reset.')); return c; }
  const chips = el('div', 'chips');
  add(chips, chip('Runs', String(A.runs.length)), chip('Model calls', String(A.model_calls)), chip('Hand-offs', String(A.handoffs)),
      chip('Time', `${A.seconds}s`), chip('Boss left it', A.runs.at(-1).boss_status || '—'),
      chip('Ticket now', A.status_now, A.status_now === 'resolved' ? 'ok' : 'warn'));
  c.append(chips);

  c.append(el('div', 'label', 'Which agents worked'));
  const tb = el('table', 'act'), hr = el('tr');
  ['Agent', 'Asked by', 'Delegated to', 'Tools used', 'Refused steps'].forEach(h => hr.append(el('th', null, h)));
  tb.append(hr);
  A.agents.forEach(a => {
    const tr = el('tr');
    const tools = el('td'); a.tools.forEach(([n, k]) => tools.append(chip('', `${n}${k > 1 ? ' ×' + k : ''}`)));
    add(tr, add(el('td'), el('b', null, a.label)), el('td', null, a.asked_by.join(', ') || '—'),
        el('td', null, a.delegated_to.join(', ') || '—'), tools, el('td', null, a.refused.join(' · ') || '—'));
    tb.append(tr);
  });
  c.append(tb);
  const idle = Object.values({boss:'Boss',inventory:'Inventory',accounting:'Accounting',facilities:'Facilities',customer_service:'Customer Service'})
    .filter(l => !A.agents.some(a => a.label === l));
  if (idle.length) c.append(el('p', 'empty', 'Did not work on this ticket: ' + idle.join(', ')));

  c.append(el('div', 'label', 'What they delegated, in order'));
  if (!A.delegations.length) c.append(el('p', 'empty', 'No hand-offs.'));
  else {
    const ol = el('ol', 'dlg');
    A.delegations.forEach(d => {
      const li = el('li');
      add(li, el('b', null, `${'↳ '.repeat(d.depth > 1 ? d.depth - 1 : 0)}${AGL[d.from] || d.from} → ${AGL[d.to] || d.to}: `), document.createTextNode(d.task),
          add(el('span', 'rep'), el('span', 'st-' + (d.status === 'done' ? 'done' : d.status), d.status === 'done' ? '✓ replied: ' : `✗ ${d.status}: `), document.createTextNode(d.reply || '')));
      ol.append(li);
    });
    c.append(ol);
  }

  c.append(el('div', 'label', "The human's decisions"));
  if (!A.human.length) c.append(el('p', 'empty', 'None on this ticket.'));
  else {
    const ul = el('ul');
    A.human.forEach(h => ul.append(add(el('li'), el('b', null, h.what), document.createTextNode(` → ${h.status}${h.by ? ' by ' + h.by : ''}. ${h.summary || ''}`))));
    c.append(ul);
  }
  if (A.runs.at(-1).decision) add(c, el('div', 'label', "Boss's final call"), el('p', null, A.runs.at(-1).decision));
  return c;
}

function ticketView(t) {
  const wrap = el('div');
  // Header: what the ticket is
  const head = el('div', 'card');
  add(head, el('h2', null, `Ticket ${t.id} · ${typeName(t.type)}`), el('div', null, `${t.requester} — ${t.subject}`));
  add(head, el('div', 'label', "Requester's words (untrusted text, shown as-is)"), el('div', 'quote', t.notes || ''));
  const f = t.facts, chips = el('div', 'chips');
  if (f.sku) add(chips, chip('Item', `${f.sku} ${f.size} × ${f.qty}`), chip('On hand', String(f.on_hand)), chip('Short', String(f.short_by), f.short_by ? 'warn' : 'ok'));
  if (f.invoice) add(chips, chip('Invoice', f.invoice, 'warn'));
  if (f.lease) add(chips, chip('Lease', f.lease));
  add(chips, chip('Ticket status now', t.status_now, 'brand'));
  add(head, chips);
  wrap.append(head);

  wrap.append(expectedCard(t));
  wrap.append(actualCard(t));

  if (!t.run.run_id) { wrap.append(add(el('div', 'card'), el('p', 'empty', 'No agent run for this ticket yet. Run: .venv/bin/python -m agent_team'))); return wrap; }

  // Run + Boss's call
  const row = el('div', 'row');
  const run = el('div', 'card');
  const pct = t.score.required ? Math.round(100 * t.score.done / t.score.required) : 0;
  add(run, el('h2', null, 'Playbook match'), el('div', 'score', `${t.score.done} / ${t.score.required}`),
      el('div', 'empty', `required expected steps done${t.score.out_of_order ? ` · ${t.score.out_of_order} out of order` : ''}`), add(el('div', 'bar'), Object.assign(el('i'), {style: `width:${pct}%`})));
  const rc = el('div', 'chips');
  add(rc, chip('Model', t.run.model || '?'), chip('Model calls', String(t.run.model_calls)), chip('Hand-offs', String(t.run.handoffs)),
      chip('Time', `${t.run.seconds}s`), chip('Database', t.run.database), chip('Run', t.run.run_id));
  if (t.run.error) add(rc, chip('Error', t.run.error, 'bad'));
  add(run, rc);
  const boss = el('div', 'card');
  add(boss, el('h2', null, "Boss's final call"), chip('Final status', t.boss.final_status || '—', 'brand'));
  add(boss, el('div', 'label', 'Understanding'), el('p', null, t.boss.understanding || '—'));
  add(boss, el('div', 'label', 'Decision'), el('p', null, t.boss.decision || '—'));
  if ((t.boss.pending_approvals || []).length) {
    const ul = el('ul'); t.boss.pending_approvals.forEach(p => ul.append(el('li', null, p)));
    add(boss, el('div', 'label', 'Waiting for the human'), ul);
  }
  add(row, run, boss); wrap.append(row);

  // Expected vs actual, per agent
  const sec = el('div', 'card'); add(sec, el('h2', null, 'Expected vs actual — by agent'));
  const grid = el('div', 'grid');
  t.agents.forEach(a => {
    const c = el('div', 'card agent');
    add(c, add(el('div', 'top'), el('span', 'name', a.label), el('span', 'chip ' + verdictCls(a.verdict), a.verdict)));
    const cols = el('div', 'cols');
    const exp = el('div'); exp.append(el('h3', null, 'Expected (playbook)'));
    if (a.stay_out_reason) exp.append(el('p', 'empty', 'Should stay out: ' + a.stay_out_reason));
    else if (!a.steps.length) exp.append(el('p', 'empty', 'Nothing required on this ticket.'));
    else {
      const ul = el('ul', 'steps');
      a.steps.forEach(s => {
        const li = el('li');
        const m = markFor(s);
        const body = el('span', null, s.action + (s.required ? '' : ' (optional)'));
        body.append(el('span', 'why', s.why + (s.evidence.length ? ' · ' + s.evidence.join(', ') : '')));
        add(li, m, body); ul.append(li);
      });
      exp.append(ul);
    }
    const act = el('div'); act.append(el('h3', null, 'Actual (audit trail)'));
    if (!a.actual.length) act.append(el('p', 'empty', 'Did nothing on this ticket.'));
    else {
      const ul = el('ul', 'acts');
      a.actual.forEach(x => { const li = el('li'); add(li, el('span', 'k', x.kind.replace('_', ' ')), document.createTextNode(x.text)); ul.append(li); });
      act.append(ul);
    }
    add(cols, exp, act); c.append(cols);
    if (a.extra_actions.length) c.append(el('div', 'extra', 'Not in the playbook: ' + a.extra_actions.join(', ')));
    grid.append(c);
  });
  sec.append(grid); wrap.append(sec);

  // Full timeline
  const tl = el('div', 'card'); const det = el('details');
  det.append(el('summary', null, `Full timeline (${t.timeline.length} steps, in order)`));
  const list = el('div', 'tl');
  t.timeline.forEach(x => {
    const r = el('div', 'k-' + x.kind);
    const who = el('span', 'a', '  '.repeat(x.depth) + (x.depth ? '↳ ' : '') + ({boss:'Boss',inventory:'Inventory',accounting:'Accounting',facilities:'Facilities',customer_service:'Customer Svc'}[x.agent] || x.agent));
    add(r, el('span', 't', x.time), who, el('span', 'x', x.text)); list.append(r);
  });
  det.append(list); tl.append(det); wrap.append(tl);
  return wrap;
}

const nav = document.getElementById('tabs'), panel = document.getElementById('panel');
function show(id) {
  [...nav.children].forEach(b => b.setAttribute('aria-selected', b.dataset.id === id));
  panel.replaceChildren(tabs.find(x => x.id === id).render());
  history.replaceState(null, '', '#' + id);
}
tabs.forEach(x => { const b = el('button', x.later ? 'later' : '', x.label); b.dataset.id = x.id; b.setAttribute('role', 'tab'); b.onclick = () => show(x.id); nav.append(b); });
show(tabs.some(x => '#' + x.id === location.hash) ? location.hash.slice(1) : tabs[0].id);
</script>
</body></html>
"""

if __name__ == "__main__":
    print(f"Wrote {build().relative_to(HW5)}")
