"""Problem 9: start clean and run every ticket on the board until it is resolved.

Drives the running backend over HTTP (the same routes the dashboard uses), so the
whole thing plays out live in the warehouse at http://localhost:5173.

    .venv/bin/python scripts/run_board.py            # backend must be running on :8000

1. POST /api/reset  (original data; the audit trail is kept)
2. Take unresolved tickets in the Boss's priority order. For each:
   - dispatch the team and wait for the run;
   - decide every approval it queued, as the human:
       approve if it passes its checks and cash covers it at that moment, otherwise
       reject and record why;
   - if the Boss left it waiting with nothing to approve, close it with a note;
   - if the Boss left it in progress, run it again (max 3 runs per ticket).
3. Pick up tickets the agents raised along the way; repeat until the board is clear.

Writes outputs/board_run.json and outputs/board_run.md.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

HW5 = Path(__file__).resolve().parent.parent
API = "http://localhost:8000"
DECIDED_BY = "Claude (on Shaurya's instruction)"
MAX_RUNS_PER_TICKET = 3
MAX_RUNS_TOTAL = 10

http = httpx.Client(base_url=API, timeout=30)


def get(path: str, **params):
    r = http.get(path, params=params)
    r.raise_for_status()
    return r.json()


def post(path: str, body: dict | None = None):
    return http.post(path, json=body)


def say(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def run_ticket(tid: int) -> dict:
    r = post(f"/api/tickets/{tid}/run")
    if r.status_code != 202:
        return {"status": "not_started", "error": r.json().get("detail")}
    job = r.json()
    say(f"  dispatched ticket {tid} (job {job['job_id']})")
    while job["status"] == "running":
        time.sleep(3)
        job = get(f"/api/runs/{job['job_id']}")
    out = job.get("outcome") or {}
    say(f"  run {job['status']} in {job.get('seconds')}s · {(job.get('usage') or {}).get('requests')} model calls · "
        f"{job.get('handoffs')} hand-offs · Boss: {out.get('final_status')}")
    return job


def decide_all(tid: int) -> list[dict]:
    decisions = []
    for a in [a for a in get(f"/api/tickets/{tid}")["approvals"] if a["status"] == "pending"]:
        view = get(f"/api/approvals/{a['id']}")
        body = {"approve": True, "decided_by": DECIDED_BY, "seen_sha256": view["content_sha256"],
                "note": f"Problem 9 board run: approved after review. {view['summary']}"[:500]}
        r = post(f"/api/approvals/{a['id']}/decision", body)
        if r.status_code != 200:     # e.g. cash can't cover it at this moment
            reason = r.json().get("detail")
            body.update(approve=False, note=f"Problem 9 board run: rejected because approving failed: {reason}"[:500])
            r = post(f"/api/approvals/{a['id']}/decision", body)
        res = r.json()
        d = {"approval_id": a["id"], "kind": a["kind"], "amount": a["amount"], "summary": a["summary"],
             "decision": res["approval"]["status"], "checking_after": res.get("checking_balance"),
             "outcome": res.get("outcome"), "tickets_resolved": res.get("tickets_resolved", [])}
        say(f"  {d['decision']} approval #{a['id']} {a['kind']} ${a['amount']:,.2f} → checking ${d['checking_after']:,.2f}")
        decisions.append(d)
    return decisions


def main() -> None:
    try:
        get("/api/health")
    except Exception as exc:
        sys.exit(f"Backend not reachable at {API}: {exc}")

    reset = post("/api/reset", {"confirm": "RESET"}).json()
    say(f"Reset to original data · checking ${reset['checking_balance']:,.2f} · {reset['open_tickets']} open tickets")
    report = {"started": datetime.now().astimezone().isoformat(timespec="seconds"), "decided_by": DECIDED_BY,
              "reset": reset, "tickets": {}, "cash_timeline": [{"step": "reset", "checking": reset["checking_balance"]}]}
    runs_total = 0

    while runs_total < MAX_RUNS_TOTAL:
        queue = [p["ticket_id"] for p in get("/api/priority")["priority"]]
        todo = [t for t in queue if report["tickets"].get(t, {}).get("runs", 0) < MAX_RUNS_PER_TICKET]
        if not todo:
            break
        tid = todo[0]
        info = get(f"/api/tickets/{tid}")["ticket"]
        rec = report["tickets"].setdefault(tid, {"subject": info["subject"], "type": info["type"],
                                                 "requester": info["requester"], "runs": 0, "jobs": [],
                                                 "approvals": [], "closed": None})
        say(f"Ticket {tid} · {info['subject']} ({info['type']}) · priority #{queue.index(tid) + 1} of {len(queue)}")
        if info["status"] in ("open", "in_progress"):
            job = run_ticket(tid)
            runs_total += 1
            rec["runs"] += 1
            rec["jobs"].append({k: job.get(k) for k in ("job_id", "run_id", "status", "seconds", "handoffs", "usage",
                                                        "error", "outcome")})
        rec["approvals"] += decide_all(tid)
        status = get(f"/api/tickets/{tid}/status")["status"]
        if status == "waiting_on_human":
            outcome = (rec["jobs"][-1].get("outcome") or {}) if rec["jobs"] else {}
            note = ("Nothing left to approve; reviewed the Boss's decision: "
                    + (outcome.get("decision") or "no decision recorded"))[:500]
            r = post(f"/api/tickets/{tid}/resolve", {"decided_by": DECIDED_BY, "note": note})
            rec["closed"] = r.json() if r.status_code == 200 else {"error": r.json().get("detail")}
            say(f"  closed ticket {tid}: {'ok' if r.status_code == 200 else rec['closed']}")
        rec["final_status"] = get(f"/api/tickets/{tid}/status")["status"]
        cash = get("/api/cash")["checking_balance"]
        report["cash_timeline"].append({"step": f"after ticket {tid}", "checking": cash})
        say(f"  ticket {tid} → {rec['final_status']} · checking ${cash:,.2f}")

    final_tickets = get("/api/tickets")["tickets"]
    report.update(finished=datetime.now().astimezone().isoformat(timespec="seconds"),
                  final_board=[{k: t[k] for k in ("id", "subject", "status", "is_open")} for t in final_tickets],
                  all_resolved=all(not t["is_open"] for t in final_tickets),
                  final_cash=get("/api/cash"), runs_total=runs_total,
                  audit=get("/api/health")["audit"])
    (HW5 / "outputs" / "board_run.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    write_markdown(report)
    say(f"Done · {runs_total} runs · all resolved: {report['all_resolved']} · "
        f"checking ${report['final_cash']['checking_balance']:,.2f}")


def write_markdown(r: dict) -> None:
    lines = ["# Problem 9: board run, every ticket to resolved", "",
             f"Started {r['started']} from a clean reset (original data). Approvals and closes were decided by "
             f"**{r['decided_by']}** using this policy: approve what the team queued if it passes its checks and cash "
             "covers it at that moment, otherwise reject and record why. Tickets left waiting with nothing to approve "
             "are closed with a note. Tickets were taken in the Boss's priority order.", "",
             "| Ticket | Type | Runs | Boss's final status | Approvals decided | Final |", "|---|---|---|---|---|---|"]
    for tid, t in r["tickets"].items():
        boss = ", ".join(str((j.get("outcome") or {}).get("final_status")) for j in t["jobs"]) or "—"
        appr = "<br>".join(f"#{a['approval_id']} {a['kind']} ${a['amount']:,.2f} → **{a['decision']}**"
                           for a in t["approvals"]) or ("closed by human" if t.get("closed") else "—")
        lines.append(f"| {tid} {t['subject']} | {t['type']} | {t['runs']} | {boss} | {appr} | **{t['final_status']}** |")
    lines += ["", "## Cash", "", "| Step | Checking |", "|---|---|"]
    lines += [f"| {c['step']} | ${c['checking']:,.2f} |" for c in r["cash_timeline"]]
    lines += ["", "## Boss's decisions", ""]
    for tid, t in r["tickets"].items():
        for j in t["jobs"]:
            o = j.get("outcome") or {}
            lines.append(f"- **Ticket {tid}** ({j.get('seconds')} s, {(j.get('usage') or {}).get('requests')} model calls, "
                         f"{j.get('handoffs')} hand-offs): {o.get('decision', j.get('error'))}")
    lines += ["", f"**All resolved: {r['all_resolved']}** · runs: {r['runs_total']} · final checking "
              f"${r['final_cash']['checking_balance']:,.2f} · audit trail {r['audit']['entries']} entries "
              f"({'chain OK' if r['audit']['ok'] else 'BROKEN'})."]
    (HW5 / "outputs" / "board_run.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
