# Cortex Code — Hooks Configuration & Enterprise Deployment Guide

**Audience:** Platform / IT Administrators  
**Purpose:** Configure lifecycle hooks for all employees and deploy via MDM or config management

---

## What Are Hooks?

Hooks run custom shell scripts at specific points during Cortex Code execution. They can validate, block, log, or inject context into sessions — enabling compliance, auditing, and guardrails organization-wide.

---

## Available Events & When to Use Each

| # | Event | Fires When | Use Case |
|---|-------|-----------|----------|
| 1 | `SessionStart` | User opens a new session | Audit logging, inject context, load team config |
| 2 | `SessionEnd` | Session closes | Cleanup, log session duration, write metrics |
| 3 | `UserPromptSubmit` | User sends a message | Block banned topics, inject disclaimers, log prompts |
| 4 | `PreToolUse` | Before a tool executes | Block dangerous commands, enforce policies |
| 5 | `PostToolUse` | After a tool executes | Audit tool outputs, detect sensitive data in results |
| 6 | `PermissionRequest` | Agent asks for permission | Auto-approve safe tools, auto-deny risky ones |
| 7 | `Stop` | Agent finishes a turn | Verify completion, add summary context |
| 8 | `SubagentStop` | A subagent finishes | Log subagent results, validate outputs |
| 9 | `Notification` | Notification fires | Custom notification routing |
| 10 | `PreCompact` | Before context compaction | Inject must-keep context before summarization |

---

## hooks.json (Full Configuration)

Place at `~/.snowflake/cortex/hooks.json` on each user's machine:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "/opt/cortex-hooks/session-start.sh", "timeout": 10 }] }
    ],
    "SessionEnd": [
      { "hooks": [{ "type": "command", "command": "/opt/cortex-hooks/session-end.sh", "timeout": 10 }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "/opt/cortex-hooks/user-prompt-submit.sh", "timeout": 10 }] }
    ],
    "PreToolUse": [
      { "matcher": ".*", "hooks": [{ "type": "command", "command": "/opt/cortex-hooks/pre-tool-use.sh", "timeout": 10 }] }
    ],
    "PostToolUse": [
      { "matcher": ".*", "hooks": [{ "type": "command", "command": "/opt/cortex-hooks/post-tool-use.sh", "timeout": 10 }] }
    ],
    "PermissionRequest": [
      { "matcher": ".*", "hooks": [{ "type": "command", "command": "/opt/cortex-hooks/permission-request.sh", "timeout": 10 }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "/opt/cortex-hooks/stop.sh", "timeout": 10 }] }
    ],
    "SubagentStop": [
      { "hooks": [{ "type": "command", "command": "/opt/cortex-hooks/subagent-stop.sh", "timeout": 10 }] }
    ],
    "Notification": [
      { "hooks": [{ "type": "command", "command": "/opt/cortex-hooks/notification.sh", "timeout": 10 }] }
    ],
    "PreCompact": [
      { "hooks": [{ "type": "command", "command": "/opt/cortex-hooks/pre-compact.sh", "timeout": 10 }] }
    ]
  }
}
```

---

## Sample Shell Scripts

Each script receives JSON on **stdin** with fields like `session_id`, `cwd`, `hook_event_name`, and event-specific data.

### Exit Code Behavior

| Code | Meaning |
|------|---------|
| `0` | Success — continue normally |
| `2` | **Block** the operation (stderr message sent to agent) |
| Other | Non-blocking error (shown to user as warning) |

---

### 1. session-start.sh

```bash
#!/bin/bash
# Logs session start for audit trail
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
USER=$(whoami)
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) SESSION_START user=$USER session=$SESSION_ID" >> /var/log/cortex-hooks/audit.log
exit 0
```

---

### 2. session-end.sh

```bash
#!/bin/bash
# Logs session end
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) SESSION_END session=$SESSION_ID" >> /var/log/cortex-hooks/audit.log
exit 0
```

---

### 3. user-prompt-submit.sh

```bash
#!/bin/bash
# Blocks prompts containing sensitive keywords
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.tool_input.prompt // empty')

BLOCKED_WORDS="password|secret|credentials|api_key"
if echo "$PROMPT" | grep -qiE "$BLOCKED_WORDS"; then
  echo "Prompt blocked: contains sensitive keywords. Please remove references to secrets." >&2
  exit 2
fi
exit 0
```

---

### 4. pre-tool-use.sh

```bash
#!/bin/bash
# Blocks dangerous bash commands
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name')
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if [[ "$TOOL" == "Bash" ]]; then
  if echo "$CMD" | grep -qE "rm -rf|drop database|truncate|format|mkfs"; then
    echo "Blocked: destructive command not allowed by policy." >&2
    exit 2
  fi
fi
exit 0
```

---

### 5. post-tool-use.sh

```bash
#!/bin/bash
# Logs tool usage for compliance
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) TOOL_USED tool=$TOOL session=$SESSION_ID" >> /var/log/cortex-hooks/audit.log
exit 0
```

---

### 6. permission-request.sh

```bash
#!/bin/bash
# Auto-approve read tools, deny dangerous ones
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name')

if [[ "$TOOL" == "Read" || "$TOOL" == "Glob" || "$TOOL" == "Grep" ]]; then
  echo '{"decision":"approve","hookSpecificOutput":{"hookEventName":"PermissionRequest","permissionDecision":"allow"}}'
  exit 0
fi
exit 0
```

---

### 7. stop.sh

```bash
#!/bin/bash
# Logs agent stop event
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) AGENT_STOP session=$SESSION_ID" >> /var/log/cortex-hooks/audit.log
exit 0
```

---

### 8. subagent-stop.sh

```bash
#!/bin/bash
# Logs subagent completion
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) SUBAGENT_STOP session=$SESSION_ID" >> /var/log/cortex-hooks/audit.log
exit 0
```

---

### 9. notification.sh

```bash
#!/bin/bash
# Forward notifications to a logging endpoint
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) NOTIFICATION session=$SESSION_ID" >> /var/log/cortex-hooks/audit.log
exit 0
```

---

### 10. pre-compact.sh

```bash
#!/bin/bash
# Inject compliance reminder before context compaction
INPUT=$(cat)
echo '{"decision":"approve","reason":"Compaction approved"}'
exit 0
```

---

## How to Deploy Hooks to All Employees

### Option A: Managed Settings (Recommended — MDM/Config Management)

Cortex Code supports **managed settings** — a system-owned JSON file that users **cannot override**.

**File locations (requires admin/root to write):**

| Platform | Path |
|----------|------|
| macOS | `/Library/Application Support/Cortex/managed-settings.json` |
| Linux/WSL | `/etc/cortex/managed-settings.json` |
| Windows | `%ProgramData%\Cortex\managed-settings.json` |

**Deploy using:**
- **Jamf** (macOS)
- **Intune** (Windows/macOS)
- **SCCM** (Windows)
- **Ansible / Chef / Puppet** (Linux)

**Deploy the hook scripts** to a shared system path (e.g., `/opt/cortex-hooks/`) and the `hooks.json` to each user's `~/.snowflake/cortex/hooks.json`.

---

### Option B: Plugin via Git Repository (Self-Service)

Package hooks as a **Cortex Code Plugin** in a Git repo:

```
company-hooks/
├── .cortex-plugin/
│   └── plugin.json
└── hooks/
    ├── hooks.json
    ├── session-start.sh
    ├── session-end.sh
    ├── user-prompt-submit.sh
    ├── pre-tool-use.sh
    ├── post-tool-use.sh
    ├── permission-request.sh
    ├── stop.sh
    ├── subagent-stop.sh
    ├── notification.sh
    └── pre-compact.sh
```

**plugin.json:**

```json
{
  "name": "company-compliance-hooks",
  "description": "Corporate compliance hooks for Cortex Code",
  "version": "1.0.0",
  "author": { "name": "Platform Team" },
  "hooks": "./hooks/hooks.json"
}
```

Employees install with:

```bash
cortex plugin install your-org/company-hooks
```

Or enforce via **connection profile** so all users of that profile automatically get the hooks.

---

### Option C: Direct File Deployment (Ansible Example)

Push `hooks.json` and scripts directly to every user's machine:

```yaml
- name: Deploy Cortex Code hooks
  hosts: all
  tasks:
    - name: Create hooks script directory
      file:
        path: /opt/cortex-hooks
        state: directory
        mode: "0755"

    - name: Copy hook scripts
      copy:
        src: hooks/
        dest: /opt/cortex-hooks/
        mode: "0755"

    - name: Deploy hooks.json for all users
      copy:
        src: hooks.json
        dest: "/home/{{ item }}/.snowflake/cortex/hooks.json"
      loop: "{{ users }}"

    - name: Create log directory
      file:
        path: /var/log/cortex-hooks
        state: directory
        mode: "0777"
```

---

## Deployment Methods Comparison

| Method | Enforceability | Effort | User Can Override? |
|--------|---------------|--------|-------------------|
| **Managed Settings** (MDM) | Highest | Medium | No |
| **Plugin via Git** | Medium | Low | Yes (can disable) |
| **Direct file push** | Low | Low | Yes (can edit) |

**Recommendation:** Use **Managed Settings via MDM** for compliance-critical hooks. Use the **Plugin approach** for team-recommended hooks that don't require strict enforcement.

---

## Prerequisites on Employee Machines

- **Cortex Code CLI** installed (minimum version as required)
- **`jq`** installed (for JSON parsing in hook scripts)
- **Log directory** exists: `/var/log/cortex-hooks/` (writable by user)
- Hook scripts must be **executable**: `chmod +x /opt/cortex-hooks/*.sh`

---

## Testing Hooks

Test any hook manually by piping sample JSON:

```bash
# Test pre-tool-use hook
echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"},"session_id":"test-123"}' | /opt/cortex-hooks/pre-tool-use.sh
# Expected: exit code 2, stderr message about blocked command

# Test session-start hook
echo '{"session_id":"test-456","hook_event_name":"SessionStart"}' | /opt/cortex-hooks/session-start.sh
# Expected: exit code 0, line written to audit.log
```

---

## Key Notes

- Hooks config is **snapshotted at session start** — users must restart Cortex Code to pick up changes
- Use **absolute paths** for all script references in hooks.json
- Keep hooks **fast** (respect the timeout setting)
- Use exit code `2` **only** for true blocks — don't overuse
- stdout from hooks is interpreted as JSON output to the agent
- stderr with exit code 2 is the block message shown to the agent
