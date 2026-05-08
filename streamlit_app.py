"""
streamlit_app.py
----------------
Cortex Code Agent chat UI — full-transparency edition.

Shows the agent's complete activity stream live:
- Token-level text + thinking
- Every tool call (input + result, success/error)
- Subagent (multi-agent) task lifecycle (started / progress / done)
- Step timings and final cost / turn count

Toggle "Deep analysis" in the sidebar to switch to a high-effort
multi-tool loop with subagent delegation.

Run:
    pip install streamlit requests
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import json
import os
import time
from typing import Generator

import requests
import streamlit as st


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_BASE = os.getenv("CORTEX_SDK_API", "http://127.0.0.1:8000")

PRESETS = {
    "Fast (default)": {
        "model": "claude-sonnet-4-6",
        "effort": "low",
        "max_turns": 6,
        "allowed_tools": ["Read", "Glob", "Grep", "Bash", "SQL"],
        # None = keep the SDK default system prompt (which knows about all built-in tools,
        # Cortex agents, SQL execution etc). DO NOT pass a plain string here — it
        # fully replaces the default and the agent stops calling tools.
        "system_prompt": None,
    },
    "Deep analysis (multi-agent)": {
        "model": "claude-opus-4-6",
        "effort": "high",
        "max_turns": 25,
        "allowed_tools": ["Read", "Glob", "Grep", "Bash", "Task", "SQL", "Write", "Edit"],
        # APPEND form — keeps the SDK default prompt (tool guidance, agent
        # orchestration, etc) and adds our deep-mode hint on top.
        "system_prompt": {
            "type": "preset",
            "append": (
                "When the task is complex, prefer using the Task tool to spawn "
                "subagents (e.g. subagent_type='general-purpose' or 'Explore') "
                "and run multiple investigations in parallel. End complex "
                "responses with a structured Markdown report: "
                "## Summary, ## Findings, ## Recommendations. "
                "Always EXECUTE SQL via the SQL tool — never ask the user to "
                "run it manually. Use cortex agents/MCP/Snowflake tools "
                "directly when available."
            ),
        },
    },
}

HTTP = requests.Session()
HTTP.headers.update({"Connection": "keep-alive"})

st.set_page_config(
    page_title="Cortex Code Agent — Live",
    page_icon=":material/robot:",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def _init_state() -> None:
    st.session_state.setdefault("session_id", None)
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("last_meta", {})
    st.session_state.setdefault("connection", "my_snowflake_connection")
    st.session_state.setdefault("cwd", ".")
    st.session_state.setdefault("preset", "Fast (default)")


_init_state()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def api_health() -> dict | None:
    cached = st.session_state.get("_health_cache")
    if cached and (cached["ts"] + 5) > time.time():
        return cached["val"]
    try:
        r = HTTP.get(f"{API_BASE}/health", timeout=2)
        r.raise_for_status()
        val = r.json()
    except Exception:
        val = None
    st.session_state["_health_cache"] = {"ts": time.time(), "val": val}
    return val


def api_create_session() -> str:
    cfg = PRESETS[st.session_state.preset]
    body = {
        "connection": st.session_state.connection,
        "cwd": st.session_state.cwd,
        **cfg,
    }
    r = HTTP.post(f"{API_BASE}/sessions", json=body, timeout=60)
    if not r.ok:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        st.error(f"API returned {r.status_code} on /sessions")
        if isinstance(detail, dict):
            st.write(f"**{detail.get('error', 'Error')}**: {detail.get('message', '')}")
            if detail.get("trace"):
                st.code(detail["trace"], language="text")
        else:
            st.code(str(detail), language="text")
        st.stop()
    return r.json()["session_id"]


def api_close_session(sid: str) -> None:
    try:
        HTTP.delete(f"{API_BASE}/sessions/{sid}", timeout=10)
    except Exception:
        pass


def stream_turn(sid: str, prompt: str) -> Generator[dict, None, None]:
    url = f"{API_BASE}/sessions/{sid}/messages/stream"
    with HTTP.post(url, json={"prompt": prompt}, stream=True, timeout=900) as r:
        r.raise_for_status()
        event = None
        for raw in r.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    data = {"raw": payload}
                yield {"type": event or "message", **data}
                event = None
            elif line == "":
                event = None


def ensure_session() -> str:
    if not st.session_state.session_id:
        st.session_state.session_id = api_create_session()
    return st.session_state.session_id


def new_thread() -> None:
    if st.session_state.session_id:
        api_close_session(st.session_state.session_id)
    st.session_state.session_id = None
    st.session_state.messages = []
    st.session_state.last_meta = {}


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### :material/robot: Cortex Code Agent")
    st.caption(f"API: `{API_BASE}`")

    health = api_health()
    if health and health.get("ok"):
        st.success(f"API up — {health.get('sessions', 0)} active session(s)")
    else:
        st.error("API not reachable. Start it with:\n\n`uvicorn cortex_sdk_api:app --port 8000 --loop asyncio`")

    st.divider()
    if st.button(":material/add: New thread", use_container_width=True, type="primary"):
        new_thread()
        st.rerun()

    st.selectbox(
        ":material/tune: Mode",
        list(PRESETS.keys()),
        key="preset",
        help="Fast = small model, low effort. Deep = Opus + high effort + Task tool for multi-agent.",
    )
    cfg = PRESETS[st.session_state.preset]
    st.caption(
        f"model: `{cfg['model']}` · effort: `{cfg['effort']}` · max_turns: {cfg['max_turns']}"
    )
    st.caption("tools: " + ", ".join(f"`{t}`" for t in cfg["allowed_tools"]))

    st.divider()
    st.text_input("Snowflake connection", key="connection")
    st.text_input("Working directory", key="cwd")

    st.divider()
    st.caption("Current session")
    st.code(st.session_state.session_id or "(none — created on first prompt)", language="text")

    if st.session_state.last_meta:
        m = st.session_state.last_meta
        c1, c2 = st.columns(2)
        c1.metric("Turns", m.get("num_turns") or "—")
        cost = m.get("cost_usd")
        c2.metric("Cost (USD)", f"${cost:.4f}" if cost is not None else "—")


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------
st.title(":material/robot: Cortex Code Agent — Live")
st.caption("Watch the agent think, call tools, and delegate to subagents in real time.")

SUGGESTIONS = {
    ":green[:material/description:] Project tour":
        "Read README.md and the most important source files. Tell me what this project does and how it's organized.",
    ":blue[:material/search:] Deep dive":
        "Do a deep multi-step analysis of this codebase. Use subagents to investigate different parts in parallel: structure, dependencies, code quality, and any obvious bugs. End with a structured report.",
    ":violet[:material/database:] Snowflake check":
        "Run `cortex agents list` and SHOW WAREHOUSES. Summarize what's in this Snowflake account.",
    ":orange[:material/bug_report:] Hunt bugs":
        "Spawn 2 subagents in parallel: one to find Python bugs, one to find SQL or shell-script issues. Combine their findings into a single report.",
}

queued: str | None = None
if not st.session_state.messages:
    chosen = st.pills("Try a deep-analysis prompt:", list(SUGGESTIONS.keys()),
                      label_visibility="collapsed")
    if chosen:
        queued = SUGGESTIONS[chosen]


# ---------- Renderer for stored history ----------
def _render_event_history(events: list[dict]) -> None:
    """Re-render the activity feed for a finished turn."""
    for e in events:
        kind = e["type"]
        if kind == "thinking_block":
            with st.expander(":material/psychology: Thinking", expanded=False):
                st.markdown(e.get("text", ""))
        elif kind == "tool_use":
            with st.expander(f":material/build: tool · `{e.get('name')}`", expanded=False):
                st.code(json.dumps(e.get("input", {}), indent=2), language="json")
        elif kind == "tool_result":
            icon = ":material/error:" if e.get("is_error") else ":material/check_circle:"
            with st.expander(f"{icon} tool result", expanded=False):
                st.code(e.get("content", "")[:4000], language="text")
        elif kind == "task_started":
            st.info(f":material/group_add: subagent started · `{e.get('description', '')[:120]}`")
        elif kind == "task_progress":
            st.caption(f":material/sync: subagent progress · `{e.get('last_tool_name', '...')}`")
        elif kind == "task_notification":
            status = e.get("status", "")
            icon = {"completed": ":material/check_circle:",
                    "failed": ":material/error:",
                    "stopped": ":material/stop_circle:"}.get(status, ":material/info:")
            st.success(f"{icon} subagent {status} · {e.get('summary', '')[:120]}")


# ---------- Render history ----------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg.get("events"):
            _render_event_history(msg["events"])
        st.markdown(msg["content"])


# ---------- New input ----------
typed = st.chat_input("Message the agent…")
prompt = typed or queued

if prompt:
    if api_health() is None:
        st.error("FastAPI is not reachable.")
        st.stop()

    sid = ensure_session()

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        # Live activity feed at top, final answer at bottom
        feed = st.container()
        st.divider()
        text_placeholder = st.empty()

        events: list[dict] = []
        accumulated_text = ""
        accumulated_thinking = ""
        thinking_placeholder = None
        meta: dict = {}
        t0 = time.time()

        # Live status pinned at the top
        status = feed.status("Working…", expanded=True)

        try:
            for ev in stream_turn(sid, prompt):
                kind = ev.get("type")
                elapsed = f"{time.time() - t0:>5.1f}s"

                if kind == "delta":
                    accumulated_text += ev.get("text", "")
                    text_placeholder.markdown(accumulated_text + "▌")

                elif kind == "thinking":
                    if thinking_placeholder is None:
                        thinking_placeholder = status.empty()
                    accumulated_thinking += ev.get("text", "")
                    thinking_placeholder.markdown(
                        f"**:material/psychology: thinking…**\n\n{accumulated_thinking}"
                    )

                elif kind == "thinking_block":
                    events.append(ev)
                    with status:
                        with st.expander(f"`{elapsed}` :material/psychology: thinking complete"):
                            st.markdown(ev.get("text", ""))
                    accumulated_thinking = ""
                    thinking_placeholder = None

                elif kind == "tool_use":
                    events.append(ev)
                    name = ev.get("name", "?")
                    status.update(label=f"Calling `{name}`…", state="running")
                    with status:
                        with st.expander(f"`{elapsed}` :material/build: tool · `{name}`"):
                            st.code(json.dumps(ev.get("input", {}), indent=2), language="json")

                elif kind == "tool_result":
                    events.append(ev)
                    is_err = ev.get("is_error")
                    icon = ":material/error:" if is_err else ":material/check_circle:"
                    label = "tool error" if is_err else "tool result"
                    with status:
                        with st.expander(f"`{elapsed}` {icon} {label}"):
                            st.code((ev.get("content") or "")[:4000], language="text")

                elif kind == "task_started":
                    events.append(ev)
                    desc = ev.get("description", "subagent")[:120]
                    status.update(label=f"Subagent: {desc}", state="running")
                    with status:
                        st.info(f"`{elapsed}` :material/group_add: **subagent started** — {desc}")

                elif kind == "task_progress":
                    events.append(ev)
                    last = ev.get("last_tool_name", "...")
                    with status:
                        st.caption(f"`{elapsed}` :material/sync: subagent · {last}")

                elif kind == "task_notification":
                    events.append(ev)
                    s = ev.get("status", "")
                    summary = ev.get("summary", "")[:200]
                    icon = {"completed": ":material/check_circle:",
                            "failed": ":material/error:",
                            "stopped": ":material/stop_circle:"}.get(s, ":material/info:")
                    with status:
                        st.success(f"`{elapsed}` {icon} subagent {s} · {summary}")

                elif kind == "done":
                    meta = {
                        "subtype": ev.get("subtype"),
                        "is_error": ev.get("is_error"),
                        "cost_usd": ev.get("cost_usd"),
                        "num_turns": ev.get("num_turns"),
                    }
                    final_state = "error" if ev.get("is_error") else "complete"
                    status.update(
                        label=f"Done in {time.time() - t0:.1f}s · "
                              f"turns={meta.get('num_turns')} · "
                              f"cost=${meta.get('cost_usd') or 0:.4f}",
                        state=final_state,
                        expanded=False,
                    )

        except requests.HTTPError as e:
            accumulated_text += f"\n\n**API error:** {e}"
        except requests.ConnectionError:
            accumulated_text += "\n\n**Connection lost to FastAPI.**"

        text_placeholder.markdown(accumulated_text or "_(no final answer)_")

    st.session_state.messages.append({
        "role": "assistant",
        "content": accumulated_text,
        "events": events,
    })
    st.session_state.last_meta = meta
