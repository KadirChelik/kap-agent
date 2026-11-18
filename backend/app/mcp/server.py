"""
Thin MCP (Model Context Protocol) stdio JSON-RPC 2.0 server exposing KAP Agent's
three strict read-only tools (`search_disclosures`, `get_disclosure`, `run_sql`).

Run from backend/:
  python -m app.mcp.server
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from app.agent.sql_guard import SQLGuardError
from app.agent.tools import (
    execute_get_disclosure,
    execute_run_sql,
    execute_search_disclosures,
)
from app.config import get_settings
from app.schemas.agent import GetDisclosureInput, RunSqlInput, SearchDisclosuresInput

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_disclosures",
        "description": (
            "Search indexed BIST KAP disclosures using FTS5 full-text search and metadata "
            "filters (ticker, disclosure_type, date_from, date_to). Strictly read-only."
        ),
        "inputSchema": SearchDisclosuresInput.model_json_schema(),
    },
    {
        "name": "get_disclosure",
        "description": (
            "Fetch full disclosure metadata, Pydantic-extracted structured JSON, confidence score, "
            "source_url, and raw KAP text excerpt by disclosure_id."
        ),
        "inputSchema": GetDisclosureInput.model_json_schema(),
    },
    {
        "name": "run_sql",
        "description": (
            "Execute a single read-only SELECT SQL query over allowed KAP tables "
            "(disclosures, dividend_events, financial_reports, material_events). "
            "LIMIT <= 50 and 1500ms timeout are enforced automatically."
        ),
        "inputSchema": RunSqlInput.model_json_schema(),
    },
]


def _jsonrpc_result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _jsonrpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_mcp_request(
    request_msg: dict[str, Any],
    *,
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Process one MCP JSON-RPC 2.0 request and return response (or None for notifications)."""
    msg_id = request_msg.get("id")
    method = request_msg.get("method", "")
    params = request_msg.get("params") or {}
    settings = get_settings()
    resolved_db = Path(db_path) if db_path else settings.db_path

    # Notifications (no id) require no JSON-RPC response
    if msg_id is None and method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _jsonrpc_result(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "kap-agent-mcp", "version": "0.1.0"},
            },
        )

    if method == "tools/list":
        return _jsonrpc_result(msg_id, {"tools": MCP_TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}

        try:
            if tool_name == "search_disclosures":
                inp = SearchDisclosuresInput.model_validate(arguments)
                out: Any = execute_search_disclosures(
                    resolved_db, inp, timeout_ms=settings.sql_timeout_ms
                )
            elif tool_name == "get_disclosure":
                inp = GetDisclosureInput.model_validate(arguments)
                out = execute_get_disclosure(
                    resolved_db, inp, timeout_ms=settings.sql_timeout_ms
                )
                if out is None:
                    return _jsonrpc_result(
                        msg_id,
                        {
                            "content": [{"type": "text", "text": f"Disclosure not found: {inp.disclosure_id}"}],
                            "isError": True,
                        },
                    )
            elif tool_name == "run_sql":
                inp = RunSqlInput.model_validate(arguments)
                out = execute_run_sql(
                    resolved_db,
                    inp,
                    timeout_ms=settings.sql_timeout_ms,
                    max_limit=settings.sql_max_limit,
                )
            else:
                return _jsonrpc_error(msg_id, -32601, f"Unknown tool: {tool_name}")

            return _jsonrpc_result(
                msg_id,
                {
                    "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                    "isError": False,
                },
            )
        except (ValidationError, SQLGuardError, sqlite3.Error, FileNotFoundError) as exc:
            return _jsonrpc_result(
                msg_id,
                {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True,
                },
            )

    if msg_id is None:
        return None
    return _jsonrpc_error(msg_id, -32601, f"Unsupported method: {method}")


def serve_stdio(*, db_path: Optional[Path] = None) -> int:
    """Run line-delimited JSON-RPC 2.0 MCP server over stdin/stdout."""
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps(_jsonrpc_error(None, -32700, f"Parse error: {exc}")) + "\n")
            sys.stdout.flush()
            continue

        resp = handle_mcp_request(msg, db_path=db_path)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve_stdio())
