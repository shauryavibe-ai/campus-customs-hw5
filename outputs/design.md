# Campus Customs Warehouse: dashboard design

The dashboard (`frontend/`) is drawn as **the Campus Customs warehouse**. The five agents work in it as departments. The **Boss sits upstairs in a glass office on the second floor**, watching every department and sending work orders down. Everything that already worked stays the same: pick a ticket, dispatch the team, follow it live, approve on the signing desk, see the checking balance, reset.

Colours are the Campus Customs palette (approved exception in `AGENTS.md`):
- near-black navy `#0a1220` walls and floor;
- **Yale Blue `#00356B`** for structure and signage, `#286DC0` for the brand colour, `#6FA8FF` light-blue lights and highlights;
- serif signage;
- pink only for tiny accents (a "fragile" sticker, confetti).

---

## 1. Layout

```
┌──────────────────────────────────────── Header: Campus Customs · Warehouse ─────────── Checking balance ┐
├──────────────┬───────────────────────────────────────────────────────────────────┬─────────────────────┤
│ TICKETS      │   ╔══ CAMPUS CUSTOMS WAREHOUSE ═══════════════════════════════╗   │ SIGNING DESK        │
│ Unresolved 3 │   ║  2F  ┌──────── BOSS'S OFFICE (glass) ────────┐            ║   │ (your approvals)    │
│  #1 Rent due │   ║      │ Boss at desk · "Facilities, verify…" │  ← watches  ║   │ review → name →     │
│  #2 Tee      │   ║      └──────────┬──────────┬─────────┬───────┘            ║   │ approve / reject    │
│  #3 Hoodies  │   ║  ═══ mezzanine railing ═══╪═════════╪════════             ║   │ → new balance       │
│ Resolved 0   │   ║  1F ┌Inventory┐┌Accounting┐┌Facilities┐┌Customer Svc┐     ║   │                     │
│              │   ║     │ bay     ││ office   ││ workshop ││ help desk  │     ║   │ CUSTOMER MESSAGE    │
│ [Dispatch]   │   ║     └─────────┘└──────────┘└──────────┘└────────────┘     ║   │                     │
│              │   ║  LOADING DOCK · courier mascot ships delivered tickets    ║   │ EXPECTED VS ACTUAL  │
│              │   ╚═══════════════════════════════════════════════════════════╝   │                     │
│              │   Dispatch log (the live route, collapsible)                       │                     │
└──────────────┴───────────────────────────────────────────────────────────────────┴─────────────────────┘
```

- **Left, the ticket rack:**
  - two groups: **Unresolved** (in the Boss's priority order) and **Resolved**;
  - every card has a clear **Resolved ✓** or **Unresolved** tag, and unresolved cards add the reason (*needs your approval*, *in progress*, *not started*);
  - selecting a ticket loads it into the warehouse.
- **Centre, the warehouse:** the ticket being worked is pinned on the Boss's office window, with **Dispatch the team** beside it.
- **Below the warehouse:** the **dispatch log**, the same live step-by-step route as before, collapsible.
- **Right:** the **signing desk** (approvals), the customer message, and expected vs actual. These are unchanged.

## 2. The warehouse

| Area | What it is | Who works there |
|---|---|---|
| **2nd floor: Boss's office** | Glass-walled office on a mezzanine above the floor, with a desk, monitor and window. Light-blue "watch lines" run from the office down to each department | **Boss** |
| **Inventory bay** | Racking with boxes, a pallet, a barcode scanner | **Inventory** |
| **Accounting office** | Desk with ledger, calculator, cash box | **Accounting** |
| **Facilities workshop** | Tool wall, lease folder, building plan | **Facilities** |
| **Customer service help desk** | Counter with headset and message tray | **Customer Service** |
| **Loading dock** | Roll-up door; the super-courier mascot carries out the parcels when a ticket is delivered | — |

**Boss watching and assigning:**
- **Work orders:** when the Boss hands off a task, a **work-order slip** drops down the watch line into that department, and the department's ceiling lamp turns on.
- **The Boss's bubble** shows what it is doing right now: *Reading ticket 103*, *Assigning Inventory: check 20 hoodies…*, *Deciding…*, or its final call.

## 3. Agents as employees (avatars drawn in SVG, no images)

| Agent | Dressed as | Prop | Working animation |
|---|---|---|---|
| **Boss** | Navy suit jacket, white shirt, **Yale-blue tie**, neat hair | Clipboard, desk with monitor | Leans in and points down at the floor (assigning) |
| **Inventory** | **Yellow hard hat, orange hi-vis vest** with reflective stripes, work jeans, boots | Barcode scanner, box | Lifts and carries a box |
| **Accounting** | **White shirt, dark trousers, tie**, glasses, pen behind the ear | Calculator | Taps the calculator (typing hand) |
| **Facilities** | **Blue coveralls**, tool belt, work cap | Wrench | Turns the wrench |
| **Customer Service** | **Headset with mic**, cardigan over a polo, name badge | Speech bubble / phone | Talks (head nods, mic pulse) |

Each employee has its own skin tone and hair so the five read as different people.

## 4. What each department shows ("what they are working on and how")

Each department panel is a sign plus the avatar plus a **status lamp**:

| Lamp | Meaning |
|---|---|
| ⚪ **Idle** | Not involved yet, or the playbook says it should stay out (with the reason) |
| 🟡 **Working** | Has an open work order. The avatar plays its working animation |
| 🔵 **Done** | Replied to whoever asked |
| 🔴 **Blocked** | A tool was refused, e.g. the figure or confidentiality check |

Underneath:
- **Work order:** who asked and the task, in short;
- **Doing now:** the latest tool call in plain words, e.g. `check_stock · CC-HOOD-NAVY M × 20`;
- **How:** a tool tally (`check_stock`, `find_restock_vendor ×2`…), which is how the agent is working;
- **Said:** a 1–2 sentence summary of its reply;
- the **playbook verdict** after a run (*on track*, *missed 1 of 3*…).

All of it comes from the live audit events (`/api/events`, polled every 1.5 s) and `/api/agents`. Nothing new is invented on screen.

## 5. Celebration mode

When the selected ticket is **resolved** (the run ends resolved, or you approve its last approval):
- every employee leaves their pose and **dances and jumps**, each with its own timing and moves: arm waves, hops, spins;
- **confetti** falls over the warehouse in blues, white and a few pink pieces;
- a banner reads **"Ticket 102 delivered!"**;
- the courier at the loading dock rolls the parcels out the door;
- the ticket card flips to **Resolved ✓**.

People who set *reduce motion* in their system get a still "delivered" scene instead (`prefers-reduced-motion`).

## 6. What stays the same

- Ticket selection.
- Dispatch → live updates every 1.5 s.
- The approval flow: review → your name → approve or reject with the viewed seal → new **checking balance** and resolved status.
- Customer drafts.
- Expected vs actual.
- Reset.
- The audit check.
- The backend at `http://localhost:8000`.
- All text from tickets and agents rendered as plain text.

## 7. Files

| File | Purpose |
|---|---|
| `frontend/src/components/Avatars.tsx` | The five role avatars (SVG) and their idle, working and dancing states |
| `frontend/src/components/Warehouse.tsx` | The building: Boss office, departments, watch lines, work orders, loading dock, confetti; works out each department's state from events |
| `frontend/src/components/TicketList.tsx` | Unresolved and resolved groups with explicit tags |
| `frontend/src/App.tsx` | Layout, data loading and polling (unchanged logic) |
| `frontend/src/styles.css` | Warehouse, avatar animations, celebration, reduced motion |

---

## 8. Built and checked (2026-10-14, live with `gpt-6-luna`)

- **Before a run:** the rack showed 3 **Unresolved** tickets. The warehouse showed the Boss at his desk upstairs ("Ticket 102 is on my desk"), Accounting and Facilities *Idle*, and Inventory and Customer Service *Staying out* with the playbook's reason.
- **Dispatching ticket 102:**
  - the Boss's bubble went "Watching Facilities work…" → "Reviewing what the team found…";
  - a **work-order slip** dropped into the Facilities Workshop, with its watch line lit;
  - Facilities went *Working* → *Done*, showing **Doing** (`add_lease_note…`) and **How** (`verify_rent_notice`, `list_leases`, `check_rent_due`, …);
  - Accounting was then asked about cash.
- **Run finished** (35 s, 15 model calls, 2 hand-offs) → *Waiting for your approval (1)*.
- **Approved on the signing desk:**
  - checking **$1,000.00** and **Ticket 102 resolved ✓**;
  - **celebration**: all 5 employees dancing and jumping, 46 confetti pieces, the banner, every lamp *Delivered!*, the dock "Out for delivery!";
  - the ticket moved to the **Resolved ✓** rack.
- **Reset** → back to $3,400 and 3 unresolved tickets. No console errors.
- **Side effect worth noting:** during the run the Boss used `create_ticket` to raise a separate `cash_issue` ticket about low projected cash, which appeared in the Unresolved rack. Reset cleared it with the rest of the run's data.
