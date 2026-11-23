---
name: antigravity
description: "Delegate tasks to Google Antigravity (`agy` / Gemini Neon) as a subagent or orchestrator via the native `antigravity` MCP bridge."
version: 1.0.0
platforms: [windows, macos, linux]
metadata:
  hermes:
    tags: [Coding-Agent, Antigravity, Gemini-Neon, Orchestrator, Subagent, MCP, Automation]
    related_skills: [claude-code, codex, hermes-agent]
---

# Antigravity — Hermes Subagent & Orchestrator Guide

Delegate focused tasks or multi-step orchestration work to **Google Antigravity** (`agy`, including Gemini Neon) directly through the configured `antigravity` MCP server.

## Available MCP Tools

| Tool | Purpose |
|------|---------|
| `mcp_antigravity_delegate` | Run a synchronous Antigravity turn and return `{ response, conversation_id, duration_seconds }` |
| `mcp_antigravity_start_async` | Launch a long-running Antigravity orchestration job in the background; returns `job_id` immediately |
| `mcp_antigravity_check_job` | Poll status and retrieve the final result + `conversation_id` of an async job |
| `mcp_antigravity_list_jobs` | List recent background Antigravity jobs |
| `mcp_antigravity_health` | Verify that `agy` CLI is installed and runnable |

## Choosing a Role

- **`role: "subagent"`** (default) — Focused single-step or narrow tasks (implement a function, review a diff, write a test, research a file).
- **`role: "orchestrator"`** — Larger multi-file or multi-step goals where Antigravity decomposes the work, runs its own subagents/verification, and returns a structured summary with file paths and test results.
- **`role: "raw"`** — Send the prompt verbatim without framing.

## Patterns

1. **Focused subagent call:** `mcp_antigravity_delegate(prompt=..., cwd=..., role="subagent")`. Pass `auto_approve=true` only when Antigravity should run tools without interactive prompts.
2. **Multi-turn follow-up:** Pass the returned `conversation_id` back into `mcp_antigravity_delegate(prompt=..., cwd=..., conversation_id=...)` to continue the same Antigravity session.
3. **Long-running orchestrator job:** Call `mcp_antigravity_start_async(prompt=..., cwd=..., role="orchestrator", timeout_seconds=900)`, then poll with `mcp_antigravity_check_job(job_id=...)`.

## Safety & Prerequisites

- If `mcp_antigravity_health` reports `ready: false`, install the Antigravity CLI (`agy`) and run `agy` once to sign in.
- Cross-agent hops track `HERMES_AGY_BRIDGE_DEPTH` (default max depth `10`, override via `HERMES_AGY_MAX_DEPTH`) to prevent infinite loops.
- Always pass `cwd` so Antigravity operates in the intended project directory.
