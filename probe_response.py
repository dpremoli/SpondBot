"""
Probe the Spond API to inspect what change_response actually returns.
Run from the repo root:
    python probe_response.py

It reads credentials from data/config.json (decrypted), fetches the first
upcoming event, prints the raw event responses structure, and then prints
what change_response returns when you accept — using YOUR OWN user_id so
it's a real accept of a real event.

Set DRY_PROBE=1 to skip the change_response call and only print event shape.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Allow importing from the repo root
sys.path.insert(0, str(Path(__file__).parent))

from webui.app import load_config, decrypt_password
from spond.spond import Spond

DRY_PROBE = os.environ.get("DRY_PROBE", "0") == "1"


async def main() -> None:
    cfg = load_config()
    username = os.environ.get("SPOND_USER") or cfg.get("username", "")
    password = os.environ.get("SPOND_PASS") or cfg.get("password", "")

    if not username or not password:
        print("ERROR: no credentials found.")
        print("  Either configure them in the UI (data/config.json), or run:")
        print("  SPOND_USER=you@example.com SPOND_PASS=yourpassword python probe_response.py")
        return

    print(f"Logging in as {username} ...")
    s = Spond(username=username, password=password)
    await s.login()
    print("  OK\n")

    # Fetch profile
    profile = await s.get_profile()
    user_id = (profile.get("profile") or {}).get("id") or profile.get("id")
    print(f"User ID: {user_id}\n")

    # Fetch events
    group_ids = cfg.get("group_ids") or [None]
    events = await s.get_events(group_id=group_ids[0], include_scheduled=True, max_events=20) or []

    if not events:
        print("No events found.")
        await s.clientsession.close()
        return

    # Show the first event's raw shape (especially responses + recipients)
    e = events[0]
    print(f"=== First event: {e.get('heading')} ({e.get('id')}) ===")
    print(f"  startTimestamp : {e.get('startTimestamp')}")
    print(f"  inviteTime     : {e.get('inviteTime')}")
    print(f"  maxAccepted    : {e.get('maxAccepted')}")

    responses = e.get("responses", {})
    print(f"\n  responses keys : {list(responses.keys())}")
    print(f"  responses (trimmed):")
    # Print up to 3 entries per list to keep output readable
    for k, v in responses.items():
        if isinstance(v, list):
            print(f"    {k}: {v[:3]}{'...' if len(v) > 3 else ''}")
        else:
            print(f"    {k}: {v}")

    # Find this user's current status in the responses
    print(f"\n  Searching for user {user_id} in responses...")
    for k, v in responses.items():
        if isinstance(v, list) and any(
            (entry.get("profile", {}).get("id") == user_id or entry.get("id") == user_id)
            if isinstance(entry, dict) else entry == user_id
            for entry in v
        ):
            print(f"    Found in '{k}'")

    if DRY_PROBE:
        print("\nDRY_PROBE=1 — skipping change_response call.")
        await s.clientsession.close()
        return

    # Call change_response and print the full raw result
    selected = cfg.get("selected_event_ids", [])
    target = next((ev for ev in events if ev["id"] in selected), events[0])
    print(f"\n=== Calling change_response on: {target.get('heading')} ===")
    print("  payload: {\"accepted\": \"true\"}")
    result = await s.change_response(target["id"], user_id, {"accepted": "true"})
    print(f"\n  Raw result type : {type(result).__name__}")
    print(f"  Raw result      :")
    print(json.dumps(result, indent=2, default=str))

    await s.clientsession.close()


if __name__ == "__main__":
    asyncio.run(main())
