"""Checks that the MCP server starts, reads the database and exposes the agents' tools."""

import json
import sqlite3

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_server import db
from mcp_server.server import mcp

ALL_TABLES = set(db.TABLE_OWNERS) | set(db.OPS_TABLE_OWNERS)


async def read_json(uri: str) -> dict:
    async with Client(mcp) as client:
        contents = await client.read_resource(uri)
    return json.loads(contents[0].text)


async def test_lists_resources():
    async with Client(mcp) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
    assert uris == {"campus://database", "campus://schema"}


async def test_database_overview_reads_database():
    data = await read_json("campus://database")
    assert data["business_date"] == "2026-08-31"
    assert data["row_counts"]["tickets"] == 3
    assert set(data["table_owners"]) == ALL_TABLES


async def test_schema_has_all_tables():
    schema = await read_json("campus://schema")
    assert set(schema) == ALL_TABLES
    assert "approved_by" in schema["payments"]


async def test_tools_are_tagged_by_agent():
    async with Client(mcp) as client:
        tools = await client.list_tools()
    by_agent: dict[str, set[str]] = {}
    for t in tools:
        for tag in t.meta["fastmcp"]["tags"]:
            by_agent.setdefault(tag, set()).add(t.name)
    assert by_agent == {
        "boss": {"list_tickets", "update_ticket", "recent_activity"},
        "customer_service": {"draft_customer_message", "list_customer_messages"},
        "inventory": {"check_stock", "find_shortfalls", "find_restock_vendor", "receive_purchase_order"},
        "accounting": {"get_cash_position", "list_invoices", "check_margin", "prepare_invoice_payment",
                       "prepare_purchase_order", "request_discount_approval"},
        "facilities": {"list_leases", "check_rent_due", "verify_rent_notice", "prepare_rent_payment",
                       "add_lease_note"},
        "shared": {"get_ticket", "create_ticket", "list_approvals"},
    }
    # The human's approve step must never be callable by an agent.
    assert not any("decide" in t.name or t.name.startswith("approve") for t in tools)


async def test_call_tool_over_mcp():
    async with Client(mcp) as client:
        result = await client.call_tool("check_stock", {"sku": "CC-TEE-WHITE", "size": "S"})
        assert result.data["sizes"] == [{"size": "S", "qty": 0, "status": "out_of_stock"}]
        with pytest.raises(ToolError, match="Unknown SKU"):
            await client.call_tool("check_stock", {"sku": "NOPE"})


def test_foreign_keys_enforced_and_read_only():
    with db.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE desk SET notes = 'x'")


def test_refuses_original_database(monkeypatch):
    monkeypatch.setenv("CAMPUS_DB_PATH", str(db.ORIGINAL_DB_PATH))
    with pytest.raises(RuntimeError):
        db.db_path()


def test_missing_database_is_not_created(monkeypatch, tmp_path):
    missing = tmp_path / "nope.db"
    monkeypatch.setenv("CAMPUS_DB_PATH", str(missing))
    with pytest.raises(FileNotFoundError):
        db.db_path()
    assert not missing.exists()
