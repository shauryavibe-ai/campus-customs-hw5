# Campus Customs Backend (FastAPI)

The API the frontend will use to watch and steer the agent team. It runs on the **working database** (`data/campus_customs_new.db`). Use `POST /api/reset` to go back to the original data for a fresh run.

Start the server from `hw 5/backend/`, with auto-reload while developing. Activate the environment first, because `uvicorn` only exists inside `hw 5/.venv`:

```bash
source ../.venv/bin/activate
```

```bash
uvicorn main:app --reload --port 8000
```

Or, from `hw 5/` and without activating:

```bash
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Opening http://127.0.0.1:8000 redirects to the docs.

Interactive docs: http://127.0.0.1:8000/docs

Run the backend tests (scripted fake models, temporary database, no API calls):

```bash
.venv/bin/python -m pytest -q tests/test_backend.py
```

On start-up the app opens the agent team once (5 MCP servers, one per agent, plus `gpt-6-luna` via Portkey) and reuses it for every request.

## Routes

| Method | Route | Does |
|---|---|---|
| GET | `/api/health` | Business date, database, agents, runs in progress, audit-trail check |
| GET | `/api/tickets` | All tickets with `status`, **`is_open`** and `running`. Filters: `?status=` and `?open_only=true` |
| GET | `/api/tickets/{id}` | One ticket in full: linked invoice or lease, approvals, purchase orders, drafts, history, hand-offs, injection flags |
| GET | `/api/tickets/{id}/status` | **Is the ticket open or not**: `{status, is_open, running}` |
| POST | `/api/tickets/{id}/run` | **Run the agent team on a ticket.** Returns `202` straight away with a `job_id`; the run continues in the background. `409` if the ticket is resolved or already running, `429` if 3 runs are in progress |
| GET | `/api/runs` / `/api/runs/{job_id}` | Run status: `running` / `done` / `failed`, the Boss's outcome, hand-offs, model usage, seconds |
| GET | `/api/events` | **Recent agent events**, oldest first: who did what, tool calls with arguments, hand-offs with the task, replies with what was said, final calls. Filters: `ticket_id`, `run_id`, `agent`, `kind`, `limit`. **Refresh** with `?after_seq=<last_seq>` to get only new events |
| GET | `/api/agents` | **Per agent: what it said** (latest replies and final calls), **which tools it used** (with counts), who it asked, errors, last active. Filters: `ticket_id`, `run_id` |
| GET | `/api/approvals` | The human's queue (`?status=pending` by default; `?status=` for all) |
| GET | `/api/approvals/{id}` | **View** one approval before deciding: summary, details, current checking, pending total and its `content_sha256` seal |
| POST | `/api/approvals/{id}/decision` | **Approve or reject after viewing**. Body: `{"approve": true, "decided_by": "<human name>", "seen_sha256": "<seal from the view>", "note": "…"}`. `409` if the approval changed since it was viewed, was already decided, or `decided_by` is an agent. Approving pays (invoice or rent), moves cash and marks the invoice paid or moves rent forward, or approves a purchase order or discount. Returns the outcome and the new checking balance |
| GET | `/api/messages` | Customer drafts, each with `sealed_ok` (unchanged since it was checked) |
| GET | `/api/cash` | **Current checking balance** from `cash_accounts`, all accounts, payments waiting for approval, and checking after them |
| POST | `/api/reset` | **Reset to the original values** for a fresh run. Body must be `{"confirm": "RESET"}`. It backs up the working database to `data/backups/`, swaps in a copy of the original `data/campus_customs.db`, and recreates the operations tables. Refused while a run is in progress. **The audit trail is kept**, and a `reset` entry is added |

## Typical frontend loop

1. `GET /api/tickets` → show open tickets.
2. `POST /api/tickets/102/run` → get `job_id`.
3. Every few seconds: `GET /api/events?after_seq=<last_seq>` (new steps) and `GET /api/runs/<job_id>` (done yet?).
4. `GET /api/agents?run_id=…` → what each agent said and which tools it used.
5. `GET /api/approvals` → `GET /api/approvals/{id}` (the human reviews it) → `POST …/decision` with its `content_sha256`.
6. `GET /api/cash` → the balance after the decision.
7. `POST /api/reset` with `{"confirm": "RESET"}` → start over.

## Safety

- **Network:** binds to `127.0.0.1`. CORS allows only the Vite dashboard's page origin, `http://localhost:5173` (and `http://127.0.0.1:5173`). Change it with `FRONTEND_ORIGINS` (comma-separated). Browsers from any other origin are refused, including preflight checks. No route returns keys, file paths beyond the database file name, or prompt text.
- **Human-only approvals:** a named human must decide, sending back the **seal of the version they viewed**. Agents can't approve, and their names are refused.
- **Audit:** every agent step, human decision and reset goes into the append-only, hash-chained `outputs/audit_trail.json`.
- **Reset:** needs explicit confirmation, takes a backup first, and never runs while agents are working.

## Live check (2026-10-13, `gpt-6-luna`, working database)

- **Health and tickets:** health OK; 3 open tickets; checking $3,400.
- **Run:** `POST /api/tickets/102/run` returned `202` at once and the run finished in 43 s (13 model calls, 2 hand-offs). Facilities verified the notice and queued rent approval #1 for $2,400; Accounting confirmed $1,000 would be left; the final status was `waiting_on_human`.
- **Watching:** `/api/events` and `/api/agents` showed every tool call and what each agent said.
- **Approval:** with a stale seal → `409`. With the viewed seal (signed "API smoke test") → paid; checking $1,000; rent next due 2026-10-02.
- **Reset:** without confirmation → `422`. With `RESET` → checking $3,400, 3 open tickets, 0 approvals, backup saved, audit trail kept (287 entries, chain OK).
