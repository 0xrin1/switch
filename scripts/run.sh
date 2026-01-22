#!/bin/bash
# Run Switch directly (not via systemd)

cd "$(dirname "$0")/.."

# Load environment
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# Check for password
if [ -z "$XMPP_PASSWORD" ]; then
    echo "Error: XMPP_PASSWORD not set"
    echo "Create .env file with XMPP_PASSWORD=yourpassword"
    exit 1
fi

# Check for opencode
if ! command -v opencode &> /dev/null; then
    if [ -x "$HOME/.opencode/bin/opencode" ]; then
        export PATH="$HOME/.opencode/bin:$PATH"
    fi
fi

if ! command -v opencode &> /dev/null; then
    echo "Warning: opencode command not found (OpenCode sessions won't work)"
fi

# Check for claude
if ! command -v claude &> /dev/null; then
    echo "Warning: claude command not found (Claude sessions won't work)"
fi

# Check for tmux
if ! command -v tmux &> /dev/null; then
    echo "Error: tmux not found"
    exit 1
fi

echo "Starting Switch..."
uv run python -m src.bridge
