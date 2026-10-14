# Campus Customs Operations — System Harness

How the Campus Customs multi-agent operations team works: the data it uses, which agent owns each table, how the tables link, and the rules the agents follow. The agents run customer orders, unpaid bills and discount requests. They share tools through an MCP server, a FastAPI backend runs them, and a React dashboard lets a human watch them work and approve requests.

**Database:** `data/campus_customs_new.db`, the working copy. The original `data/campus_customs.db` is never written to.
**Business date:** `desk.date_today` = **2026-08-31**. All "overdue" and "days until due" calculations use this date, not the computer's clock.
**Related:** `outputs/data_relationships.md` maps all 9 tables.

| Part | Sections |
|---|---|
| **Agent team** | [1. The team and who owns which table](#1-the-agent-team-and-table-ownership) · [2. Ticket routing](#2-ticket-routing) |
| **Boss agent** | [3. desk](#3-desk) · [4. tickets](#4-tickets) |
| **Inventory agent** | [5. inventory](#5-inventory) · [6. pricing](#6-pricing) |
| **Accounting agent** | [7. vendors](#7-vendors) · [8. invoices](#8-invoices) · [9. payments](#9-payments) · [10. cash_accounts](#10-cash_accounts) |
| **Facilities agent** | [11. leases](#11-leases) |
| **Customer service agent** | [12. What it reads and does](#12-customer-service-agent) |
| **Links** | [13. How the tables link (common columns)](#13-how-the-tables-link-common-columns) · [14. Gaps the build must fill](#14-gaps-the-build-must-fill) |
| **MCP server** (Problem 3) | [15. MCP server](#15-mcp-server-problem-3) · [16. How agents use the tools](#16-how-agents-use-the-tools-call-tool) · [17. Inventory tools](#17-inventory-agent-tools) · [18. Accounting tools](#18-accounting-agent-tools) · [19. Facilities tools](#19-facilities-agent-tools) · [20. Human approval](#20-human-approval-not-an-mcp-tool) · [21. Operations tables](#21-operations-tables-added-in-problem-3) |
| **Claude connection** (Problem 4) | [22. Connecting to Claude](#22-connecting-to-claude-problem-4) |
| **Agent team** (Problem 5) | [23. Agent team](#23-agent-team-problem-5) |
| **Monitor page** (Problem 6) | [24. Agent Monitor page](#24-agent-monitor-page-problem-6) |

---

## 1. The agent team and table ownership

```
                         Human (React dashboard: watches + approves)
                                          │
                                   ┌──────▼──────┐
                                   │ Boss agent  │  main interface · reads every ticket · routes · asks the human for approval
                                   └──────┬──────┘
             ┌──────────────────┬─────────┴────────┬──────────────────────┐
     ┌───────▼───────┐  ┌───────▼───────┐  ┌───────▼───────┐  ┌───────────▼──────────┐
     │   Inventory   │  │  Accounting   │  │  Facilities   │  │   Customer service   │
     │ stock, prices │  │ bills, cash,  │  │ shop lease,   │  │ customer requests,   │
     │ restock needs │  │ vendor pay    │  │ rent notices  │  │ replies, quotes      │
     └───────────────┘  └───────────────┘  └───────────────┘  └──────────────────────┘
                     all agents reach the database only through MCP server tools
```

Each table has **one owner** who can change it. Other agents can only read it, through MCP tools. **W** = owns and writes, **R** = reads, blank = no access.

| Table | Boss | Inventory | Accounting | Facilities | Customer service |
|---|---|---|---|---|---|
| `desk` | **W** | R | R | R | R |
| `tickets` | **W** (routes, sets status) | R | R | R | R (and adds notes through Boss) |
| `inventory` | R | **W** | | | R |
| `pricing` | R | **W** | R (margins) | | R (quotes) |
| `vendors` | R | R (lead times) | **W** | | |
| `invoices` | R | | **W** | | |
| `payments` | R | | **W** (only after human approval) | | |
| `cash_accounts` | R | | **W** | R (can rent be paid?) | |
| `leases` | R | | R | **W** | |

**Money, discounts and purchase orders always need a human.** No agent writes to `payments`, lowers a price below list, or changes `cash_accounts` until the human approves on the dashboard. The approver's name goes into `payments.approved_by`.

---

## 2. Ticket routing

The Boss agent reads each open ticket and routes it by `tickets.type`. One ticket can need several agents.

| `type` | Lead agent | Helpers | What happens | Needs human approval? |
|---|---|---|---|---|
| `customer_order` | Customer service | Inventory (is it in stock?), Accounting (is a linked invoice blocking restock?) | Confirm stock, reserve or explain the delay, reply to the customer | Only if it triggers a payment |
| `rent_notice` | Facilities | Accounting (enough cash? record the payment) | Check the lease and amount, prepare the rent payment | **Yes**: payment |
| `price_override` | Accounting | Inventory (stock for the quantity), Customer service (reply) | Check the margin floor and stock, propose a discount | **Yes**: any price below list |

**The three open tickets:**

| Ticket | Type | Lead | Facts the agents will find | Decision for the human |
|---|---|---|---|---|
| 101 | customer_order | Customer service | Tee size S: 0 in stock. Linked invoice 501 ($840, 3 days overdue) is the rush reprint. Vendor lead time 5 days | Pay invoice 501 to release the reprint? |
| 102 | rent_notice | Facilities | Lease 1: $2,400 due 2026-09-02 (2 days). Checking: $3,400 | Approve $2,400 rent? |
| 103 | price_override | Accounting | 20 hoodies size M wanted, 8 in stock. List $58, cost $22. List total $1,160 | Approve a discount, and sell 8 now or restock 12? |

Paying both 101's invoice and 102's rent leaves **$160** in checking. The Boss agent should show that combined effect before the human approves either.

---

# Boss agent tables

## 3. desk

```sql
CREATE TABLE desk (
    date_today TEXT NOT NULL,
    notes      TEXT
);
```

| Field | Type | Meaning |
|---|---|---|
| `date_today` | TEXT `YYYY-MM-DD` | The shop's "today". Every agent uses it for due-date maths |
| `notes` | TEXT, optional | Shift notes for the whole team (empty today) |

| date_today | notes |
|---|---|
| 2026-08-31 | — |

A single-row table. The Boss agent owns it and can leave notes for the team in `notes`.

## 4. tickets

```sql
CREATE TABLE tickets (
    id INTEGER PRIMARY KEY, type TEXT NOT NULL, requester TEXT NOT NULL, subject TEXT NOT NULL,
    sku TEXT, size TEXT, qty INTEGER, lease_id INTEGER, invoice_id INTEGER,
    status TEXT NOT NULL, notes TEXT, created_at TEXT NOT NULL,
    FOREIGN KEY (lease_id)   REFERENCES leases(id),
    FOREIGN KEY (invoice_id) REFERENCES invoices(id)
);
```

| Field | Type | Meaning | Filled for |
|---|---|---|---|
| `id` | INTEGER, primary key | Ticket number | all |
| `type` | TEXT | `customer_order`, `rent_notice` or `price_override`. Decides routing | all |
| `requester` | TEXT | Who asked: a customer, student org or landlord (no customers table exists) | all |
| `subject` | TEXT | Short title | all |
| `sku`, `size` | TEXT, optional | Product and size. Together they match `inventory (sku, size)` | customer_order, price_override |
| `qty` | INTEGER, optional | Units wanted | customer_order, price_override |
| `lease_id` | INTEGER, FK → `leases.id` | Lease the ticket is about | rent_notice |
| `invoice_id` | INTEGER, FK → `invoices.id` | Invoice the ticket depends on | customer_order (101) |
| `status` | TEXT | Only `open` so far | all |
| `notes` | TEXT, optional | The request in the requester's words | all |
| `created_at` | TEXT, ISO 8601 with time zone | When the ticket arrived | all |

| id | type | requester | subject | sku | size | qty | lease_id | invoice_id | status | created_at |
|---|---|---|---|---|---|---|---|---|---|---|
| 101 | customer_order | Tauhid Zaman | Bulldog tee | CC-TEE-WHITE | S | 1 | — | 501 | open | 2026-08-31 09:15 |
| 102 | rent_notice | Elm City Properties | Rent due | — | — | — | 1 | — | open | 2026-08-31 11:05 |
| 103 | price_override | Yale AI Club | Bulk hoodie discount | CC-HOOD-NAVY | M | 20 | — | — | open | 2026-08-31 16:55 |

Notes: 101 "Needs a tee in size S." · 102 "Email: shop rent due in 2 days." · 103 "Student org wants 20 hoodies, size M, asking for a bulk discount."

---

# Inventory agent tables

## 5. inventory

```sql
CREATE TABLE inventory (
    sku TEXT NOT NULL, name TEXT NOT NULL, size TEXT NOT NULL,
    qty INTEGER NOT NULL, location TEXT NOT NULL,
    PRIMARY KEY (sku, size)
);
```

| Field | Type | Meaning |
|---|---|---|
| `sku` | TEXT | Product code. Matches `pricing.sku` and `tickets.sku` |
| `name` | TEXT | Product name |
| `size` | TEXT | `S`, `M`, `L`, `XL`, or `OS` (one size) |
| `qty` | INTEGER | Units on hand |
| `location` | TEXT | Where the product sits in the shop |

One row per **product and size**: `(sku, size)` is the primary key.

| sku | name | S | M | L | XL | OS | Total | Location |
|---|---|---|---|---|---|---|---|---|
| CC-HOOD-NAVY | Basic Hoodie Big Yale | 4 | **8** | 14 | 6 | | 32 | Aisle A |
| CC-TEE-WHITE | Classic Bulldog Tee | **0** | 5 | 3 | 2 | | 10 | Aisle B |
| CC-HAT-BLUE | Yale Cap | | | | | 40 | 40 | Aisle C |
| CC-MUG-CREST | Crest Mug | | | | | **0** | 0 | Aisle D |

Out of stock: tee size S (ticket 101 is waiting on it) and the Crest Mug (no ticket and no reorder).

## 6. pricing

```sql
CREATE TABLE pricing (
    sku        TEXT PRIMARY KEY,
    unit_cost  REAL NOT NULL,
    list_price REAL NOT NULL
);
```

| Field | Type | Meaning |
|---|---|---|
| `sku` | TEXT, primary key | Product code. One price for all sizes |
| `unit_cost` | REAL (USD) | What the shop pays per unit. **The floor for any discount** |
| `list_price` | REAL (USD) | Normal selling price |

| sku | unit_cost | list_price | Margin / unit | Margin % | Stock at cost | Stock at list |
|---|---|---|---|---|---|---|
| CC-HOOD-NAVY | $22.00 | $58.00 | $36.00 | 62.1% | $704 | $1,856 |
| CC-TEE-WHITE | $8.00 | $28.00 | $20.00 | 71.4% | $80 | $280 |
| CC-HAT-BLUE | $6.00 | $22.00 | $16.00 | 72.7% | $240 | $880 |
| CC-MUG-CREST | $3.50 | $14.00 | $10.50 | 75.0% | $0 | $0 |
| **Total** | | | | | **$1,024** | **$3,016** |

---

# Accounting agent tables

## 7. vendors

```sql
CREATE TABLE vendors (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    specialty  TEXT    NOT NULL,
    lead_days  INTEGER NOT NULL
);
```

| Field | Type | Meaning |
|---|---|---|
| `id` | INTEGER, primary key | Vendor ID. Referenced by `invoices.vendor_id` |
| `name` | TEXT | Vendor's business name |
| `specialty` | TEXT | What the vendor supplies (the vendor's type) |
| `lead_days` | INTEGER | Days from placing an order to delivery. The Inventory agent uses it to estimate restock dates |

| id | name | specialty | lead_days | Restocks |
|---|---|---|---|---|
| 1 | Bulldog Print Co | apparel reprint | 5 | Hoodies, tees, caps |
| 2 | Elm City Gifts | mugs and small goods | 3 | Crest Mug |
| 3 | QuickShip CT | local courier | 1 | Delivery, not stock |

The "Restocks" column is inferred from `specialty`. The database has no table linking vendors to SKUs.

## 8. invoices

```sql
CREATE TABLE invoices (
    id          INTEGER PRIMARY KEY,
    vendor_id   INTEGER NOT NULL,
    amount      REAL    NOT NULL,
    due_date    TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    description TEXT,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id)
);
```

| Field | Type | Meaning |
|---|---|---|
| `id` | INTEGER, primary key | Invoice number. Referenced by `payments.ref_id` (when `kind` = invoice) and `tickets.invoice_id` |
| `vendor_id` | INTEGER, FK → `vendors.id` | The vendor who sent the invoice |
| `amount` | REAL (USD) | Total owed |
| `due_date` | TEXT `YYYY-MM-DD` | Payment deadline |
| `status` | TEXT | Only `open` appears so far |
| `description` | TEXT, optional | What the invoice is for. This is the only place a product is mentioned (in free text) |

| id | vendor | amount | due_date | status | description | vs. business date |
|---|---|---|---|---|---|---|
| 501 | 1 — Bulldog Print Co | $840.00 | 2026-08-28 | open | Rush reprint CC-TEE-WHITE S | **3 days overdue** |

Invoice 501 is linked to ticket 101 (`tickets.invoice_id = 501`). That customer's size-S tee is out of stock and waiting on this reprint.

## 9. payments

```sql
CREATE TABLE payments (
    id          INTEGER PRIMARY KEY,
    kind        TEXT    NOT NULL,
    ref_id      INTEGER,
    amount      REAL    NOT NULL,
    account     TEXT    NOT NULL,
    paid_at     TEXT    NOT NULL,
    approved_by TEXT    NOT NULL
);
```

| Field | Type | Meaning |
|---|---|---|
| `id` | INTEGER, primary key | Payment ID |
| `kind` | TEXT | What is being paid. Decides which table `ref_id` points to: `invoice` → `invoices.id`, `rent` → `leases.id` |
| `ref_id` | INTEGER, optional | ID of the invoice or lease being paid. **Not a declared foreign key**, because it can point to different tables |
| `amount` | REAL (USD) | Amount paid |
| `account` | TEXT | Account the money came from. Matches `cash_accounts.name` |
| `paid_at` | TEXT | When the payment was made |
| `approved_by` | TEXT, **required** | The human who approved it on the dashboard |

**Empty today.** No payment has been made yet, so the database has no examples of `kind` values. This harness fixes them as `invoice` and `rent`. A payment is one step that must also:
1. reduce `cash_accounts.balance` by `amount`;
2. for `kind = invoice`: set `invoices.status` to `paid` once payments cover the full amount;
3. for `kind = rent`: move `leases.next_due` forward one month.

## 10. cash_accounts

```sql
CREATE TABLE cash_accounts (
    name    TEXT PRIMARY KEY,
    balance REAL NOT NULL,
    date    TEXT NOT NULL
);
```

| Field | Type | Meaning |
|---|---|---|
| `name` | TEXT, primary key | Account name. Matches `payments.account` |
| `balance` | REAL (USD) | Money available |
| `date` | TEXT `YYYY-MM-DD` | When the balance was last updated |

| name | balance | date |
|---|---|---|
| checking | $3,400.00 | 2026-08-31 |

**Cash check before any payment:**

| Item | Amount | Due |
|---|---|---|
| Checking balance | **$3,400** | as of 2026-08-31 |
| Invoice 501 (Bulldog Print Co) | −$840 | 2026-08-28 (overdue) |
| Lease 1 rent | −$2,400 | 2026-09-02 |
| **Left if both are paid** | **$160** | |

---

# Facilities agent tables

## 11. leases

```sql
CREATE TABLE leases (
    id           INTEGER PRIMARY KEY,
    space_name   TEXT NOT NULL,
    landlord     TEXT NOT NULL,
    monthly_rent REAL NOT NULL,
    next_due     TEXT NOT NULL,
    notes        TEXT
);
```

| Field | Type | Meaning |
|---|---|---|
| `id` | INTEGER, primary key | Lease ID. Referenced by `tickets.lease_id` and by `payments.ref_id` when `kind = rent` |
| `space_name` | TEXT | The rented space |
| `landlord` | TEXT | Who is paid. Matches `tickets.requester` on rent notices (text match) |
| `monthly_rent` | REAL (USD) | Rent per month |
| `next_due` | TEXT `YYYY-MM-DD` | Next rent date |
| `notes` | TEXT, optional | Lease notes (empty today) |

| id | space_name | landlord | monthly_rent | next_due | vs. business date |
|---|---|---|---|---|---|
| 1 | Chapel Street shop | Elm City Properties | $2,400.00 | 2026-09-02 | **due in 2 days** |

The Facilities agent checks that a rent notice's requester and amount match the lease before passing it to Accounting. This guards against fake rent requests.

---

# Customer service agent

## 12. Customer service agent

Customer service owns no table. It is the voice to customers and reads other agents' data through MCP tools:

| Needs | Reads | Example |
|---|---|---|
| Is the item in stock, and where? | `inventory` (sku, size, qty, location) | Tee S: 0 left |
| What does it cost? | `pricing.list_price` | Tee $28, hoodie $58 |
| When will it be back? | `tickets.invoice_id` → `invoices` → `vendors.lead_days` (from Accounting) | Reprint about 5 days after invoice 501 is paid |
| What did the customer ask? | `tickets` (requester, subject, notes, qty) | "Needs a tee in size S." |

It drafts replies and proposes ticket updates. The Boss agent shows them on the dashboard and records the outcome on the ticket. It never promises a discount or a delivery date that Accounting or Inventory hasn't confirmed.

---

## 13. How the tables link (common columns)

| Common column | Join | Declared? | Used by |
|---|---|---|---|
| `vendors.id` = `invoices.vendor_id` | Invoice → who it's from | **FK** | Accounting |
| `invoices.id` = `tickets.invoice_id` | Ticket → the bill it depends on | **FK** | Boss, Customer service |
| `leases.id` = `tickets.lease_id` | Ticket → the lease it's about | **FK** | Boss, Facilities |
| `invoices.id` = `payments.ref_id` when `kind = invoice` | Payment → invoice it settles | implicit | Accounting |
| `leases.id` = `payments.ref_id` when `kind = rent` | Payment → lease it pays | implicit | Accounting, Facilities |
| `cash_accounts.name` = `payments.account` | Payment → account it came from | implicit | Accounting |
| `inventory.sku` = `pricing.sku` | Stock → price and cost | implicit | Inventory, Accounting, Customer service |
| `tickets (sku, size)` = `inventory (sku, size)` | Ticket → the exact stock row | implicit | Inventory, Customer service |
| `invoices.description` mentions a sku + size | Invoice → product | free text only | Inventory (restock), Accounting |
| `tickets.requester` = `leases.landlord` | Rent notice → landlord | text match | Facilities |
| `desk.date_today` | "Today" for every date check | — | All |

- **Foreign keys aren't enforced.** SQLite has foreign-key checks off by default (`PRAGMA foreign_keys = 0`). Every connection must run `PRAGMA foreign_keys = ON`.
- **Implicit links are checked only by the MCP tools.** For example, a payment tool must check `kind`, then confirm the invoice or lease exists, before writing.

**Example join:** each invoice with its vendor and how much is still owed:
```sql
SELECT i.id, v.name AS vendor, v.specialty, i.amount, i.due_date, i.status,
       i.amount - COALESCE(SUM(p.amount), 0) AS still_owed,
       julianday((SELECT date_today FROM desk)) - julianday(i.due_date) AS days_overdue
FROM invoices i
JOIN vendors v       ON v.id = i.vendor_id
LEFT JOIN payments p ON p.ref_id = i.id AND p.kind = 'invoice'
GROUP BY i.id;
-- 501 | Bulldog Print Co | apparel reprint | 840.0 | 2026-08-28 | open | 840.0 | 3.0
```

**Example join:** each ticket with the stock and price it depends on:
```sql
SELECT t.id, t.type, t.requester, t.sku, t.size, t.qty AS wanted, i.qty AS on_hand,
       p.list_price, p.unit_cost
FROM tickets t
LEFT JOIN inventory i ON i.sku = t.sku AND i.size = t.size
LEFT JOIN pricing  p ON p.sku = t.sku
WHERE t.sku IS NOT NULL;
-- 101 | customer_order | Tauhid Zaman | CC-TEE-WHITE | S | 1  | 0 | 28.0 | 8.0
-- 103 | price_override | Yale AI Club | CC-HOOD-NAVY | M | 20 | 8 | 58.0 | 22.0
```

---

## 14. Gaps the build must fill

The original data can't support everything the team needs. Status of each gap:

| Gap | Why the agents need it | Status |
|---|---|---|
| No approvals queue | The dashboard needs somewhere to hold "waiting for human" requests with approve/reject and who decided | **Filled (Problem 3):** `approvals` table, section 21 |
| No agent activity log | The human needs to watch which agent did what, when, and why | **Filled (Problem 3):** `activity_log` table, section 21 |
| No vendor-to-product link | Which vendor restocks which SKU was only implied by `specialty` | **Filled (Problem 3):** `vendor_products` table, section 21 |
| No purchase orders | Restocking needs a record of what was ordered, from whom, and when it arrives | **Filled (Problem 3):** `purchase_orders` table, section 21 |
| No messages between agents | The Boss agent hands work to the others and collects their answers. Those hand-offs should be recorded | Open: Boss agent / backend problem |
| Only `open` statuses exist | Tickets need more states, e.g. `in_progress`, `waiting_on_human`, `closed` | Partly filled: invoices now go to `paid`. Ticket statuses: Boss agent problem |
| No customers or orders table | Customer orders exist only as tickets | Open: customer service problem |

---

## 15. MCP server (Problem 3)

Every agent reaches the database through one FastMCP server in `mcp_server/`. No agent opens SQLite directly.

| File | What it does |
|---|---|
| `mcp_server/db.py` | The only code that opens the database. Opens `data/campus_customs_new.db` (or `CAMPUS_DB_PATH`), refuses the original `campus_customs.db`, never creates a missing file, turns on `PRAGMA foreign_keys`, opens read-only unless a write is requested, and rolls back on errors. Also creates the 4 operations tables (section 21) and holds shared helpers: business date, approval queue, activity log |
| `mcp_server/server.py` | The FastMCP server `campus-customs-ops` (v0.2.0). Registers the 16 tools, each tagged with its agent |
| `mcp_server/inventory.py` | Inventory agent tools (section 17) |
| `mcp_server/accounting.py` | Accounting agent tools (section 18) |
| `mcp_server/facilities.py` | Facilities agent tools (section 19) |
| `mcp_server/approvals.py` | The human's approve/reject step (section 20). **Not an MCP tool** |
| `mcp_server/__main__.py` | Startup: checks the 9 shop tables, creates the operations tables if missing, then serves |
| `tests/` | 31 tests, each run against a temporary copy of the database (`conftest.py`), so tests never change the working copy |

**Resources (read-only reference):**

| URI | Returns |
|---|---|
| `campus://database` | Database path, business date (`2026-08-31`), row counts for every table, table owners |
| `campus://schema` | The `CREATE TABLE` statement for every table |

**Run it** (from `hw 5/`):
```bash
.venv/bin/python -m mcp_server          # stdio, for agents
.venv/bin/python -m mcp_server --http   # HTTP at http://127.0.0.1:8765/mcp
.venv/bin/python -m pytest -q           # tests
```

---

## 16. How agents use the tools ("call tool")

MCP has two sides:

| Side | What it is | In our code |
|---|---|---|
| **Server declares tools** | Each tool is a named function with a description and typed inputs. The server reads the tables, does the maths and returns a structured result | `mcp.tool(inventory.check_stock, tags={"inventory"})` in `server.py` |
| **Agent calls a tool** | The agent sends `tools/call` with the tool name and arguments, and gets the result back | `await client.call_tool("check_stock", {"sku": "CC-TEE-WHITE", "size": "S"})` |

So the "call tool" idea is right: it's how every agent reads data and comes back with a result. The key design choice is **what the agent can call**. We give each agent **specific tools** (`check_stock`, `find_shortfalls`, ...) rather than one generic "call any table" or "run any SQL" tool, because:

1. **Safety.** A generic tool would let a model read or change any table, e.g. write a payment with no approver. Specific tools enforce the rules in code: one owner per table, human approval for money, rent always at the lease amount.
2. **Fewer mistakes.** The tool does the joins and maths (shortfalls, margins, days overdue) the same way every time, instead of the model writing SQL.
3. **Better tool choice.** The model picks tools by name and description. `find_restock_vendor` is easier to pick correctly than "query".
4. **Per-agent access.** Each tool is tagged `inventory`, `accounting`, `facilities` or `shared`, so each agent can be given only its own tools.

**Resources** (`campus://...`) complement tools for fixed reference data the agent just reads (schema, business date). **Tool annotations** tell the client which tools only read (`readOnlyHint`) and which change data.

**Every read tool's result includes `business_date`**, so agents always reason from 2026-08-31, not the real date.

**Errors come back as messages the agent can act on**, e.g. `Unknown SKU 'NOPE'. Known SKUs: CC-HAT-BLUE, ...` or `Bulldog Print Co doesn't supply CC-MUG-CREST. Suppliers: Elm City Gifts.`

### All 16 tools

| Agent | Read | Queue for human approval | Change data directly |
|---|---|---|---|
| Inventory | `check_stock`, `find_shortfalls`, `find_restock_vendor` | | `receive_purchase_order` |
| Accounting | `get_cash_position`, `list_invoices`, `check_margin` | `prepare_invoice_payment`, `prepare_purchase_order`, `request_discount_approval` | |
| Facilities | `list_leases`, `check_rent_due`, `verify_rent_notice` | `prepare_rent_payment` | `add_lease_note` |
| Shared | `list_approvals` | | |

Only two tools change shop data without approval: receiving goods into stock, and adding a lease note. Neither moves money.

---

## 17. Inventory agent tools

| Tool | Inputs | What it does | Tables |
|---|---|---|---|
| `check_stock` | `sku`, `size`?, `qty_needed`? | Stock for every size (or one size) with status `in_stock` / `low_stock` (≤ 3) / `out_of_stock`, location, list price, and stock already on its way. With `size` + `qty_needed`: `can_fill`, `short_by`, and other sizes in stock | inventory, pricing, purchase_orders, invoices |
| `find_shortfalls` | `low_stock_threshold` (default 3) | Every size that is out or low, and every open ticket wanting more than is on hand, each with incoming stock | inventory, tickets, purchase_orders, invoices |
| `find_restock_vendor` | `sku`, `size`?, `qty`? | Vendors that supply the SKU, fastest first, with expected arrival (business date + `lead_days`), unit cost and estimated total. Recommends the fastest | vendor_products, vendors, pricing |
| `receive_purchase_order` | `purchase_order_id` | When goods arrive: adds an **approved** order's units to stock and marks it `received`. Refuses unapproved orders | purchase_orders, inventory |

**"Incoming stock"** comes from two places: open purchase orders, and open vendor invoices whose description names the SKU and size. That's how the agent knows invoice 501 ("Rush reprint CC-TEE-WHITE S") is the restock for ticket 101.

**Results on today's data:**

| Call | Result |
|---|---|
| `find_shortfalls()` | Out: tee S, Crest Mug. Low: tee L (3), tee XL (2). Ticket 101 short 1, ticket 103 short 12 |
| `check_stock("CC-TEE-WHITE", "S", qty_needed=1)` | 0 on hand, short 1; M/L/XL in stock; incoming: invoice 501 reprint |
| `find_restock_vendor("CC-HOOD-NAVY", "M", qty=12)` | Bulldog Print Co, 5 days, arrives 2026-09-05, 12 × $22 = $264 |
| `find_restock_vendor("CC-MUG-CREST")` | Elm City Gifts, 3 days |

---

## 18. Accounting agent tools

| Tool | Inputs | What it does | Tables |
|---|---|---|---|
| `get_cash_position` | `within_days` (default 30) | Checking balance; open invoices (still owed, days overdue); rent due in the window; purchase orders committed; payments waiting for approval; balance after pending payments and after everything | cash_accounts, invoices, payments, leases, purchase_orders, approvals |
| `list_invoices` | `status` (default `open`; `None` = all), `vendor_id`? | Each invoice with vendor, still owed, days overdue / until due, linked tickets, and any pending payment approval | invoices, vendors, payments, tickets, approvals |
| `check_margin` | `sku`, `unit_price`, `qty` (default 1) | Discount %, margin per unit and %, revenue and margin totals, the lowest price that keeps the minimum margin, and a verdict: `list_price` / `discount_ok` / `below_min_margin` (< 30%) / `below_cost` | pricing |
| `prepare_invoice_payment` | `invoice_id`, `reason`, `amount`? | Queues a payment (default: full amount still owed) for approval. Checks the invoice is open, amount ≤ owed, and cash covers it. Warns if all pending payments together would overdraw | invoices, payments, cash_accounts → **approvals** |
| `prepare_purchase_order` | `vendor_id`, `sku`, `size`, `qty`, `reason`, `ticket_id`? | Drafts a purchase order at unit cost with an expected date, and queues it for approval. Refuses a vendor that doesn't supply the SKU, a size that doesn't exist, or qty outside 1–500 | vendor_products, vendors, pricing → **purchase_orders**, **approvals** |
| `request_discount_approval` | `ticket_id`, `unit_price`, `reason` | For an open `price_override` ticket: runs the margin check on the ticket's quantity and queues the discount. **Refuses any price below unit cost** | tickets, pricing, inventory → **approvals** |
| `list_approvals` (shared) | `status` (default `pending`) | The human's queue, newest first, plus total cash waiting on pending payments | approvals |

**Policy constants** (in `accounting.py`, easy to change): minimum margin `MIN_MARGIN_PCT = 30`, purchase order cap `MAX_PO_UNITS = 500`.

**Results on today's data:**

| Call | Result |
|---|---|
| `get_cash_position()` | $3,400 checking; owes $840 (invoice 501, 3 days overdue) + $2,400 rent → **$160 left** |
| `check_margin("CC-HOOD-NAVY", 48, qty=20)` | 17.2% off, 54.2% margin, $520 total margin → `discount_ok` |
| `check_margin("CC-HOOD-NAVY", 30)` | 26.7% margin → `below_min_margin` (lowest price at 30% margin is $31.43) |
| `check_margin("CC-HOOD-NAVY", 20)` | `below_cost` |

**Duplicates are blocked:** a second payment request for the same invoice or lease, or a second discount for the same ticket, returns the existing pending request instead of creating another.

---

## 19. Facilities agent tools

| Tool | Inputs | What it does | Tables |
|---|---|---|---|
| `list_leases` | — | Every lease: rent, next due, days until due, status (`ok` / `due_soon` ≤ 7 days / `overdue`), notes, last rent payment, pending rent approval | leases, payments, approvals |
| `check_rent_due` | `within_days` (default 7) | Rent due in the window or overdue, and whether checking covers it after payments already waiting | leases, cash_accounts, approvals |
| `verify_rent_notice` | `ticket_id` | Checks a `rent_notice` ticket: names a lease, **requester matches the lease's landlord**, rent is actually coming due, not already queued. Verdict `verified` / `needs_review` | tickets, leases, approvals |
| `prepare_rent_payment` | `lease_id`, `reason`, `ticket_id`? | Queues this period's rent for approval. **Amount is always the lease's `monthly_rent`**, never a figure from the notice. With a ticket, the notice must pass `verify_rent_notice` | leases, cash_accounts → **approvals** |
| `add_lease_note` | `lease_id`, `note` (≤ 500 chars) | Keeps a dated note on the lease (repairs, landlord contact, renewal terms). Notes are appended, never overwritten | **leases** |

**Results on today's data:**

| Call | Result |
|---|---|
| `list_leases()` | Chapel Street shop, $2,400, due 2026-09-02 → 2 days, `due_soon` |
| `verify_rent_notice(102)` | All 4 checks pass → `verified`, pay $2,400 |
| Same ticket with the requester changed to someone else | `needs_review`, and `prepare_rent_payment` refuses it |

---

## 20. Human approval (not an MCP tool)

`mcp_server/approvals.py` → `decide(approval_id, approve, decided_by, note)`. The FastAPI backend will call it when the human clicks Approve or Reject on the dashboard. It is **not** registered as an MCP tool, so no agent can approve its own request. It also refuses a `decided_by` that is an agent's name.

What approving does, in one transaction (all or nothing):

| Approval kind | Prepared by | On approve | On reject |
|---|---|---|---|
| `invoice_payment` | Accounting | Re-checks the invoice is still open and the cash is there. Inserts a `payments` row (`kind = invoice`, `approved_by` = human), lowers checking, sets the invoice to `paid` when fully covered | Nothing changes |
| `rent_payment` | Facilities | Inserts a `payments` row (`kind = rent`), lowers checking, moves `leases.next_due` forward one month (2026-09-02 → 2026-10-02) | Nothing changes |
| `purchase_order` | Accounting | Purchase order → `approved`. The Inventory agent can then receive it. No cash moves: the vendor bills later | Purchase order → `rejected` |
| `discount` | Accounting | Records the approved unit price for the ticket | Nothing changes |

Every request and every decision is written to `activity_log`.

**The flow for today's tickets:**
```
101 tee      Inventory: find_shortfalls → tee S short 1, invoice 501 is the reprint
             Accounting: prepare_invoice_payment(501) ──▶ approvals (pending, $840)
102 rent     Facilities: verify_rent_notice(102) → verified
             Facilities: prepare_rent_payment(1, ticket 102) ──▶ approvals (pending, $2,400)
103 hoodies  Inventory: check_stock(M, 20) → short 12; find_restock_vendor → Bulldog, $264, 09-05
             Accounting: check_margin → request_discount_approval(103, price) ──▶ approvals
             Accounting: prepare_purchase_order(1, HOOD M, 12) ──▶ purchase_orders + approvals
Human (dashboard): approve / reject each  ──▶ approvals.decide ──▶ payments, cash, invoices, leases
```

---

## 21. Operations tables added in Problem 3

Created by `ensure_ops_tables()` when the server starts (`CREATE TABLE IF NOT EXISTS`, so it's safe to run every time). The 9 shop tables keep their original columns.

| Table | Owner | Fields | Purpose |
|---|---|---|---|
| `vendor_products` | Accounting | `vendor_id` → vendors, `sku` → pricing | Which vendor restocks which product. Seeded from `specialty`: Bulldog Print Co → hoodie, tee, cap; Elm City Gifts → mug. QuickShip CT is a courier, so none |
| `purchase_orders` | Accounting | `id`, `vendor_id`, `sku`, `size` (→ inventory), `qty` (> 0), `unit_cost`, `total`, `expected_date`, `ticket_id`?, `status` (`pending_approval` / `approved` / `rejected` / `received`), `reason`, `created_at` | Restock orders |
| `approvals` | Human | `id`, `kind` (`invoice_payment` / `rent_payment` / `purchase_order` / `discount`), `ref_id`, `amount`, `summary`, `details` (JSON), `requested_by`, `status` (`pending` / `approved` / `rejected`), `decided_by`, `decision_note`, `decided_at`, `created_at` | The human's queue on the dashboard |
| `activity_log` | Boss | `id`, `agent`, `action`, `details` (JSON), `business_date`, `created_at` | Feed of everything agents and the human changed |

**Updated read/write matrix for the new tables** (adds to section 1):

| Table | Boss | Inventory | Accounting | Facilities | Customer service | Human |
|---|---|---|---|---|---|---|
| `vendor_products` | R | R | **W** | | | |
| `purchase_orders` | R | R, marks `received` | **W** (drafts) | | | approves / rejects |
| `approvals` | R | | adds requests | adds rent requests | | **decides** |
| `activity_log` | **R** | appends | appends | appends | | appends |

---

## 22. Connecting to Claude (Problem 4)

The server runs as **three MCP servers, one per agent**. Each is the same code started with `--agent <name>`, which shows only that agent's tools plus the shared `list_approvals` tool and the 2 resources. This lets each agent be tested on its own, and an agent can't call another agent's tools.

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

No API keys are in the file: the server makes no AI calls. Running without `--agent` still serves all 16 tools.

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

## 23. Agent team (Problem 5)

Five fully connected agents in `agent_team/` (full details in `agent_team/README.md`).
- **Model:** `gpt-6-luna` via Portkey, built on Pydantic AI.
- **Tools:** each agent reaches the data only through its own MCP server from `mcp.json`.
- **Delegation:** every agent also has `ask_agent(agent, task)`, so any agent can delegate to any other and get the answer back.

| Agent | Job | MCP server |
|---|---|---|
| Boss | Reads each ticket, routes it, makes the final call (`update_ticket` + `TicketOutcome`), reviews across tickets (`DayReview`) | `campus-boss` |
| Inventory | Stock by SKU/size, shortfalls, restock vendor | `campus-inventory` |
| Accounting | Cash, invoices, margins; queues payments / purchase orders / discounts | `campus-accounting` |
| Facilities | Leases, rent, rent-notice checks, rent payments for approval, lease notes | `campus-facilities` |
| Customer Service | One draft message per ticket for the human to send | `campus-customer-service` |

**New MCP tools (Problem 5):**
- Boss: `list_tickets`, `update_ticket`, `recent_activity`;
- Customer Service: `draft_customer_message`, `list_customer_messages`;
- shared: `get_ticket`, which returns a ticket with its invoice or lease, approvals, purchase orders, drafts, history and hand-offs.

**New operations tables:**

| Table | Owner | Fields |
|---|---|---|
| `ticket_updates` | Boss | `ticket_id`, `old_status`, `new_status`, `decision`, `agent`, `created_at` |
| `customer_messages` | Customer Service | `ticket_id`, `to_name`, `subject`, `body`, `status` (`draft` / `approved` / `sent` / `discarded`), `drafted_by` |
| `agent_messages` | Boss | `run_id`, `ticket_id`, `from_agent`, `to_agent`, `task`, `response`, `status` (`working` / `done` / `refused` / `error`), `depth` |

**Ticket statuses:** `open` → `in_progress` / `waiting_on_human` / `resolved`.

**Delegation guard rails:**
- no hand-off to yourself;
- no hand-off to an agent already waiting on you in the chain;
- chains at most 4 agents deep;
- 12 hand-offs per ticket.

**Run:**
```bash
.venv/bin/python -m agent_team   # all open tickets on a database copy -> outputs/agent_team_run.json
```

**Live result:** all three tickets ended `waiting_on_human` with 4 approvals queued:
- #1: rent $2,400;
- #2: invoice 501, $840;
- #3: 15% hoodie discount;
- #4: purchase order for 12 hoodies, $264.

The Boss's review said to approve rent first, warned that rent + invoice leave $160, and said to hold the purchase order (it would leave −$104).

**Agent prompts:** `backend/prompts/`, one complete file per agent:
- `boss.md`
- `inventory.md`
- `accounting.md`
- `facilities.md`
- `customer_service.md`

Each file starts with the agent's job as written in the assignment, then lists its tools and working steps, and ends with the shared team rules.

**Boss and Inventory (Problem 5, prompts 3–4):**
- **Boss:** the sole decision maker, and every ticket comes to it first. It reads and states what the ticket asks (`understanding`), then checks each agent's **capability** (its real tools) and **capacity** (current load, hand-offs left) in a team roster put into its instructions (`team_roster` refreshes it). It assigns each part with a reason (`assignments`) and makes the final call. Only the Boss has `update_ticket` and `team_roster`.
- **Inventory:** works out which vendor can restock, then asks **Accounting** to queue the purchase order for human approval, and reports the PO and approval ids back.

**Problem 5 additions:**
- **`create_ticket`:** shared, internal ticket types only: restock / vendor_followup / facilities_issue / cash_issue / other.
- **Automatic figure check:** plain code, in `mcp_server/fact_check.py`. A customer draft or Boss decision is refused if any $ amount, % or record id isn't backed by the database. Details: `mcp_server/README.md` section 18.

## Audit trail: `outputs/audit_trail.json` (append-only, never wiped)

Written by `agent_team/audit.py` **as the agents run**, one entry per step. It is shared by every run, by the CLI and, later, by the backend. It is never cleared.

| Entry `kind` | Recorded |
|---|---|
| `session_start` | Session id, database path (working copy or trial copy), business date, model per agent, each agent's MCP server, **sha256 of every prompt file** (which instructions were in force), each agent's tools, delegation limits |
| `start` / `run_end` | Each run (one ticket, or the Boss's review): the task (the full review prompt), then usage (model calls, tokens), hand-offs, time, error |
| `tool_call` / `tool_result` / `tool_error` | Agent, chain (who asked whom), tool, **full arguments**, **full result** or the exact error (e.g. a figure-check refusal) |
| `handoff` / `handoff_reply` / `handoff_refused` | From → to, the full task, the **full answer**, status, and why a hand-off was refused |
| `final` | The Boss's full `TicketOutcome` / `DayReview` |
| `session_end` | Normal end or the error that stopped it |

Every entry also has `seq`, `time` (local, ms), `session_id`, `run_id`, `ticket_id` and `depth`.

**Never wiped, tamper-evident:**
- **Appending:** each entry is inserted before the closing `]}` under an exclusive file lock. Earlier bytes are never rewritten, and the file is valid JSON after every write, one entry per line.
- **Hash chain:** `prev_hash` + `hash` = sha256(prev_hash + entry). Editing, deleting or reordering any entry breaks the chain.
- **Damage:** if the file is damaged, it is left untouched and the step fails ("no audit, no action").
- **Privacy:** emails and card-length numbers are masked.
- **Tests** use their own temporary trail.

**Check it:**
```bash
.venv/bin/python -m agent_team.audit verify   # "<n> entries, chain OK"
```

**First live entries:** 2 sessions, 5 runs and 232 entries (89 tool calls, 17 hand-offs + replies, 1 refused hand-off, 3 tool errors). Two of those errors are the figure check refusing a customer draft that quoted unapproved prices ($52.20, $46.40), followed by the corrected retry.

**Safety (Problem 5):** the confidentiality check on customer drafts, untrusted ticket text with injection flags, API key isolation, content seals on approvals and drafts, and a live red-team test. Full write-up: `outputs/safety.md`.

---

## 24. Agent Monitor page (Problem 6)

**`outputs/desk_tickets.html`**: double-click to open. It's one self-contained file with no server, no internet and nothing loaded from outside, in the Campus Customs colours (dark Yale blue).

| Tab | Shows |
|---|---|
| **Ticket 101 / 102 / 103** (one per open ticket) | The ticket and the requester's words (shown as untrusted text) and its facts (item, on hand, short, linked invoice or lease). The **playbook score** (required expected steps done). Run details (model, model calls, hand-offs, time, database, run id). The **Boss's final call** (understanding, decision, final status, what waits for the human). **Expected vs actual for the Boss and each agent**: the playbook's steps marked ✓ done / ✗ missed / ○ optional, next to what the agent actually did (tool calls, hand-offs, replies, refusals), plus a verdict (`on track`, `missed n of m`, `stayed out as expected`, `should have stayed out`, `helped (not in the playbook)`) and any extra actions. A **full timeline** of the run in order, with hand-offs indented |
| **Cash** | Empty placeholder, filled in later |
| **Reflection** | Empty placeholder, filled in later |

- **Actual:** the latest run per ticket, read from `outputs/audit_trail.json`.
- **Expected:** `agent_team/playbook.py` (plain code). It reads each ticket's own data and works out the ideal steps. Examples:
  - the tee order is short 1 and the reprint is held by unpaid invoice 501, so Accounting should queue that payment and **no** duplicate purchase order;
  - the hoodie request is 12 short, so Inventory → Accounting should queue a purchase order for 12, Accounting should check margins and queue a discount, and Customer Service should draft;
  - the rent notice should be verified and queued by Facilities, with Customer Service and Inventory staying out.
- **Rebuild:** `.venv/bin/python scripts/build_agent_monitor.py`. It also rebuilds automatically after every `python -m agent_team` run.
- **Safety:** all data is embedded as JSON with every `<` escaped and rendered with `textContent`, so a customer's note can't inject anything into the page (tested).

**First build:**
- 101: 8/8.
- 102: 6/6.
- 103: 12/13. Accounting didn't queue the discount, because the Boss chose to wait for the club's target price; the page shows it as a gap against the playbook.
- The Boss also raised a separate `cash_issue` ticket about the projected −$104, which is allowed.

**Expected section (Problem 6, prompt 2):** each ticket tab now opens with **Expected — what the Boss and the team should do**:

1. **The Boss's job:** read and understand every open ticket and decide the priority; call the agent who can solve this ticket first; hand out the rest in order (facts → requests for approval → customer draft); make the final call alone.
2. **Priority across open tickets**, from `playbook.priority_order`. Tiers:
   - **1** keeps the shop open (rent, facilities, cash risk);
   - **2** a customer or stock is waiting;
   - **3** a sales opportunity with no deadline.

   Ties go to the nearest due date. Today: **#1 102 rent → #2 101 tee → #3 103 hoodies**. This is compared with the first recommendation in the Boss's latest cross-ticket review (✓ it put rent first).
3. **Who should be called first** on this ticket, with the reason, against the Boss's actual first hand-off. Customer order and discount request → Inventory; rent notice → Facilities.
4. **The expected flow in order**, each step marked ✓ done / ⚠ done out of order / ✗ missed / ○ optional. Order is checked against the audit trail's sequence. Restock example (ticket 103), all ✓ and in order:
   1. Boss calls Inventory first;
   2. Inventory checks stock by SKU and size;
   3. Inventory finds the 12 short and the vendor;
   4. Inventory sends the purchase-order details to Accounting;
   5. Accounting creates the purchase order for human approval.

---

## 25. Backend (Problem 7)

FastAPI app in `backend/main.py`. Full route list and the frontend loop are in `backend/README.md`.

Run it:
```bash
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

| Need | Route |
|---|---|
| Is a ticket open? | `GET /api/tickets`, `GET /api/tickets/{id}/status` |
| Run the agents on a ticket | `POST /api/tickets/{id}/run` (202 + background job) → `GET /api/runs/{job_id}` |
| Recent agent events, what each agent said, which tools | `GET /api/events?after_seq=…` (refreshable), `GET /api/agents` |
| Human views, then approves a payment | `GET /api/approvals/{id}` → `POST /api/approvals/{id}/decision` with the viewed seal |
| Current checking balance | `GET /api/cash` |
| Reset to original values | `POST /api/reset` with `{"confirm": "RESET"}` (backs up first; audit trail kept) |

**Tests:** `tests/test_backend.py`.

**Live check:** ticket 102 run → approval viewed and approved → checking $1,000 → reset to $3,400, all through the API.

---

## 26. Frontend dashboard (Problem 8)

`frontend/`: React + Vite + TypeScript, "Campus Customs · Super Delivery Desk". Full details in `frontend/README.md`.

Run it with the backend on port 8000:
```bash
cd frontend && npm run dev   # http://127.0.0.1:5173
```

- **Layout:**
  - pending tickets in the Boss's priority order;
  - **Dispatch the team** with a live delivery route (Boss first, then each hand-off, tool and reply);
  - status banner (Resolved ✓ / waiting for approval);
  - a short summary per agent;
  - the approval desk (review → name → approve/reject with the viewed seal → new checking balance and resolved ticket);
  - customer draft, expected vs actual, reset, and the checking balance in the header.
- **Backend additions for it:**
  - deciding the last approval on a ticket the Boss left `waiting_on_human` marks it **resolved**;
  - `GET /api/priority`;
  - `GET /api/tickets/{id}/expected`;
  - runs from before a reset no longer count as a ticket's latest run.
- **Style:** Campus Customs colours (dark Yale blue, changed from black and pink on request), with an SVG super-courier mascot (no images) that idles, runs while agents work, and sparkles on delivery.
- **Live check:** ticket 102 dispatched → approved → checking $1,000 → resolved → reset to $3,400. 83 tests pass, and the build is clean.

**Frontend ↔ backend wiring (Problem 8, prompt 3):**
- **Direct calls:** the dashboard (`npm run dev` → http://localhost:5173, fixed port) calls the backend directly at **http://localhost:8000** (`API_BASE` in `frontend/src/api.ts`, override with `VITE_API_BASE`). The Vite proxy was removed.
- **CORS:** the backend allows the Vite page origins `http://localhost:5173` and `http://127.0.0.1:5173` (override with `FRONTEND_ORIGINS`) for GET and POST with `Content-Type`.
- **Checked live:** every call went to `localhost:8000` with 200s, and the browser's preflight for a cross-origin POST got `OPTIONS 200` before the POST. An outside origin is refused (tested).

**Warehouse redesign (Problem 8, prompt 4):** the dashboard is now the Campus Customs warehouse (design in `outputs/design.md`).
- **Layout:** the Boss in a 2nd-floor glass office watching and assigning (bubble, work-order slips, watch lines); four ground-floor departments with role avatars (`frontend/src/components/Avatars.tsx`).
- **Each department shows:** its status lamp, work order, what it's doing, how (tools), what it said, and its playbook verdict, all derived live from the audit events (`Warehouse.tsx`).
- **Ticket rack:** split into **Unresolved** / **Resolved ✓**.
- **Celebration mode:** dancing and jumping employees, confetti and a banner when a ticket is resolved. "Reduce motion" is respected.
- **Unchanged:** selection, dispatch, approvals and reset work as before. Checked live: ticket 102 → approve → $1,000 → celebration → reset.

---

## 27. Board run and desk tickets (Problem 9)

- **`scripts/run_board.py`:** resets, then runs every ticket in priority order through the backend, decides approvals as the human, closes tickets that have nothing to approve, and repeats until the board is clear. Result: all 3 resolved, checking $3,400 → $1,000 → $160. Report: `outputs/board_run.md`.
- **`POST /api/tickets/{id}/resolve`:** a human closes a ticket that has nothing to approve. Refused while any approval is pending. Also available on the dashboard's signing desk.
- **`outputs/desk_tickets.html`** (Problem 6's page, renamed): each ticket tab has Expected (always from the original data) and **Actual — what happened in this run** (agents who worked, delegations in order, tools used, the human's decisions).

