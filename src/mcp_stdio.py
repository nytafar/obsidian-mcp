"""Entry point for stdio MCP transport.

Registry sandboxes (Glama et al.) test MCP servers via stdio, but
this server speaks Streamable HTTP in production. This module
re-exposes the same FastMCP instance over stdio so the test harness
can enumerate tools without spinning up FastAPI.

Tools register but cannot run — there's no DB connection, no indexer,
no vault mount. Registries only call `list_tools`, which is satisfied
by the decorator-time registration in `src.mcp_server.server`.

Production deployments use `src.main:app` over HTTP; this entry
exists solely so stdio-only harnesses have something to talk to.
"""
import logging

from src.mcp_server.server import mcp

logging.basicConfig(level=logging.WARNING)


if __name__ == "__main__":
    mcp.run("stdio")
