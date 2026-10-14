# Campus Customs MCP Server

`campus-customs-ops` (v0.3.0) is the shared data server for the Campus Customs multi-agent operations team. It is built with [FastMCP](https://gofastmcp.com) 4.1.

The Boss, Inventory, Accounting, Facilities and Customer service agents never open the database themselves. They call this server's **tools** over MCP. Each tool reads the shop tables, applies the business rules, and returns a structured result. Anything involving money, a discount or a purchase order is only **queued** for a human, who approves or rejects it on the dashboard.

```
 Agents (Boss · Inventory · Accounting · Facilities · Customer service)
        │  MCP tools/call  (stdio or HTTP)
        ▼
 campus-customs-ops  (mcp_server/server.py)
   ├─ boss.py         4 tools  ─┐   (list_tickets, update_ticket, recent_activity + shared get_ticket)
   ├─ inventory.py    4 tools   │
   ├─ accounting.py   7 tools   ├─ db.py ──▶ data/campus_customs_new.db
   ├─ customer_service.py 2 tools │
   ├─ facilities.py   5 tools  ─┘
   └─ 2 resources (campus://database, campus://schema)

 FastAPI backend (later) ──▶ approvals.decide()   ← human Approve / Reject. Not an MCP tool
```

Full system design: [`../outputs/harness.md`](../outputs/harness.md) (sections 15–21 cover this server). Table relationships: [`../outputs/data_relationships.md`](../outputs/data_relationships.md).

---

## Contents

1. [Quick start](#1-quick-start)
2. [Files](#2-files)
3. [Database and configuration](#3-database-and-configuration)
4. [How agents use the server](#4-how-agents-use-the-server)
5. [Resources](#5-resources)
6. [Inventory agent tools](#6-inventory-agent-tools)
7. [Accounting agent tools](#7-accounting-agent-tools)
8. [Facilities agent tools](#8-facilities-agent-tools)
9. [Shared tools](#9-shared-tools)
10. [Human approval](#10-human-approval-approvalsdecide)
11. [Operations tables](#11-operations-tables)
12. [Safety rules](#12-safety-rules)
13. [Worked example: today's three tickets](#13-worked-example-todays-three-tickets)
14. [Tests](#14-tests)
15. [Adding a tool](#15-adding-a-tool)
16. [Connecting to Claude (Problem 4)](#16-connecting-to-claude-problem-4)
17. Boss and Customer Service tools (Problem 5)
18. `create_ticket` and the automatic figure check (Problem 5)

---

## 1. Quick start

Run everything from the `hw 5/` folder with the project's virtual environment.

Install the dependencies (already done in `.venv`):

```bash
.venv/bin/pip install -r requirements.txt
```

Start the server for agents, over stdio:

```bash
.venv/bin/python -m mcp_server
```

Start one agent's server, showing only that agent's tools plus the shared ones (`inventory`, `accounting` or `facilities`; this is how Claude runs it, see [section 16](#16-connecting-to-claude-problem-4)):

```bash
.venv/bin/python -m mcp_server --agent inventory
```

Or start it over HTTP at `http://127.0.0.1:8765/mcp`:

```bash
.venv/bin/python -m mcp_server --http
```

Run the tests:

```bash
.venv/bin/python -m pytest -q
```

On start-up the server checks that all 9 shop tables exist and creates the 4 operations tables if they're missing ([section 11](#11-operations-tables)). It stops with a clear error if the database is missing or incomplete.

---

## 2. Files

| File | Purpose |
|---|---|
| `__init__.py` | Marks the package |
| `__main__.py` | `python -m mcp_server` entry point: database check, create operations tables, then serve over stdio or `--http` |
| `server.py` | Creates the FastMCP server, registers the 22 tools with their agent tags and read/write hints, and defines the 2 resources. A `lifespan` hook repeats the start-up checks when the server is used in-process (tests, the FastAPI backend) |
| `db.py` | **The only code that opens the database.** Connection rules, the operations-table schema, and shared helpers: business date, money rounding, activity log, approval queue, cash balance |
| `inventory.py` | Inventory agent tools |
| `accounting.py` | Accounting agent tools, plus the shared `list_approvals` |
| `facilities.py` | Facilities agent tools |
| `approvals.py` | `decide()`: the human's approve/reject step. **Deliberately not an MCP tool** |
| `README.md` | This file |

Tests live in `../tests/` (`conftest.py`, `test_mcp_server.py`, `test_agent_tools.py`).

---

## 3. Database and configuration

| Setting | Value |
|---|---|
| Database | `hw 5/data/campus_customs_new.db`, the working copy |
| Override | Environment variable `CAMPUS_DB_PATH` (the tests use this to point at a temporary copy) |
| Original data | `hw 5/data/campus_customs.db`. **The server refuses to open it**, so it stays pristine |
| Business date | `desk.date_today` = **2026-08-31**. Every date calculation (overdue, due soon, arrival dates) uses this, never the computer's clock. Every read tool's result includes `business_date` |
| AI keys | None. The server makes no AI calls, so it needs nothing from `.env` |

Every connection (`db.connect()`):
- opens **read-only** unless the caller passes `read_only=False`;
- turns on `PRAGMA foreign_keys = ON` (SQLite leaves foreign keys unchecked by default);
- uses `mode=rw`, never `rwc`, so a wrong path can't silently create an empty database;
- commits only if the whole block succeeds and **rolls back on any error**.

**Policy constants** (change them in the code):

| Constant | File | Value | Meaning |
|---|---|---|---|
| `LOW_STOCK_THRESHOLD` | `inventory.py` | 3 | A size with this many units or fewer is `low_stock` |
| `MIN_MARGIN_PCT` | `accounting.py` | 30.0 | Discounts leaving less margin than this are flagged `below_min_margin` |
| `MAX_PO_UNITS` | `accounting.py` | 500 | Largest allowed purchase order |
| `DUE_SOON_DAYS` | `facilities.py` | 7 | Rent due within this many days is `due_soon` |

---

## 4. How agents use the server

MCP has two sides:

| Side | What happens | Code |
|---|---|---|
| Server **declares** tools | Named function + description + typed inputs. Reads tables, does the maths, returns JSON | `mcp.tool(inventory.check_stock, tags={"inventory"}, annotations=READ)` |
| Agent **calls** a tool | Sends MCP `tools/call` with the tool name and arguments, and gets the result back | `await client.call_tool("check_stock", {"sku": "CC-TEE-WHITE", "size": "S"})` |

**Why specific tools instead of one generic "read any table / run SQL" tool:**
1. **Safety.** The rules live in code (one owner per table, human approval for money, rent always at the lease amount). A generic tool would let a model write a payment with no approver.
2. **Consistency.** Joins and maths (shortfalls, margins, days overdue) are done the same way every time.
3. **Better tool choice.** Models choose tools by name and description.
4. **Per-agent access.** Every tool is tagged `inventory`, `accounting`, `facilities` or `shared`, so each agent can be given only its own tools.

**Tool annotations** tell the client what each tool does:

| Annotation set | Meaning | Used by |
|---|---|---|
| `READ` | `readOnlyHint: true`. Never changes data | All read tools |
| `QUEUE` | Writes only to the approval queue. Calling it twice returns the same pending request | `prepare_invoice_payment`, `request_discount_approval`, `prepare_rent_payment` |
| `WRITE` | Changes data; each call creates a new change | `prepare_purchase_order`, `receive_purchase_order`, `add_lease_note` |

**Errors** are raised as `ToolError` with a message the agent can act on. The MCP client receives them as tool errors. Examples:
- `Unknown SKU 'NOPE'. Known SKUs: CC-HAT-BLUE, CC-HOOD-NAVY, CC-MUG-CREST, CC-TEE-WHITE`
- `Elm City Gifts doesn't supply CC-HOOD-NAVY. Suppliers: Bulldog Print Co.`
- `$20.00 is below the $22.00 unit cost; not allowed.`

**Connecting from Python** (this is how the FastAPI agent team will connect):

```python
from fastmcp import Client
from mcp_server.server import mcp            # in-process, or:
# from fastmcp.client.transports import StdioTransport
# client = Client(StdioTransport(command=".venv/bin/python", args=["-m", "mcp_server"]))

async with Client(mcp) as client:
    tools = await client.list_tools()
    inventory_tools = [t for t in tools if "inventory" in t.meta["fastmcp"]["tags"]]
    result = await client.call_tool("find_shortfalls", {})
    print(result.data["ticket_shortfalls"])
```

SKUs and sizes are case-insensitive in tool inputs (`"cc-tee-white"`, `"s"` work).

---

## 5. Resources

Read-only reference data, fetched with `client.read_resource(uri)`.

| URI | Returns |
|---|---|
| `campus://database` | `path`, `business_date`, `row_counts` for every table, `table_owners` (which agent owns each table) |
| `campus://schema` | `{table_name: "CREATE TABLE ..."}` for every table |

---

## 6. Inventory agent tools

Owns `inventory` and `pricing`. Reads `tickets`, `vendors`, `vendor_products`, `purchase_orders` and `invoices`.

### `check_stock(sku, size=None, qty_needed=None)` · read

Stock for every size of a product, or one size.

| Input | Type | Notes |
|---|---|---|
| `sku` | str | e.g. `CC-HOOD-NAVY` |
| `size` | str, optional | `S`, `M`, `L`, `XL`, `OS` |
| `qty_needed` | int, optional | Needs `size`. Adds an `order_check` |

Returns `sku`, `name`, `location`, `list_price`, `business_date`, `sizes` (each with `qty` and `status`: `in_stock` / `low_stock` / `out_of_stock`), `total_units`, `incoming` (see below) and, with `qty_needed`, `order_check`.

```json
// check_stock("CC-HOOD-NAVY", "M", qty_needed=20)
{
  "sku": "CC-HOOD-NAVY", "name": "Basic Hoodie Big Yale", "location": "Aisle A",
  "list_price": 58.0, "business_date": "2026-08-31",
  "sizes": [{"size": "M", "qty": 8, "status": "in_stock"}],
  "total_units": 8, "incoming": [],
  "order_check": {"qty_needed": 20, "on_hand": 8, "can_fill": false, "short_by": 12,
                  "other_sizes_in_stock": {"L": 14, "S": 4, "XL": 6}}
}
```

**Incoming stock** comes from two places:
- purchase orders that are `pending_approval` or `approved`;
- **open vendor invoices whose description names the SKU and size**. For example, invoice 501, "Rush reprint CC-TEE-WHITE S", shows up as incoming for the size-S tee, with a note that the reprint may be held until the invoice is paid.

### `find_shortfalls(low_stock_threshold=3)` · read

Everything the shop is short of. Returns:
- `stock_issues`: each size at or below the threshold, with `status` and `incoming`;
- `ticket_shortfalls`: each **open** ticket asking for more units than are on hand, with `qty_wanted`, `on_hand`, `short_by` and `incoming`.

Today: tee S and the mug are out of stock, and tee L (3) and XL (2) are low. Ticket 101 is short 1 and ticket 103 is short 12.

### `find_restock_vendor(sku, size=None, qty=None)` · read

Who can restock the product, fastest first. For each vendor it gives `vendor_id`, `vendor`, `specialty`, `lead_days`, `expected_date` (business date + lead days), `unit_cost` and, with `qty`, `estimated_total`. It also returns `recommended_vendor_id` (the fastest) and any `incoming` stock. Ordering is Accounting's job (`prepare_purchase_order`).

Today: `find_restock_vendor("CC-HOOD-NAVY", "M", qty=12)` → Bulldog Print Co, 5 days, arriving 2026-09-05, $264. For the mug, it's Elm City Gifts at 3 days.

### `receive_purchase_order(purchase_order_id)` · write

When goods physically arrive: adds an **approved** purchase order's units to `inventory` and sets the order to `received`. Refuses orders that are pending, rejected or already received. Logged to `activity_log`. Returns `qty_added`, `new_qty`, `status`.

---

## 7. Accounting agent tools

Owns `vendors`, `invoices`, `payments`, `cash_accounts`, `vendor_products` and `purchase_orders`, but **never writes `payments` or `cash_accounts` itself**. It queues requests, and only `approvals.decide` moves money.

### `get_cash_position(within_days=30)` · read

The full cash picture:

| Field | Meaning |
|---|---|
| `accounts`, `checking_balance` | Cash on hand ($3,400) |
| `open_invoices` | Each open invoice (same shape as `list_invoices`) |
| `rent_due` | Leases due within `within_days`, with `days_until_due` |
| `totals` | `open_invoices`, `rent_due`, `purchase_orders_committed`, `payments_waiting_for_approval`, `all_obligations` |
| `balance_after_pending_payments` | Checking minus payments already queued |
| `balance_after_all_obligations` | Checking minus everything owed. **Today: $160** |
| `can_cover_all` | true / false |

### `list_invoices(status="open", vendor_id=None)` · read

Invoices with `vendor`, `amount`, `still_owed` (amount minus payments made), `due_date`, `days_overdue`, `days_until_due`, `linked_tickets` and `pending_approval_id`. Pass `status=None` for all invoices. Today: invoice 501, $840 owed, **3 days overdue**, linked to ticket 101.

### `check_margin(sku, unit_price, qty=1)` · read

Returns `unit_cost`, `list_price`, `discount_pct`, `margin_per_unit`, `margin_pct`, `revenue`, `total_margin`, `revenue_at_list`, `min_margin_pct`, `lowest_price_at_min_margin`, `needs_human_approval` (true for any price below list) and a `verdict`:

| Verdict | Rule | Hoodie example (cost $22, list $58) |
|---|---|---|
| `list_price` | price ≥ list | $58 |
| `discount_ok` | below list, margin ≥ 30% | $48 → 17.2% off, 54.2% margin, $520 margin on 20 |
| `below_min_margin` | margin < 30% but ≥ cost | $30 → 26.7% margin (floor at 30% is $31.43) |
| `below_cost` | price < unit cost | $20 |

### `prepare_invoice_payment(invoice_id, reason, amount=None)` · queue

Queues a vendor payment for approval. `amount` defaults to the full amount still owed.

It refuses if:
- the invoice isn't `open`;
- `amount` is ≤ 0 or more than what's still owed;
- checking can't cover the payment;
- `reason` is empty.

If a request for this invoice is already pending, it returns that one (`already_pending: true`). It returns a `warning` if approving every pending payment would overdraw checking. The approval's `details` include the invoice, the reason, `checking_before` and `checking_after_all_pending`.

### `prepare_purchase_order(vendor_id, sku, size, qty, reason, ticket_id=None)` · write

Creates a `purchase_orders` row (`pending_approval`), priced at `unit_cost × qty`, with `expected_date` = business date + vendor lead days, and queues it for approval. Optionally links the order to a ticket.

It refuses if:
- the vendor doesn't supply the SKU (per `vendor_products`);
- the size doesn't exist;
- `qty` is outside 1–500;
- `reason` is empty.

Returns `purchase_order` and `approval`.

### `request_discount_approval(ticket_id, unit_price, reason)` · queue

For an **open `price_override` ticket**. It runs `check_margin` on the ticket's SKU and quantity, then queues the discount. The approval's `details` include the full margin breakdown, `on_hand` and `short_by`, so the human sees any stock gap too.

It refuses:
- **prices below unit cost**;
- list price or higher (no discount needed);
- tickets of any other type.

It returns a `warning` if the margin is below the minimum.

---

## 8. Facilities agent tools

Owns `leases`. Reads `tickets`, `payments` and `cash_accounts`.

### `list_leases()` · read

Every lease with `monthly_rent`, `next_due`, `days_until_due`, `rent_status` (`ok` / `due_soon` ≤ 7 days / `overdue`), `notes`, `last_rent_payment` and `pending_approval_id`. Today: Chapel Street shop, $2,400, due 2026-09-02, 2 days away, `due_soon`.

### `check_rent_due(within_days=7)` · read

Leases due within the window (or overdue), `total_rent_due`, `checking_balance`, `payments_waiting_for_approval`, `checking_after_pending_and_this_rent` and `can_cover`.

### `verify_rent_notice(ticket_id)` · read

Checks a `rent_notice` ticket before any money is prepared:

| Check | Passes when |
|---|---|
| `lease_linked` | The ticket names a lease that exists |
| `requester_is_landlord` | The ticket's requester matches the lease's landlord (case-insensitive) |
| `rent_coming_due` | The lease's next due date is within 31 days |
| `not_already_queued` | No rent payment for this lease is already pending |

Returns each check, the lease, `amount_to_pay` (always the lease's rent) and `verdict`: `verified` or `needs_review`. Ticket 102 is `verified`. If the requester were anyone other than Elm City Properties, it would be `needs_review`.

### `prepare_rent_payment(lease_id, reason, ticket_id=None)` · queue

Queues one month's rent for approval. **The amount is always `leases.monthly_rent`**, never a figure from the notice. With a `ticket_id`, the notice must pass `verify_rent_notice` and must be about the same lease. If a rent payment for this lease is already pending, it returns that one. The approval's `details` include `next_due_after_payment` (2026-10-02).

### `add_lease_note(lease_id, note)` · write

Stores a dated note on the lease (repairs, landlord contact, renewal terms). Notes are **appended**, never overwritten (`[2026-08-31] Back door lock sticks`). The limit is 500 characters per note. Logged to `activity_log`.

---

## 9. Shared tools

### `list_approvals(status="pending")` · read · tag `shared`

The human's queue, newest first. Each item has `id`, `kind`, `ref_id`, `amount`, `summary`, `details`, `requested_by`, `status`, `decided_by`, `decision_note`, `decided_at` and `created_at`. Also returns `pending_payment_total`. Pass `status="approved"`, `"rejected"` or `None` for all.

---

## 10. Human approval: `approvals.decide`

```python
from mcp_server.approvals import decide, ApprovalError
decide(approval_id=1, approve=True, decided_by="Shaurya", note="Pay it, the reprint is blocking an order")
```

- **Not registered as an MCP tool.** Agents can't approve their own requests. The FastAPI backend calls it when the human clicks Approve or Reject on the dashboard.
- **Refuses** an empty `decided_by` or an agent name (`boss`, `inventory`, `accounting`, `facilities`, `customer_service`). It also refuses a request that was already decided.
- **One transaction.** Every change below happens together or not at all. Before paying, it re-checks the invoice is still open and the cash is still there.

| `kind` | On approve | On reject |
|---|---|---|
| `invoice_payment` | Inserts `payments` (`kind='invoice'`, `ref_id`=invoice, `account='checking'`, `paid_at`=business date, `approved_by`=human). Lowers checking. Sets the invoice to `paid` once fully covered | No change |
| `rent_payment` | Inserts `payments` (`kind='rent'`, `ref_id`=lease). Lowers checking. Moves `leases.next_due` forward one month (clamped: Jan 31 → Feb 28) | No change |
| `purchase_order` | Order → `approved` (Inventory can now receive it). No cash moves; the vendor invoices later | Order → `rejected` |
| `discount` | Records the approved unit price for the ticket | No change |

Every decision is written to `activity_log` with the human's name.

```json
// decide(1, True, "Shaurya") on the $840 invoice-501 request
{"payment_id": 1, "amount": 840.0, "checking_after": 2560.0, "invoice_status": "paid"}
```

---

## 11. Operations tables

Created by `db.ensure_ops_tables()` on start-up (`CREATE TABLE IF NOT EXISTS`, safe to run every time). The 9 original shop tables keep their original columns.

| Table | Owner | Key fields | Purpose |
|---|---|---|---|
| `vendor_products` | Accounting | `vendor_id` → vendors, `sku` → pricing | Which vendor restocks which product. Seeded from `vendors.specialty`: Bulldog Print Co (apparel reprint) → hoodie, tee, cap; Elm City Gifts (mugs and small goods) → mug. QuickShip CT is a courier, so it supplies nothing |
| `purchase_orders` | Accounting | `vendor_id`, `sku` + `size` → inventory, `qty` > 0, `unit_cost`, `total`, `expected_date`, `ticket_id`, `status` (`pending_approval` → `approved` / `rejected` → `received`), `reason`, `created_at` | Restock orders |
| `approvals` | Human | `kind` (`invoice_payment` / `rent_payment` / `purchase_order` / `discount`), `ref_id`, `amount`, `summary`, `details` (JSON), `requested_by`, `status` (`pending` / `approved` / `rejected`), `decided_by`, `decision_note`, `decided_at` | The dashboard's approval queue |
| `activity_log` | Boss | `agent`, `action`, `details` (JSON), `business_date`, `created_at` | Feed of every change made by agents or the human |

What `ref_id` points to in `approvals`: invoice id, lease id, purchase order id, or ticket id (for discounts).

---

## 12. Safety rules

| Rule | Enforced by |
|---|---|
| The original database is never opened | `db.db_path()` |
| Every connection checks foreign keys; reads can't write | `db.connect()` |
| No agent can pay, discount or order without a named human | Prepare tools only write `approvals`; `decide()` isn't an MCP tool and refuses agent names |
| `payments.approved_by` is always a human | Only `decide()` inserts payments |
| Rent is always the lease amount, to the lease's landlord | `verify_rent_notice`, `prepare_rent_payment` |
| No selling below cost | `request_discount_approval` |
| No duplicate pending requests for the same invoice, lease or ticket | `db.pending_approval()` check |
| Cash is re-checked at the moment of approval | `approvals._pay()` |
| Only approved purchase orders can be received | `receive_purchase_order` |
| Every change is logged | `db.log_activity()` |
| Agents can't run raw SQL | Only purpose-built tools are exposed |

---

## 13. Worked example: today's three tickets

```
Ticket 101  customer_order  Tauhid Zaman · 1× tee S
  Inventory   check_stock("CC-TEE-WHITE","S",qty_needed=1) → 0 on hand, short 1; incoming: invoice 501 reprint
  Accounting  list_invoices()                              → 501: $840, 3 days overdue, Bulldog Print Co
  Accounting  prepare_invoice_payment(501, reason=...)     → approval #1 pending ($840)

Ticket 102  rent_notice  Elm City Properties
  Facilities  verify_rent_notice(102)                      → verified, $2,400
  Facilities  prepare_rent_payment(1, reason=..., ticket_id=102) → approval #2 pending ($2,400)

Ticket 103  price_override  Yale AI Club · 20× hoodie M, wants a discount
  Inventory   check_stock("CC-HOOD-NAVY","M",qty_needed=20) → 8 on hand, short 12
  Inventory   find_restock_vendor("CC-HOOD-NAVY","M",qty=12) → Bulldog, $264, arrives 2026-09-05
  Accounting  check_margin("CC-HOOD-NAVY", 48, qty=20)       → 17.2% off, 54.2% margin, discount_ok
  Accounting  request_discount_approval(103, 48, reason=...) → approval #3 pending ($960 revenue)
  Accounting  prepare_purchase_order(1,"CC-HOOD-NAVY","M",12, reason=..., ticket_id=103) → PO + approval #4

Any agent    get_cash_position() → $3,400 − $840 − $2,400 = $160 left (+ $264 PO committed)
Human        decide(#1..#4, approve/reject, "Shaurya") on the dashboard
```

---

## 14. Tests

```bash
.venv/bin/python -m pytest -q        # 31 passed
```

`tests/conftest.py` copies the **original** database to a temp folder for every test and points `CAMPUS_DB_PATH` at it, so tests never change `campus_customs_new.db`.

| File | Covers |
|---|---|
| `test_mcp_server.py` (8) | Resources; business date; schema; all 22 tools present with the right agent tags; no approve tool exposed; `call_tool` over MCP including a `ToolError`; foreign keys on; read-only connections; original database refused; missing file not created |
| `test_mcp_json.py` (4) | Launches each server in `mcp.json` exactly as Claude does and checks its tool list |
| `test_agent_tools.py` (19) | Each tool on the real data, plus the full flows: invoice payment (pending → approve → payment row, cash $2,560, invoice paid); agent can't approve; purchase order (approve → receive → stock 20); wrong vendor refused; rejected order; discount refused below cost; rent notice verified; **fake landlord refused**; rent payment (next due → 2026-10-02, cash $1,000); lease notes appended; month clamping; activity log |

---

## 15. Adding a tool

1. Write a plain function in the agent's module (`inventory.py`, `accounting.py`, `facilities.py`). Its **docstring becomes the tool description** the model reads, and its **type hints become the input schema**.
2. Open the database only with `db.connect()`. Pass `read_only=False` only if the tool writes.
3. Use `business_date(conn)` for dates. Raise `ToolError("…")` with a message the agent can act on.
4. If it involves money, a discount or an order, **queue it with `db.create_approval(...)`**, never write `payments` or `cash_accounts` directly.
5. Log writes with `db.log_activity(conn, agent, action, details)`.
6. Register it in `server.py`, e.g. `mcp.tool(module.fn, tags={"<agent>"}, annotations=READ | QUEUE | WRITE)`.
7. Add a test in `tests/test_agent_tools.py`, and update the tag list in `test_tools_are_tagged_by_agent`.

---

## 16. Connecting to Claude (Problem 4)

The server runs as **five MCP servers, one per agent** (Problem 5 added `campus-boss` and `campus-customer-service`). Each is the same code started with `--agent <name>`, which shows only that agent's tools plus the shared `get_ticket` and `list_approvals` tools and the 2 resources. This lets each agent be tested on its own, and an agent can't call another agent's tools.

| Server name | Command | Tools it shows |
|---|---|---|
| `campus-inventory` | `python -m mcp_server --agent inventory` | `check_stock`, `find_shortfalls`, `find_restock_vendor`, `receive_purchase_order`, `list_approvals` |
| `campus-accounting` | `python -m mcp_server --agent accounting` | `get_cash_position`, `list_invoices`, `check_margin`, `prepare_invoice_payment`, `prepare_purchase_order`, `request_discount_approval`, `list_approvals` |
| `campus-facilities` | `python -m mcp_server --agent facilities` | `list_leases`, `check_rent_due`, `verify_rent_notice`, `prepare_rent_payment`, `add_lease_note`, `list_approvals` |

**Connection files** (same JSON in both):

| File | Why |
|---|---|
| `hw 5/mcp.json` | The connection JSON for this assignment |
| `AI Foundations/.mcp.json` | Where Claude Code looks for project MCP servers (the folder the Claude session is opened in) |

Each entry uses absolute paths, because Claude starts the server from its own folder:
- `command`: the project's `.venv/bin/python`;
- `args`: `-m mcp_server --agent <name>`;
- `env.PYTHONPATH`: the `hw 5` folder, so `mcp_server` can be imported from anywhere.

No API keys are in the file: the server makes no AI calls. Running without `--agent` still serves all 22 tools.

**First use:** start a new Claude Code session in `AI Foundations/`. Claude asks you to approve the three project servers, and their tools then appear as `mcp__campus-inventory__check_stock`, etc.

**Tested:** `tests/test_mcp_json.py` reads `mcp.json` and launches each server with exactly that command, args and env, from an unrelated folder. It checks each one shows only its own tools and that a tool call works.

**Careful when testing through Claude:** read tools are safe. `prepare_*` / `request_*` tools add real pending requests to `approvals` in `campus_customs_new.db`, and `add_lease_note` / `receive_purchase_order` change data. To test writes, start a copy with `CAMPUS_DB_PATH` set to a scratch database.

**Smoke tests through `mcp.json`** (`scripts/mcp_smoke.py`). Each test starts the agent's server exactly as `mcp.json` describes, calls its tools over MCP, then compares the results with the database using plain SQL. Results are saved, keyed by test name, in `outputs/mcp_smoke.json` and `outputs/mcp_smoke_db_match.json`.

| Test | Server | Calls | Result | Database match |
|---|---|---|---|---|
| `inventory-shortfalls` | `campus-inventory` | `find_shortfalls` | PASS | 36/36 |
| `accounting-purchase-order` | `campus-accounting` | `prepare_purchase_order` (ticket 103: 12 × hoodie M, $264), the same order to the wrong vendor (refused), `list_approvals`, `get_cash_position`. Runs on a temporary copy because it writes | PASS | 31/31 |
| `facilities-rent-payable` | `campus-facilities` | `check_rent_due`, `list_leases`, `verify_rent_notice(102)` (verified, $2,400), `verify_rent_notice(101)` (refused) | PASS | 22/22 |

```bash
.venv/bin/python scripts/mcp_smoke.py            # all three
```

---

## 17. Boss and Customer Service tools (Problem 5)

Added for the agent team (`../agent_team/`). Full team design: `../agent_team/README.md`.

| Tool | Agent | What it does |
|---|---|---|
| `list_tickets(status="open")` | Boss | Tickets with suggested routing (lead + helpers) per type |
| `get_ticket(ticket_id)` | shared | One ticket with its linked invoice or lease, related approvals, purchase orders, customer drafts, status history and agent hand-offs |
| `update_ticket(ticket_id, status, decision)` | Boss | The final call: status `open` / `in_progress` / `waiting_on_human` / `resolved`, plus the decision in words (≤ 1,500 chars). Recorded in `ticket_updates` |
| `recent_activity(limit=20)` | Boss | Latest `activity_log` entries |
| `draft_customer_message(ticket_id, subject, body)` | Customer Service | Saves a draft to the ticket's requester. **One unsent draft per ticket**: calling again updates it. A human sends it |
| `list_customer_messages(ticket_id?, status?)` | Customer Service | Drafts and sent messages |

New tables: `ticket_updates`, `customer_messages`, and `agent_messages` (every agent-to-agent hand-off, written by the team runtime).

---

## 18. `create_ticket` and the automatic figure check (Problem 5)

**`create_ticket(type, subject, notes, sku?, size?, qty?, invoice_id?, lease_id?, raised_by?)`**: shared, so every agent has it.
- **Purpose:** raise a problem you spotted that no open ticket covers. It opens `open` with requester "<agent> agent" and the shop's business date, and the Boss routes it on a later pass.
- **Types agents may create:** `restock` (→ inventory), `vendor_followup` (→ accounting), `facilities_issue` (→ facilities), `cash_issue` (→ accounting), `other` (→ boss). Customer orders, rent notices and price overrides only come from outside, so an agent can't invent one (e.g. a fake rent notice).
- **Who raised it:** when the tool runs on an agent's own MCP server (`--agent X`), `CAMPUS_AGENT` sets `raised_by`, so an agent can't claim to be another.
- **Validation:** the SKU/size, invoice and lease must exist. If an open ticket already covers the same thing (same type and SKU/size/invoice/lease, or the same subject), that ticket is returned instead.

**Automatic figure check (`mcp_server/fact_check.py`, plain code, no AI)**: runs inside `draft_customer_message` and `update_ticket`. Every `$` amount, `%` and record id (approval #, PO #, invoice, draft #, ticket, lease) must be backed by the database:

| Kind | What counts as backed by the database |
|---|---|
| Dollar amounts | Stored values (prices, costs, invoices, rent, cash, purchase orders, approval amounts and their details), margin per unit, ticket qty × price/cost, discount per unit and in total, and cash left after **any combination** of what is owed (plus the totals of those combinations, e.g. $3,504 and −$104). Exact to the cent if the text gives cents; rounding to whole dollars is allowed if it doesn't |
| Percentages | List margins, the margin and discount % of queued discounts, the 30% minimum (to the precision written) |
| Ids | The record must exist |

If anything fails, the draft or decision is **not saved**. The tool error lists each bad figure, and the agent corrects it and tries again.
- **Limit:** it checks numbers, not the words around them. "$1,000 discount" passes if $1,000 is a real figure (e.g. checking left after rent).
- **Tests:** a fake price, a made-up %, and unknown approval/invoice ids are refused; a decision using derived totals ($160, $3,504, −$104, $960, $200 off) is accepted.

**Safety (Problem 5):** the confidentiality check on customer drafts, untrusted ticket text with injection flags, API key isolation, content seals on approvals and drafts, and a live red-team test. Full write-up: `outputs/safety.md`.
