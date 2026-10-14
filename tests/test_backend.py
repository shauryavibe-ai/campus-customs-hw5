"""The FastAPI backend, end to end with scripted fake models (no API calls), the real
MCP servers and a temporary database."""

import json
import time

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from agent_team import audit
from agent_team.team import AGENT_NAMES, Team
from backend.main import create_app
from mcp_server import db


def n_returns(messages):
    return sum(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", []))


def script(steps):
    def fn(messages, info):
        i = n_returns(messages)
        step = steps[min(i, len(steps) - 1)]
        step = step(info) if callable(step) else step
        if step[0] == "text":
            return ModelResponse(parts=[TextPart(step[1])])
        return ModelResponse(parts=[ToolCallPart(step[0], step[1])])
    return FunctionModel(fn)


def scripted_team() -> Team:
    outcome = {"ticket_id": 102, "understanding": "Landlord says rent is due.", "assignments": [],
               "lead_agent": "facilities", "agents_consulted": ["facilities"], "final_status": "waiting_on_human",
               "decision": "Rent notice verified; rent queued as approval #1."}
    echo = lambda n: FunctionModel(lambda m, i: ModelResponse(parts=[TextPart(n)]))
    return Team(models={n: echo(n) for n in AGENT_NAMES} | {
        "boss": script([("get_ticket", {"ticket_id": 102}),
                        ("ask_agent", {"agent": "facilities", "task": "Verify ticket 102 and queue the rent."}),
                        ("update_ticket", {"ticket_id": 102, "status": "waiting_on_human",
                                           "decision": outcome["decision"]}),
                        lambda info: (info.output_tools[0].name, outcome)]),
        "facilities": script([("verify_rent_notice", {"ticket_id": 102}),
                              ("prepare_rent_payment", {"lease_id": 1, "ticket_id": 102, "reason": "Verified."}),
                              ("text", "Verified; rent queued as approval #1.")]),
    })


@pytest.fixture
def client():
    with TestClient(create_app(team_factory=scripted_team)) as c:
        yield c


def wait_for(client, job_id, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        job = client.get(f"/api/runs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.2)
    raise AssertionError("run didn't finish")


def test_tickets_and_status(client):
    r = client.get("/api/tickets").json()
    assert [t["id"] for t in r["tickets"]] == [101, 102, 103] and r["open_count"] == 3
    assert client.get("/api/tickets/102/status").json() == {"ticket_id": 102, "status": "open", "is_open": True,
                                                            "running": False}
    assert client.get("/api/tickets/999/status").status_code == 404
    assert client.get("/api/tickets/101").json()["invoice"]["id"] == 501
    assert client.get("/api/health").json()["business_date"] == "2026-08-31"


def test_run_events_agents_approve_cash_and_reset(client):
    # Run the agents on a ticket (in the background), then watch.
    start = client.post("/api/tickets/102/run")
    assert start.status_code == 202 and start.json()["status"] == "running"
    job = wait_for(client, start.json()["job_id"])
    assert job["status"] == "done" and job["outcome"]["final_status"] == "waiting_on_human"
    assert client.get("/api/tickets/102/status").json()["status"] == "waiting_on_human"

    ev = client.get("/api/events", params={"ticket_id": 102}).json()
    tools = [e["tool"] for e in ev["events"] if e["kind"] == "tool_call"]
    assert "prepare_rent_payment" in tools and "verify_rent_notice" in tools
    assert client.get("/api/events", params={"after_seq": ev["last_seq"]}).json()["events"] == []  # nothing new

    agents = {a["agent"]: a for a in client.get("/api/agents", params={"ticket_id": 102}).json()["agents"]}
    assert agents["facilities"]["tools_used"] == {"verify_rent_notice": 1, "prepare_rent_payment": 1}
    assert agents["facilities"]["said"][-1]["text"] == "Verified; rent queued as approval #1."
    assert agents["boss"]["asked_others"][0]["to"] == "facilities"

    # A human views the approval, then approves it.
    pending = client.get("/api/approvals").json()["approvals"]
    assert [(a["kind"], a["amount"]) for a in pending] == [("rent_payment", 2400.0)]
    view = client.get(f"/api/approvals/{pending[0]['id']}").json()
    url = f"/api/approvals/{view['id']}/decision"
    stale = client.post(url, json={"approve": True, "decided_by": "Shaurya", "seen_sha256": "0" * 64})
    assert stale.status_code == 409 and "version you viewed" in stale.json()["detail"]
    bot = client.post(url, json={"approve": True, "decided_by": "accounting", "seen_sha256": view["content_sha256"]})
    assert bot.status_code == 409
    ok = client.post(url, json={"approve": True, "decided_by": "Shaurya", "seen_sha256": view["content_sha256"]})
    assert ok.status_code == 200 and ok.json()["checking_balance"] == 1000.0
    assert client.post(url, json={"approve": True, "decided_by": "Shaurya",
                                  "seen_sha256": view["content_sha256"]}).status_code == 409  # already decided
    assert client.get("/api/cash").json()["checking_balance"] == 1000.0

    # Reset to the original data for a fresh run.
    assert client.post("/api/reset", json={"confirm": "yes"}).status_code == 422
    r = client.post("/api/reset", json={"confirm": "RESET"}).json()
    assert r["reset"] and r["checking_balance"] == 3400.0 and r["open_tickets"] == 3
    assert (db.db_path().parent / r["backup"]).exists()
    assert client.get("/api/approvals").json()["approvals"] == []
    assert client.get("/api/tickets/102/status").json()["status"] == "open"
    kinds = [e["kind"] for e in json.loads(audit.audit_path().read_text())["entries"]]
    assert "human_decision" in kinds and "reset" in kinds and audit.verify()["ok"]   # trail kept, chain intact


def test_cannot_run_a_resolved_or_missing_ticket(client):
    with db.connect(read_only=False) as conn:
        conn.execute("UPDATE tickets SET status = 'resolved' WHERE id = 101")
    assert client.post("/api/tickets/101/run").status_code == 409
    assert client.post("/api/tickets/999/run").status_code == 404


def test_cors_only_for_the_local_frontend(client):
    ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):   # the Vite page, either host name
        pre = client.options("/api/approvals/1/decision", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})
        assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == origin
    blocked = client.options("/api/reset", headers={"Origin": "https://evil.example",
                                                    "Access-Control-Request-Method": "POST"})
    assert blocked.status_code == 400


def test_priority_expected_and_auto_resolve(client):
    pr = client.get("/api/priority").json()["priority"]
    assert [(p["ticket_id"], p["first_agent"]) for p in pr] == [(102, "facilities"), (101, "inventory"), (103, "inventory")]
    job = wait_for(client, client.post("/api/tickets/102/run").json()["job_id"])
    assert job["status"] == "done"
    exp = client.get("/api/tickets/102/expected").json()
    assert exp["first_agent"] == {"expected": "facilities", "why": exp["first_agent"]["why"], "actual": "facilities"}
    assert exp["score"]["done"] == exp["score"]["required"]
    view = client.get("/api/approvals/1").json()
    r = client.post("/api/approvals/1/decision",
                    json={"approve": True, "decided_by": "Shaurya", "seen_sha256": view["content_sha256"]}).json()
    assert r["tickets_resolved"] == [102]                     # last approval decided -> ticket resolved
    assert client.get("/api/tickets/102/status").json() == {"ticket_id": 102, "status": "resolved", "is_open": False,
                                                            "running": False}
    assert [p["ticket_id"] for p in client.get("/api/priority").json()["priority"]] == [101, 103]


def test_human_can_close_a_ticket_only_when_nothing_is_pending(client):
    job = wait_for(client, client.post("/api/tickets/102/run").json()["job_id"])
    assert job["status"] == "done"
    body = {"decided_by": "Shaurya", "note": "Handled outside the system."}
    r = client.post("/api/tickets/102/resolve", json=body)
    assert r.status_code == 409 and "Decide approval #1 first" in r.json()["detail"]
    assert client.post("/api/tickets/102/resolve", json={**body, "decided_by": "boss"}).status_code == 409
    with db.connect(read_only=False) as conn:          # an internal ticket with nothing to approve
        conn.execute("INSERT INTO tickets (type, requester, subject, status, notes, created_at)"
                     " VALUES ('cash_issue', 'boss agent', 'Low cash', 'waiting_on_human', 'x', '2026-08-31T12:00:00')")
    tid = max(t["id"] for t in client.get("/api/tickets").json()["tickets"])
    ok = client.post(f"/api/tickets/{tid}/resolve", json=body)
    assert ok.status_code == 200 and ok.json()["new_status"] == "resolved"
    assert client.post(f"/api/tickets/{tid}/resolve", json=body).status_code == 409   # already resolved
