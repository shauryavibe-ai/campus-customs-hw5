# Campus Customs Agent Team

Five AI agents that run the Campus Customs back office: customer orders, unpaid bills, rent and discount requests. They are **fully connected**: any agent can delegate a task to any other agent and get the answer back. Each agent reaches the shop data only through its own MCP server (`../mcp_server/`). Anything involving money is only *queued* for a human to approve.

| Agent | Job | Its MCP tools (+ shared `get_ticket`, `list_approvals`) |
|---|---|---|
| **Boss** | Reads each ticket, decides who works on it, makes the final call | `list_tickets`, `update_ticket`, `recent_activity` |
| **Inventory** | Stock by SKU and size, shortfalls, which vendor can restock | `check_stock`, `find_shortfalls`, `find_restock_vendor`, `receive_purchase_order` |
| **Accounting** | Cash, invoices, margins; prepares payments / purchase orders / discounts for approval | `get_cash_position`, `list_invoices`, `check_margin`, `prepare_invoice_payment`, `prepare_purchase_order`, `request_discount_approval` |
| **Facilities** | Leases, rent, rent notices | `list_leases`, `check_rent_due`, `verify_rent_notice`, `prepare_rent_payment`, `add_lease_note` |
| **Customer Service** | Drafts messages to customers (a human sends them) | `draft_customer_message`, `list_customer_messages` |

Every agent also has **`ask_agent(agent, task)`**, the delegation tool.

- **Model:** `gpt-6-luna` through Portkey (`model.py`).
- **Key:** `PORTKEY_API_KEY` comes from the root `.env` and is never printed.
- **Framework:** Pydantic AI 2.55, which connects to MCP through FastMCP.

---

## Run it

From `hw 5/`, run the team on every open ticket, in parallel, followed by the Boss's final review:

```bash
.venv/bin/python -m agent_team
```

Run it on one ticket only (repeat `--ticket` for more):

```bash
.venv/bin/python -m agent_team --ticket 103
```

Write to the real working database instead of a copy:

```bash
.venv/bin/python -m agent_team --working-db
```

Run the tests (scripted fake models, no API calls):

```bash
.venv/bin/python -m pytest -q
```

- **Database:** by default the team works on a **fresh copy** of `data/campus_customs_new.db` (`data/runs/team_run_<time>.db`), so trying it never changes the real data.
- **Live output:** the terminal shows every step as it happens:
  - `>>` start
  - `->` hand-off
  - `<-` reply
  - `x>` refused hand-off
  - `*` tool call
  - `OK` final call
- **Saved runs:** each run is saved to `outputs/agent_runs/team_run_<time>.json`, and the latest also to `outputs/agent_team_run.json`.

From Python:

```python
from agent_team import Team
async with Team() as team:                    # starts the 5 MCP servers once
    report = await team.run_ticket(102)       # one ticket
    everything = await team.run_open_tickets()  # all open tickets + Boss review
```

---

## How it works

```
                 Human (dashboard: watches, approves, sends drafts)
                                    ▲  approvals / drafts / ticket decisions
 ┌──────────────────────────────────┴───────────────────────────────────┐
 │                               Boss                                    │
 └──┬──────────────┬──────────────┬──────────────┬──────────────────────┘
    │ ask_agent    │              │              │        (any agent can ask any other:
 Inventory ◄──► Accounting ◄──► Facilities ◄──► Customer Service    20 directed pairs)
    │              │              │              │
 campus-inventory  campus-accounting  campus-facilities  campus-customer-service   (+ campus-boss)
    └──────────────┴──── MCP (stdio, from mcp.json) ─────┴──► data/campus_customs_new.db
```

1. **`team.run_ticket(id)`**: every ticket goes to the **Boss first, the sole decision maker**. The Boss:
   - **reads and understands** the ticket with `get_ticket`: who is asking, what exactly (SKU, size, qty, amount, extras like a discount), linked records, deadline, and what a good outcome is;
   - **checks capability and capacity** in the team roster, which is put into the Boss's instructions at the start of each ticket and can be refreshed with the Boss-only `team_roster` tool. The roster gives each agent's job, the tools it actually has (read live from the MCP server's tags), how many tasks it has in progress across all tickets, how often it has been asked on this ticket, and the hand-offs left;
   - gives each part to the agent that has the tool for it, **once, with one complete task**.
2. **`ask_agent`** runs the target agent on the task with its own tools and returns its answer as the tool result. The target can delegate further.
3. **Specialists** check facts and queue requests (`prepare_*` / `request_*`). Customer Service saves a draft.
4. **The Boss alone decides.** Specialists' answers are findings and recommendations; only the Boss has `update_ticket` and `team_roster`. It records its final call with `update_ticket` and returns a `TicketOutcome`:
   - `understanding`: what the ticket asks;
   - `assignments`: each agent, its task, and *why* (which capability);
   - lead agent and agents consulted;
   - final status;
   - the decision;
   - pending approvals;
   - draft ids.

   An output check rejects the outcome if the ticket's status in the database doesn't match, so the final call is always recorded.
   **Restocks:** Inventory finds the shortfall and the vendor, then asks **Accounting** to queue the purchase order for human approval (vendor, SKU, size, units short, cost, arrival, ticket), and reports the PO and approval ids back. If a reprint is held by an unpaid vendor invoice, Inventory tells Accounting about that invoice instead of ordering twice.
5. **`team.run_open_tickets()`** runs all tickets **in parallel**, then `team.review()`. The Boss looks across tickets (combined cash, conflicts) and returns a `DayReview` with one recommendation per pending approval.

**Default routing** (the Boss can change it):

| Ticket type | Lead | Helpers |
|---|---|---|
| `customer_order` | customer_service | inventory, accounting |
| `rent_notice` | facilities | accounting |
| `price_override` | accounting | inventory, customer_service |

### Delegation guard rails (full connectivity without infinite loops)

| Rule | Why |
|---|---|
| Any agent can start a hand-off to any other agent | Full connectivity; tested on all 20 pairs |
| Can't hand off to yourself | Use your own tools |
| Can't hand off to an agent already waiting on you in the same chain (A → B → A) | Stops ping-pong. In the first live run, Inventory asked the Boss back, which started a second Boss run and a duplicate customer draft |
| Chain at most 4 agents deep (`MAX_CHAIN`) | Bounded work |
| At most 12 hand-offs per ticket (`MAX_HANDOFFS_PER_RUN`) | Bounded cost |
| Usage caps: Boss 20 model calls / 30 tool calls; others 12 / 20 per request | Bounded cost |

A refused hand-off comes back to the agent as text ("Refused: …"), so it carries on with what it has. A failed sub-agent run comes back as an error message, not a crash.

### What the human sees

| Where | What |
|---|---|
| `approvals` | Every payment, purchase order and discount waiting for approval, with summary, reason and cash before/after |
| `customer_messages` | One draft per ticket (saving again updates it) |
| `ticket_updates` + `tickets.status` | The Boss's final call per ticket |
| `agent_messages` | Every hand-off: who asked whom, the task, the answer, status, depth |
| `activity_log` | Every change any agent made |
| Run JSON / `Team(on_event=...)` | Every tool call and hand-off in order, live. The dashboard can subscribe to the same events |

Approving is done by `mcp_server.approvals.decide()`, which no agent can call.

---

## Files

| File | Purpose |
|---|---|
| `team.py` | `Team`: builds the 5 agents (each with its MCP toolset from `mcp.json` + `ask_agent`), delegation and guard rails, event recording, `run_ticket`, `review`, `run_open_tickets`, output models |
| `model.py` | `gpt-6-luna` via Portkey |
| `../backend/prompts/<agent>.md` | **One complete prompt per agent** (`boss`, `inventory`, `accounting`, `facilities`, `customer_service`). Each file has: the agent's job in the assignment's words, its tools, how it works, and the team rules (connectivity, facts only from tools, money needs a human, reply format), which are repeated in every file. Edit these to change behaviour |
| `__main__.py` | `python -m agent_team` runner: database copy, live trace, saves the run |
| `../tests/test_agent_team.py` | Connectivity on all 20 pairs, guard rails, a full scripted ticket run through the real MCP servers, the Boss's must-record-final-call check |

---

## Live results (gpt-6-luna, 2026-10-13, on a copy of the database)

Run `outputs/agent_runs/team_run_20261013T034859Z.json` (2nd full run, after the fixes below; `outputs/agent_team_run.json` always holds the latest run). All three tickets ran in parallel. Total: 57 model calls and 11 hand-offs.

| Ticket | Who worked on it | What the team did | Final call |
|---|---|---|---|
| 101 tee S (Tauhid Zaman) | Boss → Inventory, Accounting (→ Inventory), Customer Service | 0 in stock, short 1; the reprint is tied to overdue invoice 501. Queued **approval #2: pay $840**. No duplicate purchase order. Draft #1 to the customer | `waiting_on_human` |
| 102 rent (Elm City Properties) | Boss → Accounting, Facilities | Notice **verified** against lease 1. Queued **approval #1: $2,400 rent**. One lease note | `waiting_on_human` |
| 103 20 hoodies M (Yale AI Club) | Boss → Inventory, Accounting (→ Inventory), Customer Service | 8 on hand, 12 short. Margin checks at $58 / $52.20 / $49.30. Queued **approval #3: 15% discount at $49.30** (55.4% margin) and **approval #4: purchase order for 12 from Bulldog Print Co, $264, arriving 09-05**. Draft #2 says both are pending | `waiting_on_human` |
| Boss review | Boss → Accounting | Rent + invoice leave **$160**. Adding the purchase order would leave **−$104** | Approve #1 (rent) first, then #2 (invoice); #3 (discount) is OK, no cash out; **hold #4** (purchase order) until there's cash |

**Fixed after the first live run:**
- the A → B → A loop guard;
- one draft per ticket;
- Inventory costs restocks for the units short (12), not the full order (20);
- Facilities writes one lease note per notice.

### Latest live run (after the Boss and Inventory changes, `outputs/agent_runs/team_run_20261013T041017Z.json`)

| Ticket | What happened | Approvals queued |
|---|---|---|
| 101 tee S | The Boss stated what the ticket asks and assigned Inventory, Accounting and Customer Service. **Inventory → Accounting:** the reprint is held by overdue invoice 501, so Accounting queued its payment | #1 pay invoice 501, $840 |
| 102 rent | Notice verified against lease 1 | #3 rent $2,400 |
| 103 hoodies | **Inventory → Accounting:** 12 short, Bulldog Print Co, so Accounting queued **PO #1 for $264** (arriving 09-05). Accounting checked margins and queued the discount at $50 (56% margin). **The Boss caught a wrong figure in the customer draft** ("$1,000 total discount") and had it corrected before deciding | #2 PO $264, #4 discount $50 × 20 |
| Boss review | Approve rent first. Hold the invoice until the reprint quantity is confirmed. Hold the purchase order and the discount: invoice + PO + rent would leave −$104, and only 8 of 20 hoodies are on hand | |

### Safety nets added later
- **`create_ticket`**, shared by every agent: raise a separate problem (e.g. the out-of-stock mug) as an internal ticket instead of widening the current one. The Boss routes it later. Agents can't create customer orders, rent notices or price overrides.
- **Automatic figure check (plain code, no tokens):** customer drafts and Boss decisions are refused if any $ amount, % or record id isn't backed by the database, so the agent has to fix it before anything reaches a customer or the human. Live check on ticket 103: the draft and decision passed; a tampered "20% off" was caught.

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
