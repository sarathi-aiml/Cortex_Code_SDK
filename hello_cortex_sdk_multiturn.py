#!/usr/bin/env python3
"""
hello_cortex_sdk_multiturn.py
-----------------------------
Multi-turn sample of the Cortex Code Agent SDK.

Holds one persistent agent session and sends multiple prompts; each
prompt sees the full prior conversation (files read, tool results,
intermediate reasoning).

Prereqs (one-time):
    curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh | sh
    pip install cortex-code-agent-sdk
    # ~/.snowflake/connections.toml must contain a connection named below

Run:
    python hello_cortex_sdk_multiturn.py
"""

import asyncio

from cortex_code_agent_sdk import (
    CortexCodeSDKClient,
    CortexCodeAgentOptions,
    AssistantMessage,
    ResultMessage,
)


# ---------- Helpers --------------------------------------------------------

async def stream_response(client: CortexCodeSDKClient) -> None:
    """Print assistant text + tool names; stop after the ResultMessage."""
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if hasattr(block, "text"):
                    print(block.text, end="", flush=True)
                elif hasattr(block, "name"):           # ToolUseBlock
                    print(f"\n[tool] {block.name} {block.input}")
        elif isinstance(msg, ResultMessage):
            print(
                f"\n--- turn done: {msg.subtype}  "
                f"cost=${msg.total_cost_usd}  turns={msg.num_turns} ---\n"
            )


# ---------- Main -----------------------------------------------------------

async def main() -> None:
    options = CortexCodeAgentOptions(
        cwd=".",                                       # project root
        connection="my_snowflake_connection",           # ~/.snowflake/connections.toml
        model="auto",                                  # or claude-sonnet-4-6, openai-gpt-5.2
        allowed_tools=["Read", "Glob", "Grep", "Bash"],
        max_turns=8,
    )

    async with CortexCodeSDKClient(options) as client:
        # Turn 1: the agent reads and absorbs the file
        await client.query("Read README.md and remember its contents.")
        await stream_response(client)

        # Turn 2: relies on context from turn 1 ("it" -> README.md)
        await client.query("Summarize it in three bullet points.")
        await stream_response(client)

        # Turn 3: builds further on the same conversation
        await client.query(
            "Suggest two concrete improvements based on what you just read."
        )
        await stream_response(client)


if __name__ == "__main__":
    asyncio.run(main())
