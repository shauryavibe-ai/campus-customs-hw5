"""Run the agent team.

    python -m agent_team                  every open ticket (in parallel), then the Boss's review
    python -m agent_team --ticket 102     one ticket (repeat --ticket for several)
    python -m agent_team --working-db     write to data/campus_customs_new.db itself

By default the team works on a fresh copy of the working database
(data/runs/team_run_<time>.db), so trying it out never changes the real data.
The full run (outcomes + every tool call and hand-off) is saved to
outputs/agent_runs/team_run_<time>.json and outputs/agent_team_run.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

HW5 = Path(__file__).resolve().parent.parent

ICONS = {"start": ">>", "handoff": "->", "handoff_reply": "<-", "handoff_refused": "x>", "tool_call": " *",
         "tool_error": " !", "final": "OK", "failed": "!!"}


def print_event(e: dict) -> None:
    if e["kind"] == "tool_result":
        return
    pad = "    " * e["depth"]
    who = f"[{e['ticket_id'] or 'review'}] {pad}{ICONS.get(e['kind'], '  ')} {e['agent']}"
    if e["kind"] == "tool_call":
        detail = f"{e['tool']}({json.dumps(e['args'])[:90]})"
    elif e["kind"] in ("handoff", "handoff_refused"):
        detail = f"asks {e['to']}: {e['task'][:110]}" + (f"  REFUSED: {e['reason']}" if e["kind"] == "handoff_refused" else "")
    elif e["kind"] == "handoff_reply":
        detail = f"replies to {e['to']} ({e['status']}): {e['answer'][:110]}"
    elif e["kind"] == "tool_error":
        detail = f"{e['tool']} error: {e['error'][:110]}"
    elif e["kind"] == "final":
        out = e["output"]
        detail = f"{out.get('final_status', '')} {out.get('decision', out.get('summary', ''))[:120]}"
    else:
        detail = e.get("task") or e.get("error", "")
    print(f"{who}: {detail}".replace("\n", " "), flush=True)


async def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m agent_team")
    parser.add_argument("--ticket", type=int, action="append", help="ticket id (repeatable); default: all open")
    parser.add_argument("--working-db", action="store_true", help="write to data/campus_customs_new.db")
    parser.add_argument("--no-review", action="store_true", help="skip the Boss's final review")
    args = parser.parse_args()

    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    working = HW5 / "data" / "campus_customs_new.db"
    if args.working_db:
        db_path = working
    else:
        db_path = HW5 / "data" / "runs" / f"team_run_{stamp}.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(working, db_path)
    os.environ["CAMPUS_DB_PATH"] = str(db_path)  # this process and every agent's MCP server use it

    from agent_team.model import MODEL_NAME
    from agent_team.team import Team

    print(f"Model: {MODEL_NAME} via Portkey | database: {db_path.relative_to(HW5)}", flush=True)
    async with Team(on_event=print_event) as team:
        if args.no_review:
            ids = args.ticket
            if ids is None:
                from mcp_server import db
                with db.connect() as conn:
                    ids = [r["id"] for r in conn.execute("SELECT id FROM tickets WHERE status = 'open' ORDER BY id")]
            result = {"tickets": list(await asyncio.gather(*(team.run_ticket(t) for t in ids))), "review": None}
        else:
            result = await team.run_open_tickets(args.ticket)

    report = {"run_at": stamp, "model": MODEL_NAME, "database": str(db_path.relative_to(HW5)), **result}
    out_dir = HW5 / "outputs" / "agent_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, default=str) + "\n"
    (out_dir / f"team_run_{stamp}.json").write_text(text)
    (HW5 / "outputs" / "agent_team_run.json").write_text(text)
    print(f"\nSaved outputs/agent_runs/team_run_{stamp}.json and outputs/agent_team_run.json")
    sys.path.insert(0, str(HW5 / "scripts"))
    from build_agent_monitor import build  # refresh the double-click monitor page after every run
    print(f"Updated {build().relative_to(HW5)}")
    for t in result["tickets"]:
        o = t["output"] or {}
        print(f"  ticket {t['ticket_id']}: {o.get('final_status', 'FAILED: ' + str(t['error']))}"
              f" | {t['handoffs']} hand-offs | {t['usage']['requests']} model calls | {t['elapsed_s']} s")


if __name__ == "__main__":
    asyncio.run(main())
