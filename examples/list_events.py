#!/usr/bin/env python3
"""List upcoming Spond events for a group.

Usage:
    python list_events.py [--group GROUP_ID]

Reads credentials from config.py (or SPOND_USERNAME / SPOND_PASSWORD env vars).
If no --group is given, falls back to club_id from config.py.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime

try:
    from config import password, username
except ImportError:
    username = os.environ.get("SPOND_USERNAME", "")
    password = os.environ.get("SPOND_PASSWORD", "")

try:
    from config import club_id as default_group_id
except ImportError:
    default_group_id = None

from spond import spond


async def main(group_id: str | None) -> None:
    session = spond.Spond(username=username, password=password)
    try:
        now = datetime.utcnow()
        events = await session.get_events(group_id=group_id, min_start=now)

        if not events:
            print("No upcoming events found.")
            return

        print(f"\n{len(events)} upcoming event(s):\n")
        for i, event in enumerate(events):
            print(
                f"[{i}] {event.get('heading', '<no heading>')}\n"
                f"     Start : {event.get('startTimestamp', '?')}\n"
                f"     ID    : {event['id']}\n"
                f"     Status: accepted={len(event.get('responses', {}).get('acceptedIds', []))} "
                f"declined={len(event.get('responses', {}).get('declinedIds', []))} "
                f"unanswered={len(event.get('responses', {}).get('unansweredIds', []))}\n"
            )
    finally:
        await session.clientsession.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List upcoming Spond events.")
    parser.add_argument(
        "--group",
        metavar="GROUP_ID",
        default=default_group_id,
        help="Group ID to filter events (defaults to club_id from config.py)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    if not username or not password:
        raise SystemExit(
            "No credentials found. Create examples/config.py or set "
            "SPOND_USERNAME / SPOND_PASSWORD environment variables."
        )
    args = _parse_args()
    asyncio.run(main(args.group))
