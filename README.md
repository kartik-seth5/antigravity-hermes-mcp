# Antigravity ↔ Hermes MCP Bridge

A zero-dependency Python stdio MCP server that connects **Google Antigravity** (`agy` / **Gemini Neon**) and **Hermes Agent** in both directions — letting Hermes call Antigravity (and Gemini Neon) as a subagent or autonomous orchestrator, and letting Antigravity call Hermes back.

## Features

- **Use Gemini Neon via Hermes:** Call Antigravity directly from Hermes (CLI, Telegram, Discord, Slack, etc.) via native MCP tools (`mcp_antigravity_delegate`, `mcp_antigravity_start_async`, `mcp_antigravity_check_job`, `mcp_antigravity_list_jobs`, `mcp_antigravity_health`).
- **Subagent & Orchestrator Modes:** Switch between focused `subagent` calls, full `orchestrator` runs (where Antigravity decomposes tasks and runs its own subagents/verification), or `raw` prompts.
- **Multi-Turn Continuity:** Captures and returns `conversation_id` so Hermes can send follow-up turns into the same Antigravity session.
- **Async Background Jobs:** Avoid MCP tool timeouts on long refactors or research tasks with `start_async` + `check_job`.
- **Bidirectional:** Includes `hermes_mcp_server.py` so Antigravity can also ask Hermes, run async Hermes jobs, and list recent Hermes sessions.
- **Zero Dependencies & Fast Startup:** Pure Python 3.10+ standard library (`<50ms` startup so Hermes MCP discovery never times out).
- **Recursion Guard:** Tracks `HERMES_AGY_BRIDGE_DEPTH` across hops (default cap `10`, override with `HERMES_AGY_MAX_DEPTH`) to prevent infinite agent-to-agent loops.

## Prerequisites

1. **Python 3.10+**
2. **Hermes Agent** installed (`hermes` CLI)
3. **Google Antigravity CLI (`agy`)** installed and signed in:
   - **Windows (PowerShell):**
     ```powershell
     irm https://antigravity.google/cli/install.ps1 | iex
     agy
     ```
   - **macOS / Linux:** follow the [Antigravity CLI docs](https://antigravity.google/docs/cli/reference.md) and run `agy` once to sign in.

## Setup

Clone this repo:

```bash
git clone https://github.com/kartik-seth5/antigravity-hermes-mcp.git
cd antigravity-hermes-mcp
```

### 1. Connect Hermes → Antigravity (use Gemini Neon from Hermes)

Add `antigravity` under `mcp_servers:` in your Hermes `config.yaml` (`~/.hermes/config.yaml` on macOS/Linux, or `%LOCALAPPDATA%\hermes\config.yaml` on Windows):

```yaml
mcp_servers:
  antigravity:
    command: python  # or full path, e.g. C:/Python314/python.exe
    args:
      - /path/to/antigravity-hermes-mcp/antigravity_mcp_server.py
    env:
      # On Windows, include LOCALAPPDATA & USERPROFILE so Hermes's filtered MCP env finds agy.exe:
      LOCALAPPDATA: C:\Users\<you>\AppData\Local
      USERPROFILE: C:\Users\<you>
      # Optional: override max chained cross-agent hops (default: 10)
      # HERMES_AGY_MAX_DEPTH: '10'
      # Optional: explicit path to agy binary
      # AGY_BIN: C:\Users\<you>\AppData\Local\agy\bin\agy.exe
    timeout: 600
    connect_timeout: 30
    enabled: true
```

*(Optional)* Install the included Hermes skill so Hermes knows how to orchestrate Antigravity:

- Copy `skills/antigravity/SKILL.md` into `$HERMES_HOME/skills/autonomous-ai-agents/antigravity/SKILL.md`.

Then reload Hermes (`/reload-mcp` and `/reload-skills`, or restart Hermes) and verify:

```bash
hermes mcp test antigravity
```

### 2. Connect Antigravity → Hermes (optional, for bidirectional calls)

Add `hermes-bridge` to `~/.gemini/antigravity/mcp_config.json`:

```json
{
  "mcpServers": {
    "hermes-bridge": {
      "command": "python",
      "args": [
        "/path/to/antigravity-hermes-mcp/hermes_mcp_server.py"
      ],
      "env": {
        "HERMES_HOME": "/path/to/hermes-home",
        "HERMES_BIN": "/path/to/hermes"
      }
    }
  }
}
```

Restart Antigravity so it discovers `hermes-bridge`.

## Tools Exposed

### `antigravity_mcp_server.py` (called by Hermes)
| Tool | Description |
|------|-------------|
| `delegate` | Run Antigravity synchronously (`prompt`, `cwd`, `role`, `model`, `effort`, `mode`, `conversation_id`, `continue_last`, `auto_approve`, `timeout_seconds`) |
| `start_async` | Start a background Antigravity job and return `job_id` immediately |
| `check_job` | Poll status and retrieve result + `conversation_id` for a `job_id` |
| `list_jobs` | List recent background Antigravity jobs |
| `health` | Check `agy` binary path, version, and bridge depth |

### `hermes_mcp_server.py` (called by Antigravity)
| Tool | Description |
|------|-------------|
| `ask` | Send a synchronous prompt/task to Hermes (`prompt`, `cwd`, `session_id`, `continue_last`, `model`, `provider`, `toolsets`, `skills`, `yolo`, `timeout_seconds`) |
| `start_async` | Start a background Hermes job and return `job_id` |
| `check_job` | Poll status and result of a background Hermes job |
| `list_jobs` | List recent background Hermes jobs |
| `list_sessions` | Read recent Hermes sessions from `state.db` |
| `health` | Check `hermes` binary path, version, and bridge depth |

## Testing

```bash
python test_bridge.py
```

## License

MIT
