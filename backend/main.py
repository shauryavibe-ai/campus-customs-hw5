"""FastAPI backend for the Campus Customs agent team.

Run (from hw 5/):
    .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
Docs: http://127.0.0.1:8000/docs

The app starts the agent team once (five MCP servers, one per agent) and keeps it
for every request. Agent runs take 40-120 s, so POST /api/tickets/{id}/run starts the
run in the background and returns at once; the frontend polls /api/runs/{id} and
/api/events?after_seq=... to watch the agents work.

Safety:
- Binds to localhost and only allows the local frontend (CORS). No route returns keys.
- Payments, purchase orders and discounts are approved only through
  /api/approvals/{id}/decision, by a named human, who must send back the content seal
  of the approval they viewed: if it changed after they looked, it is refused.
- Reset needs {"confirm": "RESET"}, backs up the working database first, refuses
  while a run is in progress, and never touches the audit trail.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

HW5 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HW5))

from agent_team import audit  # noqa: E402
from agent_team.team import AGENT_NAMES, Team  # noqa: E402
from agent_team.playbook import compare, expected_steps, priority_order, run_events  # noqa: E402
from mcp_server import accounting, approvals, boss, customer_service, db  # noqa: E402

MAX_PARALLEL_RUNS = 3
# The Vite dev server's page origin(s) allowed to call the API from the browser.
# Override with FRONTEND_ORIGINS="http://localhost:5173,https://…" if the dashboard runs elsewhere.
FRONTEND_ORIGINS = [o.strip() for o in os.environ.get(
    "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]


# --- Request bodies -----------------------------------------------------------------

class Decision(BaseModel):
    approve: bool
    decided_by: str = Field(min_length=2, max_length=60, description="The human's name (not an agent).")
    seen_sha256: str = Field(min_length=64, max_length=64,
                             description="content_sha256 of the approval as the human viewed it.")
    note: str | None = Field(default=None, max_length=500)


class CloseRequest(BaseModel):
    decided_by: str = Field(min_length=2, max_length=60, description="The human's name (not an agent).")
    note: str = Field(min_length=3, max_length=500, description="Why the ticket can be closed.")


class ResetRequest(BaseModel):
    confirm: Literal["RESET"]


# --- Helpers --------------------------------------------------------------------------

def _entries() -> list[dict]:
    path = audit.audit_path()
    return json.loads(path.read_text())["entries"] if path.exists() else []


def _short(value: Any, n: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= n else text[:n] + "…"


def _summary(e: dict) -> str:
    k = e["kind"]
    if k == "tool_call":
        return f"called {e['tool']}({_short(e.get('args', {}), 150)})"
    if k == "tool_result":
        return f"{e['tool']} returned {_short(e.get('result'), 200)}"
    if k == "tool_error":
        return f"{e['tool']} refused: {_short(e.get('error', ''), 250)}"
    if k == "handoff":
        return f"asked {e['to']}: {_short(e.get('task', ''), 300)}"
    if k == "handoff_reply":
        return f"replied to {e['to']}: {_short(e.get('answer', ''), 400)}"
    if k == "handoff_refused":
        return f"hand-off to {e['to']} refused: {e.get('reason', '')}"
    if k == "final":
        out = e.get("output") or {}
        return f"final call: {out.get('final_status', '')} {_short(out.get('decision') or out.get('summary', ''), 300)}"
    if k == "run_end":
        return f"run ended ({(e.get('usage') or {}).get('requests')} model calls, {e.get('elapsed_s')} s)"
    return k.replace("_", " ")


def _public_event(e: dict) -> dict:
    """An audit entry trimmed for the frontend (full detail stays in the audit trail)."""
    keep = ("seq", "time", "session_id", "run_id", "ticket_id", "agent", "depth", "chain", "kind", "tool", "to",
            "status")
    out = {k: e[k] for k in keep if k in e}
    out["summary"] = _summary(e)
    if e["kind"] == "tool_call":
        out["args"] = e.get("args")
    if e["kind"] in ("handoff", "handoff_refused"):
        out["task"] = e.get("task")
    if e["kind"] == "handoff_reply":
        out["said"] = e.get("answer")
    if e["kind"] == "final":
        out["output"] = e.get("output")
    return out


def _ticket_row(t: dict, running: set[int]) -> dict:
    return {"id": t["id"], "type": t["type"], "requester": t["requester"], "subject": t["subject"],
            "status": t["status"], "is_open": t["status"] != "resolved", "running": t["id"] in running,
            "created_at": t["created_at"]}


def _rebuild_monitor() -> None:
    try:
        sys.path.insert(0, str(HW5 / "scripts"))
        from build_agent_monitor import build
        build()
    except Exception:  # the page is a convenience; never fail a request because of it
        pass


# --- App --------------------------------------------------------------------------------

def create_app(team_factory: Callable[[], Team] = Team) -> FastAPI:
    jobs: dict[str, dict] = {}            # run requests made through this backend
    tasks: dict[str, asyncio.Task] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.check_database()
        db.ensure_ops_tables()
        async with team_factory() as team:
            app.state.team = team
            yield
        for t in tasks.values():
            t.cancel()

    app = FastAPI(title="Campus Customs agent backend", version="1.0.0", lifespan=lifespan,
                  description="Tickets, agent runs, agent activity, human approvals, cash and reset.")
    app.add_middleware(CORSMiddleware, allow_origins=FRONTEND_ORIGINS, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"], max_age=600)

    def running_tickets() -> set[int]:
        return {j["ticket_id"] for j in jobs.values() if j["status"] == "running"}

    # Root: opening the server in a browser goes to the interactive API docs ----------------
    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    # Health ----------------------------------------------------------------------------
    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        with db.connect() as conn:
            today = db.business_date(conn).isoformat()
        return {"ok": True, "business_date": today, "database": db.db_path().name,
                "agents": list(AGENT_NAMES), "runs_in_progress": len(running_tickets()),
                "audit": audit.verify()}

    # Tickets ---------------------------------------------------------------------------
    @app.get("/api/tickets", tags=["tickets"])
    def list_tickets(status: str | None = Query(None, description="open | in_progress | waiting_on_human | resolved"),
                     open_only: bool = False) -> dict:
        with db.connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM tickets ORDER BY id")]
        run = running_tickets()
        out = [_ticket_row(t, run) for t in rows
               if (status is None or t["status"] == status) and (not open_only or t["status"] != "resolved")]
        return {"tickets": out, "open_count": sum(t["is_open"] for t in out)}

    @app.get("/api/tickets/{ticket_id}", tags=["tickets"])
    def get_ticket(ticket_id: int) -> dict:
        try:
            detail = boss.get_ticket(ticket_id)
        except Exception:
            raise HTTPException(404, f"No ticket {ticket_id}")
        detail["is_open"] = detail["ticket"]["status"] != "resolved"
        detail["running"] = ticket_id in running_tickets()
        return detail

    @app.get("/api/tickets/{ticket_id}/status", tags=["tickets"])
    def ticket_status(ticket_id: int) -> dict:
        with db.connect() as conn:
            t = conn.execute("SELECT id, status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if t is None:
            raise HTTPException(404, f"No ticket {ticket_id}")
        return {"ticket_id": t["id"], "status": t["status"], "is_open": t["status"] != "resolved",
                "running": ticket_id in running_tickets()}

    @app.get("/api/priority", tags=["tickets"])
    def priority() -> dict:
        """The order the Boss should take open tickets in, and who it should call first on each."""
        with db.connect() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM tickets WHERE status != 'resolved' ORDER BY id")]
        from agent_team.playbook import in_original
        orig = [i for i in ids if in_original(i)]
        ranking = priority_order(orig, original=True) + priority_order([i for i in ids if i not in orig])
        ranking.sort(key=lambda r: (r["tier"], r["due"], r["ticket_id"]))
        for n, r in enumerate(ranking, 1):
            r["rank"] = n
        for r in ranking:
            exp = expected_steps(r["ticket_id"])
            r["first_agent"], r["first_agent_why"] = exp.first_agent, exp.first_agent_why
        return {"priority": ranking}

    @app.get("/api/tickets/{ticket_id}/expected", tags=["tickets"])
    def expected(ticket_id: int) -> dict:
        """The playbook's expected flow for this ticket vs the latest run in the audit trail."""
        with db.connect() as conn:
            if not conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone():
                raise HTTPException(404, f"No ticket {ticket_id}")
        run_id, events = run_events(_entries(), ticket_id)
        result = compare(expected_steps(ticket_id), events)
        return {"ticket_id": ticket_id, "run_id": run_id, **result}

    @app.post("/api/tickets/{ticket_id}/resolve", tags=["tickets"])
    def close_ticket(ticket_id: int, body: CloseRequest) -> dict:
        """A human closes a ticket with nothing left to approve (e.g. an internal ticket)."""
        if ticket_id in running_tickets():
            raise HTTPException(409, f"Ticket {ticket_id} is still being worked on.")
        try:
            result = approvals.close_ticket(ticket_id, body.decided_by, body.note)
        except approvals.ApprovalError as exc:
            raise HTTPException(409 if "No ticket" not in str(exc) else 404, str(exc))
        audit.append({"kind": "human_closed_ticket", "agent": "human", "ticket_id": ticket_id,
                      "decided_by": body.decided_by, "note": body.note})
        return result

    # Runs ------------------------------------------------------------------------------
    @app.post("/api/tickets/{ticket_id}/run", status_code=202, tags=["runs"])
    async def run_ticket(ticket_id: int) -> dict:
        with db.connect() as conn:
            t = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if t is None:
            raise HTTPException(404, f"No ticket {ticket_id}")
        if t["status"] == "resolved":
            raise HTTPException(409, f"Ticket {ticket_id} is resolved.")
        if ticket_id in running_tickets():
            raise HTTPException(409, f"Ticket {ticket_id} is already being worked on.")
        if len(running_tickets()) >= MAX_PARALLEL_RUNS:
            raise HTTPException(429, f"{MAX_PARALLEL_RUNS} runs are already in progress; try again shortly.")
        job_id = uuid.uuid4().hex[:12]
        jobs[job_id] = {"job_id": job_id, "ticket_id": ticket_id, "status": "running",
                        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "run_id": None, "outcome": None, "error": None, "handoffs": None, "usage": None}
        team: Team = app.state.team

        async def work() -> None:
            started = time.perf_counter()
            try:
                report = await team.run_ticket(ticket_id)
                jobs[job_id].update(status="done" if report["error"] is None else "failed", run_id=report["run_id"],
                                    outcome=report["output"], error=report["error"], handoffs=report["handoffs"],
                                    usage=report["usage"])
            except Exception as exc:
                jobs[job_id].update(status="failed", error=f"{type(exc).__name__}: {exc}")
            jobs[job_id]["seconds"] = round(time.perf_counter() - started, 1)
            await asyncio.to_thread(_rebuild_monitor)

        tasks[job_id] = asyncio.create_task(work())
        return {**jobs[job_id], "poll": f"/api/runs/{job_id}", "events": f"/api/events?ticket_id={ticket_id}"}

    @app.get("/api/runs", tags=["runs"])
    def list_runs() -> dict:
        return {"runs": sorted(jobs.values(), key=lambda j: j["started_at"], reverse=True)}

    @app.get("/api/runs/{job_id}", tags=["runs"])
    def get_run(job_id: str) -> dict:
        if job_id not in jobs:
            raise HTTPException(404, f"No run {job_id}")
        return jobs[job_id]

    # Agent activity ----------------------------------------------------------------------
    @app.get("/api/events", tags=["agents"])
    def events(ticket_id: int | None = None, run_id: str | None = None, agent: str | None = None,
               kind: str | None = None, after_seq: int = 0, limit: int = Query(100, ge=1, le=1000)) -> dict:
        """Recent agent events, oldest first. Poll with after_seq = the last seq you saw."""
        rows = [e for e in _entries() if e["seq"] > after_seq
                and (ticket_id is None or e.get("ticket_id") == ticket_id)
                and (run_id is None or e.get("run_id") == run_id)
                and (agent is None or e.get("agent") == agent)
                and (kind is None or e.get("kind") == kind)
                and e["kind"] not in ("session_start", "session_end")]
        rows = rows[-limit:]
        return {"events": [_public_event(e) for e in rows],
                "last_seq": rows[-1]["seq"] if rows else after_seq}

    @app.get("/api/agents", tags=["agents"])
    def agents(ticket_id: int | None = None, run_id: str | None = None, said_limit: int = Query(5, ge=1, le=50)) -> dict:
        """What each agent said (its replies and final calls) and which tools it used."""
        rows = [e for e in _entries() if (ticket_id is None or e.get("ticket_id") == ticket_id)
                and (run_id is None or e.get("run_id") == run_id)]
        out = []
        for name in AGENT_NAMES:
            mine = [e for e in rows if e.get("agent") == name]
            tools: dict[str, int] = {}
            for e in mine:
                if e["kind"] == "tool_call":
                    tools[e["tool"]] = tools.get(e["tool"], 0) + 1
            said = [{"seq": e["seq"], "time": e["time"], "ticket_id": e.get("ticket_id"),
                     "to": e.get("to") or "human",
                     "text": e.get("answer") if e["kind"] == "handoff_reply"
                     else (e.get("output") or {}).get("decision") or (e.get("output") or {}).get("summary")}
                    for e in mine if e["kind"] in ("handoff_reply", "final")]
            asked = [{"seq": e["seq"], "to": e["to"], "task": e.get("task")} for e in mine if e["kind"] == "handoff"]
            out.append({"agent": name, "tools_used": tools, "tool_calls": sum(tools.values()),
                        "errors": sum(e["kind"] == "tool_error" for e in mine),
                        "said": said[-said_limit:], "asked_others": asked[-said_limit:],
                        "last_active": mine[-1]["time"] if mine else None})
        return {"agents": out}

    # Approvals -------------------------------------------------------------------------
    @app.get("/api/approvals", tags=["approvals"])
    def list_approvals(status: str | None = "pending") -> dict:
        return accounting.list_approvals(status=status)

    @app.get("/api/approvals/{approval_id}", tags=["approvals"])
    def view_approval(approval_id: int) -> dict:
        """What the human reviews before deciding. Send its content_sha256 back with the decision."""
        with db.connect() as conn:
            if not conn.execute("SELECT 1 FROM approvals WHERE id = ?", (approval_id,)).fetchone():
                raise HTTPException(404, f"No approval {approval_id}")
            row = db.approval_row(conn, approval_id)
            row["checking_balance_now"] = db.cash_balance(conn)
            row["payments_waiting_for_approval"] = db.pending_payment_total(conn)
        return row

    @app.post("/api/approvals/{approval_id}/decision", tags=["approvals"])
    def decide(approval_id: int, body: Decision) -> dict:
        with db.connect() as conn:
            row = conn.execute("SELECT content_sha256, status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"No approval {approval_id}")
        if row["content_sha256"] != body.seen_sha256:
            raise HTTPException(409, "This approval isn't the version you viewed. Reload it and review again.")
        try:
            result = approvals.decide(approval_id, body.approve, body.decided_by, body.note)
        except approvals.ApprovalError as exc:
            raise HTTPException(409, str(exc))
        audit.append({"kind": "human_decision", "agent": "human", "approval_id": approval_id,
                      "approve": body.approve, "decided_by": body.decided_by, "note": body.note,
                      "outcome": result["outcome"]})
        with db.connect() as conn:
            result["checking_balance"] = db.cash_balance(conn)
        return result

    # Customer messages (drafts for the human to review) ---------------------------------
    @app.get("/api/messages", tags=["messages"])
    def messages(ticket_id: int | None = None, status: str | None = None) -> dict:
        out = customer_service.list_customer_messages(ticket_id=ticket_id, status=status)
        for m in out["messages"]:
            m["sealed_ok"] = customer_service.verify_customer_message(m["id"])
        return out

    # Cash ------------------------------------------------------------------------------
    @app.get("/api/cash", tags=["cash"])
    def cash() -> dict:
        with db.connect() as conn:
            accounts = [dict(r) for r in conn.execute("SELECT name, balance, date FROM cash_accounts")]
            pending = db.pending_payment_total(conn)
            checking = db.cash_balance(conn)
        return {"checking_balance": checking, "accounts": accounts,
                "payments_waiting_for_approval": pending,
                "checking_after_pending": round(checking - pending, 2)}

    # Reset -----------------------------------------------------------------------------
    @app.post("/api/reset", tags=["system"])
    def reset(body: ResetRequest) -> dict:
        """Put the working database back to the original data for a fresh run. The current
        working database is backed up first; the audit trail is kept."""
        if running_tickets():
            raise HTTPException(409, "Agents are still working; wait for runs to finish before resetting.")
        target = db.db_path()
        backups = target.parent / "backups"
        backups.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup = backups / f"{target.stem}_{stamp}.db"
        shutil.copy2(target, backup)
        staging = target.with_suffix(".resetting")
        shutil.copy2(db.ORIGINAL_DB_PATH, staging)
        os.replace(staging, target)              # atomic swap
        db.ensure_ops_tables()
        jobs.clear()
        audit.append({"kind": "reset", "agent": "human", "database": target.name, "backup": backup.name,
                      "source": db.ORIGINAL_DB_PATH.name})
        _rebuild_monitor()
        with db.connect() as conn:
            balance = db.cash_balance(conn)
            open_count = conn.execute("SELECT COUNT(*) FROM tickets WHERE status != 'resolved'").fetchone()[0]
        return {"reset": True, "backup": str(backup.relative_to(target.parent)), "checking_balance": balance,
                "open_tickets": open_count}

    return app


app = create_app()
