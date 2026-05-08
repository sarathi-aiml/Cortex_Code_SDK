# Cortex Code Agent SDK — FastAPI + Streamlit Demo

> **Before running:** Replace these placeholders with your own values:
> - `<your-org>-<your-account>` → your Snowflake org/account hostname (the part before `.snowflakecomputing.com`)
> - `my_snowflake_connection` → name of your profile in `~/.snowflake/connections.toml`
> - `MY_DB.MY_SCHEMA.*` → your real database/schema/agent/MCP names
>
> Search & replace once, and you're set.

A complete, copy-paste-ready demo of the **Cortex Code Agent SDK** (preview):

- `cortex_sdk_api.py` — a FastAPI HTTP layer around the SDK (REST + SSE)
- `streamlit_app.py` — a chat UI consuming the API with full agent transparency (thinking, tools, subagents)
- `hello_cortex_sdk_multiturn.py` — minimal multi-turn sample (no FastAPI)

```
┌──────────────────┐  HTTP/SSE  ┌────────────────────┐  stdio  ┌────────────────┐
│  Your app /      │ ─────────► │ cortex_sdk_api.py  │ ──────► │  cortex CLI    │
│  Streamlit / curl│ ◄───────── │  (FastAPI)         │ ◄────── │  (Cortex Code) │
└──────────────────┘            └────────────────────┘         └────────────────┘
                                                                       │
                                                                       ▼
                                                                  Snowflake
                                                                 (Cortex AI,
                                                                  agents, MCP,
                                                                  SQL, Search…)
```

The FastAPI layer is **stateful**: it keeps SDK sessions alive in memory so multi-turn chat preserves context across HTTP calls.

---

## 1. Prerequisites

| Requirement | Why | Install |
|---|---|---|
| Python ≥ 3.10 | SDK requires it | `python3.11 -m venv .venv && source .venv/bin/activate` |
| Cortex Code CLI | The SDK shells out to it | `curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh \| sh` |
| Snowflake connection | Auth | `~/.snowflake/connections.toml` with at least one connection profile |
| Python deps | API + UI | `pip install "cortex-code-agent-sdk" "fastapi[standard]" uvicorn streamlit requests` |

Sample `~/.snowflake/connections.toml`:
```toml
[<your-snowflake-connection>]
account = "<your-org>-<your-account>"
user = "youruser"
authenticator = "externalbrowser"
```

Set `default_connection_name = "<your-snowflake-connection>"` in `~/.snowflake/config.toml` if you don't want to pass `connection=` everywhere.

---

## 2. Run the API

```bash
cd /path/to/cortex-sdk-demo
uvicorn cortex_sdk_api:app --reload --port 8000 --loop asyncio
```

> **`--loop asyncio` is required.** uvloop's `subprocess_exec` doesn't accept the `user=` kwarg the SDK uses to launch the CLI and will error with `unexpected kwargs: user`.

Verify:
```bash
curl http://127.0.0.1:8000/health      # {"ok": true, "sessions": 0}
open http://127.0.0.1:8000/docs        # OpenAPI / Swagger UI
```

---

## 3. API reference

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/health` | Liveness + active session count |
| `POST` | `/chat` | One-shot prompt; returns full text + cost/turns |
| `POST` | `/chat/stream` | One-shot prompt; SSE stream |
| `POST` | `/sessions` | Open a long-lived multi-turn session, returns `session_id` |
| `GET`  | `/sessions` | List active session IDs |
| `POST` | `/sessions/{sid}/messages` | Send a turn; returns full reply |
| `POST` | `/sessions/{sid}/messages/stream` | Send a turn; SSE stream |
| `DELETE` | `/sessions/{sid}` | Close a session and free its CLI subprocess |

### Request body fields (all optional except `prompt`)

```json
{
  "prompt": "List the tables in MY_DB.MY_SCHEMA",
  "cwd": "/abs/path",
  "connection": "<your-snowflake-connection>",
  "model": "claude-sonnet-4-6",
  "effort": "low",
  "allowed_tools": ["Read", "Glob", "Grep", "Bash", "SQL", "Task"],
  "max_turns": 6,
  "system_prompt": null
}
```

`system_prompt` accepts:
- `null` (default) — keep the SDK's default prompt verbatim
- `"raw text"` — full override (NOT recommended; strips tool guidance)
- `{"type": "preset", "append": "extra text"}` — append to the default

### SSE event types

The streaming endpoints emit these events. Each event is `event: <type>\ndata: <json>\n\n`.

| Event | Payload | Meaning |
|---|---|---|
| `delta` | `{"text": "..."}` | Streaming answer text token |
| `thinking` | `{"text": "..."}` | Streaming reasoning token (Claude only) |
| `thinking_block` | `{"text": "..."}` | Completed reasoning block |
| `tool_use` | `{"id","name","input"}` | Agent invoked a tool |
| `tool_result` | `{"tool_use_id","content","is_error"}` | Tool returned |
| `task_started` | `{"task_id","description",...}` | Subagent spawned |
| `task_progress` | `{"task_id","last_tool_name",...}` | Subagent ran a tool |
| `task_notification` | `{"status","summary",...}` | Subagent finished/failed |
| `done` | `{"subtype","is_error","cost_usd","num_turns"}` | Turn complete |

---

## 4. Calling the API from your app

### Python (sync `requests`)
```python
import requests
r = requests.post(
    "http://127.0.0.1:8000/chat",
    json={"prompt": "Summarize today's pipeline."},
    timeout=300,
)
print(r.json()["text"])
```

### Python (streaming SSE)
```python
import requests, json
with requests.post(
    "http://127.0.0.1:8000/chat/stream",
    json={"prompt": "Read README.md and summarize."},
    stream=True, timeout=300,
) as r:
    event = None
    for line in r.iter_lines(decode_unicode=True):
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = json.loads(line.split(":", 1)[1])
            if event == "delta":
                print(data["text"], end="", flush=True)
            elif event == "tool_use":
                print(f"\n[tool] {data['name']}")
            elif event == "done":
                print(f"\nturns={data['num_turns']} cost=${data['cost_usd']}")
```

### JavaScript (browser EventSource)
```js
const ctrl = new AbortController();
const r = await fetch("http://127.0.0.1:8000/chat/stream", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({prompt: "Hi"}),
  signal: ctrl.signal,
});
const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
let buf = "";
while (true) {
  const {value, done} = await reader.read();
  if (done) break;
  buf += value;
  // parse SSE chunks here…
}
```

### Multi-turn pattern
```python
import requests
B = "http://127.0.0.1:8000"
sid = requests.post(f"{B}/sessions", json={}).json()["session_id"]
print(requests.post(f"{B}/sessions/{sid}/messages",
                    json={"prompt":"Read app.py"}).json()["text"])
print(requests.post(f"{B}/sessions/{sid}/messages",
                    json={"prompt":"Now write tests for it"}).json()["text"])
requests.delete(f"{B}/sessions/{sid}")
```

---

## 5. Cortex Code Agent SDK options reference

Every option in `CortexCodeAgentOptions` can be set per-session. Most are wired through this API.

| Option | Type | Default | What it does |
|---|---|---|---|
| `cwd` | `str \| Path` | `None` | Working directory the agent operates in |
| `connection` | `str` | from `config.toml` | Snowflake CLI connection profile name |
| `profile` | `str` | `None` | Loads a profile from `~/.snowflake/cortex/profiles/` |
| `model` | `str` | `auto` | `auto`, `claude-sonnet-4-6`, `claude-opus-4-6`, `openai-gpt-5.2`, etc. |
| `effort` | `str` | `None` | Thinking budget: `minimal`, `low`, `medium`, `high`, `max` |
| `permission_mode` | `str` | `default` | `default`, `autoAcceptPlans`, `plan`, `bypassPermissions` |
| `allow_dangerously_skip_permissions` | `bool` | `False` | Required for `bypassPermissions` |
| `allowed_tools` | `list[str]` | `[]` | Auto-approve list (e.g. `["Read","Bash","SQL","Task"]`) |
| `disallowed_tools` | `list[str]` | `[]` | Always-deny list |
| `max_turns` | `int` | `None` | Cap on agent turns per query |
| `system_prompt` | `str \| dict \| None` | `None` | `None` keeps default; `dict` `{"type":"preset","append":"..."}` adds to it; `str` replaces it |
| `add_dirs` | `list[str]` | `[]` | Extra dirs the agent can read from beyond `cwd` |
| `env` | `dict` | `{}` | Env vars passed to the CLI subprocess |
| `mcp_servers` | `dict` | `{}` | External MCP servers (stdio/HTTP/SSE) the agent can call |
| `hooks` | `dict` | `None` | Lifecycle callbacks (PreToolUse, PostToolUse, Stop, etc.) |
| `can_use_tool` | `Callable` | `None` | Per-call permission decision function |
| `output_format` | `dict` | `None` | Force structured JSON: `{"type":"json_schema","schema":{...}}` |
| `include_partial_messages` | `bool` | `False` | Emit token-level `StreamEvent` (we set this true on streaming endpoints) |
| `max_thinking_tokens` | `int` | model default | Cap reasoning tokens |
| `continue_conversation` | `bool` | `False` | Continue most recent CLI session |
| `resume` | `str` | `None` | Resume a specific session ID |
| `fork_session` | `bool` | `False` | When resuming, fork to a new session |
| `setting_sources` | `list[str]` | `None` | `"user"`, `"project"`, `"local"` — controls which settings files load |
| `cli_path` | `str` | `cortex` | Override path to the CLI binary |
| `extra_args` | `dict` | `{}` | Extra CLI flags to pass through |
| `abort_event` | `asyncio.Event` | `None` | Cancel a running turn while keeping the session alive |
| `plugins` | `list[dict]` | `[]` | Local plugin configs |
| `stderr` | `Callable[[str], None]` | `None` | Receive each stderr line from the CLI for logging |

### Useful built-in tools

`Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`, `SQL`, `Task` (subagent), plus any tools exposed via attached MCP servers (`mcp_servers={...}`).

---

## 6. Going deeper: larger analytical use cases

The current setup covers single-host, single-user demos. For production analytical workloads on Snowflake — code review at repo scale, multi-table audits, governance scans — extend along these axes:

### 6a. Use subagents (multi-agent) aggressively
Add `Task` to `allowed_tools` and instruct the agent to spawn subagents in parallel for orthogonal investigations:

```json
{
  "system_prompt": {
    "type": "preset",
    "append": "For investigative tasks, spawn parallel subagents via the Task tool. Run at least 3 in parallel: structure, dependencies, data quality. Aggregate findings."
  }
}
```

Subagent lifecycle (`task_started`, `task_progress`, `task_notification`) flows through SSE so the UI can show progress for each.

### 6b. Wire in MCP servers
Connect Snowflake-managed MCP servers (Cortex Analyst, Cortex Search, SQL execution, custom UDFs/SPs as tools) so the agent has a richer tool surface beyond the CLI built-ins.

```python
options.mcp_servers = {
    "sales_toolbox": {
        "type": "http",
        "url": "https://<org>-<account>.snowflakecomputing.com/api/v2/databases/MY_DB/schemas/DATA/mcp-servers/MY_MCP_SERVER",
        "headers": {"Authorization": "Bearer <PAT>"},
    },
}
```

See `../scripts/03_create_managed_mcp.sql` for creating one.

### 6c. Force structured output for downstream pipelines
When the agent's reply feeds another system, force JSON Schema:

```json
{
  "prompt": "Analyze table MY_DB.MY_SCHEMA.OPPORTUNITIES.",
  "output_format": {
    "type": "json_schema",
    "schema": {
      "type": "object",
      "properties": {
        "anomalies": {"type": "array", "items": {"type": "string"}},
        "recommended_indexes": {"type": "array", "items": {"type": "string"}},
        "risk_score": {"type": "number"}
      },
      "required": ["anomalies", "risk_score"]
    }
  }
}
```

`ResultMessage.structured_output` then contains validated JSON.

### 6d. Hooks for audit + data governance
Use `hooks` (`PreToolUse`, `PostToolUse`) to log every SQL the agent runs, redact PII before display, or block writes to specific schemas:

```python
async def gate_writes(input_data, *_):
    sql = (input_data.get("tool_input") or {}).get("statement", "").lower()
    if any(k in sql for k in ("drop ", "truncate ", "delete ")):
        return {"continue_": False, "stopReason": "writes blocked in this env"}
    return {"continue_": True}
```

### 6e. Scale-out architecture
- **Multiple concurrent users** → `uvicorn ... --workers N`. Each worker has its own session map; route by sticky session.
- **Survive restarts** → externalize the session registry to Redis with `sid → CLI invocation args`. On reconnect, the SDK supports `resume="<sid>"`.
- **Long batch jobs** → use the one-shot `/chat` endpoint with `max_turns` budgeted, not interactive sessions.
- **GPU/CPU isolation** → run uvicorn behind a task queue (Celery/Arq) so a stuck CLI subprocess can't exhaust the API host.

### 6f. Cost & latency control
- Default to `claude-sonnet-4-6` + `effort="low"` for chat
- Reserve `claude-opus-4-6` + `effort="high"` for explicit "deep analysis" buttons
- Cap `max_turns` (3–6 for chat, 15–25 for analysis)
- `max_thinking_tokens` to bound reasoning cost on Claude
- Emit per-turn `cost_usd` from `done` events into your usage table

### 6g. Security
- Always run with a least-privilege Snowflake connection
- Never `permission_mode="bypassPermissions"` on a network-exposed API
- Add an auth layer (`Authorization: Bearer …` FastAPI dependency) before exposing port 8000
- Add CORS middleware if calling from a browser
- Never echo agent-generated SQL back to a privileged role unreviewed — gate via hooks

### 6h. Observability
- Wire `stderr=lambda line: log.info(line)` on options to capture CLI logs
- Persist every SSE event to a Snowflake table for audit/replay
- Emit OpenTelemetry spans per tool call from a `PostToolUse` hook

---

## 7. Streamlit chat UI

`streamlit_app.py` is a chat-style frontend that consumes this API. It shows the agent's complete activity in real time: streaming text, thinking blocks, tool calls + results, and subagent (multi-agent) lifecycle events.

Highlights:
- **Two presets** in the sidebar: *Fast* (Sonnet + low effort) and *Deep analysis (multi-agent)* (Opus + high effort + Task tool)
- **Live activity feed** with timestamps for every event
- **Expandable details** for each tool call and tool result
- **New thread** button — closes the current SDK session and starts fresh
- **Per-thread cost / turn count** in the sidebar

### Run it

Keep the FastAPI running in one terminal, then in a second terminal:
```bash
cd /path/to/cortex-sdk-demo
streamlit run streamlit_app.py
```
Streamlit opens at <http://localhost:8501>.

Point to a different API host:
```bash
CORTEX_SDK_API=http://other-host:8000 streamlit run streamlit_app.py
```

---

## 8. Files in this folder

| File | What it is |
|---|---|
| `cortex_sdk_api.py` | FastAPI server — REST + SSE wrapper around the SDK |
| `streamlit_app.py` | Streamlit chat UI consuming the API |
| `hello_cortex_sdk_multiturn.py` | Minimal multi-turn SDK sample (no FastAPI) |
| `README.md` | This file |

## 9. Caveats / production notes

- Sessions live **in process memory** — restart loses them
- One `cortex` CLI subprocess per open session; cap concurrency
- `--loop asyncio` is mandatory (uvloop incompatibility)
- The SDK is in **preview** — option names may evolve

## 10. References

- [Cortex Code Agent SDK](https://docs.snowflake.com/en/user-guide/cortex-code-agent-sdk/cortex-code-agent-sdk)
- [Python SDK reference](https://docs.snowflake.com/en/user-guide/cortex-code-agent-sdk/python-reference)
- [Quickstart](https://docs.snowflake.com/en/user-guide/cortex-code-agent-sdk/quickstart)
- [Snowflake-managed MCP server](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp)
- [Hooks](https://docs.snowflake.com/en/user-guide/cortex-code-agent-sdk/hooks)
- [Structured output](https://docs.snowflake.com/en/user-guide/cortex-code-agent-sdk/structured-output)
