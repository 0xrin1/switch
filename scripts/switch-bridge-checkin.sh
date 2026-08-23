#!/bin/bash
# GlitchTip HEARTBEAT check-in for the Switch bridge.
# Only checks in when switch.service is actually active — silence = down.
# Monitor: "Switch bridge (heartbeat)" id 17, interval 180s.
set -u
CHECKIN_URL="http://localhost:8000/api/0/organizations/switch/heartbeat_check/4fde1e08-22a7-4576-a07f-863b337eff0f/"

if [ "$(systemctl --user is-active switch.service 2>/dev/null)" = "active" ]; then
  curl -s -o /dev/null -m 10 -X POST "$CHECKIN_URL"
  exit 0
fi
# Bridge not active: no check-in → heartbeat expires → monitor flips down.
exit 1
