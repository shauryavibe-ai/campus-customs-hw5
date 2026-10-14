"""The Campus Customs agent team: Boss, Inventory, Accounting, Facilities and
Customer Service, fully connected.

- Each agent gets its own MCP server from mcp.json (`--agent <name>`), so it can only
  call its own tools plus the shared ones.
- Every agent also has `ask_agent`, which runs any other agent on a task and returns
  its answer. That is the full connectivity: any agent can delegate to any other.
- Guard rails on delegation: no asking yourself, no asking an agent that is already
  waiting on you in the same chain (A -> B -> A ping-pong), a maximum chain length, and
  a budget of hand-offs per ticket, so agents can't loop forever. Any agent can still
  start a hand-off to any other agent.
- Every tool call and hand-off is recorded in order (RunState.events), and hand-offs
  are also written to the `agent_messages` table for the dashboard.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from fastmcp.client.transports import StdioTransport
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.output import ToolOutput
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from agent_team import audit
from mcp_server import db

HW5 = Path(__file__).resolve().parent.parent
PROMPTS = HW5 / "backend" / "prompts"   # one complete prompt file per agent; edit there
MCP_CONFIG = HW5 / "mcp.json"

AgentName = Literal["boss", "inventory", "accounting", "facilities", "customer_service"]
AGENT_NAMES: tuple[str, ...] = ("boss", "inventory", "accounting", "facilities", "customer_service")

# Each agent's job, in the assignment's words (shown to the Boss in team_roster).
JOBS = {
    "boss": "reads each ticket, decides who should work on it, and makes final calls",
    "inventory": "checks stock by SKU and size, spots shortfalls, and figures out which vendor can restock",
    "accounting": "watches cash and invoices, checks margins, and prepares payments or purchase orders for human approval",
    "facilities": "handles the shop space side (leases, rent, etc.)",
    "customer_service": "drafts messages for customers",
}

SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PORTKEY", re.I)

MAX_CHAIN = 4              # boss -> A -> B -> C, no deeper
MAX_HANDOFFS_PER_RUN = 12  # hand-offs allowed while handling one ticket
BOSS_LIMITS = UsageLimits(request_limit=20, tool_calls_limit=30)
AGENT_LIMITS = UsageLimits(request_limit=12, tool_calls_limit=20)
MODEL_SETTINGS = {"max_tokens": 4000}


# --- Outputs ---------------------------------------------------------------------------

TicketStatus = Literal["open", "in_progress", "waiting_on_human", "resolved"]


class Assignment(BaseModel):
    """One piece of work the Boss gave to an agent."""
    agent: AgentName
    task: str = Field(description="What the agent was asked to do.")
    why: str = Field(description="The capability (tools / role) that made this agent the right one.")


class TicketOutcome(BaseModel):
    """The Boss's final call on one ticket."""
    ticket_id: int
    understanding: str = Field(description="What the ticket is asking, in the Boss's own words: who wants what, "
                                           "how much, linked records, deadline, and what a good outcome looks like.")
    assignments: list[Assignment] = Field(description="Who the Boss gave which task to, and why.")
    lead_agent: AgentName
    agents_consulted: list[AgentName] = Field(description="Every agent asked for help on this ticket.")
    final_status: TicketStatus = Field(description="Must match the status set with update_ticket.")
    decision: str = Field(description="The Boss's final call in words, for the human: found, done, decided, "
                                      "recommended. The Boss alone makes this call.")
    pending_approvals: list[str] = Field(
        default_factory=list, description="Each request waiting for the human, e.g. 'Approval #1: pay $840 to ...'.")
    customer_message_ids: list[int] = Field(default_factory=list, description="Draft message ids for this ticket.")


class DayReview(BaseModel):
    """The Boss's final review across all tickets handled in one run."""
    summary: str = Field(description="What happened across all tickets, in a few sentences.")
    cash_check: str = Field(description="Checking balance, total waiting for approval, and what would be left.")
    recommendations: list[str] = Field(
        description="One line per pending approval: approve or hold, and why. Mention conflicts between tickets.")


# --- Run state and dependencies --------------------------------------------------------

def _short(value: Any, limit: int = 600) -> Any:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + f"... ({len(text)} chars)"


@dataclass
class RunState:
    """Everything that happens while the team handles one ticket (or one review)."""
    run_id: str
    ticket_id: int | None
    events: list[dict] = field(default_factory=list)
    handoffs: int = 0
    started: float = field(default_factory=time.perf_counter)
    usage: dict[str, int] = field(default_factory=lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0})
    listener: Callable[[dict], None] | None = None   # e.g. print live, or push to the dashboard
    session_id: str | None = None

    def event(self, agent: str, depth: int, kind: str, chain: tuple[str, ...] | None = None,
              full: dict | None = None, **data: Any) -> None:
        """Record one step. It goes to the audit trail first (with `full`, untruncated
        details), so if the trail can't be written the step fails."""
        audit.append({"session_id": self.session_id, "run_id": self.run_id, "ticket_id": self.ticket_id,
                      "agent": agent, "depth": depth, "chain": list(chain) if chain else [agent],
                      "kind": kind, **data, **(full or {})})
        e = {"seq": len(self.events) + 1, "t": round(time.perf_counter() - self.started, 2),
             "run_id": self.run_id, "ticket_id": self.ticket_id, "agent": agent, "depth": depth, "kind": kind, **data}
        self.events.append(e)
        if self.listener:
            self.listener(e)

    def add_usage(self, usage: Any) -> None:
        self.usage["requests"] += usage.requests or 0
        self.usage["input_tokens"] += usage.input_tokens or 0
        self.usage["output_tokens"] += usage.output_tokens or 0


@dataclass
class TeamDeps:
    team: "Team"
    run: RunState
    agent: str
    chain: tuple[str, ...]   # who asked whom to get here, e.g. ("boss", "inventory")

    @property
    def depth(self) -> int:
        return len(self.chain) - 1


# --- The delegation tool (given to every agent) -----------------------------------------

async def ask_agent(ctx: RunContext[TeamDeps], agent: AgentName, task: str) -> str:
    """Ask another agent on the team to do something or answer a question, and wait for
    their reply. boss: tickets and final calls. inventory: stock by SKU/size, shortfalls,
    restock vendors. accounting: cash, invoices, margins, payments / purchase orders /
    discounts for human approval. facilities: leases, rent, rent notices.
    customer_service: draft messages to customers. Give one specific task with the
    ticket id and exact SKUs, sizes, quantities and amounts."""
    return await ctx.deps.team.delegate(ctx.deps, agent, task)


def finalize_ticket(ctx: RunContext[TeamDeps], outcome: TicketOutcome) -> TicketOutcome:
    """Return the final outcome for the ticket. Call update_ticket first: the status here
    must match the ticket's status in the database."""
    with db.connect() as conn:
        row = conn.execute("SELECT status FROM tickets WHERE id = ?", (outcome.ticket_id,)).fetchone()
    if row is None:
        raise ModelRetry(f"Ticket {outcome.ticket_id} doesn't exist.")
    if row["status"] != outcome.final_status:
        raise ModelRetry(f"Ticket {outcome.ticket_id} is '{row['status']}' in the database. Call update_ticket "
                         f"with status '{outcome.final_status}' and your decision before returning the outcome.")
    return outcome


async def team_roster(ctx: RunContext[TeamDeps]) -> dict:
    """Who can do what, and who has room right now. For each agent: its job, the tools it
    has (capability), how many tasks it is already working on across all tickets, and how
    many hand-offs are left for this ticket (capacity). Use it to choose who works on a ticket."""
    return ctx.deps.team.roster(ctx.deps)


# --- The team -------------------------------------------------------------------------

def _load(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


class Team:
    """Five agents, each on its own MCP server, all able to delegate to each other.

    Use as `async with Team() as team:` so the MCP servers start once and are reused.
    `models` overrides the language model (one for all agents, or one per agent);
    by default every agent uses gpt-6-luna via Portkey.
    """

    def __init__(self, models: Model | dict[str, Model] | None = None, config_path: Path = MCP_CONFIG,
                 on_event: Callable[[dict], None] | None = None):
        self._models = models
        self._on_event = on_event
        self._config = json.loads(Path(config_path).read_text())["mcpServers"]
        self.agents: dict[str, Agent[TeamDeps, Any]] = {name: self._build_agent(name) for name in AGENT_NAMES}
        self._stack: AsyncExitStack | None = None
        self.session_id: str | None = None

    # Construction -------------------------------------------------------------------

    def _toolset(self, name: str) -> MCPToolset:
        server = self._config[f"campus-{name.replace('_', '-')}"]
        env = self.server_env(server)
        transport = StdioTransport(command=server["command"], args=server["args"], env=env,
                                   cwd=server.get("cwd") or str(HW5))
        return MCPToolset(transport, id=name, max_retries=2, process_tool_call=self._record_tool_call)

    @staticmethod
    def server_env(server: dict) -> dict[str, str]:
        """Least privilege: MCP servers get CAMPUS_DB_PATH etc. but never keys, tokens or secrets."""
        return {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)} | server.get("env", {})

    def _build_agent(self, name: str) -> Agent[TeamDeps, Any]:
        agent = Agent(
            None,
            name=name,
            deps_type=TeamDeps,
            output_type=str,
            instructions=_load(name),
            toolsets=[self._toolset(name)],
            tools=[ask_agent, team_roster] if name == "boss" else [ask_agent],
            retries=2,
            model_settings=MODEL_SETTINGS,
            defer_model_check=True,
        )

        @agent.instructions
        def who_am_i(ctx: RunContext[TeamDeps]) -> str:
            d = ctx.deps
            left = MAX_CHAIN - len(d.chain)
            lines = [f"You are the **{d.agent}** agent.",
                     f"Hand-off chain so far: {' -> '.join(d.chain)}."]
            if d.run.ticket_id is not None:
                lines.append(f"This work is for ticket {d.run.ticket_id}.")
            lines.append(f"You can hand off {left} more level(s) deep." if left > 0
                         else "You can't hand off any further: answer with your own tools.")
            if d.agent == "boss" and d.depth == 0:
                lines.append(d.team.roster_text(d))
            return "\n".join(lines)

        return agent

    def model_for(self, name: str) -> Model:
        if isinstance(self._models, dict):
            return self._models[name]
        if self._models is not None:
            return self._models
        from agent_team.model import build_model
        return build_model()

    async def __aenter__(self) -> "Team":
        db.ensure_ops_tables()
        await self._load_capabilities()
        self.session_id = f"session-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        with db.connect() as conn:
            today = db.business_date(conn).isoformat()
        audit.append({
            "session_id": self.session_id, "kind": "session_start", "agent": "system",
            "database": str(db.db_path()), "business_date": today,
            "models": {n: getattr(self.model_for(n), "model_name", str(self.model_for(n))) for n in AGENT_NAMES},
            "mcp_servers": {n: self._config[f"campus-{n.replace('_', '-')}"]["args"] for n in AGENT_NAMES},
            "prompt_sha256": audit.prompt_fingerprints(PROMPTS),
            "tools": {n: [t["tool"] for t in self.capabilities[n]] for n in AGENT_NAMES},
            "shared_tools": [t["tool"] for t in self.shared_tools] + ["ask_agent"],
            "limits": {"max_chain": MAX_CHAIN, "max_handoffs_per_run": MAX_HANDOFFS_PER_RUN,
                       "boss_requests": BOSS_LIMITS.request_limit, "agent_requests": AGENT_LIMITS.request_limit},
        })
        self._stack = AsyncExitStack()
        for agent in self.agents.values():
            await self._stack.enter_async_context(agent)  # starts that agent's MCP server once
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack:
            await self._stack.aclose()
        audit.append({"session_id": self.session_id, "kind": "session_end", "agent": "system",
                      "error": f"{exc[0].__name__}: {exc[1]}" if exc and exc[0] else None})

    # Capability and capacity --------------------------------------------------------

    async def _load_capabilities(self) -> None:
        """Read each agent's tools from the MCP server's tags, so the roster always matches
        what the agents can really do."""
        from mcp_server.server import mcp as server
        self.capabilities = {name: [] for name in AGENT_NAMES}
        self.shared_tools = []
        for tool in await server.list_tools():
            entry = {"tool": tool.name, "does": (tool.description or "").split(". ")[0].strip()}
            for tag in tool.tags:
                if tag == "shared":
                    self.shared_tools.append(entry)
                elif tag in self.capabilities:
                    self.capabilities[tag].append(entry)

    def roster(self, deps: TeamDeps) -> dict:
        with db.connect() as conn:
            busy = {r["to_agent"]: r["n"] for r in conn.execute(
                "SELECT to_agent, COUNT(*) AS n FROM agent_messages WHERE status = 'working' GROUP BY to_agent")}
        asked = {}
        for e in deps.run.events:
            if e["kind"] == "handoff":
                asked[e["to"]] = asked.get(e["to"], 0) + 1
        return {
            "decision_maker": "boss (you). Specialists check facts, queue requests and recommend; you decide.",
            "agents": [{
                "agent": name,
                "job": JOBS[name],
                "tools": self.capabilities.get(name, []),
                "can_approve_money": False,
                "tasks_in_progress_now": busy.get(name, 0),
                "asked_on_this_ticket": asked.get(name, 0),
            } for name in AGENT_NAMES if name != "boss"],
            "shared_tools_everyone_has": [t["tool"] for t in self.shared_tools] + ["ask_agent"],
            "capacity_for_this_ticket": {
                "handoffs_used": deps.run.handoffs,
                "handoffs_left": MAX_HANDOFFS_PER_RUN - deps.run.handoffs,
                "max_chain_depth": MAX_CHAIN,
            },
            "only_a_human_can": ["approve or reject payments, purchase orders and discounts", "send customer messages"],
        }

    def roster_text(self, deps: TeamDeps) -> str:
        """The roster as short text, put into the Boss's instructions at the start of each ticket."""
        r = self.roster(deps)
        lines = ["## Team roster right now (capability = tools, capacity = current load)"]
        for a in r["agents"]:
            lines.append(f"- **{a['agent']}**: {a['job']}. Tools: {', '.join(t['tool'] for t in a['tools'])}. "
                         f"Tasks in progress now (all tickets): {a['tasks_in_progress_now']}; "
                         f"asked on this ticket: {a['asked_on_this_ticket']}.")
        c = r["capacity_for_this_ticket"]
        lines.append(f"- Everyone also has: {', '.join(r['shared_tools_everyone_has'])}. No agent can approve money.")
        lines.append(f"- Hand-offs left for this ticket: {c['handoffs_left']} of {MAX_HANDOFFS_PER_RUN}. "
                     "Call team_roster for a fresh view of who is busy.")
        return "\n".join(lines)

    # Recording ------------------------------------------------------------------------

    async def _record_tool_call(self, ctx: RunContext[TeamDeps], call_tool, name: str, args: dict) -> Any:
        d = ctx.deps
        d.run.event(d.agent, d.depth, "tool_call", chain=d.chain, tool=name, args=args)
        try:
            result = await call_tool(name, args)
        except Exception as exc:
            d.run.event(d.agent, d.depth, "tool_error", chain=d.chain, full={"error": str(exc)},
                        tool=name, error=_short(str(exc), 300))
            raise
        d.run.event(d.agent, d.depth, "tool_result", chain=d.chain, full={"result": result},
                    tool=name, result=_short(result))
        return result

    @staticmethod
    def _open_message(run: RunState, frm: str, to: str, task: str, depth: int,
                      status: str = "working", response: str | None = None) -> int:
        with db.connect(read_only=False) as conn:
            cur = conn.execute(
                "INSERT INTO agent_messages (run_id, ticket_id, from_agent, to_agent, task, response, status,"
                " depth, created_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run.run_id, run.ticket_id, frm, to, task, response, status, depth, db.now_utc(),
                 db.now_utc() if status != "working" else None))
            return cur.lastrowid

    @staticmethod
    def _close_message(message_id: int, response: str, status: str) -> None:
        with db.connect(read_only=False) as conn:
            conn.execute("UPDATE agent_messages SET response = ?, status = ?, finished_at = ? WHERE id = ?",
                         (response, status, db.now_utc(), message_id))

    # Delegation -----------------------------------------------------------------------

    async def delegate(self, deps: TeamDeps, to: str, task: str) -> str:
        run, frm = deps.run, deps.agent
        task = task.strip()
        refusal = None
        if to not in self.agents:
            refusal = f"there is no '{to}' agent"
        elif to == frm:
            refusal = "you can't hand a task to yourself; use your own tools"
        elif not task:
            refusal = "the task is empty"
        elif to in deps.chain:
            refusal = (f"{to} is already waiting on this chain ({' -> '.join(deps.chain)}); "
                       "answer them with your own tools instead of asking back")
        elif len(deps.chain) >= MAX_CHAIN:
            refusal = f"the hand-off chain is already {len(deps.chain)} agents long; answer with what you have"
        elif run.handoffs >= MAX_HANDOFFS_PER_RUN:
            refusal = f"this ticket has used all {MAX_HANDOFFS_PER_RUN} hand-offs; answer with what you have"
        if refusal:
            self._open_message(run, frm, to, task, deps.depth + 1, status="refused", response=refusal)
            run.event(frm, deps.depth, "handoff_refused", chain=deps.chain, to=to, task=task, reason=refusal)
            return f"Refused: {refusal}."

        run.handoffs += 1
        message_id = self._open_message(run, frm, to, task, deps.depth + 1)
        run.event(frm, deps.depth, "handoff", chain=deps.chain, to=to, task=task, message_id=message_id)
        child = TeamDeps(team=self, run=run, agent=to, chain=deps.chain + (to,))
        prompt = (f"[Ticket {run.ticket_id}] " if run.ticket_id is not None else "") + \
            f"Request from the {frm} agent:\n{task}"
        try:
            result = await self.agents[to].run(prompt, deps=child, model=self.model_for(to),
                                               usage_limits=AGENT_LIMITS)
            run.add_usage(result.usage)
            answer, status = str(result.output), "done"
        except Exception as exc:  # the asking agent gets the failure as an answer and carries on
            answer, status = f"The {to} agent couldn't finish: {type(exc).__name__}: {exc}", "error"
        self._close_message(message_id, answer, status)
        run.event(to, child.depth, "handoff_reply", chain=child.chain, full={"answer": answer},
                  to=frm, status=status, answer=_short(answer, 1500),
                  message_id=message_id)
        return answer

    # Entry points ---------------------------------------------------------------------

    def _new_run(self, ticket_id: int | None) -> RunState:
        return RunState(run_id=f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}", ticket_id=ticket_id,
                        listener=self._on_event, session_id=self.session_id)

    @staticmethod
    def _report(run: RunState, output: Any, error: str | None) -> dict:
        elapsed = round(time.perf_counter() - run.started, 1)
        run.event("boss", 0, "run_end", usage=dict(run.usage), handoffs=run.handoffs, elapsed_s=elapsed,
                  error=error, ok=error is None)
        return {
            "run_id": run.run_id, "ticket_id": run.ticket_id,
            "output": output.model_dump() if isinstance(output, BaseModel) else output,
            "error": error, "handoffs": run.handoffs,
            "elapsed_s": round(time.perf_counter() - run.started, 1),
            "usage": run.usage, "events": run.events,
        }

    async def run_ticket(self, ticket_id: int) -> dict:
        """The Boss handles one ticket end to end and records the final call."""
        run = self._new_run(ticket_id)
        deps = TeamDeps(team=self, run=run, agent="boss", chain=("boss",))
        run.event("boss", 0, "start", task=f"Handle ticket {ticket_id}")
        output, error = None, None
        try:
            result = await self.agents["boss"].run(
                f"Handle ticket {ticket_id}.", deps=deps, model=self.model_for("boss"),
                output_type=ToolOutput(finalize_ticket, name="final_outcome"), usage_limits=BOSS_LIMITS)
            run.add_usage(result.usage)
            output = result.output
            run.event("boss", 0, "final", output=output.model_dump())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            run.event("boss", 0, "failed", error=error)
        return self._report(run, output, error)

    async def review(self, ticket_reports: list[dict]) -> dict:
        """The Boss's final calls across tickets: combined cash impact and what to approve."""
        run = self._new_run(None)
        deps = TeamDeps(team=self, run=run, agent="boss", chain=("boss",))
        outcomes = [r["output"] or {"ticket_id": r["ticket_id"], "error": r["error"]} for r in ticket_reports]
        prompt = ("Today's tickets have been handled. Their outcomes:\n"
                  f"{json.dumps(outcomes, indent=2)}\n\n"
                  "Make the final calls across all of them. Check list_approvals and ask accounting for the "
                  "current cash position, then tell the human what checking would be left with if everything "
                  "pending were approved, which approvals to approve first and which to hold, and why. "
                  "Point out any conflicts between tickets.")
        run.event("boss", 0, "start", full={"prompt": prompt}, task="Final review across tickets")
        output, error = None, None
        try:
            result = await self.agents["boss"].run(prompt, deps=deps, model=self.model_for("boss"),
                                                    output_type=DayReview, usage_limits=BOSS_LIMITS)
            run.add_usage(result.usage)
            output = result.output
            run.event("boss", 0, "final", output=output.model_dump())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            run.event("boss", 0, "failed", error=error)
        return self._report(run, output, error)

    async def run_open_tickets(self, ticket_ids: list[int] | None = None) -> dict:
        """Handle tickets in parallel (default: every open ticket), then the Boss's review."""
        if ticket_ids is None:
            with db.connect() as conn:
                ticket_ids = [r["id"] for r in conn.execute("SELECT id FROM tickets WHERE status = 'open' ORDER BY id")]
        reports = list(await asyncio.gather(*(self.run_ticket(t) for t in ticket_ids)))
        review = await self.review(reports)
        return {"tickets": reports, "review": review}
