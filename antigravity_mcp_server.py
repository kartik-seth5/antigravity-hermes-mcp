"""Antigravity MCP Server for Hermes Agent.

Exposes Google Antigravity CLI (`agy`) as first-class MCP tools so Hermes can
call Antigravity as a subagent or orchestrator, continue multi-turn sessions,
and run long-lived background orchestration jobs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from job_store import JobManager, check_recursion_guard, get_bridge_depth, next_hop_env
from mcp_stdio import StdioMCPServer


JOBS = JobManager("antigravity")

ORCHESTRATOR_PREAMBLE = (
    "[Delegated by Hermes Agent — Role: Orchestrator]\n"
    "You are acting as an autonomous orchestrator called by Hermes. "
    "Break down the objective if needed, use your own subagents/tools/verification "
    "to complete it thoroughly, and finish with a concise structured summary covering: "
    "(1) outcome, (2) files created/modified with exact paths, (3) verification performed, "
    "and (4) any open questions or blockers.\n\n"
    "Objective:\n"
)

SUBAGENT_PREAMBLE = (
    "[Delegated by Hermes Agent — Role: Subagent]\n"
    "Complete the focused task below concisely and accurately. Report the result, "
    "any files touched, and any verification output.\n\n"
    "Task:\n"
)


def find_agy_binary() -> Optional[str]:
    env_override = os.environ.get("AGY_BIN", "").strip()
    if env_override and Path(env_override).exists():
        return env_override

    on_path = shutil.which("agy")
    if on_path:
        return on_path

    local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    candidates = [
        Path(local_appdata) / "agy" / "bin" / "agy.exe",
        Path(local_appdata) / "agy" / "bin" / "agy.cmd",
        Path.home() / ".local" / "bin" / "agy.exe",
        Path.home() / ".local" / "bin" / "agy",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return None


def _parse_agy_output(stdout: str) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """Parse `agy -p ... --output-format json` output, with plain-text fallback."""
    text = stdout.strip()
    if not text:
        return "", None, None

    # Try full JSON object first, then last JSON line (in case of leading warnings).
    parsed: Optional[Dict[str, Any]] = None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            parsed = obj
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        parsed = obj
                        break
                except json.JSONDecodeError:
                    continue

    if not parsed:
        return text, None, None

    conv_id = (
        parsed.get("conversation_id")
        or parsed.get("conversationId")
        or parsed.get("session_id")
        or parsed.get("sessionId")
    )
    response_text = (
        parsed.get("response")
        or parsed.get("result")
        or parsed.get("text")
        or parsed.get("output")
        or parsed.get("message")
    )
    if isinstance(response_text, dict):
        response_text = json.dumps(response_text, ensure_ascii=False)
    if response_text is None:
        response_text = text
    return str(response_text), (str(conv_id) if conv_id else None), parsed


def _run_agy(
    prompt: str,
    cwd: Optional[str] = None,
    role: str = "subagent",
    model: Optional[str] = None,
    effort: Optional[str] = None,
    mode: Optional[str] = None,
    conversation_id: Optional[str] = None,
    continue_last: bool = False,
    auto_approve: bool = False,
    timeout_seconds: int = 300,
) -> Dict[str, Any]:
    guard_error = check_recursion_guard()
    if guard_error:
        return {"ok": False, "error": guard_error}

    agy_bin = find_agy_binary()
    if not agy_bin:
        return {
            "ok": False,
            "error": (
                "Antigravity CLI (`agy`) was not found. Install it on Windows PowerShell with:\n"
                "  irm https://antigravity.google/cli/install.ps1 | iex\n"
                "Then run `agy` once in a terminal to complete Google sign-in, or set AGY_BIN."
            ),
        }

    work_dir = cwd.strip() if cwd else os.getcwd()
    if not Path(work_dir).exists():
        return {"ok": False, "error": f"Working directory does not exist: {work_dir}"}

    if conversation_id or continue_last:
        full_prompt = prompt
    elif role == "orchestrator":
        full_prompt = ORCHESTRATOR_PREAMBLE + prompt
    elif role == "subagent":
        full_prompt = SUBAGENT_PREAMBLE + prompt
    else:
        full_prompt = prompt

    cmd: List[str] = [agy_bin, "-p", full_prompt, "--output-format", "json"]
    if conversation_id:
        cmd.extend(["--conversation", conversation_id])
    elif continue_last:
        cmd.append("--continue")
    if model:
        cmd.extend(["--model", model])
    if effort:
        cmd.extend(["--effort", effort])
    if mode:
        cmd.extend(["--mode", mode])
    if auto_approve:
        cmd.append("--dangerously-skip-permissions")
    if timeout_seconds and timeout_seconds > 0:
        cmd.extend(["--print-timeout", f"{int(timeout_seconds)}s"])

    start_ts = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            env=next_hop_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(10, int(timeout_seconds) + 15),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"Antigravity timed out after {timeout_seconds}s. Use antigravity_start_async for long tasks.",
            "cwd": work_dir,
            "stderr_tail": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
        }
    except OSError as exc:
        return {"ok": False, "error": f"Failed to launch agy: {exc}", "agy_bin": agy_bin}

    duration_s = round(time.time() - start_ts, 2)
    response_text, returned_conv_id, raw_json = _parse_agy_output(proc.stdout or "")
    stderr_tail = (proc.stderr or "").strip()[-1500:]

    if proc.returncode != 0:
        return {
            "ok": False,
            "exit_code": proc.returncode,
            "error": stderr_tail or response_text or f"agy exited with status {proc.returncode}",
            "conversation_id": returned_conv_id or conversation_id,
            "cwd": work_dir,
            "duration_seconds": duration_s,
        }

    result: Dict[str, Any] = {
        "ok": True,
        "role": role,
        "conversation_id": returned_conv_id or conversation_id,
        "response": response_text,
        "cwd": work_dir,
        "duration_seconds": duration_s,
    }
    if stderr_tail:
        result["stderr_tail"] = stderr_tail
    if raw_json and isinstance(raw_json.get("stats"), dict):
        result["stats"] = raw_json["stats"]
    return result


def handle_delegate(args: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return {"ok": False, "error": "prompt is required"}
    return _run_agy(
        prompt=prompt,
        cwd=args.get("cwd"),
        role=str(args.get("role") or "subagent"),
        model=args.get("model"),
        effort=args.get("effort"),
        mode=args.get("mode"),
        conversation_id=args.get("conversation_id"),
        continue_last=bool(args.get("continue_last", False)),
        auto_approve=bool(args.get("auto_approve", False)),
        timeout_seconds=int(args.get("timeout_seconds") or 300),
    )


def handle_start_async(args: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return {"ok": False, "error": "prompt is required"}
    work_dir = str(args.get("cwd") or os.getcwd())
    timeout_seconds = int(args.get("timeout_seconds") or 900)
    job = JOBS.start(
        prompt=prompt,
        cwd=work_dir,
        fn=lambda: _run_agy(
            prompt=prompt,
            cwd=work_dir,
            role=str(args.get("role") or "orchestrator"),
            model=args.get("model"),
            effort=args.get("effort"),
            mode=args.get("mode"),
            conversation_id=args.get("conversation_id"),
            continue_last=bool(args.get("continue_last", False)),
            auto_approve=bool(args.get("auto_approve", False)),
            timeout_seconds=timeout_seconds,
        ),
    )
    return {
        "ok": True,
        "job_id": job.job_id,
        "status": job.status,
        "cwd": work_dir,
        "message": "Antigravity job started in background. Call check_job with this job_id.",
    }


def handle_check_job(args: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(args.get("job_id", "")).strip()
    if not job_id:
        return {"ok": False, "error": "job_id is required"}
    record = JOBS.get(job_id)
    if not record:
        return {"ok": False, "error": f"Unknown job_id: {job_id}"}
    return {"ok": True, **record}


def handle_list_jobs(args: Dict[str, Any]) -> Dict[str, Any]:
    limit = int(args.get("limit") or 20)
    jobs = JOBS.list_jobs(limit=max(1, min(limit, 100)))
    return {"ok": True, "count": len(jobs), "jobs": jobs}


def handle_health(_args: Dict[str, Any]) -> Dict[str, Any]:
    agy_bin = find_agy_binary()
    version_out = ""
    ready = False
    if agy_bin:
        try:
            proc = subprocess.run(
                [agy_bin, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                stdin=subprocess.DEVNULL,
            )
            version_out = (proc.stdout or proc.stderr or "").strip()
            ready = proc.returncode == 0
        except Exception as exc:
            version_out = f"error: {exc}"

    return {
        "ok": True,
        "ready": ready,
        "agy_bin": agy_bin,
        "version": version_out,
        "bridge_depth": get_bridge_depth(),
        "install_hint": (
            None
            if ready
            else "Run `irm https://antigravity.google/cli/install.ps1 | iex` in PowerShell, then run `agy` once to sign in."
        ),
    }


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        name="antigravity-bridge",
        version="1.0.0",
        instructions=(
            "Call Google Antigravity (`agy`) as a subagent or orchestrator. "
            "Use `delegate` for synchronous tasks (and pass `conversation_id` for multi-turn follow-ups), "
            "`start_async` + `check_job` for long multi-step orchestration, and `health` to check readiness."
        ),
    )

    delegate_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Task or follow-up message for Antigravity."},
            "cwd": {"type": "string", "description": "Working directory / repo path for Antigravity to operate in."},
            "role": {
                "type": "string",
                "enum": ["subagent", "orchestrator", "raw"],
                "default": "subagent",
                "description": "subagent = focused task; orchestrator = decompose, spawn subagents/swarm, verify; raw = send prompt as-is.",
            },
            "model": {"type": "string", "description": "Optional Antigravity model override."},
            "effort": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Optional reasoning effort level.",
            },
            "mode": {
                "type": "string",
                "description": "Optional Antigravity execution mode (e.g. 'accept-edits', 'plan').",
            },
            "conversation_id": {
                "type": "string",
                "description": "Resume a previous Antigravity conversation ID for multi-turn back-and-forth.",
            },
            "continue_last": {
                "type": "boolean",
                "default": False,
                "description": "Continue the most recent Antigravity conversation in cwd.",
            },
            "auto_approve": {
                "type": "boolean",
                "default": False,
                "description": "Pass --dangerously-skip-permissions for fully autonomous execution.",
            },
            "timeout_seconds": {
                "type": "integer",
                "default": 300,
                "description": "Maximum seconds to wait before timing out.",
            },
        },
        "required": ["prompt"],
    }

    server.register_tool(
        name="delegate",
        description=(
            "Invoke Antigravity synchronously as a subagent or orchestrator. "
            "Returns Antigravity's response and a conversation_id you can pass back for follow-up turns."
        ),
        input_schema=delegate_schema,
        handler=handle_delegate,
    )

    server.register_tool(
        name="start_async",
        description=(
            "Launch a long-running Antigravity orchestration or coding task in the background. "
            "Returns a job_id immediately; poll with check_job."
        ),
        input_schema=delegate_schema,
        handler=handle_start_async,
    )

    server.register_tool(
        name="check_job",
        description="Check the status and result of a background Antigravity job started with start_async.",
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job_id returned by start_async."},
            },
            "required": ["job_id"],
        },
        handler=handle_check_job,
    )

    server.register_tool(
        name="list_jobs",
        description="List recent background Antigravity jobs and their statuses.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=handle_list_jobs,
    )

    server.register_tool(
        name="health",
        description="Check whether the Antigravity CLI (`agy`) is installed, runnable, and its version.",
        input_schema={"type": "object", "properties": {}},
        handler=handle_health,
    )

    return server


if __name__ == "__main__":
    build_server().run()
