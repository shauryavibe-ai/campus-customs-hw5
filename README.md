# Campus Customs: multi-agent operations team (HW 5)

A team of five AI agents that runs the back office of **Campus Customs**, a Yale campus merchandise shop: customer orders, unpaid bills, rent and discount requests.
- **The team:** a **Boss** reads each ticket, decides who should work on it and makes the final call. **Inventory**, **Accounting**, **Facilities** and **Customer Service** do the work and can delegate to each other.
- **How they reach the data:** every agent works through its own **MCP server**.
- **What you see:** a **FastAPI** backend runs the team, and a **React** dashboard shows the warehouse at work.
- **Money:** only moves when a **human approves** it.

Built for *AI Foundations for Managers* (Yale SOM). Every prompt used to build it is logged in [`AI_prompts.md`](AI_prompts.md).

## What's here

| Folder / file | What it is |
|---|---|
| [`mcp_server/`](mcp_server/README.md) | FastMCP server with 22 tools, tagged per agent (stock, cash, invoices, margins, leases, tickets, customer drafts), plus the human-only approval step, the figure check and the confidentiality check |
| [`agent_team/`](agent_team/README.md) | The five Pydantic AI agents on `gpt-6-luna` (via Portkey), fully connected with `ask_agent`, plus guard rails, the playbook (expected vs actual) and the append-only, hash-chained audit trail |
| [`backend/`](backend/README.md) | FastAPI routes: tickets, run a ticket, live events, per-agent activity, view → approve, close ticket, cash, reset. Agent prompts are in `backend/prompts/` |
| [`frontend/`](frontend/README.md) | React + Vite + TypeScript **warehouse dashboard**: the Boss on the 2nd floor, departments below, role avatars, live work orders, the signing desk and celebration mode |
| `mcp.json` | One MCP server per agent, used by Claude and by the agent team |
| `data/` | `campus_customs.db` (the original course data, never modified) and `campus_customs_new.db` (the working copy) |
| `outputs/` | `desk_tickets.html` (expected vs actual per ticket, cash reconciliation, reflection), `harness.md`, `design.md`, `safety.md`, the run reports and `audit_trail.json` |
| `scripts/` | MCP smoke tests, the red-team test, the board runner and the desk-tickets page builder |
| `tests/` | 86 tests (scripted fake models, so no API calls), each on a temporary copy of the database |

## Run it

You need Python 3.14 and Node 24. The API key goes in a `.env` **one folder above** this repo (`PORTKEY_API_KEY=…`), and is never committed.

Set up the Python environment:

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

Start the backend, from `backend/`:

```bash
../.venv/bin/uvicorn main:app --reload --port 8000
```

Start the dashboard, from `frontend/`:

```bash
npm install
```

```bash
npm run dev
```

Then open http://localhost:5173, pick a ticket, and click **Dispatch the team**.

Other entry points, all run from the repo root:

- Run every ticket to resolved:

  ```bash
  .venv/bin/python scripts/run_board.py
  ```

- Rebuild `outputs/desk_tickets.html`:

  ```bash
  .venv/bin/python scripts/build_agent_monitor.py
  ```

- Run the tests:

  ```bash
  .venv/bin/python -m pytest -q
  ```

## Safety in brief

- **Money:** nothing is paid, ordered or discounted without a named human approving the exact version they reviewed (sealed approvals).
- **Customer messages:** drafts can only contain public facts. A confidentiality check and a figure check run on every draft.
- **Ticket text:** anything written by requesters is treated as untrusted data.
- **The API key:** never reaches the MCP servers, the audit trail or this repo.

Full write-up: [`outputs/safety.md`](outputs/safety.md).
