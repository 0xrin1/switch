#!/bin/bash
# GlitchTip HEARTBEAT check-in for the Switch bridge.
# Only checks in when switch.service is actually active — silence = down.
# Config: SWITCH_GLITCHTIP_CHECKIN_URL (systemd unit or environment).
set -u
CHECKIN_URL="${SWITCH_GLITCHTIP_CHECKIN_URL:-}"
if [ -z "$CHECKIN_URL" ]; then
  echo "switch-bridge-checkin: SWITCH_GLITCHTIP_CHECKIN_URL is unset" >&2
  exit 1
fi

if [ "$(systemctl --user is-active switch.service 2>/dev/null)" = "active" ]; then
  curl -s -o /dev/null -m 10 -X POST "$CHECKIN_URL"
  exit 0
fi
# Bridge not active: no check-in → heartbeat expires → monitor flips down.
exit 1
