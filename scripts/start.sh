#!/bin/bash
# Start Switch

systemctl --user start switch.service
systemctl --user status switch.service --no-pager

echo ""
echo "Commands:"
echo "  Logs:    scripts/logs.sh"
echo "  Stop:    scripts/stop.sh"
echo "  Status:  systemctl --user status switch"
