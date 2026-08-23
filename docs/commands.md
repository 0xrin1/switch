# Commands Reference

Commands are implemented in `~/switch/src/commands/handlers.py` using decorator-based registration.

## Dispatcher Commands

Send these to the dispatcher bot (`cc@`, `oc@`, or `pi-gpt@`):

| Command | Description |
|---------|-------------|
| `<message>` | Create new session (backend depends on which dispatcher you message) |
| `/list` | Show all sessions |
| `/recent` | Show 10 most recent sessions with status |
| `/kill <name>` | End a session (best-effort goodbye + unregister XMPP account + stop reconnect + mark closed) |
| `/ralph <args>` | Create a session and start a Ralph loop |
| `/heartbeat [args]` | Create a session, start the heartbeat ralph, point the watchdog |
| `/help` | Show help message |

## Session Commands

Send these to a session contact (`session-name@domain`):

### Engine Control

| Command | Description |
|---------|-------------|
| `/agent oc` | Switch to OpenCode backend |
| `/agent cc` | Switch to Claude backend |
| `/agent cursor` | Switch to Cursor Agent backend |
| `/model <id>` | Set the backend model ID |
| `/thinking [level]` | Show or set reasoning effort (levels depend on backend/model) |
| `/speed [standard\|fast]` | Show or set speed for models with selectable variants |
| `/reset` | Clear session context (start fresh) |

### Execution Control

| Command | Description |
|---------|-------------|
| `/cancel` | Abort current AI run |
| `/peek [N]` | Show last N lines of output (default: 30, min: 100) |
| `/kill` | End this session (best-effort goodbye + unregister XMPP account + stop reconnect + mark closed) |

### Shell Commands

| Command | Description |
|---------|-------------|
| `!<command>` | Run shell command and show output |

Example: `!git status`, `!pwd`, `!ls -la`

### Ralph Loop (Autonomous)

| Command | Description |
|---------|-------------|
| `/ralph <prompt>` | Start infinite loop (dangerous!) |
| `/ralph <N> <prompt>` | Run up to N iterations |
| `/ralph <prompt> --max N` | Same as above |
| `/ralph <prompt> --done "promise"` | Stop when AI outputs `<promise>...</promise>` |
| `/ralph <prompt> --wait M` | Wait M minutes between iterations |
| `/ralph <prompt> --look` | Prompt-only mode: each iteration runs with no prior context |
| `/ralph-look <prompt>` | Alias for `/ralph <prompt> --look` |
| `/ralph-status` | Check loop progress |
| `/ralph-cancel` | Stop after current iteration |
| `/heartbeat [prompt/flags]` | Cycle prompt + `--wait 30`, or same flags as `/ralph`. Points the watchdog. No `--swarm` |
| `/heartbeat-status` | Loop status + which session the watchdog watches |
| `/heartbeat-cancel` | Stop loop and idle the watchdog |

Example:
```
/ralph 10 Fix all TypeScript errors --wait 5 --done "All errors fixed"
```

### Sibling Sessions

When a session is busy processing, prefix with `+` to spawn a parallel session:

```
+Start a new task while the other one runs
```

## Optional Integrations

### Calendar (`/cal`)

| Command | Description |
|---------|-------------|
| `/cal` | List upcoming events |
| `/cal add <title> <YYYY-MM-DD> [HH:MM]` | Add event |
| `/cal rm <event-id>` | Remove event |

### Telegram (`/tg`)

| Command | Description |
|---------|-------------|
| `/tg send <message>` | Send to all configured chats |
| `/tg history [N]` | Show last N messages |
| `/tg poll` | Fetch new messages |
