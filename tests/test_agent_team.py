"""The agent team, run with scripted fake models (no API calls) against the real MCP
servers from mcp.json and a temporary copy of the database."""

import itertools
import json

import pytest
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent_team.team import AGENT_NAMES, MAX_CHAIN, MAX_HANDOFFS_PER_RUN, RunState, Team, TeamDeps
from mcp_server import db


def returns(messages) -> list[ToolReturnPart]:
    return [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]


def echo(name: str) -> FunctionModel:
    """An agent that just answers with its own name."""
    return FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart(f"{name} here")]))


def script(steps):
    """An agent that makes the given tool calls one by one, then answers. A step is
    (tool_name, args), or a callable(info) -> step for the final structured output."""
    def fn(messages, info: AgentInfo):
        i = len(returns(messages))
        if i < len(steps):
            step = steps[i](info) if callable(steps[i]) else steps[i]
            if step[0] == "text":
                return ModelResponse(parts=[TextPart(step[1])])
            return ModelResponse(parts=[ToolCallPart(step[0], step[1])])
        return ModelResponse(parts=[TextPart("done")])
    return FunctionModel(fn)


async def test_every_agent_can_reach_every_other_agent():
    team = Team(models={n: echo(n) for n in AGENT_NAMES})
    async with team:
        for frm, to in itertools.permutations(AGENT_NAMES, 2):
            run = RunState(run_id=f"{frm}-{to}", ticket_id=None)  # each pair is its own run
            deps = TeamDeps(team=team, run=run, agent=frm, chain=(frm,))
            assert await team.delegate(deps, to, "say hi") == f"{to} here"
    assert len(list(itertools.permutations(AGENT_NAMES, 2))) == 20
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_messages WHERE status = 'done'").fetchone()[0] == 20


async def test_delegation_guard_rails():
    team = Team(models={n: echo(n) for n in AGENT_NAMES})
    async with team:
        run = RunState(run_id="t", ticket_id=101)
        boss = TeamDeps(team=team, run=run, agent="boss", chain=("boss",))
        assert (await team.delegate(boss, "boss", "x")).startswith("Refused: you can't hand a task to yourself")
        assert (await team.delegate(boss, "janitor", "x")).startswith("Refused: there is no")
        mid = TeamDeps(team=team, run=run, agent="inventory", chain=("boss", "inventory"))
        assert "already waiting on this chain" in await team.delegate(mid, "boss", "x")
        assert await team.delegate(mid, "accounting", "x") == "accounting here"
        deep = TeamDeps(team=team, run=run, agent="facilities",
                        chain=("boss", "inventory", "accounting", "facilities"))
        assert len(deep.chain) == MAX_CHAIN
        assert "chain is already" in await team.delegate(deep, "customer_service", "x")
        run.handoffs = MAX_HANDOFFS_PER_RUN
        assert "used all" in await team.delegate(boss, "inventory", "x")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_messages WHERE status = 'refused'").fetchone()[0] == 5


async def test_ticket_run_end_to_end():
    """Boss -> inventory (check_stock) -> accounting (list_invoices), then the Boss
    records its final call and returns a TicketOutcome."""
    outcome = {"ticket_id": 101, "understanding": "Tauhid Zaman wants 1 Bulldog tee, size S.",
               "assignments": [{"agent": "inventory", "task": "Can we fill it?", "why": "has check_stock"}],
               "lead_agent": "customer_service", "agents_consulted": ["inventory"],
               "final_status": "waiting_on_human", "decision": "Tee S is out; reprint waits on invoice 501.",
               "pending_approvals": [], "customer_message_ids": []}
    models = {
        "boss": script([
            ("get_ticket", {"ticket_id": 101}),
            ("team_roster", {}),
            ("ask_agent", {"agent": "inventory", "task": "Can we fill 1 x CC-TEE-WHITE size S?"}),
            ("update_ticket", {"ticket_id": 101, "status": "waiting_on_human", "decision": outcome["decision"]}),
            lambda info: (info.output_tools[0].name, outcome),
        ]),
        "inventory": script([
            ("check_stock", {"sku": "CC-TEE-WHITE", "size": "S", "qty_needed": 1}),
            ("ask_agent", {"agent": "accounting", "task": "Is invoice 501 paid?"}),
            ("text", "0 on hand, short 1. Reprint is invoice 501, still open."),
        ]),
        "accounting": script([("list_invoices", {}), ("text", "Invoice 501: $840 open, 3 days overdue.")]),
        "facilities": echo("facilities"),
        "customer_service": echo("customer_service"),
    }
    events = []
    async with Team(models=models, on_event=events.append) as team:
        report = await team.run_ticket(101)

    assert report["error"] is None
    assert report["output"]["final_status"] == "waiting_on_human"
    assert report["handoffs"] == 2
    calls = [(e["agent"], e["depth"], e["tool"]) for e in report["events"] if e["kind"] == "tool_call"]
    assert report["output"]["assignments"][0]["agent"] == "inventory"
    assert calls == [("boss", 0, "get_ticket"), ("inventory", 1, "check_stock"), ("accounting", 2, "list_invoices"),
                     ("boss", 0, "update_ticket")]
    stock = next(e for e in report["events"] if e["kind"] == "tool_result" and e["tool"] == "check_stock")
    assert '"short_by": 1' in stock["result"] or '"short_by":1' in stock["result"]
    assert events == report["events"]  # the live listener saw every event, in order
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM tickets WHERE id = 101").fetchone()[0] == "waiting_on_human"
        hops = [(r["from_agent"], r["to_agent"], r["depth"], r["status"]) for r in conn.execute(
            "SELECT * FROM agent_messages ORDER BY id")]
    assert hops == [("boss", "inventory", 1, "done"), ("inventory", "accounting", 2, "done")]


async def test_boss_must_record_its_final_call():
    """If the Boss returns an outcome without calling update_ticket, it is sent back to do it."""
    outcome = {"ticket_id": 102, "understanding": "Rent notice.", "assignments": [], "lead_agent": "facilities", "agents_consulted": [], "final_status": "resolved",
               "decision": "x", "pending_approvals": [], "customer_message_ids": []}
    seen = []

    def boss(messages, info):
        seen.append(len(messages))
        n = len([p for m in messages for p in getattr(m, "parts", [])
                 if isinstance(p, (ToolReturnPart, RetryPromptPart))])
        if n == 0:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, outcome)])  # skips update_ticket
        if n == 1:
            return ModelResponse(parts=[ToolCallPart("update_ticket", {"ticket_id": 102, "status": "resolved",
                                                                         "decision": "x"})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, outcome)])

    models = {n: echo(n) for n in AGENT_NAMES} | {"boss": FunctionModel(boss)}
    async with Team(models=models) as team:
        report = await team.run_ticket(102)
    assert report["error"] is None and report["output"]["final_status"] == "resolved"
    assert len(seen) == 3  # first answer rejected, then update_ticket, then accepted


def test_one_prompt_file_per_agent():
    from agent_team.team import PROMPTS
    assert sorted(p.stem for p in PROMPTS.glob("*.md")) == sorted(AGENT_NAMES)
    jobs = {"boss": "reads each ticket, decides who should work on it, and makes final calls",
            "inventory": "checks stock by SKU and size, spots shortfalls, and figures out which vendor can restock",
            "accounting": "watches cash and invoices, checks margins, and prepares payments or purchase orders",
            "facilities": "handles the shop space side (leases, rent, etc.)",
            "customer_service": "drafts messages for customers"}
    for name, job in jobs.items():
        text = (PROMPTS / f"{name}.md").read_text()
        assert job in text and "any agent may delegate tasks to any other agent" in text
        assert "## Team rules" in text


async def test_only_the_boss_can_decide_and_see_the_roster():
    """Capability boundaries: what each agent's model is actually offered."""
    offered = {}

    def capture(name):
        def fn(messages, info):
            offered[name] = {t.name for t in info.function_tools}
            return ModelResponse(parts=[TextPart("ok")])
        return FunctionModel(fn)

    async with Team(models={n: capture(n) for n in AGENT_NAMES}) as team:
        for name in AGENT_NAMES:
            await team.agents[name].run("hi", deps=TeamDeps(team=team, run=RunState(run_id="t", ticket_id=None),
                                                             agent=name, chain=(name,)), model=team.model_for(name))
    assert {"update_ticket", "team_roster", "list_tickets"} <= offered["boss"]
    for name in AGENT_NAMES[1:]:
        assert not {"update_ticket", "team_roster"} & offered[name], name
        assert {"ask_agent", "get_ticket", "list_approvals"} <= offered[name]
    assert "prepare_invoice_payment" in offered["accounting"] and "prepare_invoice_payment" not in offered["boss"]


async def test_roster_shows_capability_and_capacity():
    async with Team(models={n: echo(n) for n in AGENT_NAMES}) as team:
        run = RunState(run_id="t", ticket_id=103)
        boss = TeamDeps(team=team, run=run, agent="boss", chain=("boss",))
        await team.delegate(boss, "inventory", "check hoodies")
        roster = team.roster(boss)
    agents = {a["agent"]: a for a in roster["agents"]}
    assert set(agents) == {"inventory", "accounting", "facilities", "customer_service"}
    assert {t["tool"] for t in agents["inventory"]["tools"]} == {
        "check_stock", "find_shortfalls", "find_restock_vendor", "receive_purchase_order"}
    assert "verify_rent_notice" in {t["tool"] for t in agents["facilities"]["tools"]}
    assert agents["accounting"]["job"].startswith("watches cash and invoices")
    assert agents["inventory"]["asked_on_this_ticket"] == 1
    assert agents["inventory"]["tasks_in_progress_now"] == 0  # finished
    assert roster["capacity_for_this_ticket"]["handoffs_left"] == MAX_HANDOFFS_PER_RUN - 1
    assert not any(a["can_approve_money"] for a in agents.values())


async def test_inventory_gets_accounting_to_queue_the_purchase_order():
    """Boss -> Inventory (finds the 12 short and the vendor) -> Accounting (queues the PO for approval)."""
    po = {"vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 12, "ticket_id": 103,
          "reason": "Ticket 103 is 12 short; Bulldog Print Co restocks in 5 days."}
    models = {n: echo(n) for n in AGENT_NAMES} | {
        "inventory": script([
            ("check_stock", {"sku": "CC-HOOD-NAVY", "size": "M", "qty_needed": 20}),
            ("find_restock_vendor", {"sku": "CC-HOOD-NAVY", "size": "M", "qty": 12}),
            ("ask_agent", {"agent": "accounting", "task": f"Queue this purchase order for approval: {json.dumps(po)}"}),
            ("text", "Short 12. Accounting queued purchase order #1 (approval #1), pending human approval."),
        ]),
        "accounting": script([
            ("get_cash_position", {}),
            ("prepare_purchase_order", po),
            ("text", "Purchase order #1, approval #1, $264, arriving 2026-09-05."),
        ]),
    }
    async with Team(models=models) as team:
        run = RunState(run_id="t", ticket_id=103)
        answer = await team.delegate(TeamDeps(team=team, run=run, agent="boss", chain=("boss",)),
                                     "inventory", "Check stock for 20 x CC-HOOD-NAVY M and arrange any restock.")
    assert "purchase order #1" in answer
    with db.connect() as conn:
        order = conn.execute("SELECT * FROM purchase_orders").fetchone()
        approval = conn.execute("SELECT * FROM approvals").fetchone()
        hops = [(r["from_agent"], r["to_agent"]) for r in conn.execute("SELECT * FROM agent_messages ORDER BY id")]
    assert (order["qty"], order["total"], order["status"], order["ticket_id"]) == (12, 264.0, "pending_approval", 103)
    assert (approval["kind"], approval["requested_by"], approval["status"]) == ("purchase_order", "accounting", "pending")
    assert hops == [("boss", "inventory"), ("inventory", "accounting")]


async def test_boss_sees_roster_in_its_instructions():
    seen = {}

    def boss(messages, info):
        seen["instructions"] = info.instructions or ""
        return ModelResponse(parts=[TextPart("ok")])

    async with Team(models={n: echo(n) for n in AGENT_NAMES} | {"boss": FunctionModel(boss)}) as team:
        deps = TeamDeps(team=team, run=RunState(run_id="t", ticket_id=103), agent="boss", chain=("boss",))
        await team.agents["boss"].run("hi", deps=deps, model=team.model_for("boss"))
    text = seen["instructions"]
    assert "Team roster right now" in text
    assert "**inventory**: checks stock by SKU and size" in text and "find_restock_vendor" in text
    assert "Hand-offs left for this ticket: 12 of 12" in text
