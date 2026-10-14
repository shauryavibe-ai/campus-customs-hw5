"""Expected-vs-actual playbook and the double-click monitor page."""

import json
import re
import sys
from pathlib import Path

from agent_team import audit
from agent_team.playbook import compare, expected_steps, priority_order
from mcp_server import db

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def ev(kind, agent, **kw):
    return {"kind": kind, "agent": agent, "run_id": "r1", "ticket_id": 102, **kw}


def test_playbook_reads_the_ticket():
    e = expected_steps(101)
    assert e.facts["short_by"] == 1 and e.facts["restock_held_by_invoice"] and e.first_agent == "inventory"
    assert any(s.agent == "accounting" and "prepare_invoice_payment" in s.tools for s in e.steps)
    assert not any("prepare_purchase_order" in s.tools for s in e.steps)   # no duplicate order: reprint is coming
    assert "facilities" in e.stay_out
    assert expected_steps(102).first_agent == "facilities"


def test_restock_flow_is_inventory_first_then_accounting():
    """Boss -> Inventory (stock by SKU/size, shortfall, vendor) -> Accounting (purchase order)."""
    steps = {s.id: s for s in expected_steps(103).steps}
    assert steps["first"].handoff_to == "inventory" and steps["first"].first_handoff
    assert steps["stock"].after == ("first",) and steps["vendor"].after == ("stock",)
    assert (steps["to_acct"].handoff_from, steps["to_acct"].handoff_to) == ("inventory", "accounting")
    assert steps["po"].after == ("to_acct",) and "12" in steps["po"].action


def test_priority_rent_then_customer_then_discount():
    assert [r["ticket_id"] for r in priority_order([101, 102, 103])] == [102, 101, 103]


def test_out_of_order_is_caught():
    exp = expected_steps(103)
    ev103 = lambda kind, agent, seq, **kw: {"kind": kind, "agent": agent, "seq": seq, "run_id": "r", **kw}
    events = [ev103("handoff", "boss", 1, to="accounting", depth=0),                     # wrong agent first
              ev103("tool_result", "accounting", 2, tool="prepare_purchase_order"),       # PO before Inventory said so
              ev103("handoff", "boss", 3, to="inventory", depth=0),
              ev103("tool_result", "inventory", 4, tool="check_stock"),
              ev103("tool_result", "inventory", 5, tool="find_restock_vendor"),
              ev103("handoff", "inventory", 6, to="accounting", depth=1)]
    r = compare(exp, events)
    assert r["first_agent"] == {"expected": "inventory", "why": exp.first_agent_why, "actual": "accounting"}
    flow = {s["id"]: s for s in r["flow"]}
    assert flow["first"]["done"] is False
    assert flow["po"]["done"] and flow["po"]["in_order"] is False


def test_compare_marks_done_missed_and_extra():
    exp = expected_steps(102)
    events = [ev("tool_result", "boss", tool="get_ticket"), ev("handoff", "boss", to="facilities"),
              ev("tool_call", "facilities", tool="verify_rent_notice"), ev("tool_result", "facilities", tool="verify_rent_notice"),
              ev("tool_call", "inventory", tool="check_stock"), ev("tool_result", "inventory", tool="check_stock"),
              ev("final", "boss", output={"final_status": "waiting_on_human"})]
    for i, e in enumerate(events, 1):
        e["seq"] = i
    events[1]["depth"] = 0
    r = compare(exp, events)
    assert r["per_agent"]["facilities"]["verdict"] == "missed 1 of 2"          # never queued the rent
    assert r["per_agent"]["inventory"]["verdict"] == "should have stayed out"
    assert r["per_agent"]["customer_service"]["verdict"] == "stayed out as expected"
    assert r["score"]["done"] < r["score"]["required"]


def test_page_is_self_contained_and_safe(tmp_path, monkeypatch):
    import build_agent_monitor as page
    monkeypatch.setattr(page, "OUT", tmp_path / "monitor.html")
    with db.connect(read_only=False) as conn:   # a hostile customer note
        conn.execute("UPDATE tickets SET notes = '</script><script>alert(1)</script>' WHERE id = 101")
    audit.append({"kind": "start", "agent": "boss", "run_id": "r1", "ticket_id": 101, "task": "Handle ticket 101"})
    html = page.build().read_text()
    assert "<script>alert(1)" not in html                                    # can't break out of the data block
    data = json.loads(re.search(r'type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
    assert [t["id"] for t in data["tickets"]] == [101, 102, 103]
    assert data["tickets"][0]["notes"] == "</script><script>alert(1)</script>"  # kept as text, shown as text
    assert "http://" not in html and "https://" not in html                   # nothing loaded from outside
    assert "Reflection" in html and "'Cash'" in html


def test_runs_before_a_reset_dont_count():
    from agent_team.playbook import run_events
    entries = [{"seq": 1, "kind": "start", "ticket_id": 102, "run_id": "old"},
               {"seq": 2, "kind": "reset"},
               {"seq": 3, "kind": "start", "ticket_id": 101, "run_id": "new"}]
    assert run_events(entries, 102) == (None, [])
    assert run_events(entries, 101)[0] == "new"


def test_expected_stays_as_filled_after_a_run_changes_the_data(tmp_path, monkeypatch):
    import build_agent_monitor as page
    monkeypatch.setattr(page, "OUT", tmp_path / "desk.html")
    with db.connect(read_only=False) as conn:       # what a finished board run does to the data
        conn.execute("UPDATE invoices SET status = 'paid' WHERE id = 501")
        conn.execute("UPDATE tickets SET status = 'resolved'")
    audit.append({"kind": "reset", "agent": "human"})
    audit.append({"kind": "start", "agent": "boss", "run_id": "r9", "ticket_id": 101, "task": "Handle ticket 101"})
    audit.append({"kind": "handoff", "agent": "boss", "run_id": "r9", "ticket_id": 101, "to": "inventory",
                  "task": "Check tee S", "message_id": 1, "depth": 0})
    audit.append({"kind": "tool_call", "agent": "inventory", "run_id": "r9", "ticket_id": 101, "tool": "check_stock",
                  "args": {"sku": "CC-TEE-WHITE"}, "depth": 1})
    audit.append({"kind": "handoff_reply", "agent": "inventory", "run_id": "r9", "ticket_id": 101, "to": "boss",
                  "answer": "0 on hand.", "status": "done", "message_id": 1, "depth": 1})
    html = page.build().read_text()
    data = json.loads(re.search(r'type="application/json">(.*?)</script>', html, re.S).group(1))
    assert [t["id"] for t in data["tickets"]] == [101, 102, 103]          # resolved tickets keep their tabs
    t101 = data["tickets"][0]
    assert any(s["id"] == "pay" for s in t101["flow"])                     # expected from the ORIGINAL data
    assert t101["actual"]["status_now"] == "resolved"
    assert [(a["label"], a["tools"]) for a in t101["actual"]["agents"]] == [("Boss", []), ("Inventory", [["check_stock", 1]])]
    assert t101["actual"]["delegations"][0] == {"from": "boss", "to": "inventory", "depth": 0, "task": "Check tee S",
                                                "status": "done", "reply": "0 on hand."}


def test_cash_tab_reconciles_and_catches_a_mismatch(tmp_path, monkeypatch):
    import build_agent_monitor as page
    from mcp_server import approvals, facilities
    monkeypatch.setattr(page, "OUT", tmp_path / "desk.html")
    audit.append({"kind": "reset", "agent": "human"})
    a = facilities.prepare_rent_payment(1, reason="rent")["approval"]
    approvals.decide(a["id"], True, "Shaurya")
    c = page.cash_data(json.loads(audit.audit_path().read_text())["entries"])
    assert (c["start"], c["end"], c["db_balance"], c["matches"]) == (3400.0, 1000.0, 1000.0, True)
    assert c["flow"][0]["category"] == "Pay" and c["flow"][0]["tickets"] == [102] and c["flow"][0]["cash_out"] == 2400.0
    with db.connect(read_only=False) as conn:          # someone edits the balance outside the system
        conn.execute("UPDATE cash_accounts SET balance = 1500 WHERE name = 'checking'")
    c = page.cash_data(json.loads(audit.audit_path().read_text())["entries"])
    assert not c["matches"] and c["difference"] == 500.0 and not c["ledger_matches"]
