"""End-to-end stdio MCP protocol tests for both bridge servers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parent


def rpc_session(script_name: str, requests: List[Dict[str, Any]], extra_env: Dict[str, str] | None = None) -> List[Dict[str, Any]]:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / script_name)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    out, err = proc.communicate(payload, timeout=20)
    if proc.returncode != 0:
        raise AssertionError(f"{script_name} exited {proc.returncode}: {err}")
    responses = [json.loads(line) for line in out.splitlines() if line.strip()]
    return responses


def test_antigravity_server() -> None:
    resps = rpc_session(
        "antigravity_mcp_server.py",
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "health", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "list_jobs", "arguments": {}}},
        ],
    )
    assert len(resps) == 4, f"Expected 4 responses, got {len(resps)}: {resps}"
    assert resps[0]["result"]["serverInfo"]["name"] == "antigravity-bridge"
    tool_names = [t["name"] for t in resps[1]["result"]["tools"]]
    for expected in ("delegate", "start_async", "check_job", "list_jobs", "health"):
        assert expected in tool_names, f"Missing tool {expected} in {tool_names}"
    health_data = json.loads(resps[2]["result"]["content"][0]["text"])
    assert health_data["ok"] is True
    print(f"[PASS] antigravity_mcp_server: tools={tool_names}, agy_ready={health_data['ready']}, agy_bin={health_data['agy_bin']}")


def test_hermes_server() -> None:
    resps = rpc_session(
        "hermes_mcp_server.py",
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "health", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "list_sessions", "arguments": {"limit": 3}}},
        ],
    )
    assert len(resps) == 4, f"Expected 4 responses, got {len(resps)}: {resps}"
    assert resps[0]["result"]["serverInfo"]["name"] == "hermes-bridge"
    tool_names = [t["name"] for t in resps[1]["result"]["tools"]]
    for expected in ("ask", "start_async", "check_job", "list_jobs", "list_sessions", "health"):
        assert expected in tool_names, f"Missing tool {expected} in {tool_names}"
    health_data = json.loads(resps[2]["result"]["content"][0]["text"])
    assert health_data["ok"] is True
    sessions_data = json.loads(resps[3]["result"]["content"][0]["text"])
    print(
        f"[PASS] hermes_mcp_server: tools={tool_names}, hermes_ready={health_data['ready']}, "
        f"sessions_count={sessions_data.get('count')}"
    )


def test_recursion_guard() -> None:
    resps = rpc_session(
        "antigravity_mcp_server.py",
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "delegate", "arguments": {"prompt": "hello"}},
            },
        ],
        extra_env={"HERMES_AGY_BRIDGE_DEPTH": "10"},
    )
    data = json.loads(resps[1]["result"]["content"][0]["text"])
    assert data["ok"] is False and "recursion limit" in data["error"].lower()
    assert resps[1]["result"]["isError"] is True
    print("[PASS] recursion guard blocks nested loop at depth=10")


if __name__ == "__main__":
    test_antigravity_server()
    test_hermes_server()
    test_recursion_guard()
    print("ALL BRIDGE TESTS PASSED")
