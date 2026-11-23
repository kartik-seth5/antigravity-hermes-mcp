"""Hermes MCP Server for Google Antigravity.

Exposes Hermes Agent (`hermes chat -q ...`) as first-class MCP tools so
Antigravity can communicate with Hermes, delegate tasks to Hermes, resume
Hermes sessions, and inspect recent Hermes sessions.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from job_store import JobManager, check_recursion_guard, get_bridge_depth, next_hop_env
from mcp_stdio import StdioMCPServer


JOBS = JobManager("hermes")


def get_hermes_home() -> Path:
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        return Path(env_home)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidate = Path(local_appdata) / "hermes"
        if candidate.exists():
            return candidate
    return Path.home() / ".hermes"


def find_hermes_binary() -> Optional[str]:
    env_override = os.environ.get("HERMES_BIN", "").strip()
    if env_override and Path(env_override).exists():
        return env_override

    hermes_home = get_hermes_home()
    candidates = [
        hermes_home / "bin" / "hermes.exe",
        hermes_home / "bin" / "hermes.cmd",
    ]
    for path in candidates:
        if path.exists():
            return str(path)

    return shutil.which("hermes")


def _extract_session_id(text: str) -> Optional[str]:
    match = re.search(r"(?:session[_\s-]*id|session)\s*[:=]\s*([A-Za-z0-9_-]{8,64})", text, re.IGNORECASE)
    return match.group(1) if match else None


def _run_hermes(
    prompt: str,
    cwd: Optional[str] = None,
    session_id: Optional[str] = None,
    continue_last: bool = False,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    toolsets: Optional[str] = None,
    skills: Optional[str] = None,
    yolo: bool = False,
    timeout_seconds: int = 300,
) -> Dict[str, Any]:
    guard_error = check_recursion_guard()
    if guard_error:
        return {"ok": False, "error": guard_error}

    hermes_bin = find_hermes_binary()
    if not hermes_bin:
        return {
            "ok": False,
            "error": (
                "Hermes CLI was not found. Checked HERMES_BIN, %LOCALAPPDATA%\\hermes\\bin\\hermes.exe, and PATH."
            ),
        }

    work_dir = cwd.strip() if cwd else os.getcwd()
    if not Path(work_dir).exists():
        return {"ok": False, "error": f"Working directory does not exist: {work_dir}"}

    cmd: List[str] = [hermes_bin]
    if session_id:
        cmd.extend(["--resume", session_id])
    elif continue_last:
        cmd.append("--continue")
    if skills:
        cmd.extend(["--skills", skills])
    if yolo:
        cmd.append("--yolo")

    cmd.extend(["chat", "-q", prompt, "--quiet", "--source", "antigravity-bridge"])
    if model:
        cmd.extend(["-m", model])
    if provider:
        cmd.extend(["--provider", provider])
    if toolsets:
        cmd.extend(["-t", toolsets])

    env = next_hop_env()
    env.setdefault("HERMES_HOME", str(get_hermes_home()))

    start_ts = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(10, int(timeout_seconds)),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"Hermes timed out after {timeout_seconds}s. Use start_async for long tasks.",
            "cwd": work_dir,
            "stderr_tail": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
        }
    except OSError as exc:
        return {"ok": False, "error": f"Failed to launch hermes: {exc}", "hermes_bin": hermes_bin}

    duration_s = round(time.time() - start_ts, 2)
    stdout_text = (proc.stdout or "").strip()
    stderr_tail = (proc.stderr or "").strip()[-1500:]
    detected_session = _extract_session_id(stderr_tail) or _extract_session_id(stdout_text) or session_id

    if proc.returncode != 0:
        return {
            "ok": False,
            "exit_code": proc.returncode,
            "error": stderr_tail or stdout_text or f"hermes exited with status {proc.returncode}",
            "session_id": detected_session,
            "cwd": work_dir,
            "duration_seconds": duration_s,
        }

    result: Dict[str, Any] = {
        "ok": True,
        "response": stdout_text,
        "session_id": detected_session,
        "cwd": work_dir,
        "duration_seconds": duration_s,
    }
    if stderr_tail:
        result["stderr_tail"] = stderr_tail
    return result


def handle_ask(args: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return {"ok": False, "error": "prompt is required"}
    return _run_hermes(
        prompt=prompt,
        cwd=args.get("cwd"),
        session_id=args.get("session_id"),
        continue_last=bool(args.get("continue_last", False)),
        model=args.get("model"),
        provider=args.get("provider"),
        toolsets=args.get("toolsets"),
        skills=args.get("skills"),
        yolo=bool(args.get("yolo", False)),
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
        fn=lambda: _run_hermes(
            prompt=prompt,
            cwd=work_dir,
            session_id=args.get("session_id"),
            continue_last=bool(args.get("continue_last", False)),
            model=args.get("model"),
            provider=args.get("provider"),
            toolsets=args.get("toolsets"),
            skills=args.get("skills"),
            yolo=bool(args.get("yolo", False)),
            timeout_seconds=timeout_seconds,
        ),
    )
    return {
        "ok": True,
        "job_id": job.job_id,
        "status": job.status,
        "cwd": work_dir,
        "message": "Hermes job started in background. Call check_job with this job_id.",
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


def handle_list_sessions(args: Dict[str, Any]) -> Dict[str, Any]:
    limit = max(1, min(int(args.get("limit") or 15), 100))
    db_path = get_hermes_home() / "state.db"
    if not db_path.exists():
        return {"ok": False, "error": f"Hermes state.db not found at {db_path}"}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        select_cols = [c for c in ("id", "title", "source", "model", "started_at", "last_active") if c in cols]
        order_col = "last_active" if "last_active" in cols else ("started_at" if "started_at" in cols else "rowid")
        rows = conn.execute(
            f"SELECT {', '.join(select_cols)} FROM sessions ORDER BY {order_col} DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return {"ok": True, "count": len(rows), "sessions": [dict(r) for r in rows]}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to read Hermes sessions: {exc}"}


def handle_health(_args: Dict[str, Any]) -> Dict[str, Any]:
    hermes_bin = find_hermes_binary()
    version_out = ""
    ready = False
    if hermes_bin:
        try:
            proc = subprocess.run(
                [hermes_bin, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                stdin=subprocess.DEVNULL,
            )
            version_out = (proc.stdout or proc.stderr or "").strip()
            ready = proc.returncode == 0
        except Exception as exc:
            version_out = f"error: {exc}"

    return {
        "ok": True,
        "ready": ready,
        "hermes_bin": hermes_bin,
        "hermes_home": str(get_hermes_home()),
        "version": version_out,
        "bridge_depth": get_bridge_depth(),
    }


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        name="hermes-bridge",
        version="1.0.0",
        instructions=(
            "Communicate with and delegate tasks to Hermes Agent. "
            "Use `ask` for synchronous requests, `start_async` + `check_job` for long tasks, "
            "`list_sessions` to inspect recent Hermes sessions, and `health` to check readiness."
        ),
    )

    ask_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Message or task to send to Hermes Agent."},
            "cwd": {"type": "string", "description": "Working directory for Hermes to operate in."},
            "session_id": {"type": "string", "description": "Optional Hermes session ID to resume."},
            "continue_last": {"type": "boolean", "default": False, "description": "Resume most recent Hermes session."},
            "model": {"type": "string", "description": "Optional Hermes model override."},
            "provider": {"type": "string", "description": "Optional Hermes provider override."},
            "toolsets": {"type": "string", "description": "Optional comma-separated Hermes toolsets."},
            "skills": {"type": "string", "description": "Optional comma-separated Hermes skills to preload."},
            "yolo": {"type": "boolean", "default": False, "description": "Skip Hermes command approval prompts."},
            "timeout_seconds": {"type": "integer", "default": 300, "description": "Timeout in seconds."},
        },
        "required": ["prompt"],
    }

    server.register_tool(
        name="ask",
        description="Send a prompt or delegated task to Hermes Agent synchronously and return its response.",
        input_schema=ask_schema,
        handler=handle_ask,
    )

    server.register_tool(
        name="start_async",
        description="Start a long-running Hermes Agent task in the background and return a job_id immediately.",
        input_schema=ask_schema,
        handler=handle_start_async,
    )

    server.register_tool(
        name="check_job",
        description="Check the status and result of a background Hermes job.",
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
        description="List recent background Hermes jobs.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=handle_list_jobs,
    )

    server.register_tool(
        name="list_sessions",
        description="List recent Hermes Agent sessions from state.db so they can be inspected or resumed.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 15},
            },
        },
        handler=handle_list_sessions,
    )

    server.register_tool(
        name="health",
        description="Check whether Hermes CLI is installed and runnable.",
        input_schema={"type": "object", "properties": {}},
        handler=handle_health,
    )

    return server


if __name__ == "__main__":
    build_server().run()
