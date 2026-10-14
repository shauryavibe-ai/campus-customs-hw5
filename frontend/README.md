# Campus Customs · Warehouse Operations (frontend)

React 19 + Vite 8 + TypeScript dashboard for the agent team. The page runs at **http://localhost:5173** and calls the FastAPI backend (`../backend`) directly at **http://localhost:8000**. The backend allows the page's origin with CORS. To point it elsewhere, set `VITE_API_BASE` in `.env.local` (see `.env.example`). It uses the Campus Customs colours: near-black navy, Yale Blue `#00356B`, light-blue highlights, serif headings, and pink only for tiny accents. The Yale Campus Customs super-courier mascot (Yale-blue cape, white “Y” cap) is drawn in SVG, with no image files.

## Run it (two terminals)

**Terminal 1, the backend** (from `hw 5/backend/`). Activate the environment:

```bash
source ../.venv/bin/activate
```

```bash
uvicorn main:app --reload --port 8000
```

**Terminal 2, the dashboard** (from `hw 5/frontend/`). Install once:

```bash
npm install
```

```bash
npm run dev
```

Open http://localhost:5173. Vite always uses port 5173 (`strictPort`), so the origin matches what the backend allows.

Type-check and production build:

```bash
npm run build
```

## What's on the page

The centre of the page is **the Campus Customs warehouse**. The Boss sits in a glass office on the 2nd floor, and Inventory, Accounting, Facilities and Customer Service are departments on the floor below, each with a role avatar (suit and tie, hard hat and hi-vis, shirt-and-tie accountant, coveralls, headset). When a ticket is resolved, the warehouse goes into **celebration mode**. Full design: `../outputs/design.md`.


| Area | Shows | Backend routes |
|---|---|---|
| **Header** | The courier mascot: idle; running with a flapping cape and speed lines while agents work; sparkling when a ticket is delivered. A speech bubble says what's happening. Shop date, audit-trail check, approvals waiting, **Reset to fresh run** (asks to confirm), and the **checking balance** with pending payments and checking after them | `/api/health`, `/api/cash`, `/api/approvals`, `/api/reset` |
| **Pending tickets** (left) | Every open ticket **in the Boss's priority order** (#1 rent → #2 tee → #3 hoodies), with status and "Boss should call X first". Resolved tickets move to *Delivered* | `/api/tickets`, `/api/priority` |
| **Ticket + Dispatch** (centre) | The ticket, the requester's words (shown as plain text), linked invoice or lease, any suspicious-text flag, and **Dispatch the team** | `/api/tickets/{id}`, `POST /api/tickets/{id}/run` |
| **Status banner** | *Ready to dispatch* → *The team is working* → **Resolved ✓** / *Waiting for your approval (n)* / *In progress*, plus run time, model calls and hand-offs | `/api/runs/{job}` |
| **Delivery route** | **Live, every 1.5 s:** the Boss picks up the ticket, then each hand-off the Boss decides (Boss → Facilities…), each tool call as a parcel chip, each reply as a speech bubble (expand with "more"), refusals from the safety checks, and the Boss's final call. Shows who is working right now | `/api/events?ticket_id=…&after_seq=…` |
| **What each agent did** | One card per agent: a short summary of what it said, the tools it used (with counts), who it asked, refused steps, and its playbook verdict (after a run). Agents that stayed out say why | `/api/agents?run_id=…`, `/api/tickets/{id}/expected` |
| **Your approvals** (right) | The last stage: each payment, purchase order or discount the team queued. **Review** shows the amount, reason, checking now and checking after all pending. Enter your name, then **Approve** or **Reject**. The seal of what you reviewed is sent, so a changed request is refused. Afterwards it shows **the new checking balance** and whether the ticket is now **resolved** | `/api/approvals/{id}`, `POST /api/approvals/{id}/decision` |
| **Customer message** | The draft for the requester (passed the confidentiality and figure checks; a person sends it) | from `/api/tickets/{id}` |
| **Expected vs actual** | The Boss's job, who should be called first (✓ / ✗), the playbook's ordered flow with ✓ / ⚠ / ✗ / ○, and the score | `/api/tickets/{id}/expected` |

When you decide the last approval on a ticket, the backend marks it **resolved** automatically and it moves to *Delivered*.

## Safety in the UI

- **Untrusted text:** text written by requesters and agents is always rendered as text, never as HTML.
- **Approvals:** need a person's name, plus the seal of the version they reviewed.
- **Reset:** asks for confirmation.

## Live check (2026-10-13, `gpt-6-luna`)

1. Opened on #1 priority **ticket 102 (rent)**.
2. **Dispatch the team:** the route filled in live, Boss → Facilities (verify notice, queue rent, note lease) → the Boss's final call. Done in 24.7 s with 12 model calls. Banner: *Waiting for your approval (1)*.
3. **Review** showed $2,400, the reason, checking $3,400 → $1,000. Approved as "UI smoke test". The page showed **checking $1,000.00**, **Ticket 102 is now resolved ✓**, the ticket moved to *Delivered*, and the mascot switched to "delivered".
4. **Reset to fresh run:** back to $3,400 and 3 pending tickets, with the audit trail kept.

No console errors.
