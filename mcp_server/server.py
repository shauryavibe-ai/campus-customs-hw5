"""Campus Customs MCP server (FastMCP).

The shared doorway between the agent team (Boss, Inventory, Accounting,
Facilities, Customer service) and the shop database. Agents call these tools over
MCP (`tools/call`); no agent opens the database itself. Each tool is tagged with the
agent it belongs to, so each agent can be given only its own tools.

Run it:  .venv/bin/python -m mcp_server                      (stdio, all tools)
         .venv/bin/python -m mcp_server --agent inventory    (stdio, one agent's tools + shared)
         .venv/bin/python -m mcp_server --http               (HTTP on 127.0.0.1:8765/mcp)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastmcp import FastMCP

from mcp_server import accounting, boss, customer_service, facilities, inventory
from mcp_server.db import OPS_TABLE_OWNERS, TABLE_OWNERS, check_database, connect, ensure_ops_tables


@asynccontextmanager
async def lifespan(server: FastMCP):
    check_database()       # fail fast if the database is missing or incomplete
    ensure_ops_tables()    # approvals, purchase_orders, vendor_products, activity_log
    yield {}


mcp = FastMCP(
    name="campus-customs-ops",
    instructions=(
        "Shared data server for the Campus Customs operations agents. "
        "It reads and writes the shop database (data/campus_customs_new.db). "
        "Use the shop's business date (desk.date_today, returned by every tool) for date checks, "
        "not the real clock. Tools named prepare_* or request_* only queue a request for a human; "
        "nothing is paid, ordered or discounted until the human approves it on the dashboard."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

READ = {"readOnlyHint": True}
QUEUE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}

# Boss agent
mcp.tool(boss.list_tickets, tags={"boss"}, annotations=READ)
mcp.tool(boss.update_ticket, tags={"boss"}, annotations=WRITE)
mcp.tool(boss.recent_activity, tags={"boss"}, annotations=READ)

# Inventory agent
mcp.tool(inventory.check_stock, tags={"inventory"}, annotations=READ)
mcp.tool(inventory.find_shortfalls, tags={"inventory"}, annotations=READ)
mcp.tool(inventory.find_restock_vendor, tags={"inventory"}, annotations=READ)
mcp.tool(inventory.receive_purchase_order, tags={"inventory"}, annotations=WRITE)

# Accounting agent
mcp.tool(accounting.get_cash_position, tags={"accounting"}, annotations=READ)
mcp.tool(accounting.list_invoices, tags={"accounting"}, annotations=READ)
mcp.tool(accounting.check_margin, tags={"accounting"}, annotations=READ)
mcp.tool(accounting.prepare_invoice_payment, tags={"accounting"}, annotations=QUEUE)
mcp.tool(accounting.prepare_purchase_order, tags={"accounting"}, annotations=WRITE)
mcp.tool(accounting.request_discount_approval, tags={"accounting"}, annotations=QUEUE)

# Facilities agent
mcp.tool(facilities.list_leases, tags={"facilities"}, annotations=READ)
mcp.tool(facilities.check_rent_due, tags={"facilities"}, annotations=READ)
mcp.tool(facilities.verify_rent_notice, tags={"facilities"}, annotations=READ)
mcp.tool(facilities.prepare_rent_payment, tags={"facilities"}, annotations=QUEUE)
mcp.tool(facilities.add_lease_note, tags={"facilities"}, annotations=WRITE)

# Customer service agent
mcp.tool(customer_service.draft_customer_message, tags={"customer_service"}, annotations=WRITE)
mcp.tool(customer_service.list_customer_messages, tags={"customer_service"}, annotations=READ)

# Shared: every agent can read a ticket in full, raise a new internal ticket, and see
# what is waiting for the human
mcp.tool(boss.get_ticket, tags={"shared"}, annotations=READ)
mcp.tool(boss.create_ticket, tags={"shared"}, annotations=WRITE)
mcp.tool(accounting.list_approvals, tags={"shared"}, annotations=READ)

AGENTS = ("boss", "inventory", "accounting", "facilities", "customer_service")


def restrict_to_agent(agent: str) -> FastMCP:
    """Show only one agent's tools plus the shared tool and resources. Used when each
    agent runs as its own MCP server (see mcp.json). Changes this process's server."""
    if agent not in AGENTS:
        raise ValueError(f"Unknown agent {agent!r}; choose from {', '.join(AGENTS)}")
    return mcp.enable(tags={agent, "shared"}, only=True)


@mcp.resource(
    "campus://database",
    name="database_overview",
    tags={"shared"},
    description="Database path, the shop's business date, row counts for every table, and table owners.",
    mime_type="application/json",
)
def database_overview() -> dict:
    summary = check_database()
    summary["table_owners"] = {**TABLE_OWNERS, **OPS_TABLE_OWNERS}
    return summary


@mcp.resource(
    "campus://schema",
    name="database_schema",
    tags={"shared"},
    description="CREATE TABLE statement for every table in the shop database.",
    mime_type="application/json",
)
def database_schema() -> dict:
    with connect() as conn:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    return {r["name"]: r["sql"] for r in rows}
