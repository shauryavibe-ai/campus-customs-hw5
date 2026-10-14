# AI Prompts Log — HW 5

Every prompt given to the AI assistant for this assignment, recorded verbatim and in order, with a short note on what the assistant did in response.

---

# Setup

## Prompt 1
> Today we are doing hw 5

**Response:** Found the `hw 5/` folder empty. Asked for the assignment instructions and whether to keep a prompt log like HW 4.

## Prompt 2
> Create a AI_prompts.md file in the folder to start with which will record everything we talk about

**Response:** Created this file (`hw 5/AI_prompts.md`) to log every prompt verbatim, with a short summary of each response.

## Prompt 3
> first create a virtual safe environment

**Response:** Created a Python 3.14 virtual environment at `hw 5/.venv` (the root `.gitignore` already ignores it). Upgraded pip, installed `python-dotenv` and `openai`, and recorded them in `hw 5/requirements.txt`.

## Prompt 4
> Unzip the folder

**Response:** Extracted `data (2).zip` into `hw 5/data/`. It holds one SQLite database, `campus_customs.db`, with 9 tables: desk (1 row), inventory (10), pricing (4), vendors (3), leases (1), cash_accounts (1), payments (0), invoices (1), tickets (3). This is a different dataset from the HW 4 database. The original zip was left in place.

---

# Problem 2

## Prompt 1
> label everything from now in problem 2, open the campus customs database and look through every table and its fields in data/campus_customs.db. Copy everything original file to data/campus_customs_new.db

**Response:** Copied `data/campus_customs.db` to `data/campus_customs_new.db` (byte-identical, original untouched). Reviewed the schema and every row of all 9 tables. The data is a store back office as of 2026-08-31 with $3,400 in checking and three open tickets: a customer wants an out-of-stock size-S tee (linked to an overdue $840 reprint invoice), $2,400 rent is due 2026-09-02, and the Yale AI Club wants 20 size-M hoodies at a bulk discount when only 8 are in stock. The `payments` table is empty and requires an `approved_by` for each payment.

## Prompt 2
> We are building a campus customs multi-agent operations agentic team that runs everything in the shop like customer orders, unpaid bills, discount requests. All these will talk to each other over a MCP serve, FastAPI backend with a multi-agent team and a react dashboard so a human can watch the agent at work and approve requests. I will give you all the instructions

**Response:** Acknowledged the overall goal: a multi-agent operations team for Campus Customs. The agents share an MCP server, a FastAPI backend runs the agent team, and a React dashboard lets a human watch the agents and approve requests. No code written yet; waiting for step-by-step instructions.

## Prompt 3
> Now the data you went through, study that well to see how they link to other tables

**Response:** Traced every link between the tables and wrote it up in `hw 5/outputs/data_relationships.md`, with a relationship diagram and each ticket followed through the tables. Only 3 foreign keys are declared (invoices→vendors, tickets→invoices, tickets→leases), and SQLite isn't enforcing them. The rest are implicit: SKU links between inventory, pricing, and tickets; `payments.ref_id` pointing to either an invoice or a lease depending on `kind`; `payments.account` matching a cash account by name; and `desk.date_today` acting as the shared clock. Paying the rent and the overdue invoice would leave $160 of the $3,400 in checking. Also listed gaps, such as no orders or customers table and no payment history.

## Prompt 4
> Start an output/harness.md. We will be dealing with vendors table because it has vendor name, type, invoice table bcoz it will have details of the invoice and the payments table as well to find details of payments. I choose all these because they will be linked to each other and have common columns that may be used later

**Response:** Started `hw 5/outputs/harness.md` (in the existing `outputs/` folder, as in HW 4), scoped to the three tables chosen: `vendors`, `invoices` and `payments`. For each table it gives the schema, what each field means and the current rows. A linking section explains the shared columns: `vendors.id` = `invoices.vendor_id` (declared foreign key), `invoices.id` = `payments.ref_id` when `kind` is invoice (implicit), and `invoices.amount` compared with the sum of `payments.amount` to get the amount still owed. Includes a tested SQL join showing invoice 501 is $840 still owed and 3 days overdue.

## Prompt 5
> Build all tables from the data basis the fact that we will build agents that work for tickets in inventory, acocunting, facilities, customer service and at the top of all will be a boss agent running each agent and be the main interface

**Response:** Expanded `hw 5/outputs/harness.md` to cover all 9 tables, grouped by the agent that owns each one. Boss: `desk`, `tickets`. Inventory: `inventory`, `pricing`. Accounting: `vendors`, `invoices`, `payments`, `cash_accounts`. Facilities: `leases`. Customer service owns no table and reads stock, prices and tickets. Added:
- a team diagram;
- a read/write matrix (one owner writes each table; payments and discounts need human approval);
- ticket routing by `type`, with lead and helper agents for tickets 101–103;
- each table's schema, field meanings and current data, with margins and stock value worked out;
- a full table of shared columns, with a second tested join;
- a list of gaps the build must fill (approvals queue, agent activity log, agent-to-agent messages, more status values).

No changes were made to the database.

---

# Problem 3

## Prompt 1
> Problem 3 Lets build a mcp server mcp_server/ using FastMCP. It will read data/campus_customs_new.db and then in subsequent problems it we will use tools in this server and connect to database but that is later

**Response:** Built the FastMCP server in `hw 5/mcp_server/` (FastMCP 4.1, installed in `.venv` and added to `requirements.txt`).
- `db.py` is the only code that opens the database. It opens `data/campus_customs_new.db`, refuses the original database, never creates a missing file, turns on foreign-key checks, and is read-only unless a write is requested.
- `server.py` defines the `campus-customs-ops` server with two read-only resources: `campus://database` (business date, row counts, table owners) and `campus://schema` (every `CREATE TABLE` statement). No tools yet; they come in later problems.
- `__main__.py` checks all 9 tables exist, then serves over stdio, or over HTTP on port 8765 with `--http`.

Added 6 tests in `tests/test_mcp_server.py`, and all pass. Also started the server as a separate process and read the database over stdio. The database file is unchanged (same checksum as the original). Documented the server in section 15 of `outputs/harness.md`.

## Prompt 2
> Now, in the MCP, in output/harness.md, we will be adding tools for the inventory agent. This has to check the stock by SKU and size, spot shortfalls, and figure out which vendor can restock. This will be using a combination of the call tool so that it can check the data from the tables that we have stored, and then it will give the result. If there is some other tool that needs to be in this, also add that. I have chosen the call tool within it so that it can call the table, read the data, and come back. If I am wrong, do suggest the other or the right tool for it.
>
> Then comes the accounting tool, which understands the cash and invoices, checks the margins, and prepares payments. Purchase orders for human approval here use use a tool to check the margins and create payments or purchase orders that will later be approved by a human. Then we'll create a facilities tool within which it will handle all the leases and rent and everything that needs to be there, so it can read everything, keep everything there, and store data in it. If I'm missing anything, also ensure that things are included in each of the tools from inventory, accounting, and facilities perspectives.

**Response:** Added 16 MCP tools to `mcp_server/`, each tagged with its agent, and documented them in `outputs/harness.md` sections 14–21.
- **On the "call tool":** it's the right mechanism. Agents use MCP `tools/call` to run a tool and get the result back. The suggested refinement was to give each agent specific named tools (e.g. `check_stock`) rather than one generic "call any table" tool, so the rules (table ownership, human approval for money, no SQL written by the model) are enforced in code. Explained in harness section 16.
- **Inventory** (`inventory.py`): `check_stock` (by SKU and size, with can-fill and short-by for a quantity), `find_shortfalls` (out/low stock plus tickets wanting more than is on hand), `find_restock_vendor` (supplier, arrival date, cost), and the added `receive_purchase_order`.
- **Accounting** (`accounting.py`): `get_cash_position`, `list_invoices`, `check_margin`, `prepare_invoice_payment`, `prepare_purchase_order`, `request_discount_approval`, and the shared `list_approvals`. The prepare/request tools only queue requests; prices below cost are refused.
- **Facilities** (`facilities.py`): `list_leases`, `check_rent_due`, `verify_rent_notice` (requester must be the landlord), `prepare_rent_payment` (always the lease's own rent), and `add_lease_note` (stores dated notes on the lease).
- **Human approval** (`approvals.py` → `decide`): deliberately not an MCP tool, so agents can't approve their own requests. Approving writes the payment, lowers cash, marks the invoice paid or moves the rent date forward, all in one transaction.
- **New operations tables** (created on server start): `vendor_products`, `purchase_orders`, `approvals`, `activity_log`.

27 tests pass, run on a temporary copy of the database. Also started the real server over stdio and called the tools. Shop data in `campus_customs_new.db` is unchanged apart from the 4 new empty or seeded tables, and the original database is untouched.

## Prompt 3
> pUt a mcp_server/readme.md file as well with all details of these

**Response:** Wrote `hw 5/mcp_server/README.md`, a full reference for the MCP server. It covers:
- quick start, the file map, and database rules and configuration (`CAMPUS_DB_PATH`, business date, policy constants);
- how agents call tools, why the tools are specific rather than generic, annotations, errors, and a tested Python client snippet;
- both resources, and every one of the 16 tools by agent, with inputs, outputs, refusal rules and results on today's data;
- the human-only `approvals.decide` step, the 4 operations tables, a safety-rules table, and a worked example walking tickets 101–103 through the tools;
- the test suite (27 passing) and a checklist for adding a new tool.

---

# Problem 4

## Prompt 1
> Now for problem 4, add these MCP servers to claude so that you can call the tools and we can test if each agent is working. Save the connection JSON text in mcp.json at the project root.

**Response:** Set the server up as three MCP servers, one per agent: `campus-inventory`, `campus-accounting` and `campus-facilities`.
- Added a `--agent` option to `python -m mcp_server` so each server shows only that agent's tools, plus the shared `list_approvals` and the 2 resources.
- Saved the connection JSON in `hw 5/mcp.json`. It uses absolute paths to the `.venv` Python, `-m mcp_server --agent <name>`, and a `PYTHONPATH` env, with no keys. The same JSON is in `AI Foundations/.mcp.json`, which is where Claude Code loads project MCP servers from.
- Added `tests/test_mcp_json.py`, which launches each server exactly as written in `mcp.json` from an unrelated folder and checks its tool list and a tool call. That test caught two read tools missing `business_date`, which are now fixed. 31 tests pass.
- Documented in `mcp_server/README.md` section 16 and `outputs/harness.md` section 22.

The tools only become callable in Claude after a new session starts in `AI Foundations/` and the user approves the three servers, so live testing through Claude is the next step.

## Prompt 2
> Test the inventory tool to find shortfalls and save the output to outputs/mcp_smoke.json

**Response:** The `campus-*` tools hadn't loaded into the current Claude session yet, because it started before `.mcp.json` existed. So the test was run the same way Claude would connect.
- Wrote `scripts/mcp_smoke.py`. It starts the `campus-inventory` server from `mcp.json` over stdio, lists its tools (the 4 inventory tools plus `list_approvals`), calls `find_shortfalls`, checks the result against the expected shortfalls, and saves everything to `outputs/mcp_smoke.json`.
- **PASS** in about 0.8 s. Out of stock: tee S and Crest Mug. Low stock: tee L (3) and tee XL (2). Ticket 101 is short 1 and ticket 103 is short 12. Tee S shows the invoice-501 reprint as incoming stock.
- The tool is read-only; the database checksum was the same before and after.

## Prompt 3
> Match if this matches the data in our campus customs database

**Response:** Yes, it matches. Compared `outputs/mcp_smoke.json` with `data/campus_customs_new.db`, queried directly with SQL rather than through the MCP server code. All 36 fields matched with 0 mismatches. Saved the comparison to `outputs/mcp_smoke_db_match.json`.

Fields checked:
- the business date;
- every inventory row with qty ≤ 3: name, qty, status, and that no rows are missing or extra;
- every open ticket short of stock: type, requester, SKU, size, wanted, on hand, short by;
- the incoming reprint for tee S against invoice 501 and its vendor;
- no other incoming stock (0 purchase orders exist).

Also confirmed the 9 shop tables in the working database are still identical to the original.

## Prompt 4
> now test campus accounting to prepare purchase orders and save the output in the same as before. also check if it matches.

**Response:** Tested `campus-accounting` → `prepare_purchase_order` over MCP and checked it against the database. **PASS**, and all 31 database fields matched.
- **Setup:** `scripts/mcp_smoke.py` now runs several tests. Both `outputs/mcp_smoke.json` and `outputs/mcp_smoke_db_match.json` hold every test keyed by name: `inventory-shortfalls` (rerun, still 36/36) and `accounting-purchase-order`.
- **Temporary copy:** `prepare_purchase_order` writes data, so the accounting server ran on a temporary copy of the working database. The working database checksum was unchanged, with 0 purchase orders and 0 approvals.
- **The calls:**
  - `prepare_purchase_order` for ticket 103: vendor 1 (Bulldog Print Co), CC-HOOD-NAVY size M, qty 12 → PO #1 at $22 × 12 = $264, arriving 2026-09-05, status `pending_approval`, plus approval #1 pending;
  - the same order sent to vendor 2 → refused ("Elm City Gifts doesn't supply CC-HOOD-NAVY");
  - `list_approvals` → only that request;
  - `get_cash_position` → checking still $3,400, with $264 counted as committed.
- **Database match:** the PO row and the approval row match the tool output field by field. The unit cost matches `pricing`, the arrival date is the business date plus the vendor's 5-day lead time, qty 12 = 20 wanted − 8 on hand, and the vendor really supplies the SKU. Exactly one activity-log entry was written. No payment was made, cash is unchanged, and hoodie M stock is still 8.

## Prompt 5
> And lastly check the campus facilities tool to verify rent payable

**Response:** Tested `campus-facilities` over MCP to verify that rent is payable, and checked it against the database. **PASS**, with all 22 database fields matching.
- **Read-only:** this test only reads, so it ran on the real working database, and the checksum was unchanged.
- **The calls:**
  - `check_rent_due` → lease 1 (Chapel Street shop), $2,400 due 2026-09-02; checking $3,400 can cover it, leaving $1,000;
  - `list_leases` → `due_soon`, 2 days away, no rent paid or pending yet;
  - `verify_rent_notice(102)` → **verified** on all 4 checks (lease linked, requester Elm City Properties is the landlord on the lease, rent coming due, not already queued), amount payable $2,400;
  - `verify_rent_notice(101)` → refused, because it's a customer order and not a rent notice.
- **Database match:** the lease row, days until due, the cash balance, the rent total, what's left after paying, ticket 102's lease, notes and requester against the landlord, and ticket 101's type all match the database.
- **Full run:** added the test as `facilities-rent-payable` in `scripts/mcp_smoke.py`, saved to the same two output files. All three smoke tests pass (36/36, 31/31, 22/22) and 31 unit tests pass. Added a smoke-test table to `mcp_server/README.md` and `outputs/harness.md`.

---

# Problem 5

## Prompt 1
> Now in problem 5, we have to build the agent team which
>
> has full connectivity - any agent may delegate tasks to any other agent for help. In this assignment you will build these agents:
> •⁠  ⁠Boss - reads each ticket, decides who should work on it, and makes final calls
> •⁠  ⁠Inventory - checks stock by SKU and size, spots shortfalls, and figures out which vendor can restock
> •⁠  ⁠Accounting - watches cash and invoices, checks margins, and prepares payments or purchase orders for human approval
> •⁠  ⁠Facilities - handles the shop space side (leases, rent, etc.)
> •⁠  ⁠Customer Service - drafts messages for customers

**Response:** Built the fully connected five-agent team in `hw 5/agent_team/`. It uses Pydantic AI on `gpt-6-luna` via Portkey, with the key from the root `.env`.
- **Agents and tools:**
  - Boss, Inventory, Accounting, Facilities and Customer Service each connect only to their own MCP server from `mcp.json`.
  - Added `campus-boss` and `campus-customer-service`, which brings the server to 22 tools.
  - New tools: `list_tickets`, `get_ticket`, `update_ticket`, `recent_activity`, `draft_customer_message`, `list_customer_messages`.
  - New tables: `ticket_updates`, `customer_messages`, `agent_messages`.
- **Full connectivity:** every agent has `ask_agent(agent, task)`, which runs any other agent and returns its answer. Tested on all 20 agent pairs.
- **Guard rails:** no hand-off to yourself; no asking back an agent already waiting on you; chains at most 4 deep; 12 hand-offs per ticket; usage caps.
- **Boss:** routes each ticket and must record its final call with `update_ticket` (an output check enforces it). It returns a `TicketOutcome`, then a `DayReview` across tickets.
- **Recording:** every tool call and hand-off is recorded live, and every hand-off is written to `agent_messages`.
- **Runner:** `python -m agent_team` runs all open tickets in parallel on a copy of the database and saves `outputs/agent_team_run.json`.
- **Tests:** 40 pass, including scripted end-to-end team tests with no API calls, and the 3 MCP smoke tests still pass.
- **Live runs:** two runs. The first showed ping-pong delegation (a duplicate customer draft), a restock costed for 20 instead of the 12 short, and repeated lease notes. Fixed with a loop guard, one draft per ticket, and prompt changes. The second run:
  - all 3 tickets ended `waiting_on_human`;
  - approvals queued: #1 rent $2,400, #2 invoice 501 $840, #3 15% hoodie discount ($49.30, 55.4% margin), #4 purchase order for 12 hoodies at $264;
  - customer drafts for tickets 101 and 103, which say "pending approval";
  - the Boss's review recommended approving rent first and then the invoice (leaving $160), approving the discount, and holding the purchase order (it would leave −$104).
- **Documentation:** `agent_team/README.md`, `outputs/harness.md` section 23, `mcp_server/README.md` section 17.

## Prompt 2
> Put the prompts for what each agent is doing as I told you in backend/prompts/ in one file per agent

**Response:** Created `hw 5/backend/prompts/` with one complete prompt file per agent: `boss.md`, `inventory.md`, `accounting.md`, `facilities.md`, `customer_service.md`.
- **Each file** starts with the agent's job in the assignment's own words (e.g. "Boss - reads each ticket, decides who should work on it, and makes final calls") and the full-connectivity rule. Then it lists the agent's tools and working steps, and ends with the team rules (facts only from tools, money needs a human, reply format). The team rules are repeated in every file so each one stands alone.
- **The team** now loads its instructions from `backend/prompts/<agent>.md`. The old `agent_team/prompts/` folder was removed, so the prompts live in one place.
- **Checks:** added a test that the folder holds exactly one file per agent with the right job description. 41 tests pass. A live run of ticket 102 with the new files worked: rent verified, payment queued, ticket set to `waiting_on_human`.
- **Docs:** updated the agent team README and the harness to point to the new location.

## Prompt 3
> For boss, it has to understand and read what each ticket is asking. Check who is having what capability, capacity to do and be the sole decision maker upon receiving a ticket.

## Prompt 4
> For inventory, as mentioned it also figures out which vendor can restock, it should communicate with acocunting agent to help create purchase orders for human approval.

**Response (Prompts 3 and 4):**
- **Boss as sole decision maker** (`backend/prompts/boss.md` rewritten, `agent_team/team.py`):
  - Every ticket goes to the Boss first. It reads and understands the ticket (who, what, quantities, linked records, deadline, what a good outcome is), checks who has the capability and capacity, gives each part to the right agent once with a complete task, and makes the final call alone.
  - Added a Boss-only `team_roster` with each agent's job, the tools it really has (read live from the MCP server), the tasks in progress across tickets, and hand-offs left. The roster is also put into the Boss's instructions at the start of every ticket, because in the first test run the Boss never called the tool.
  - `TicketOutcome` now includes `understanding` and `assignments` (agent, task, why).
  - A new team rule in all 5 prompt files says the Boss is the sole decision maker and specialists only recommend.
- **Inventory → Accounting for purchase orders:** Inventory finds the shortfall and the vendor, then asks Accounting with full details to queue the purchase order for human approval, and reports the PO and approval ids back. Accounting's and the Boss's prompts were updated to match. Also fixed a regression where Accounting asked others for the list price instead of using `check_margin`.
- **Tests:** 45 pass, including new ones: only the Boss gets `update_ticket` and `team_roster`; the roster shows capability and capacity; the roster is in the Boss's instructions; and a scripted Boss → Inventory → Accounting hand-off queues PO #1 for $264.
- **Live run** (`outputs/agent_runs/team_run_20261013T041017Z.json`):
  - Inventory → Accounting queued the $840 invoice payment (ticket 101) and PO #1 for 12 hoodies at $264 (ticket 103);
  - the discount was queued at $50;
  - the Boss caught and corrected a wrong figure in a customer draft;
  - the review said to approve rent first and hold the rest, since invoice + PO + rent would leave −$104.

## Prompt 5
> Are there any tools are agents are missing? be creative but mindful of tokens

**Response:** Reviewed the team for gaps; nothing built yet. The last live run used 71 model calls and about 269k input tokens (17 times the output), and 13 hand-offs, mostly to fetch facts. So the token savers matter as much as new features.
- **Missing tools that would change outcomes:**
  - a customer deposit request: a 50% deposit from the Yale AI Club ($500) would close the −$104 cash gap that made the Boss hold the purchase order;
  - a vendor message draft, to confirm the reprint quantity on invoice 501 and ask for payment terms, since no agent can contact vendors;
  - stock reservations, to hold the 8 hoodies for the club;
  - a cash forecast by date, as opposed to a single number;
  - a way for agents to open a ticket for problems they spot (e.g. the out-of-stock mug that has no ticket).
- **Token savers:** a shared read-only product quote tool (stock by size + price) so Customer Service stops delegating for facts; shorter tool results; a deterministic code check of draft numbers instead of the Boss re-checking drafts.
- **New agents:** recommended none. Each extra agent adds hand-offs and tokens, and the gaps are tools, not roles. The one candidate is a non-AI auditor (plain code) that checks figures in drafts and decisions against the database.

## Prompt 6
> Make the create ticket tool

## Prompt 7
> also make this change An automatic check of figures in drafts and decisions against the database, in plain code

**Response (Prompts 6 and 7):**
- **`create_ticket`** (`mcp_server/boss.py`), a shared tool so every agent has it:
  - lets an agent raise a problem it spotted that no ticket covers (e.g. the out-of-stock Crest Mug) as an `open` internal ticket for the Boss to route later;
  - internal types only: restock / vendor_followup / facilities_issue / cash_issue / other, each with default routing. Agents can't create customer orders, rent notices or price overrides, so no fake rent notice can be invented;
  - the agent's own MCP server records who raised the ticket (`CAMPUS_AGENT`), so an agent can't claim to be another;
  - checks that the SKU, invoice and lease exist, and returns an existing open ticket instead of creating a duplicate.
- **Automatic figure check** (`mcp_server/fact_check.py`, plain code, no AI), run inside `draft_customer_message` and `update_ticket`:
  - every $ amount, % and record id must be backed by the database: stored values plus simple derived ones (qty × price, discount per unit and in total, cash left after any combination of what's owed);
  - otherwise the draft or decision isn't saved, and the agent gets a list of the bad figures to fix;
  - it checks numbers, not the words around them.
- **Prompts:** all 5 updated (raise new problems with `create_ticket`; figures are checked automatically), and the Boss got routing for the internal types.
- **Tests:** 49 pass, including new tests for create_ticket, its duplicate guard and its type restrictions; fake prices, percentages and ids refused; and derived totals such as −$104 accepted. The 3 MCP smoke tests still pass.
- **Live run of ticket 103:** the draft and the decision passed the check, purchase order #1 for $264 was queued through Inventory → Accounting, and a tampered "20% off" was caught.

## Prompt 8
> FOr each agent and all the communication between them, append to output/audit_trail.json as they run to record enough so that in case there is a audit later. Do not wipe this away each time and keep this

**Response:** Added an append-only audit trail at `hw 5/outputs/audit_trail.json` (in the existing `outputs/` folder), written by `agent_team/audit.py` as the agents run.
- **What's recorded:**
  - every session: model, database, sha256 of each prompt file, each agent's tools, limits;
  - every run's start and end, with usage;
  - every tool call with full arguments, full result or exact error;
  - every hand-off between agents, with the full task, full answer and refusals;
  - every final Boss decision.

  Each entry has a timestamp, session, run, ticket and the chain of who asked whom.
- **Never wiped:** new entries are inserted before the closing `]}` under a file lock, so the file stays valid JSON and earlier entries are never rewritten.
- **Tamper-evident:** a sha256 hash chain, checked with `python -m agent_team.audit verify`. A damaged file is left untouched and blocks further writes. Emails and card numbers are masked.
- **Tests:** they use a temporary trail. 54 pass, including: nothing earlier changes across sessions, edits are detected, a damaged file is never overwritten, and masking works.
- **Live:** two live runs wrote 197 then 232 entries, and the chain verifies OK. The trail also shows the figure check refusing two customer drafts that quoted unapproved prices, and the agent's corrected retries.

## Prompt 9
> Add safety to our agents now. So data about customers is confidential, can never be leaked, my api key never goes public, agents dont start giving internal info to customers and anyone in chat. Ensure what needs to be said is public and rest stays inside our agents. They work in this loop and with data in the database. Money is handled carefully and messages have integrity.

**Response:** Added safety controls, mostly in plain code (no tokens, and they don't rely on the model), and summarized them in `outputs/safety.md`.
- **Public vs internal:**
  - the customer draft is the only public channel;
  - `mcp_server/safety.py` checks every draft before it is saved. It refuses other people's names (customers, vendors, landlord), internal terms (cost, margin, cash, invoice, rent, agents, tools, keys), internal ids or other tickets, any $ that isn't a public price for this customer, any % other than their own discount, and links, emails or phone numbers;
  - everything else (decisions, hand-offs, tool results, the audit trail) stays internal.
- **Untrusted input:** `get_ticket` marks requester text as untrusted and flags injection attempts.
- **API key:**
  - read from the root `.env` into the model module only, not exported to `os.environ`;
  - MCP servers get an environment stripped of keys, tokens and secrets;
  - the audit trail scrubs `.env` secret values and key-like strings;
  - a scan of all 67 files in `hw 5/` found the key in none, and `.env` is git-ignored.
- **Money and integrity:**
  - sha256 seals on approvals and customer drafts;
  - `approvals.decide` refuses anything changed after it was prepared;
  - `verify_customer_message` confirms a draft is unchanged before a human sends it;
  - the existing human-only approvals, cash re-checks and audit hash chain stay in place.
- **Prompts:** a "Confidentiality and safety" section in all 5 prompt files, plus a public/never list for Customer Service.
- **Tests:** 71 pass, including `tests/test_safety.py`.
- **Live red-team** (`scripts/safety_redteam.py` → `outputs/safety_redteam.json`): a customer ticket with an injection asking for the cash balance, supplier cost, margin, other customers and the API key. The team confirmed the order only, nothing internal reached the draft, and the confidentiality check rated it clean. The audit trail now has 257 entries and the chain verifies OK. Note: the test script's first leak search wrongly flagged "Bulldog" (it's also in the product name "Classic Bulldog Tee"). It was fixed to search for "Bulldog Print" and the same draft was re-scored without a new model run.

---

# Problem 6

## Prompt 1
> Problem 6: we want to build a page which can be double clicked with one tab per ticket to see what each agent is doing on an open ticket. This will have an expected section to map what boss and each of the agent team ideally must be doing vs what they are actually doing. Add an empty cash and refelction tab as well that we will fill up later

**Response:** Built `hw 5/outputs/agent_monitor.html`, a self-contained page you can double-click (no server, no internet) in the black-and-pink style.
- **Tabs:** one per open ticket (101, 102, 103), plus empty **Cash** and **Reflection** placeholders for later.
- **Each ticket tab:**
  - the ticket and its facts;
  - a playbook score;
  - run details;
  - the Boss's final call;
  - an **Expected vs actual** card for the Boss and each agent: the playbook's ✓/✗/○ steps next to what the agent really did (tool calls, hand-offs, replies, refusals), with a verdict and any extra actions;
  - a full timeline of the run.
- **Data sources:** "actual" comes from the latest run per ticket in the audit trail; "expected" comes from `agent_team/playbook.py`, plain code that works out the ideal steps from each ticket's own data.
- **Generation:** `scripts/build_agent_monitor.py` builds the page, and it rebuilds automatically after every `python -m agent_team` run. Data is embedded with every `<` escaped and rendered as text, so customer notes can't inject anything.
- **Checks:** checked in the browser (tabs, scores, placeholders, no console errors, no outside requests). 74 tests pass.
- **First build:** 101 scored 8/8, 102 scored 6/6, 103 scored 12/13. The miss: Accounting didn't queue the discount because the Boss waited for the club's target price.

## Prompt 2
> Now for the expected section, this should be mentioned on the page - the boss is expected to read and understand which is the priority ticket and what agent can solve this first. For example, in case of inventory restock request, the boss will first call the inventory agent to check the stock by SKU and size, identifies shortfalls and the vendor from which order can be placed. then the accounting agent receives communication from the inventory agent and creates purchase orders for approvals

**Response:** Added an **Expected** section at the top of every ticket tab in `outputs/agent_monitor.html`, driven by a rewritten `agent_team/playbook.py`:
- **The Boss's job:**
  - read and understand every open ticket and decide the priority;
  - call the agent who can solve this ticket first;
  - hand out the rest in order;
  - make the final call alone.
- **Priority across open tickets:** tiers are keeps-the-shop-open, then customer/stock waiting, then no-deadline sales, with the nearest due date breaking ties. Expected order: 102 rent, 101 tee, 103 hoodies. It is checked against the Boss's actual cross-ticket review, which put rent first ✓.
- **Who should be called first,** with the reason, vs the Boss's actual first hand-off.
- **The ordered expected flow,** with each step marked ✓ done, ⚠ out of order, ✗ missed or ○ optional. Order is checked against the audit trail. In the restock flow, Accounting's purchase order only counts as in order after Inventory has sent it the details.
- **Live data, ticket 103,** followed the example exactly: Boss → Inventory first → stock by SKU and size → 12 short and vendor → Inventory → Accounting → purchase order for approval, all ✓ in order. The only gap was the discount, which the Boss chose to hold.
- **Scores:** 101 8/8, 102 6/6, 103 12/13.
- **Tests:** 77 pass, including new tests for the restock flow's order, the priority ranking, and catching out-of-order steps. Checked in the browser.

---

# Problem 7

## Prompt 1
> problem 7 we build a backend for our family of agents. use fastapi and add routes that do the following indicative tasks: the tickets status if it is open or not, take a ticket id and run your agent on it. show recent agent events and what each agent said and which tools they used so they can refresh everytime we need them to do something new. ability to approve a payment after a human views and approves.  return the current checking balance from cash_accounts, reset to original values when we want to try a fresh run. Later we willl build the frontend which will use the built backend

**Response:** Built the FastAPI backend in `hw 5/backend/main.py`, with `backend/README.md` and 4 new tests (81 in total pass). The app starts the agent team once (5 MCP servers) and reuses it. Routes:
- **ticket status / open or not:** `GET /api/tickets`, `/api/tickets/{id}`, `/api/tickets/{id}/status`;
- **run the agents on a ticket:** `POST /api/tickets/{id}/run` returns 202 with a job id and runs in the background, then `GET /api/runs/{job_id}`. It refuses resolved tickets, duplicate runs, and more than 3 runs at once;
- **recent agent events, what each agent said and which tools it used:** `GET /api/events` (filter by ticket, run, agent or kind; refresh with `after_seq`) and `GET /api/agents`;
- **approve a payment after a human views it:** `GET /api/approvals/{id}` to view, then `POST /api/approvals/{id}/decision` with the human's name and the **seal of the version they viewed**. It refuses a changed approval, an agent's name, or a second decision, and is audited;
- **checking balance:** `GET /api/cash`, from `cash_accounts`, plus pending payments;
- **reset to original values:** `POST /api/reset` with `{"confirm": "RESET"}`. It backs up the working database to `data/backups/`, swaps in the original data atomically, refuses while agents are running, and keeps the audit trail;
- **extras for the frontend:** `/api/health` and `/api/messages` (drafts with a seal check).

Security: localhost only, and CORS only for the local frontend.

**Live check through the API** (`gpt-6-luna`, working database):
- ticket 102 ran in 43 s and queued rent approval #1 for $2,400;
- events and per-agent activity showed every tool and reply;
- a stale seal was refused (409);
- approving with the viewed seal (signed "API smoke test") took checking to $1,000;
- reset without confirmation was refused (422); with `RESET` it restored $3,400, 3 open tickets and 0 approvals, and the audit trail was kept (287 entries, chain OK).

No server errors.

## Prompt 2
> run this and see if it works uvicorn main:app --reload --port 8000

**Response:** Ran `uvicorn main:app --reload --port 8000` from `hw 5/backend/`, and it works.
- **Startup:** the reloader watched `backend/`, the app started, all 5 agent MCP servers came up, and startup completed. Port 8000 was briefly still held by the earlier test server, so this check ran on 8001.
- **Routes:** health, tickets (3 open), ticket status, cash ($3,400), approvals (0), events, agents and messages all returned 200 against the working database.
- **Findings:**
  - plain `uvicorn` isn't on the shell PATH; activate `hw 5/.venv` first or use `.venv/bin/uvicorn`;
  - the preview tool couldn't execute the script inside OneDrive, but a normal shell can;
  - opening `/` returned 404, so added a root route that redirects to `/docs`.
- **Reload:** saving that edit made `--reload` restart the app cleanly, which confirms reload works.
- **Tests:** backend tests pass (4/4). README updated with the command.

---

# Problem 8

## Prompt 1
> Now for problem 8, build the frontend dashboard in frontend/ with React + Vite + typescript. it should call the routes we just built before this. It should include everuthing we have talked about and have a place that lists all the current pending tickets. Let me choose a ticket to see the agent team start working on it, starting with boss agent and flowing with his decisions thereafter. it should show if a ticket is resolved when the run finishes. Also show a short summary fo what each agent did on that ticket and once we reach the last stage like a purchase or payment, let me approve it. show the checking balance after we have approved a payment. Make it look attractive like a super delivery worker in Yale campus customs putting all things together.

**Response:** Built the dashboard in `hw 5/frontend/`: React 19 + Vite 8 + TypeScript, "Campus Customs · Super Delivery Desk", in the black-and-pink style.
- **Mascot:** a Yale Campus Customs super-courier drawn in SVG (pink cape, "Y" cap, carrying TEE/HOODIE/MUG parcels). It idles, runs with speed lines while agents work, and sparkles when a ticket is delivered.
- **Pending tickets:** listed in the Boss's priority order with "Boss should call X first".
- **Running a ticket:** pick one and **Dispatch the team**. A live **delivery route** polls the backend every 1.5 s and shows the Boss picking up the ticket, then each hand-off the Boss decides, each tool call (parcel chips) and each reply (speech bubbles), then the Boss's final call.
- **Status:** a banner shows **Resolved ✓** / waiting for approval when the run finishes.
- **Agents:** one card per agent with a short summary of what it did.
- **Approval desk** (the last stage): review → your name → Approve/Reject, sending the seal of what you reviewed. It shows **the checking balance after approving** and whether the ticket is now resolved.
- **Also on the page:** the customer draft, expected vs actual (from Problem 6), audit status, cash, and Reset.
- **Backend additions:**
  - deciding the last approval on a ticket waiting for a human now marks it **resolved**;
  - new `GET /api/priority` and `GET /api/tickets/{id}/expected`;
  - runs from before a reset no longer show as a ticket's latest run (this bug was found in the browser).
- **Live check in the browser:** ticket 102 dispatched → route filled in live (24.7 s, 12 model calls) → reviewed $2,400 → approved as "UI smoke test" → checking $1,000, ticket resolved and moved to Delivered → reset back to $3,400.
- **Bugs fixed during the check:** a stale approval banner after reset, verdict chips showing before any run, and the wrong ticket selected after reset.
- **Checks:** 83 tests pass, type-check and build are clean, and there are no console errors.

## Prompt 2
> black and pink, I dont like. make it in campus custom colours

**Response:** Switched the dashboard from black and pink to the Campus Customs colours, the same palette as the HW 4 store:
- near-black navy `#0a1220` and navy panels;
- Yale Blue `#00356B` and `#286DC0` for the brand colour, buttons, ranks and the Boss badge;
- light-blue highlights `#6FA8FF` / `#A8C8FF`;
- serif (Georgia) headings;
- agent badges in a family of blues;
- pink kept only as a tiny accent.

Also:
- **Mascot:** recoloured with a Yale-blue cape, a navy cap with a white "Y", a white "CC" chest emblem and blue parcels. One small pink "fragile" sticker is the only pink.
- **Monitor page:** `outputs/agent_monitor.html` recoloured the same way so all HW 5 pages match.
- **AGENTS.md:** recorded the approved exception (2026-10-14, `hw 5/` dashboard and monitor page use the Campus Customs colours) so future sessions don't revert to black and pink.
- **Checks:** both pages checked in the browser, the build is clean, and the docs are updated.

## Prompt 3
> The frontend has to now talk to the backend at http://localhost:8000. On the backend, the vite page origin is allowed so it can call the routes. npm run dev

**Response:** The dashboard now talks to the backend directly at `http://localhost:8000`, and the backend allows the Vite page origin.
- **Frontend:**
  - `src/api.ts` calls `API_BASE` = `http://localhost:8000` (override with `VITE_API_BASE`, see `.env.example`);
  - the Vite `/api` proxy was removed;
  - Vite is pinned to `http://localhost:5173` with `strictPort`, so the origin never changes;
  - `Content-Type` is sent only on POSTs, so GETs need no preflight.
- **Backend:** CORS allows `http://localhost:5173` and `http://127.0.0.1:5173` (override with `FRONTEND_ORIGINS`) for GET and POST. A test checks preflights from both origins and that an outside origin is refused (5/5 backend tests pass).
- **Ran `npm run dev`:** Vite was ready at `http://localhost:5173` in 180 ms.
  - The page loaded its data from `http://localhost:8000/api/...` (health, tickets, priority, cash, approvals, ticket 102, expected), all 200.
  - A cross-origin POST went through its preflight (`OPTIONS 200`) and reached the backend.
  - Tickets and the $3,400 balance displayed with no errors.
- **Docs:** frontend, backend and harness docs updated.

## Prompt 4
> output/design.md I want the dashboard to look like an actual warehouse of campus customs with agents shown as departments in the warehouse and the boss sitting on top in the second floor of the warehouse watching on all and assigning tasks. Everything else like before (let me select a ticket, approval process, etc.) stays same. tickets need to show as resolved or unresolved. all agents show what they are working and how they are working. once all the work is done and a ticket is resolved, it must show as a celebration mode with all these small agent employees dancing and jumping. each agent should have a typical avatar related to its role. like an accountant is dressed in shirt pant tie, etc.

**Response:** Wrote `hw 5/outputs/design.md`, the warehouse design: layout, the Boss's 2nd-floor office, departments, avatars, live states, celebration and what stays the same. Then rebuilt the dashboard to match it.
- **The warehouse** (`Warehouse.tsx`):
  - a Yale-blue "CAMPUS CUSTOMS WAREHOUSE" building;
  - the **Boss in a glass office on the 2nd floor** at a desk with a monitor, watching everything. His bubble says what he's doing ("Assigning Inventory: …", "Watching Facilities work…", the final call). Watch lines run down to each department and light up, and a **work-order slip** drops in for every hand-off;
  - four ground-floor departments (Inventory Bay, Accounting Office, Facilities Workshop, Customer Service Desk) with role decor;
  - a loading dock where the courier mascot heads out on delivery.
- **What each department shows:** a status lamp (Idle / Working / Done / Blocked / Staying out with the reason), plus the work order, what it's **doing** (latest tool) and **how** (tool tally), what it said, and its playbook verdict, all derived live from the audit events.
- **Role avatars** (`Avatars.tsx`, SVG, no images):
  - Boss: navy suit, Yale-blue tie, clipboard;
  - Inventory: hard hat, hi-vis vest, scanner, box;
  - Accounting: white shirt, tie, trousers, glasses, pen, calculator;
  - Facilities: coveralls, cap, tool belt, wrench;
  - Customer Service: headset, cardigan, badge.

  Each has its own working animation.
- **Ticket rack:** split into **Unresolved** (with the reason) and **Resolved ✓**.
- **Celebration mode** on a resolved ticket: all employees dance and jump with staggered timing, with confetti, a banner and "Out for delivery!". "Reduce motion" is respected.
- **Everything else unchanged:** selection, dispatch, the signing desk and approvals, the checking balance, the draft, expected vs actual, reset, and the dispatch log.
- **Live check:** ticket 102 → Facilities working, then done → approved → $1,000, resolved, celebration → reset to $3,400. Type-check and build are clean, with no console errors.

---

# Problem 9

## Prompt 1
> Now problem 9, lets start clean from the database and lets run each ticket on the board until each is resolved

**Response (Prompt 1):** Reset to the original data, then ran every ticket on the board to resolved with `scripts/run_board.py`. It drives the running backend over HTTP, so the run played out live in the warehouse dashboard. Tickets were taken in the Boss's priority order, and approvals were decided as the human, signed "Claude (on Shaurya's instruction)". The policy: approve what the team queued if it passes its checks and cash covers it at that moment; otherwise reject with the reason.
- **Results:**
  - **102 rent:** 1 run, 77.5 s. Approval #1, rent $2,400, approved; checking $1,000; resolved.
  - **101 tee:** 1 run, 188.5 s. Inventory → Accounting queued invoice 501, approval #2 for $840, approved; checking $160; resolved.
  - **103 hoodies:** 1 run, 418 s, 30 model calls, 6 hand-offs. Discount approval #3 ($628.60 for 20, i.e. $31.43 each, at the 30% margin floor) and purchase order #4 (12 for $264) were both approved; resolved.
  - **Totals:** all 3 resolved in 3 runs; final checking $160.00; audit trail 516 entries, chain OK. Report in `outputs/board_run.md` / `.json`.
- **Added along the way:**
  - a human **close-ticket** route (`POST /api/tickets/{id}/resolve`, refused while any approval is pending) and a matching **Close ticket** form on the dashboard's signing desk, for tickets left waiting with nothing to approve;
  - the dashboard now **adopts runs started elsewhere** (like this script), so they animate live.
- **Caveats:**
  - the policy approved the 103 discount at the **margin floor** (about 46% off list). The Boss had asked Accounting for the largest permissible discount;
  - it also approved the purchase order, although the Boss recommended holding it, because approving an order doesn't move cash now. The vendor would bill later, against the $160 left.

## Prompt 2
> Now open output/desk_tickets.html from problem 6. On each ticket tab, fill the actual section from this run: which agents worked, what they delegated, which tools they used. Keep the expected section as-is like we had filled before

**Response (Prompt 2):** There was no `desk_tickets.html`; Problem 6's page was `outputs/agent_monitor.html`. Rebuilt it as **`outputs/desk_tickets.html`** (the builder now writes that name; references updated; old file removed). Each ticket tab now has a new **"Actual — what happened in this run"** section, built from every run of that ticket since the reset:
- **summary:** runs, model calls, hand-offs, time, the status the Boss left it in, and the status now;
- **which agents worked:** asked by, delegated to, tools used with counts, refused steps, plus who didn't work on it;
- **what they delegated, in order:** from → to, the task, the reply;
- **the human's decisions:** approvals and the close;
- **the Boss's final call.**

**Expected is unchanged:** it is now always computed on a temporary copy of the **original** data, so it stays as filled in Problem 6 even though this run paid invoice 501 and resolved everything (tested). Tabs now cover every ticket on the board, marked ✓ when resolved.

**Against Expected:** 101 scored 8/8, 102 6/6, 103 13/13 with one step out of order: the Boss sent Inventory and Accounting their tasks at the same moment, so the discount question came before the stock check.

Checked in the browser with no console errors; 8 monitor tests pass.

## Prompt 3
> For the cash tab in the same output, also show the flow from the starting checking balance to how cash changed hands when the ticket was resolved and for what reason (purchase, pay or something else), ending checking balance and ultimately tell me if they match to cash_accounts in the working database

**Response (Prompt 3):** Filled the **Cash** tab of `outputs/desk_tickets.html` (Reflection stays empty for later).
- **The answer, at the top:** starting balance **$3,400.00** (`cash_accounts` in the original data) → cash out **$3,240.00** → computed ending balance **$160.00**, vs `cash_accounts` in the working database **$160.00**. **They match ✓.** The payments table and the audit trail's balance after each decision agree too, and every payment links to an approval.
- **How cash changed hands,** in the order approvals were decided, with ticket, what happened, reason, cash out, running balance and the audit-trail check:
  1. Ticket 102 rent: **Pay**, −$2,400 to the landlord → $1,000;
  2. Ticket 101 tee: **Pay**, −$840 to Bulldog Print Co for invoice 501 → $160;
  3. Ticket 103: **Something else**, a discounted sale of 20 hoodies at $31.43 (no cash until the club pays) → $160;
  4. Ticket 103: **Purchase**, a purchase order for 12 hoodies at $264 (no cash until the vendor bills) → $160.
- **Not cash yet:** the $264 purchase order would take checking to **−$104** when billed; $628.60 of sales is still to be paid by the club; next rent is $2,400 due 2026-10-02.
- **Check:** a new test confirms the reconciliation catches a balance edited outside the system. 9 monitor tests pass; checked in the browser with no console errors.

---

# Problem 10

## Prompt 1
> now for problem 10

**Response:** Started the Problem 10 section and asked for the instructions.

## Prompt 2
> first show me the final output so I can see, run the ticket and see all the things from expected so that I can tell you the reflection

## Prompt 3
> show me what you are working on, with the link

**Response (Prompts 2–3):**
- **The re-run:**
  - saved the Problem 9 results as `outputs/*_problem9` copies;
  - opened the live warehouse dashboard (http://localhost:5173) in the preview pane;
  - re-ran the whole board from a clean reset with `scripts/run_board.py`, so you can watch it before writing the reflection.
- **Progress shared:** 102 resolved (rent $2,400 → checking $1,000); 101 resolved (invoice 501, $840 → $160); 103 in progress, with Inventory done (12 short, vendor found) and Accounting requesting the discount at the $31.43 floor again.
- **Dashboard bug fixed:** after following one ticket's run, a late-arriving load for the previous ticket could overwrite the screen, so ticket 101 showed ticket 102's work. Loads are now tokened so older answers are ignored. Adopting a run also loads that ticket's playbook, so departments that should stay out say so.

**Response (run finished):**
- **Board run:** all 3 tickets resolved in 3 runs.
  - 102: 58 s, 12 model calls, 1 hand-off; rent $2,400 approved.
  - 101: 3 hand-offs; invoice 501, $840, approved.
  - 103: 408 s, 27 model calls, 5 hand-offs; discount $628.60 at the $31.43 floor and a purchase order for 12 at $264 approved.
  - Checking $3,400 → $1,000 → $160; it matches `cash_accounts`.
- **Rebuilt** `outputs/desk_tickets.html` and showed the celebration in the live warehouse.
- **Fixed an inconsistency found in the dashboard:** its Expected section was computed from the current data (invoice 501 now paid), so ticket 101 wrongly showed "✗ create a purchase order". `agent_team/playbook.py` now judges Expected on the original data, opened read-only, for every ticket that existed then (the dashboard, `/api/priority` and the desk tickets page all agree). The builder no longer swaps environment variables. 86 tests pass.
- **Against Expected:** 101 8/8, 102 6/6, 103 13/13 with one step out of order (the Boss asked Accounting about the discount in parallel with Inventory's stock check).

## Prompt 4
> Now in the reflection mention that the agents actually performed really well. The expected plan was followed clearly and the tools I mentioned were used. This was partly also because we were continuously conversing and adding things on the fly across the different problems. Campus customs can face the following 3 problems that may be resolved by these agents: receipt of inventory and adding them to inventory as well as reconciling what payment needs to be made vs what was received, reconciling PO, GRN and Invoice, drafting messages to customers for back in stock items by conversing with inventory tool

**Response (Prompt 4):** Filled the **Reflection** tab of `outputs/desk_tickets.html` and saved the same content as `outputs/reflection.md`.
- **How the team did:** your verdict. The agents performed really well, the expected plan was followed clearly, and the designed tools were the ones used.
- **Evidence from the run** (generated automatically from the data):
  - 101 scored 8/8, 102 6/6, 103 13/13 (one step out of order);
  - the right agent was called first on all 3;
  - all 3 tickets resolved;
  - the tools each agent actually used, per agent;
  - cash $160 matches `cash_accounts`.
- **Why it worked:** building it in conversation, with the concrete additions made on the fly from Problems 3–9 (per-agent MCP servers, the Boss's roster and sole decision-making, Inventory → Accounting purchase orders, guard rails, the figure check, create_ticket, the audit trail, safety, the playbook, auto-resolve, close-ticket, cash reconciliation).
- **Next: three Campus Customs problems**, each with the agent flow, the tools we already have and the tools to add:
  1. Receiving inventory and paying only for what arrived (add `record_goods_receipt`, `payable_for_receipt`);
  2. Reconciling PO, GRN and invoice, a three-way match (add a goods-received table, a PO link on invoices, `three_way_match`);
  3. Back-in-stock messages, where Inventory tells Customer Service who is waiting (add `customers_waiting_for` and a trigger after receiving).

Checked in the browser with no console errors; fixed long tool chips spilling outside their cards.

## Prompt 5
> fix the last part you have mentioned

**Response:** Fixed the reflection so it's accurate about the one out-of-order step, checked against the audit trail.
- **Opening paragraph:** still says the agents performed really well and the plan was followed (right agent first on all tickets, 27 of 27 required steps done, the designed tools used). It no longer claims the work was handed out in order on every ticket.
- **New paragraph:** on ticket 103 the Boss sent Accounting its discount task at the same moment as Inventory's stock check (audit entries 589/590). Inventory's answer (8 of 20 in stock) went only to the Boss, so Accounting priced without it. The Boss had asked for "the lowest permissible unit price / largest discount", which is why the $31.43 margin-floor price (about 46% off) was queued.
- **The improvement it names:** the Boss should wait for the stock answer, pass it on, and ask for a modest discount.

Updated in the Reflection tab and `outputs/reflection.md`.

---

# Problem 11

## Prompt 1
> Problem 11: Now push the code a public github repository from my git details which you already have
