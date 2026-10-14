"""Launch each server in mcp.json exactly as Claude does (same command, args and env,
started from an unrelated folder) and check each agent sees only its own tools."""

import json
import os
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

HW5 = Path(__file__).resolve().parent.parent
CONFIG = json.loads((HW5 / "mcp.json").read_text())["mcpServers"]

EXPECTED = {
    "campus-boss": ({"list_tickets", "update_ticket", "recent_activity"}, "list_tickets", {}),
    "campus-customer-service": ({"draft_customer_message", "list_customer_messages"},
                                "list_customer_messages", {}),
    "campus-inventory": ({"check_stock", "find_shortfalls", "find_restock_vendor", "receive_purchase_order"},
                         "find_shortfalls", {}),
    "campus-accounting": ({"get_cash_position", "list_invoices", "check_margin", "prepare_invoice_payment",
                           "prepare_purchase_order", "request_discount_approval"},
                          "get_cash_position", {}),
    "campus-facilities": ({"list_leases", "check_rent_due", "verify_rent_notice", "prepare_rent_payment",
                           "add_lease_note"},
                          "verify_rent_notice", {"ticket_id": 102}),
}


def test_config_covers_every_agent():
    assert set(CONFIG) == set(EXPECTED)
    for server in CONFIG.values():
        assert Path(server["command"]).is_file()


@pytest.mark.parametrize("name", sorted(EXPECTED))
async def test_server_starts_and_scopes_tools(name, temp_db, tmp_path):
    server = CONFIG[name]
    own_tools, tool, args = EXPECTED[name]
    env = {**os.environ, **server["env"], "CAMPUS_DB_PATH": str(temp_db)}
    transport = StdioTransport(command=server["command"], args=server["args"], env=env, cwd=str(tmp_path))
    async with Client(transport) as client:
        tools = {t.name for t in await client.list_tools()}
        assert tools == own_tools | {"get_ticket", "create_ticket", "list_approvals"}
        resources = {str(r.uri) for r in await client.list_resources()}
        assert resources == {"campus://database", "campus://schema"}
        result = await client.call_tool(tool, args)
        assert result.data["business_date"] == "2026-08-31"
