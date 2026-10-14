"""MCP smoke tests: start an agent's MCP server exactly as mcp.json describes, call its
tools over MCP (stdio), then check the results against the database with plain SQL
(not through mcp_server code).

    .venv/bin/python scripts/mcp_smoke.py                      # run every test
    .venv/bin/python scripts/mcp_smoke.py inventory-shortfalls # run one test

Results are merged into, keyed by test name:
    outputs/mcp_smoke.json           what the server returned + checks
    outputs/mcp_smoke_db_match.json  field-by-field comparison with the database

Tests that write (e.g. prepare_purchase_order) run against a temporary copy of
data/campus_customs_new.db, so the working database is never changed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

HW5 = Path(__file__).resolve().parent.parent
WORKING_DB = HW5 / "data" / "campus_customs_new.db"
SMOKE_OUT = HW5 / "outputs" / "mcp_smoke.json"
MATCH_OUT = HW5 / "outputs" / "mcp_smoke_db_match.json"


def sha(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def payload(result) -> object:
    """Tool result as plain JSON (or the error text for a refused call)."""
    if result.is_error:
        return {"error": " ".join(c.text for c in result.content)}
    data = result.structured_content
    return data["result"] if isinstance(data, dict) and set(data) == {"result"} else data


class Matcher:
    """Collects field-by-field comparisons between what MCP returned and the database."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def __call__(self, where: str, field: str, mcp, db) -> None:
        self.rows.append({"where": where, "field": field, "mcp": mcp, "database": db, "match": mcp == db})


# --- Test 1: inventory finds shortfalls (read-only) ---------------------------------

def inventory_shortfalls_calls() -> list[tuple[str, dict]]:
    return [("find_shortfalls", {})]


def inventory_shortfalls_checks(results: list, con: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    smoke = results[0]
    issues = {(i["sku"], i["size"], i["status"]) for i in smoke["stock_issues"]}
    tickets = {(t["ticket_id"], t["short_by"]) for t in smoke["ticket_shortfalls"]}
    tee_s = next(i for i in smoke["stock_issues"] if (i["sku"], i["size"]) == ("CC-TEE-WHITE", "S"))
    expected_issues = {("CC-MUG-CREST", "OS", "out_of_stock"), ("CC-TEE-WHITE", "S", "out_of_stock"),
                       ("CC-TEE-WHITE", "XL", "low_stock"), ("CC-TEE-WHITE", "L", "low_stock")}
    checks = [
        {"check": "business date is the shop's date", "pass": smoke["business_date"] == "2026-08-31"},
        {"check": "out-of-stock and low-stock sizes", "pass": issues == expected_issues},
        {"check": "tickets short of stock: 101 short 1, 103 short 12", "pass": tickets == {(101, 1), (103, 12)}},
        {"check": "tee S shows invoice 501 reprint as incoming",
         "pass": [i.get("invoice_id") for i in tee_s["incoming"]] == [501]},
    ]

    m = Matcher()
    th = smoke["low_stock_threshold"]
    m("desk", "business_date", smoke["business_date"], con.execute("SELECT date_today FROM desk").fetchone()[0])
    db_inv = {(r["sku"], r["size"]): dict(r) for r in con.execute(
        "SELECT sku, name, size, qty FROM inventory WHERE qty <= ?", (th,))}
    mcp_inv = {(i["sku"], i["size"]): i for i in smoke["stock_issues"]}
    m("inventory", f"rows with qty <= {th}", sorted(map(list, mcp_inv)), sorted(map(list, db_inv)))
    for (sku, size), d in db_inv.items():
        got = mcp_inv.get((sku, size), {})
        m(f"inventory {sku} {size}", "name", got.get("name"), d["name"])
        m(f"inventory {sku} {size}", "qty", got.get("qty"), d["qty"])
        m(f"inventory {sku} {size}", "status", got.get("status"), "out_of_stock" if d["qty"] == 0 else "low_stock")
    db_t = {r["id"]: dict(r) for r in con.execute(
        "SELECT t.id, t.type, t.requester, t.sku, t.size, t.qty, COALESCE(i.qty, 0) AS on_hand FROM tickets t"
        " LEFT JOIN inventory i ON i.sku = t.sku AND i.size = t.size"
        " WHERE t.status = 'open' AND t.sku IS NOT NULL AND t.qty > COALESCE(i.qty, 0)")}
    mcp_t = {t["ticket_id"]: t for t in smoke["ticket_shortfalls"]}
    m("tickets", "open tickets wanting more than on hand", sorted(mcp_t), sorted(db_t))
    for tid, d in db_t.items():
        got = mcp_t.get(tid, {})
        for mf, df in (("type", "type"), ("requester", "requester"), ("sku", "sku"), ("size", "size"),
                       ("qty_wanted", "qty"), ("on_hand", "on_hand")):
            m(f"ticket {tid}", mf, got.get(mf), d[df])
        m(f"ticket {tid}", "short_by", got.get("short_by"), d["qty"] - d["on_hand"])
    inv = dict(con.execute("SELECT i.*, v.name AS vendor, v.lead_days FROM invoices i"
                           " JOIN vendors v ON v.id = i.vendor_id WHERE i.id = 501").fetchone())
    inc = tee_s["incoming"][0]
    for mf, df in (("invoice_id", "id"), ("vendor", "vendor"), ("description", "description"),
                   ("invoice_status", "status"), ("invoice_due_date", "due_date")):
        m("incoming for tee S (invoice 501)", mf, inc[mf], inv[df])
    m("incoming for tee S (invoice 501)", "lead time in note", f"{inv['lead_days']} days" in inc["note"], True)
    others = sum(len(i["incoming"]) for k, i in mcp_inv.items() if k != ("CC-TEE-WHITE", "S"))
    others += sum(len(t["incoming"]) for t in smoke["ticket_shortfalls"] if t["sku"] != "CC-TEE-WHITE")
    m("incoming for other items", "count (only purchase orders could add any)", others,
      con.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0])
    return checks, m.rows


# --- Test 2: accounting prepares a purchase order (writes, so runs on a copy) --------

PO_ARGS = {"vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 12, "ticket_id": 103,
           "reason": "Ticket 103: Yale AI Club wants 20 size-M hoodies, only 8 on hand. Restock the 12 short."}


def accounting_po_calls() -> list[tuple[str, dict]]:
    return [
        ("prepare_purchase_order", PO_ARGS),
        # Must be refused: Elm City Gifts makes mugs, not hoodies.
        ("prepare_purchase_order", {**PO_ARGS, "vendor_id": 2, "reason": "Wrong vendor on purpose"}),
        ("list_approvals", {}),
        ("get_cash_position", {}),
    ]


def accounting_po_checks(results: list, con: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    ok, refused, queue, cash = results
    po, approval = ok["purchase_order"], ok["approval"]
    checks = [
        {"check": "purchase order created, waiting for a human",
         "pass": po["status"] == "pending_approval" and approval["status"] == "pending"},
        {"check": "12 hoodies (M) from Bulldog Print Co at $22 = $264, arriving 2026-09-05",
         "pass": (po["vendor_id"], po["sku"], po["size"], po["qty"], po["unit_cost"], po["total"],
                  po["expected_date"]) == (1, "CC-HOOD-NAVY", "M", 12, 22.0, 264.0, "2026-09-05")},
        {"check": "linked to ticket 103", "pass": po["ticket_id"] == 103},
        {"check": "wrong vendor refused (Elm City Gifts doesn't make hoodies)",
         "pass": "doesn't supply" in refused.get("error", "")},
        {"check": "the approval queue shows exactly this one request",
         "pass": [a["id"] for a in queue["approvals"]] == [approval["id"]]},
        {"check": "no money moved: checking still $3,400",
         "pass": cash["checking_balance"] == 3400.0},
        {"check": "cash position counts the $264 as committed",
         "pass": cash["totals"]["purchase_orders_committed"] == 264.0},
    ]

    m = Matcher()
    row = dict(con.execute("SELECT * FROM purchase_orders WHERE id = ?", (po["id"],)).fetchone())
    for f in ("id", "vendor_id", "sku", "size", "qty", "unit_cost", "total", "expected_date", "ticket_id",
              "status", "reason", "created_at"):
        m("purchase_orders row", f, po[f], row[f])
    m("purchase_orders", "rows in table (wrong-vendor call added none)",
      1, con.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0])
    vendor = con.execute("SELECT name, lead_days FROM vendors WHERE id = 1").fetchone()
    m("vendors 1", "vendor actually supplies the SKU (vendor_products)", True,
      con.execute("SELECT 1 FROM vendor_products WHERE vendor_id = 1 AND sku = 'CC-HOOD-NAVY'").fetchone() is not None)
    unit_cost = con.execute("SELECT unit_cost FROM pricing WHERE sku = 'CC-HOOD-NAVY'").fetchone()[0]
    m("pricing CC-HOOD-NAVY", "unit_cost", po["unit_cost"], unit_cost)
    m("pricing x qty", "total = unit_cost x 12", po["total"], round(unit_cost * 12, 2))
    today = con.execute("SELECT date_today FROM desk").fetchone()[0]
    m("desk + vendors.lead_days", "expected_date = 2026-08-31 + lead days", po["expected_date"],
      con.execute("SELECT date(?, '+' || ? || ' days')", (today, vendor["lead_days"])).fetchone()[0])
    t = con.execute("SELECT t.qty, i.qty FROM tickets t JOIN inventory i ON i.sku = t.sku AND i.size = t.size"
                    " WHERE t.id = 103").fetchone()
    m("ticket 103 vs inventory", "qty ordered = wanted - on hand", po["qty"], t[0] - t[1])
    a = dict(con.execute("SELECT * FROM approvals WHERE id = ?", (approval["id"],)).fetchone())
    for f in ("id", "kind", "ref_id", "amount", "summary", "requested_by", "status", "decided_by"):
        m("approvals row", f, approval[f], a[f])
    m("approvals row", "ref_id points to the purchase order", a["ref_id"], po["id"])
    log = [dict(r) for r in con.execute("SELECT agent, action, details FROM activity_log")]
    m("activity_log", "one entry: accounting requested_purchase_order",
      [(l["agent"], l["action"], json.loads(l["details"])["approval_id"]) for l in log],
      [("accounting", "requested_purchase_order", approval["id"])])
    m("cash_accounts checking", "balance (unchanged)", cash["checking_balance"],
      con.execute("SELECT balance FROM cash_accounts WHERE name = 'checking'").fetchone()[0])
    m("payments", "rows (no payment made)", 0, con.execute("SELECT COUNT(*) FROM payments").fetchone()[0])
    m("inventory CC-HOOD-NAVY M", "qty (unchanged until goods are received)", 8,
      con.execute("SELECT qty FROM inventory WHERE sku = 'CC-HOOD-NAVY' AND size = 'M'").fetchone()[0])
    return checks, m.rows


# --- Test 3: facilities verifies rent is payable (read-only) ------------------------

def facilities_rent_calls() -> list[tuple[str, dict]]:
    return [
        ("check_rent_due", {}),
        ("list_leases", {}),
        ("verify_rent_notice", {"ticket_id": 102}),
        # Must be refused: ticket 101 is a customer order, not a rent notice.
        ("verify_rent_notice", {"ticket_id": 101}),
    ]


def facilities_rent_checks(results: list, con: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    due, leases, verified, refused = results
    lease = leases["leases"][0]
    checks = [
        {"check": "rent due within 7 days: Chapel Street shop, $2,400 on 2026-09-02",
         "pass": [(r["lease_id"], r["monthly_rent"], r["next_due"]) for r in due["rent_due"]] == [(1, 2400.0, "2026-09-02")]},
        {"check": "lease is due_soon, 2 days away", "pass": (lease["rent_status"], lease["days_until_due"]) == ("due_soon", 2)},
        {"check": "checking can cover it: $3,400 - $2,400 = $1,000 left",
         "pass": due["can_cover"] is True and due["checking_after_pending_and_this_rent"] == 1000.0},
        {"check": "rent notice 102 verified on all 4 checks",
         "pass": verified["verdict"] == "verified" and all(c["ok"] for c in verified["checks"]) and len(verified["checks"]) == 4},
        {"check": "amount payable is the lease rent, $2,400", "pass": verified["amount_to_pay"] == 2400.0},
        {"check": "ticket 101 refused (not a rent notice)", "pass": "not a rent_notice" in refused.get("error", "")},
    ]

    m = Matcher()
    today = con.execute("SELECT date_today FROM desk").fetchone()[0]
    m("desk", "business_date", verified["business_date"], today)
    db_lease = dict(con.execute("SELECT * FROM leases WHERE id = 1").fetchone())
    for mf, df in (("lease_id", "id"), ("space_name", "space_name"), ("landlord", "landlord"),
                   ("monthly_rent", "monthly_rent"), ("next_due", "next_due"), ("notes", "notes")):
        m("leases row 1 (list_leases)", mf, lease[mf], db_lease[df])
    m("leases", "number of leases", len(leases["leases"]), con.execute("SELECT COUNT(*) FROM leases").fetchone()[0])
    days = con.execute("SELECT CAST(julianday(?) - julianday(?) AS INTEGER)", (db_lease["next_due"], today)).fetchone()[0]
    m("leases.next_due - desk.date_today", "days_until_due", lease["days_until_due"], days)
    m("payments (kind = rent, lease 1)", "last rent payment",
      lease["last_rent_payment"], con.execute("SELECT * FROM payments WHERE kind = 'rent' AND ref_id = 1").fetchone())
    m("approvals (rent_payment, pending)", "pending approval",
      lease["pending_approval_id"],
      con.execute("SELECT id FROM approvals WHERE kind = 'rent_payment' AND status = 'pending'").fetchone())
    m("leases due within 7 days", "lease ids", [r["lease_id"] for r in due["rent_due"]],
      [r[0] for r in con.execute("SELECT id FROM leases WHERE julianday(next_due) - julianday(?) <= 7", (today,))])
    m("leases due within 7 days", "total_rent_due", due["total_rent_due"],
      con.execute("SELECT SUM(monthly_rent) FROM leases WHERE julianday(next_due) - julianday(?) <= 7", (today,)).fetchone()[0])
    balance = con.execute("SELECT balance FROM cash_accounts WHERE name = 'checking'").fetchone()[0]
    m("cash_accounts checking", "balance", due["checking_balance"], balance)
    m("approvals", "payments waiting for approval", due["payments_waiting_for_approval"],
      con.execute("SELECT COALESCE(SUM(amount), 0) FROM approvals WHERE status = 'pending'"
                  " AND kind IN ('invoice_payment', 'rent_payment')").fetchone()[0])
    m("cash_accounts - leases", "checking after this rent", due["checking_after_pending_and_this_rent"],
      round(balance - db_lease["monthly_rent"], 2))
    t = dict(con.execute("SELECT * FROM tickets WHERE id = 102").fetchone())
    m("tickets 102", "type", "rent_notice", t["type"])
    m("tickets 102", "lease_id", verified["lease"]["lease_id"], t["lease_id"])
    m("tickets 102", "notes", verified["ticket_notes"], t["notes"])
    m("tickets 102 requester vs leases landlord", "requester is the landlord",
      next(c["ok"] for c in verified["checks"] if c["check"] == "requester_is_landlord"),
      t["requester"] == db_lease["landlord"])
    m("leases row 1", "amount_to_pay = monthly_rent", verified["amount_to_pay"], db_lease["monthly_rent"])
    m("tickets 101", "type (why it was refused)", "customer_order",
      con.execute("SELECT type FROM tickets WHERE id = 101").fetchone()[0])
    return checks, m.rows


TESTS = {
    "inventory-shortfalls": {"server": "campus-inventory", "writes": False,
                             "calls": inventory_shortfalls_calls, "checks": inventory_shortfalls_checks},
    "accounting-purchase-order": {"server": "campus-accounting", "writes": True,
                                  "calls": accounting_po_calls, "checks": accounting_po_checks},
    "facilities-rent-payable": {"server": "campus-facilities", "writes": False,
                                "calls": facilities_rent_calls, "checks": facilities_rent_checks},
}


async def run_test(name: str, spec: dict, scratch: Path) -> tuple[dict, dict]:
    server = json.loads((HW5 / "mcp.json").read_text())["mcpServers"][spec["server"]]
    env = {**os.environ, **server.get("env", {})}
    if spec["writes"]:
        db_path = scratch / f"{name}.db"
        shutil.copy(WORKING_DB, db_path)
        env["CAMPUS_DB_PATH"] = str(db_path)
    else:
        db_path = WORKING_DB
    before = sha(WORKING_DB)

    transport = StdioTransport(command=server["command"], args=server["args"], env=env, cwd=str(Path.home()))
    calls, results = [], []
    async with Client(transport) as client:
        tools = sorted(t.name for t in await client.list_tools())
        for tool, args in spec["calls"]():
            started = time.perf_counter()
            result = await client.call_tool(tool, args, raise_on_error=False)
            data = payload(result)
            results.append(data)
            calls.append({"tool": tool, "arguments": args, "is_error": result.is_error,
                          "elapsed_ms": round((time.perf_counter() - started) * 1000), "result": data})

    con = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    checks, match = spec["checks"](results, con)
    con.close()
    working_unchanged = sha(WORKING_DB) == before
    checks.append({"check": "working database unchanged", "pass": working_unchanged})

    run_at = datetime.now(UTC).isoformat(timespec="seconds")
    database = ("temporary copy of data/campus_customs_new.db (this test writes)" if spec["writes"]
                else "data/campus_customs_new.db (read-only test)")
    smoke = {
        "run_at": run_at,
        "server": {"name": spec["server"], "config_file": "mcp.json", "transport": "stdio",
                   "command": [Path(server["command"]).name, *server["args"]], "tools_exposed": tools},
        "database": database,
        "calls": calls,
        "checks": checks,
        "passed": all(c["pass"] for c in checks) and all(r["match"] for r in match),
    }
    db_match = {
        "checked_at": run_at, "database": database,
        "method": "plain SQL on the database after the calls, not through mcp_server code",
        "fields_compared": len(match), "mismatches": sum(not r["match"] for r in match),
        "all_match": all(r["match"] for r in match), "comparisons": match,
    }
    return smoke, db_match


def merge(path: Path, name: str, entry: dict) -> None:
    data = json.loads(path.read_text()) if path.exists() else {}
    if "tests" not in data:  # older single-test format
        data = {"tests": {}}
    data["tests"][name] = entry
    path.write_text(json.dumps(data, indent=2, default=list) + "\n")


def main() -> None:
    names = sys.argv[1:] or list(TESTS)
    failed = False
    with tempfile.TemporaryDirectory() as scratch:
        for name in names:
            smoke, db_match = asyncio.run(run_test(name, TESTS[name], Path(scratch)))
            merge(SMOKE_OUT, name, smoke)
            merge(MATCH_OUT, name, db_match)
            failed |= not smoke["passed"]
            print(f"{'PASS' if smoke['passed'] else 'FAIL'}: {name} ({smoke['server']['name']})"
                  f"  database match {db_match['fields_compared'] - db_match['mismatches']}/{db_match['fields_compared']}")
            for c in smoke["checks"]:
                print(f"  [{'ok' if c['pass'] else 'FAIL'}] {c['check']}")
            for r in db_match["comparisons"]:
                if not r["match"]:
                    print(f"  [MISMATCH] {r['where']} {r['field']}: mcp={r['mcp']!r} db={r['database']!r}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
