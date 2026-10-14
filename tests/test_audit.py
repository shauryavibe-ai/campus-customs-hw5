"""The append-only, hash-chained audit trail written as the agent team runs."""

import json

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from agent_team import audit
from agent_team.team import AGENT_NAMES, Team


def entries():
    return json.loads(audit.audit_path().read_text())["entries"]


def scripted_team():
    def boss(messages, info):
        n = sum(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", []))
        if n == 0:
            return ModelResponse(parts=[ToolCallPart("ask_agent", {"agent": "inventory", "task": "Stock for tee S?"})])
        if n == 1:
            return ModelResponse(parts=[ToolCallPart("update_ticket", {"ticket_id": 101, "status": "in_progress",
                                                                         "decision": "Checking the reprint."})])
        out = {"ticket_id": 101, "understanding": "1 tee S", "assignments": [], "lead_agent": "inventory",
               "agents_consulted": ["inventory"], "final_status": "in_progress", "decision": "Checking the reprint."}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out)])

    def inventory(messages, info):
        n = sum(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", []))
        if n == 0:
            return ModelResponse(parts=[ToolCallPart("check_stock", {"sku": "CC-TEE-WHITE", "size": "S"})])
        return ModelResponse(parts=[TextPart("Tee S: 0 on hand.")])

    echo = lambda n: FunctionModel(lambda m, i: ModelResponse(parts=[TextPart(n)]))
    return Team(models={n: echo(n) for n in AGENT_NAMES} | {"boss": FunctionModel(boss),
                                                            "inventory": FunctionModel(inventory)})


async def test_every_step_is_recorded_with_full_detail():
    async with scripted_team() as team:
        await team.run_ticket(101)
    kinds = [e["kind"] for e in entries()]
    assert kinds[0] == "session_start" and kinds[-1] == "session_end"
    assert {"start", "handoff", "tool_call", "tool_result", "handoff_reply", "final", "run_end"} <= set(kinds)
    start = entries()[0]
    assert set(start["prompt_sha256"]) == set(AGENT_NAMES) and "check_stock" in start["tools"]["inventory"]
    call = next(e for e in entries() if e["kind"] == "tool_call" and e["tool"] == "check_stock")
    assert call["agent"] == "inventory" and call["chain"] == ["boss", "inventory"]
    assert call["args"] == {"sku": "CC-TEE-WHITE", "size": "S"}
    result = next(e for e in entries() if e["kind"] == "tool_result" and e["tool"] == "check_stock")
    assert result["result"]["sizes"][0]["qty"] == 0          # full result, not a truncated string
    hop = next(e for e in entries() if e["kind"] == "handoff")
    assert (hop["agent"], hop["to"], hop["task"]) == ("boss", "inventory", "Stock for tee S?")
    reply = next(e for e in entries() if e["kind"] == "handoff_reply")
    assert reply["answer"] == "Tee S: 0 on hand."
    assert next(e for e in entries() if e["kind"] == "final")["output"]["decision"] == "Checking the reprint."
    assert audit.verify()["ok"]


async def test_trail_is_never_wiped_and_chain_continues():
    async with scripted_team() as team:
        await team.run_ticket(101)
    first = entries()
    async with scripted_team() as team:
        await team.run_ticket(101)
    both = entries()
    assert both[:len(first)] == first                       # earlier entries untouched
    assert [e["seq"] for e in both] == list(range(1, len(both) + 1))
    assert len({e["session_id"] for e in both}) == 2
    assert audit.verify() == {"ok": True, "entries": len(both), "problem": None}


def test_tampering_is_detected():
    for i in range(3):
        audit.append({"kind": "note", "agent": "test", "n": i})
    path = audit.audit_path()
    path.write_text(path.read_text().replace('"n": 1', '"n": 9'))
    v = audit.verify()
    assert not v["ok"] and "seq 2" in v["problem"]


def test_damaged_trail_is_left_alone_and_blocks_writes():
    audit.append({"kind": "note", "agent": "test"})
    path = audit.audit_path()
    damaged = path.read_text()[:-5]
    path.write_text(damaged)
    with pytest.raises(audit.AuditError, match="damaged"):
        audit.append({"kind": "note", "agent": "test"})
    assert path.read_text() == damaged                       # nothing overwritten


def test_emails_and_card_numbers_are_masked():
    e = audit.append({"kind": "note", "agent": "test", "text": "mail a@b.com card 4111111111111111 on 2026-08-31"})
    assert e["text"] == "mail [email] card [number] on 2026-08-31"
