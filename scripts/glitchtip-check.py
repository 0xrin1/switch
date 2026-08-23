#!/usr/bin/env python3
"""GlitchTip issue checker for the heartbeat.

Stdlib only, every call time-boxed, any failure = JSON error + exit 0.
Never crashes the heartbeat loop.

Usage:
  glitchtip-check.py            # list unresolved issues (one JSON object per line)
  glitchtip-check.py --mark-seen  # record current issue set as "seen"

Config (~/glitchtip/.env): GLITCHTIP_URL (default http://localhost:8000),
GLITCHTIP_API_TOKEN (required), GLITCHTIP_ORG (default "switch").

Per-line output fields:
  id, count, type, location, first_seen
  new   — issue id not seen by a previous heartbeat cycle
  grew  — event count increased since last cycle (repeating error)

State: ~/switch/state/glitchtip-heartbeat.json  ({id: last count})
"""
import json
import os
import sys
import urllib.request

BASE = os.path.expanduser("~/switch")
ENV_FILE = os.path.expanduser("~/glitchtip/.env")
STATE_FILE = os.path.join(BASE, "state", "glitchtip-heartbeat.json")
TIMEOUT = 10


def fail(msg):
    print(json.dumps({"error": msg}))
    sys.exit(0)


def load_env():
    cfg = {
        "url": "http://localhost:8000",
        "org": "switch",
        "token": "",
    }
    try:
        for line in open(ENV_FILE):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k == "GLITCHTIP_URL":
                cfg["url"] = v
            elif k == "GLITCHTIP_API_TOKEN":
                cfg["token"] = v
            elif k == "GLITCHTIP_ORG":
                cfg["org"] = v
    except Exception as e:
        fail(f"env: {e}")
    if not cfg["token"]:
        fail("no GLITCHTIP_API_TOKEN in ~/glitchtip/.env")
    return cfg


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}


def main():
    cfg = load_env()

    if "--mark-seen" in sys.argv:
        # Record the current issue set (re-fetch so seen==reality)
        issues = fetch(cfg)
        state = {i["id"]: i["count"] for i in issues}
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
        print(json.dumps({"marked_seen": sorted(state)}))
        return

    issues = fetch(cfg)
    seen = load_state()
    for i in issues:
        if i["status"] != "unresolved":
            continue
        iid = i["id"]
        out = {
            "id": iid,
            "count": int(i.get("count") or 0),
            "type": (i.get("metadata") or {}).get("type") or i.get("title", "?")[:60],
            "location": (i.get("metadata") or {}).get("filename", ""),
            "first_seen": i.get("firstSeen") or i.get("first_seen") or "",
            "new": iid not in seen,
            "grew": iid in seen and int(i.get("count") or 0) > int(seen[iid]),
        }
        print(json.dumps(out))


def fetch(cfg):
    url = f"{cfg['url'].rstrip('/')}/api/0/organizations/{cfg['org']}/issues/"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg['token']}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.load(r)
    except Exception as e:
        fail(f"fetch: {e}")
    if isinstance(data, dict) and data.get("detail"):
        fail(f"api: {data['detail']}")
    return data if isinstance(data, list) else data.get("results", [])


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        fail(f"unexpected: {e}")
