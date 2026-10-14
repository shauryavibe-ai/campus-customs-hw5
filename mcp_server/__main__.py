"""Start the MCP server.

    python -m mcp_server                     stdio, all tools
    python -m mcp_server --agent inventory   stdio, only that agent's tools + shared ones
    python -m mcp_server --http              HTTP on 127.0.0.1:8765/mcp
"""

import argparse
import os

from mcp_server.db import check_database, ensure_ops_tables
from mcp_server.server import AGENTS, mcp, restrict_to_agent

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m mcp_server")
    parser.add_argument("--agent", choices=AGENTS, help="serve only this agent's tools (plus shared ones)")
    parser.add_argument("--http", action="store_true", help="serve over HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    check_database()  # fail fast if the database is missing or incomplete
    ensure_ops_tables()
    if args.agent:
        restrict_to_agent(args.agent)
        os.environ["CAMPUS_AGENT"] = args.agent  # tools like create_ticket know which agent is calling
    if args.http:
        mcp.run(transport="http", host="127.0.0.1", port=args.port, show_banner=False)
    else:
        mcp.run(show_banner=False)  # stdout carries the MCP protocol, so no banner
