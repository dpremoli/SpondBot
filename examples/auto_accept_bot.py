#!/usr/bin/env python3
"""
SpondBot - automatically accept Spond event invites at a scheduled time.

Usage:
    python auto_accept_bot.py [--time HH:MM] [--group GROUP_ID] [--dry-run] [--once]

Credentials are read from a `config.py` file (copy `config.py.sample` and fill
it in), or from the environment variables SPOND_USERNAME and SPOND_PASSWORD.

Modes:
  Daemon (default): runs continuously, accepting all pending invites once per
                    day at the configured time.
  One-shot (--once): accepts all pending invites immediately and exits.
                     Useful when triggered by an external scheduler (cron, etc.).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta
from datetime import time as dtime

try:
    from config import password, username
except ImportError:
    username = os.environ.get("SPOND_USERNAME", "")
    password = os.environ.get("SPOND_PASSWORD", "")

from spond import spond

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_own_ids(session: spond.Spond) -> tuple[str, set[str]]:
    """Return (profile_id, set_of_member_ids) for the logged-in user.

    Spond uses two different kinds of IDs:
    - profile ID : your account-level unique ID (from get_profile)
    - member ID  : a group-specific membership ID (from get_groups)

    Which type appears in unansweredIds varies; we collect both and check
    against whichever matches.  The profile ID alone is always the fallback.
    """
    profile = await session.get_profile()
    own_profile_id: str = profile["id"]
    log.info(
        "Logged in as: %s %s (profile id: %s)",
        profile["firstName"],
        profile["lastName"],
        own_profile_id,
    )

    member_ids: set[str] = set()
    try:
        groups = await session.get_groups()
        for group in groups or []:
            for member in group.get("members", []):
                # A regular member can always see their own profile entry.
                if member.get("profile", {}).get("id") == own_profile_id:
                    member_ids.add(member["id"])
    except Exception:
        log.debug("Could not resolve group member IDs; will rely on profile ID only.")

    log.debug(
        "Own member IDs across all groups: %s (profile id: %s)",
        member_ids,
        own_profile_id,
    )
    return own_profile_id, member_ids


async def accept_pending_invites(
    session: spond.Spond,
    own_profile_id: str,
    own_member_ids: set[str],
    group_id: str | None = None,
    dry_run: bool = False,
) -> int:
    """Find and accept every pending invite for the current user.

    Parameters
    ----------
    session:
        Authenticated Spond session.
    own_profile_id:
        The user's account-level profile ID.
    own_member_ids:
        Set of group-membership IDs belonging to the user.
    group_id:
        Optional - only consider events from this group.
    dry_run:
        If True, log what would be accepted but make no API changes.

    Returns
    -------
    int
        Number of invites accepted (0 in dry-run mode).
    """
    now = datetime.utcnow()
    events = await session.get_events(group_id=group_id, min_start=now)

    if not events:
        log.info("No upcoming events found.")
        return 0

    log.info("Scanning %d upcoming event(s) for pending invites ...", len(events))
    accepted_count = 0

    for event in events:
        responses = event.get("responses", {})
        unanswered: list[str] = responses.get("unansweredIds", [])

        # Find which of our IDs (if any) appears in this event's unanswered list.
        matching_ids = (own_member_ids | {own_profile_id}) & set(unanswered)
        if not matching_ids:
            continue

        event_name: str = event.get("heading", "<no heading>")
        event_start: str = event.get("startTimestamp", "?")
        event_id: str = event["id"]

        # Use the first matching ID (typically only one will match per event).
        user_id = next(iter(matching_ids))

        if dry_run:
            log.info(
                "[DRY RUN] Would accept: '%s' (starts %s, event id: %s)",
                event_name,
                event_start,
                event_id,
            )
        else:
            log.info(
                "Accepting invite for: '%s' (starts %s)",
                event_name,
                event_start,
            )
            try:
                await session.change_response(event_id, user_id, {"accepted": True})
                log.info("  -> Accepted successfully.")
                accepted_count += 1
            except Exception:
                log.exception("  -> Failed to accept '%s'.", event_name)

    if not dry_run and accepted_count == 0:
        log.info("No pending invites found.")

    return accepted_count


def _seconds_until(target: dtime) -> float:
    """Seconds until the next wall-clock occurrence of *target* (today or tomorrow)."""
    now = datetime.now()
    candidate = datetime.combine(now.date(), target)
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------


async def run_once(args: argparse.Namespace) -> None:
    """Accept all pending invites once and exit."""
    log.info("SpondBot - one-shot mode.")
    if args.dry_run:
        log.info("DRY RUN: no invites will actually be accepted.")

    session = spond.Spond(username=username, password=password)
    try:
        own_profile_id, own_member_ids = await _get_own_ids(session)
        count = await accept_pending_invites(
            session,
            own_profile_id,
            own_member_ids,
            group_id=args.group,
            dry_run=args.dry_run,
        )
        log.info("Done. %d invite(s) accepted.", count)
    finally:
        await session.clientsession.close()


async def run_daemon(args: argparse.Namespace, accept_time: dtime) -> None:
    """Run forever, accepting invites once per day at *accept_time*."""
    log.info(
        "SpondBot - daemon mode. Will accept pending invites daily at %s.",
        accept_time.strftime("%H:%M"),
    )
    if args.dry_run:
        log.info("DRY RUN: no invites will actually be accepted.")
    if args.group:
        log.info("Filtering to group ID: %s", args.group)

    while True:
        wait_secs = _seconds_until(accept_time)
        next_run = datetime.now() + timedelta(seconds=wait_secs)
        log.info(
            "Next run at %s (in %.0f s / %.1f h).",
            next_run.strftime("%Y-%m-%d %H:%M:%S"),
            wait_secs,
            wait_secs / 3600,
        )

        await asyncio.sleep(wait_secs)

        session = spond.Spond(username=username, password=password)
        try:
            own_profile_id, own_member_ids = await _get_own_ids(session)
            await accept_pending_invites(
                session,
                own_profile_id,
                own_member_ids,
                group_id=args.group,
                dry_run=args.dry_run,
            )
        except Exception:
            log.exception("Unexpected error during acceptance run.")
        finally:
            await session.clientsession.close()

        # Brief pause to avoid immediately re-firing at the same second.
        await asyncio.sleep(61)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatically accept Spond event invites at a scheduled time."
    )
    parser.add_argument(
        "--time",
        default="08:00",
        metavar="HH:MM",
        help="Time of day to accept pending invites (default: 08:00)",
    )
    parser.add_argument(
        "--group",
        metavar="GROUP_ID",
        default=None,
        help="Only accept invites for events belonging to this group",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be accepted without making any changes",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Accept pending invites immediately and exit (skips time scheduling)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if not username or not password:
        log.error(
            "No credentials found. "
            "Either create examples/config.py (copy config.py.sample) "
            "or set the SPOND_USERNAME and SPOND_PASSWORD environment variables."
        )
        raise SystemExit(1)

    try:
        accept_time = datetime.strptime(args.time, "%H:%M").time()
    except ValueError:
        log.error(
            "Invalid time format '%s'. Expected HH:MM, e.g. --time 08:00.", args.time
        )
        raise SystemExit(1)

    if args.once:
        asyncio.run(run_once(args))
    else:
        asyncio.run(run_daemon(args, accept_time))


if __name__ == "__main__":
    main()
