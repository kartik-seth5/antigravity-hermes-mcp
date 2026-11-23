"""Minimal zero-dependency stdio MCP (Model Context Protocol) server runtime.

Speaks JSON-RPC 2.0 over newline-delimited UTF-8 stdin/stdout (MCP 2024-11-05
and 2025-03-26 stdio transport). Designed for fast startup (<50ms) on Windows
so clients with tight discovery timeouts (e.g. Hermes mcp_discovery_timeout)
never time out.
"""

from __future__ import annotations

import json
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


PROTOCOL_VERSION = "2024-11-05"


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Any]


class StdioMCPServer:
    def __init__(self, name: str, version: str = "1.0.0", instructions: str = "") -> None:
        self.name = name
        self.version = version
        self.instructions = instructions
        self._tools: Dict[str, ToolSpec] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[[Dict[str, Any]], Any],
    ) -> None:
        self._tools[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )

    def _write_message(self, payload: Dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def _send_result(self, req_id: Any, result: Dict[str, Any]) -> None:
        self._write_message({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _send_error(self, req_id: Any, code: int, message: str, data: Optional[Any] = None) -> None:
        err: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._write_message({"jsonrpc": "2.0", "id": req_id, "error": err})

    def _handle_request(self, msg: Dict[str, Any]) -> None:
        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        # Notifications have no id and must not receive a response.
        is_notification = "id" not in msg or req_id is None

        if method == "initialize":
            client_version = params.get("protocolVersion") or PROTOCOL_VERSION
            result: Dict[str, Any] = {
                "protocolVersion": client_version,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": self.name,
                    "version": self.version,
                },
            }
            if self.instructions:
                result["instructions"] = self.instructions
            if not is_notification:
                self._send_result(req_id, result)
            return

        if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
            return

        if method == "ping":
            if not is_notification:
                self._send_result(req_id, {})
            return

        if method == "tools/list":
            tools_list: List[Dict[str, Any]] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                }
                for t in self._tools.values()
            ]
            if not is_notification:
                self._send_result(req_id, {"tools": tools_list})
            return

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments") or {}
            spec = self._tools.get(tool_name)
            if not spec:
                if not is_notification:
                    self._send_error(req_id, -32602, f"Unknown tool: {tool_name}")
                return
            try:
                raw_result = spec.handler(arguments)
                if isinstance(raw_result, str):
                    text = raw_result
                else:
                    text = json.dumps(raw_result, indent=2, ensure_ascii=False)
                is_error = isinstance(raw_result, dict) and raw_result.get("ok") is False
                if not is_notification:
                    self._send_result(
                        req_id,
                        {
                            "content": [{"type": "text", "text": text}],
                            "isError": is_error,
                        },
                    )
            except Exception as exc:
                tb = traceback.format_exc()
                sys.stderr.write(f"[{self.name}] tool '{tool_name}' failed: {tb}\n")
                sys.stderr.flush()
                if not is_notification:
                    self._send_result(
                        req_id,
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(
                                        {"ok": False, "error": str(exc)},
                                        indent=2,
                                        ensure_ascii=False,
                                    ),
                                }
                            ],
                            "isError": True,
                        },
                    )
            return

        if not is_notification:
            self._send_error(req_id, -32601, f"Method not found: {method}")

    def run(self) -> None:
        # Ensure UTF-8 stdio on Windows
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")

        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                sys.stderr.write(f"[{self.name}] invalid JSON line: {exc}\n")
                sys.stderr.flush()
                continue
            if isinstance(msg, dict):
                self._handle_request(msg)
