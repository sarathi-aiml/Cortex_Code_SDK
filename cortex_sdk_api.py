#!/usr/bin/env python3
"""
cortex_sdk_api.py
-----------------
FastAPI wrapper around the Cortex Code Agent SDK.

Endpoints
---------
GET  /health                              liveness
POST /chat                                one-shot query, returns full text
POST /chat/stream                         one-shot query, streams text via SSE
POST /sessions                            create a multi-turn session
POST /sessions/{sid}/messages             send a turn, returns full text
POST /sessions/{sid}/messages/stream      send a turn, streams via SSE
GET  /sessions                            list active sessions
DELETE /sessions/{sid}                    close session

Prereqs (one-time):
    curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh | sh
    pip install "cortex-code-agent-sdk" "fastapi[standard]" uvicorn

Run:
    uvicorn cortex_sdk_api:app --reload --port 8000
    # or:  fastapi dev cortex_sdk_api.py

Try it:
    curl -s -X POST http://127.0.0.1:8000/chat \
        -H 'Content-Type: application/json' \
        -d '{"prompt":"List files in the current directory."}'

    # Streaming
    curl -N -X POST http://127.0.0.1:8000/chat/stream \
        -H 'Content-Type: application/json' \
        -d '{"prompt":"Read README.md and summarize it."}'
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cortex_code_agent_sdk import (
    AssistantMessage,
    CortexCodeAgentOptions,
    CortexCodeSDKClient,
    ResultMessage,
    query,
)


# ---------------------------------------------------------------------------
# Defaults — tweak via env vars or per-request body
# ---------------------------------------------------------------------------
DEFAULT_CONNECTION = os.getenv("SF_CONNECTION", "my_snowflake_connection")
DEFAULT_CWD = os.getenv("SF_SDK_CWD", ".")
DEFAULT_MODEL = os.getenv("SF_SDK_MODEL", "claude-sonnet-4-6")  # faster than auto
DEFAULT_EFFORT = os.getenv("SF_SDK_EFFORT", "low")              # minimal | low | medium | high | max
DEFAULT_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Bash", "Task", "SQL", "Write", "Edit"]


# ---------------------------------------------------------------------------
# Session registry — sid -> live CortexCodeSDKClient
# ---------------------------------------------------------------------------
SESSIONS: dict[str, CortexCodeSDKClient] = {}
LOCK = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Close every open SDK session on shutdown."""
    yield
    for sid, client in list(SESSIONS.items()):
        try:
            await client.disconnect()
        except Exception:
            pass
        SESSIONS.pop(sid, None)


app = FastAPI(
    title="Cortex Code Agent SDK API",
    description="Thin HTTP layer around the Cortex Code Agent SDK.",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    prompt: str = Field(..., description="User prompt")
    cwd: str = DEFAULT_CWD
    connection: str = DEFAULT_CONNECTION
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    allowed_tools: list[str] = Field(default_factory=lambda: DEFAULT_ALLOWED_TOOLS)
    max_turns: int = 4
    # str = full override of default system prompt (NOT recommended — strips tool guidance)
    # dict = {"type": "preset", "append": "..."} appends to default (recommended)
    # None = use SDK default verbatim (recommended for normal use)
    system_prompt: Optional[Union[str, dict[str, Any]]] = None


class ChatResponse(BaseModel):
    text: str
    subtype: Optional[str] = None
    is_error: bool = False
    cost_usd: Optional[float] = None
    num_turns: Optional[int] = None
    session_id: Optional[str] = None


class CreateSessionRequest(BaseModel):
    cwd: str = DEFAULT_CWD
    connection: str = DEFAULT_CONNECTION
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    allowed_tools: list[str] = Field(default_factory=lambda: DEFAULT_ALLOWED_TOOLS)
    max_turns: int = 4
    system_prompt: Optional[Union[str, dict[str, Any]]] = None


class SessionInfo(BaseModel):
    session_id: str


class TurnRequest(BaseModel):
    prompt: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _opts(req: ChatRequest | CreateSessionRequest) -> CortexCodeAgentOptions:
    return CortexCodeAgentOptions(
        cwd=req.cwd,
        connection=req.connection,
        model=req.model,
        effort=req.effort,
        allowed_tools=req.allowed_tools,
        max_turns=req.max_turns,
        system_prompt=req.system_prompt,
        include_partial_messages=True,                # token-level streaming
    )


async def _collect_text(stream) -> ChatResponse:
    """Drain an SDK message stream and return aggregated text."""
    chunks: list[str] = []
    last_result: ResultMessage | None = None
    async for msg in stream:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if hasattr(block, "text"):
                    chunks.append(block.text)
        elif isinstance(msg, ResultMessage):
            last_result = msg
    return ChatResponse(
        text="".join(chunks),
        subtype=last_result.subtype if last_result else None,
        is_error=bool(last_result and last_result.is_error),
        cost_usd=last_result.total_cost_usd if last_result else None,
        num_turns=last_result.num_turns if last_result else None,
    )


async def _sse(stream) -> AsyncIterator[bytes]:
    """Yield Server-Sent Events from an SDK message stream.

    Event types emitted:
        delta            - streaming text chunks
        thinking          - streaming reasoning chunks (claude only)
        thinking_block    - completed thinking block (full text)
        tool_use          - agent invoked a tool
        tool_result       - tool returned a result
        task_started      - subagent task started (multi-agent)
        task_progress     - subagent progress update
        task_notification - subagent task completed/failed
        done              - turn finished (with cost / turn count)
    """
    try:
        from cortex_code_agent_sdk import (
            StreamEvent,
            UserMessage,
            SystemMessage,
        )  # type: ignore
    except Exception:
        StreamEvent = UserMessage = SystemMessage = tuple()

    async for msg in stream:
        # Token-level streaming events
        if StreamEvent and isinstance(msg, StreamEvent):
            ev = msg.event or {}
            etype = ev.get("type")
            delta = ev.get("delta") or {}

            if etype == "content_block_delta":
                if delta.get("type") == "text_delta" or "text" in delta:
                    text = delta.get("text", "")
                    if text:
                        yield _sse_event("delta", {"text": text})
                elif delta.get("type") == "thinking_delta" or "thinking" in delta:
                    thought = delta.get("thinking", "")
                    if thought:
                        yield _sse_event("thinking", {"text": thought})
            continue

        # Assistant turn (full blocks)
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                btype = getattr(block, "type", None)
                if btype == "thinking" or hasattr(block, "thinking"):
                    yield _sse_event(
                        "thinking_block",
                        {"text": getattr(block, "thinking", "")},
                    )
                elif btype == "tool_use" or hasattr(block, "name"):
                    yield _sse_event(
                        "tool_use",
                        {
                            "id": getattr(block, "id", ""),
                            "name": block.name,
                            "input": block.input,
                        },
                    )
                # Final TextBlock content already streamed via "delta"
            continue

        # Tool results arrive as UserMessage with ToolResultBlock content
        if UserMessage and isinstance(msg, UserMessage):
            content = msg.content if isinstance(msg.content, list) else []
            for block in content:
                if getattr(block, "type", None) == "tool_result":
                    yield _sse_event(
                        "tool_result",
                        {
                            "tool_use_id": getattr(block, "tool_use_id", ""),
                            "content": _stringify(getattr(block, "content", "")),
                            "is_error": bool(getattr(block, "is_error", False)),
                        },
                    )
            continue

        # Subagent (multi-agent) task lifecycle
        if SystemMessage and isinstance(msg, SystemMessage):
            sub = getattr(msg, "subtype", "")
            data = getattr(msg, "data", {}) or {}
            if sub == "task_started":
                yield _sse_event("task_started", data)
            elif sub == "task_progress":
                yield _sse_event("task_progress", data)
            elif sub == "task_notification":
                yield _sse_event("task_notification", data)
            continue

        if isinstance(msg, ResultMessage):
            yield _sse_event(
                "done",
                {
                    "subtype": msg.subtype,
                    "is_error": msg.is_error,
                    "cost_usd": msg.total_cost_usd,
                    "num_turns": msg.num_turns,
                },
            )
            return


def _stringify(content) -> str:
    """Tool results may be str | list[block] | None — flatten for transport."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(c.get("text", str(c)))
            else:
                parts.append(str(c))
        return "\n".join(parts)
    return str(content)


def _sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


# ---------------------------------------------------------------------------
# One-shot endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    return {"ok": True, "sessions": len(SESSIONS)}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    return await _collect_text(query(prompt=req.prompt, options=_opts(req)))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _sse(query(prompt=req.prompt, options=_opts(req))),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# Multi-turn session endpoints
# ---------------------------------------------------------------------------
@app.post("/sessions", response_model=SessionInfo)
async def create_session(req: CreateSessionRequest) -> SessionInfo:
    sid = str(uuid.uuid4())
    client = CortexCodeSDKClient(_opts(req))
    try:
        await client.connect()
    except Exception as e:
        # Surface the real cause to the client instead of an opaque 500
        import traceback
        raise HTTPException(
            status_code=500,
            detail={
                "error": e.__class__.__name__,
                "message": str(e),
                "trace": traceback.format_exc(),
            },
        )
    async with LOCK:
        SESSIONS[sid] = client
    return SessionInfo(session_id=sid)


@app.get("/sessions")
async def list_sessions() -> dict:
    return {"session_ids": list(SESSIONS.keys())}


@app.delete("/sessions/{sid}")
async def close_session(sid: str) -> dict:
    async with LOCK:
        client = SESSIONS.pop(sid, None)
    if client is None:
        raise HTTPException(404, f"session {sid} not found")
    await client.disconnect()
    return {"closed": sid}


@app.post("/sessions/{sid}/messages", response_model=ChatResponse)
async def session_send(sid: str, req: TurnRequest) -> ChatResponse:
    client = SESSIONS.get(sid)
    if client is None:
        raise HTTPException(404, f"session {sid} not found")
    await client.query(req.prompt)
    out = await _collect_text(client.receive_response())
    out.session_id = sid
    return out


@app.post("/sessions/{sid}/messages/stream")
async def session_send_stream(sid: str, req: TurnRequest) -> StreamingResponse:
    client = SESSIONS.get(sid)
    if client is None:
        raise HTTPException(404, f"session {sid} not found")
    await client.query(req.prompt)
    return StreamingResponse(
        _sse(client.receive_response()),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# Direct run shortcut
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    # IMPORTANT: use the stdlib asyncio loop, NOT uvloop.
    # uvloop's subprocess_exec rejects the `user=` kwarg that the
    # Cortex Code SDK passes when it spawns the `cortex` CLI.
    uvicorn.run(
        "cortex_sdk_api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        loop="asyncio",
    )
