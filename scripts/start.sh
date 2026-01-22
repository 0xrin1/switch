#!/bin/bash
# Start the XMPP-OpenCode bridge

systemctl --user start xmpp-opencode-bridge.service
systemctl --user status xmpp-opencode-bridge.service --no-pager

echo ""
echo "Commands:"
echo "  Logs:    scripts/logs.sh"
echo "  Stop:    scripts/stop.sh"
echo "  Status:  systemctl --user status xmpp-opencode-bridge"
