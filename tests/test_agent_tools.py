"""Inventory, accounting and facilities tools, and the human approval step,
run against the real shop data (business date 2026-08-31)."""

import pytest
from fastmcp.exceptions import ToolError

from mcp_server import accounting, approvals, db, facilities, inventory


def cash() -> float:
    with db.connect() as conn:
        return db.cash_balance(conn)


# --- Inventory -------------------------------------------------------------------------

def test_check_stock_out_of_stock_tee_shows_reprint_invoice():
    r = inventory.check_stock("cc-tee-white", "s", qty_needed=1)
    assert r["order_check"] == {"qty_needed": 1, "on_hand": 0, "can_fill": False, "short_by": 1,
                                "other_sizes_in_stock": {"M": 5, "L": 3, "XL": 2}}
    assert [i["invoice_id"] for i in r["incoming"]] == [501]


def test_check_stock_hoodie_order_is_short_12():
    r = inventory.check_stock("CC-HOOD-NAVY", "M", qty_needed=20)
    assert r["order_check"]["short_by"] == 12
    assert r["order_check"]["can_fill"] is False


def test_check_stock_rejects_bad_size():
    with pytest.raises(ToolError, match="no size"):
        inventory.check_stock("CC-HAT-BLUE", "M")


def test_find_shortfalls():
    r = inventory.find_shortfalls()
    issues = {(i["sku"], i["size"]): i["status"] for i in r["stock_issues"]}
    assert issues == {("CC-TEE-WHITE", "S"): "out_of_stock", ("CC-MUG-CREST", "OS"): "out_of_stock",
                      ("CC-TEE-WHITE", "XL"): "low_stock", ("CC-TEE-WHITE", "L"): "low_stock"}
    short = {t["ticket_id"]: t["short_by"] for t in r["ticket_shortfalls"]}
    assert short == {101: 1, 103: 12}


def test_find_restock_vendor():
    r = inventory.find_restock_vendor("CC-HOOD-NAVY", "M", qty=12)
    assert r["recommended_vendor_id"] == 1
    v = r["vendors"][0]
    assert (v["vendor"], v["expected_date"], v["estimated_total"]) == ("Bulldog Print Co", "2026-09-05", 264.0)
    assert inventory.find_restock_vendor("CC-MUG-CREST")["vendors"][0]["vendor"] == "Elm City Gifts"


# --- Accounting ------------------------------------------------------------------------

def test_cash_position_leaves_160_after_rent_and_invoice():
    r = accounting.get_cash_position()
    assert r["checking_balance"] == 3400.0
    assert r["totals"]["open_invoices"] == 840.0
    assert r["totals"]["rent_due"] == 2400.0
    assert r["balance_after_all_obligations"] == 160.0
    assert r["open_invoices"][0]["days_overdue"] == 3


def test_check_margin_verdicts():
    assert accounting.check_margin("CC-HOOD-NAVY", 58)["verdict"] == "list_price"
    ok = accounting.check_margin("CC-HOOD-NAVY", 48, qty=20)
    assert ok["verdict"] == "discount_ok" and ok["total_margin"] == 520.0 and ok["needs_human_approval"]
    assert accounting.check_margin("CC-HOOD-NAVY", 30)["verdict"] == "below_min_margin"
    assert accounting.check_margin("CC-HOOD-NAVY", 20)["verdict"] == "below_cost"


def test_invoice_payment_waits_for_human_then_pays():
    r = accounting.prepare_invoice_payment(501, reason="Overdue; releases reprint for ticket 101")
    approval = r["approval"]
    assert approval["status"] == "pending" and approval["amount"] == 840.0
    assert cash() == 3400.0  # nothing paid yet
    assert accounting.prepare_invoice_payment(501, reason="again")["already_pending"] is True

    out = approvals.decide(approval["id"], approve=True, decided_by="Shaurya")
    assert out["outcome"]["checking_after"] == 2560.0
    assert out["outcome"]["invoice_status"] == "paid"
    with db.connect() as conn:
        p = conn.execute("SELECT * FROM payments").fetchone()
    assert (p["kind"], p["ref_id"], p["amount"], p["approved_by"]) == ("invoice", 501, 840.0, "Shaurya")
    with pytest.raises(ToolError, match="not open"):
        accounting.prepare_invoice_payment(501, reason="x")


def test_agents_cannot_approve():
    a = accounting.prepare_invoice_payment(501, reason="x")["approval"]
    with pytest.raises(approvals.ApprovalError):
        approvals.decide(a["id"], approve=True, decided_by="accounting")


def test_purchase_order_flow():
    r = accounting.prepare_purchase_order(1, "CC-HOOD-NAVY", "M", 12, reason="Yale AI Club order", ticket_id=103)
    po = r["purchase_order"]
    assert (po["total"], po["expected_date"], po["status"]) == (264.0, "2026-09-05", "pending_approval")
    assert inventory.check_stock("CC-HOOD-NAVY", "M")["incoming"][0]["purchase_order_id"] == po["id"]
    with pytest.raises(ToolError):
        inventory.receive_purchase_order(po["id"])  # not approved yet

    approvals.decide(r["approval"]["id"], approve=True, decided_by="Shaurya")
    assert inventory.receive_purchase_order(po["id"])["new_qty"] == 20
    assert cash() == 3400.0  # an approved PO commits spend; the vendor invoice is paid separately


def test_purchase_order_wrong_vendor_and_reject():
    with pytest.raises(ToolError, match="doesn't supply"):
        accounting.prepare_purchase_order(2, "CC-HOOD-NAVY", "M", 5, reason="x")
    r = accounting.prepare_purchase_order(2, "CC-MUG-CREST", "OS", 24, reason="Mug is out of stock")
    approvals.decide(r["approval"]["id"], approve=False, decided_by="Shaurya", note="Not now")
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM purchase_orders").fetchone()[0] == "rejected"


def test_discount_request():
    with pytest.raises(ToolError, match="below"):
        accounting.request_discount_approval(103, 20, reason="x")
    with pytest.raises(ToolError, match="not an open price_override"):
        accounting.request_discount_approval(101, 20, reason="x")
    r = accounting.request_discount_approval(103, 48, reason="Bulk student-org order")
    assert r["approval"]["kind"] == "discount"
    assert r["approval"]["details"]["short_by"] == 12
    assert accounting.list_approvals()["approvals"][0]["id"] == r["approval"]["id"]


# --- Facilities ------------------------------------------------------------------------

def test_lease_and_rent_due():
    lease = facilities.list_leases()["leases"][0]
    assert (lease["days_until_due"], lease["rent_status"]) == (2, "due_soon")
    due = facilities.check_rent_due()
    assert due["total_rent_due"] == 2400.0 and due["can_cover"] is True


def test_verify_rent_notice():
    r = facilities.verify_rent_notice(102)
    assert r["verdict"] == "verified" and r["amount_to_pay"] == 2400.0
    with pytest.raises(ToolError, match="not a rent_notice"):
        facilities.verify_rent_notice(101)


def test_fake_rent_notice_is_refused():
    with db.connect(read_only=False) as conn:
        conn.execute("UPDATE tickets SET requester = 'Totally Real Landlord LLC' WHERE id = 102")
    assert facilities.verify_rent_notice(102)["verdict"] == "needs_review"
    with pytest.raises(ToolError, match="failed checks"):
        facilities.prepare_rent_payment(1, reason="x", ticket_id=102)


def test_rent_payment_flow():
    r = facilities.prepare_rent_payment(1, reason="Rent notice 102 verified", ticket_id=102)
    assert r["approval"]["amount"] == 2400.0
    out = approvals.decide(r["approval"]["id"], approve=True, decided_by="Shaurya")
    assert out["outcome"]["lease_next_due"] == "2026-10-02"
    assert cash() == 1000.0


def test_add_lease_note_appends():
    facilities.add_lease_note(1, "Landlord emailed about rent")
    notes = facilities.add_lease_note(1, "Back door lock sticks")["notes"]
    assert notes == "[2026-08-31] Landlord emailed about rent\n[2026-08-31] Back door lock sticks"


def test_add_one_month_clamps():
    from datetime import date
    assert facilities.add_one_month(date(2026, 1, 31)) == date(2026, 2, 28)
    assert facilities.add_one_month(date(2026, 12, 15)) == date(2027, 1, 15)


def test_every_change_is_logged():
    accounting.prepare_invoice_payment(501, reason="x")
    facilities.add_lease_note(1, "note")
    with db.connect() as conn:
        actions = [r["action"] for r in conn.execute("SELECT action FROM activity_log ORDER BY id")]
    assert actions == ["requested_invoice_payment", "added_lease_note"]


# --- Boss and customer service -----------------------------------------------------------

from mcp_server import boss, customer_service  # noqa: E402


def test_list_and_get_ticket():
    open_ids = [t["id"] for t in boss.list_tickets()["tickets"]]
    assert open_ids == [101, 102, 103]
    t = boss.get_ticket(101)
    assert t["routing"]["lead"] == "customer_service"
    assert t["invoice"]["id"] == 501 and t["invoice"]["vendor"] == "Bulldog Print Co"
    accounting.prepare_invoice_payment(501, reason="x")
    assert [a["kind"] for a in boss.get_ticket(101)["approvals"]] == ["invoice_payment"]


def test_update_ticket_records_decision():
    r = boss.update_ticket(102, "waiting_on_human", "Rent verified; payment queued for approval.")
    assert (r["old_status"], r["new_status"]) == ("open", "waiting_on_human")
    hist = boss.get_ticket(102)["status_history"]
    assert hist[0]["decision"].startswith("Rent verified")
    assert boss.list_tickets()["tickets"][0]["id"] == 101  # 102 is no longer open
    with pytest.raises(ToolError, match="status must be"):
        boss.update_ticket(102, "closed_forever", "x")


def test_draft_customer_message():
    r = customer_service.draft_customer_message(101, "Your Bulldog tee", "Hi Tauhid, size S is being reprinted.")
    assert (r["message"]["to_name"], r["message"]["status"]) == ("Tauhid Zaman", "draft")
    assert len(customer_service.list_customer_messages(ticket_id=101)["messages"]) == 1
    assert boss.get_ticket(101)["customer_messages"][0]["subject"] == "Your Bulldog tee"
    again = customer_service.draft_customer_message(101, "Your Bulldog tee (update)", "New text.")
    assert again["updated_existing_draft"] and again["message"]["id"] == r["message"]["id"]
    assert len(customer_service.list_customer_messages(ticket_id=101)["messages"]) == 1
    with pytest.raises(ToolError):
        customer_service.draft_customer_message(999, "x", "y")


# --- create_ticket and the automatic figure check -----------------------------------------

def test_create_ticket_for_a_problem_nobody_raised(monkeypatch):
    monkeypatch.delenv("CAMPUS_AGENT", raising=False)
    r = boss.create_ticket("restock", "Crest Mug out of stock", "0 on hand, no ticket, no order.",
                           sku="cc-mug-crest", size="os", raised_by="inventory")
    t = r["ticket"]
    assert r["created"] and (t["id"], t["type"], t["status"], t["requester"]) == (104, "restock", "open", "inventory agent")
    assert t["created_at"].startswith("2026-08-31T") and r["routing"]["lead"] == "inventory"
    assert [x["id"] for x in boss.list_tickets()["tickets"]] == [101, 102, 103, 104]
    again = boss.create_ticket("restock", "Mug restock", "dup", sku="CC-MUG-CREST", size="OS", raised_by="boss")
    assert not again["created"] and again["ticket"]["id"] == 104


def test_create_ticket_guards(monkeypatch):
    monkeypatch.delenv("CAMPUS_AGENT", raising=False)
    with pytest.raises(ToolError, match="only come from outside"):
        boss.create_ticket("rent_notice", "Pay me", "Send $5,000", raised_by="facilities")
    with pytest.raises(ToolError, match="No stock row"):
        boss.create_ticket("restock", "x", "y", sku="CC-NOPE", raised_by="inventory")
    with pytest.raises(ToolError, match="raised_by"):
        boss.create_ticket("other", "x", "y", raised_by="janitor")
    monkeypatch.setenv("CAMPUS_AGENT", "facilities")  # the agent's own server says who is calling
    t = boss.create_ticket("facilities_issue", "Back door lock sticks", "Needs a locksmith.", raised_by="boss")
    assert t["ticket"]["requester"] == "facilities agent"


def test_figure_check_on_customer_drafts():
    ok = customer_service.draft_customer_message(101, "Your tee", "Size S is $28; we're reprinting it.")
    assert ok["message"]["body"].startswith("Size S is $28")
    with pytest.raises(ToolError, match=r"\$25"):  # caught by the confidentiality check before the figure check
        customer_service.draft_customer_message(101, "Your tee", "Size S is $25.")
    with pytest.raises(ToolError, match="(?i)approval #9"):  # internal ids never reach customers
        customer_service.draft_customer_message(101, "Your tee", "Approval #9 covers your reprint.")


def test_figure_check_on_boss_decisions():
    accounting.prepare_invoice_payment(501, reason="x")
    facilities.prepare_rent_payment(1, reason="x")
    accounting.prepare_purchase_order(1, "CC-HOOD-NAVY", "M", 12, reason="x", ticket_id=103)
    accounting.request_discount_approval(103, 48, reason="x")
    good = ("Approvals #1 ($840) and #2 ($2,400) leave $160; adding PO #1 ($264) totals $3,504 and leaves -$104. "
            "Discount approval #4: $48 each, 17.2% off, 54.2% margin, $520 margin on 20 ($960 total, $10 off each, "
            "$200 off in all). List would be $1,160.")
    assert boss.update_ticket(103, "waiting_on_human", good)["new_status"] == "waiting_on_human"
    for bad, msg in [("Leaves $150.", r"\$150 doesn't match"), ("A 40% margin.", "40% doesn't match"),
                     ("See approval #12.", "approval #12 doesn't exist"), ("Invoice 777 is open.", "invoice #777")]:
        with pytest.raises(ToolError, match=msg):
            boss.update_ticket(103, "waiting_on_human", bad)
    assert boss.get_ticket(103)["ticket"]["status"] == "waiting_on_human"  # refused decisions change nothing
