#!/usr/bin/env python3
"""
SpondBot - competitive Spond event invite acceptor.

Watch mode (recommended, used with Docker):
    python auto_accept_bot.py --watch --invite-time 09:00 [options]

One-shot mode (e.g. via cron):
    python auto_accept_bot.py [options]

Credentials:
    Set SPOND_USERNAME / SPOND_PASSWORD environment variables,
    or create a config.py with `username` and `password` variables.
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
# ID resolution
# ---------------------------------------------------------------------------


async def _get_own_ids(session: spond.Spond) -> tuple[str, set[str]]:
    """Return (profile_id, set_of_member_ids) for the logged-in user.

    Spond uses two ID types:
    - profile ID : account-level, from get_profile()
    - member ID  : group-specific, from get_groups()

    unansweredIds in events may contain either; we collect both so the
    match works regardless.
    """
    profile = await session.get_profile()
    own_profile_id: str = profile["id"]
    log.info(
        "Logged in as: %s %s",
        profile["firstName"],
        profile["lastName"],
    )

    member_ids: set[str] = set()
    try:
        groups = await session.get_groups()
        for group in groups or []:
            for member in group.get("members", []):
                if member.get("profile", {}).get("id") == own_profile_id:
                    member_ids.add(member["id"])
    except Exception:
        log.debug("Could not resolve group member IDs; will rely on profile ID only.")

    return own_profile_id, member_ids


# ---------------------------------------------------------------------------
# Event matching and acceptance
# ---------------------------------------------------------------------------


def _name_matches(event_name: str, filters: list[str]) -> bool:
    """Return True if event_name contains any filter string (case-insensitive)."""
    if not filters:
        return True
    name_lower = event_name.lower()
    return any(f.lower() in name_lower for f in filters)


async def _try_accept(
    session: spond.Spond,
    event_id: str,
    event_name: str,
    user_id: str,
    accept_delay: float,
    max_retries: int,
    retry_delay: float,
    dry_run: bool,
) -> bool:
    """Wait accept_delay seconds then accept the event, retrying on failure."""
    if accept_delay > 0:
        log.info("  Waiting %.2fs before accepting ...", accept_delay)
        await asyncio.sleep(accept_delay)

    if dry_run:
        log.info("[DRY RUN] Would accept: '%s'", event_name)
        return True

    for attempt in range(1, max_retries + 1):
        try:
            await session.change_response(event_id, user_id, {"accepted": True})
            log.info(
                "  -> Accepted '%s' (attempt %d/%d).", event_name, attempt, max_retries
            )
            return True
        except Exception as exc:
            log.warning(
                "  -> Attempt %d/%d failed for '%s': %s",
                attempt,
                max_retries,
                event_name,
                exc,
            )
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)

    log.error("  -> Gave up on '%s' after %d attempts.", event_name, max_retries)
    return False


async def poll_and_accept(
    session: spond.Spond,
    own_profile_id: str,
    own_member_ids: set[str],
    accepted_ids: set[str],
    args: argparse.Namespace,
) -> int:
    """Fetch upcoming events, accept any pending invites, return count accepted."""
    now = datetime.utcnow()
    events = await session.get_events(group_id=args.group, min_start=now)
    if not events:
        return 0

    newly_accepted = 0
    for event in events:
        event_id: str = event["id"]
        if event_id in accepted_ids:
            continue

        event_name: str = event.get("heading", "<no heading>")
        if not _name_matches(event_name, args.event_filter or []):
            continue

        unanswered: list[str] = event.get("responses", {}).get("unansweredIds", [])
        matching_ids = (own_member_ids | {own_profile_id}) & set(unanswered)
        if not matching_ids:
            continue

        user_id = next(iter(matching_ids))
        log.info(
            "Pending invite: '%s' (starts %s)",
            event_name,
            event.get("startTimestamp", "?"),
        )

        ok = await _try_accept(
            session,
            event_id,
            event_name,
            user_id,
            args.accept_delay,
            args.max_retries,
            args.retry_delay,
            args.dry_run,
        )
        if ok:
            accepted_ids.add(event_id)
            newly_accepted += 1

    return newly_accepted


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------


def _sleep_duration(
    invite_time: dtime | None,
    pre_window: float,
    active_poll: float,
    idle_check: float,
) -> float:
    """Return how long to sleep before the next poll.

    Inside the active window [invite_time - pre_window, invite_time + pre_window*10]:
        returns active_poll
    Outside:
        returns min(idle_check, seconds_until_window_opens)
    """
    if invite_time is None:
        return idle_check

    now = datetime.now()
    today_invite = datetime.combine(now.date(), invite_time)
    window_start = today_invite - timedelta(seconds=pre_window)
    window_end = today_invite + timedelta(seconds=pre_window * 10)

    if window_start <= now <= window_end:
        return active_poll

    if now < window_start:
        secs_until = (window_start - now).total_seconds()
    else:
        secs_until = (window_start + timedelta(days=1) - now).total_seconds()

    return min(idle_check, secs_until)


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------


async def run_watch(args: argparse.Namespace, invite_time: dtime | None) -> None:
    """Run forever, polling rapidly around invite_time and slowly otherwise."""
    log.info("SpondBot starting (watch mode).")
    if invite_time:
        log.info(
            "Invite time: %s  pre-window: %ss  active-poll: %ss  idle-check: %ss",
            args.invite_time,
            args.pre_window,
            args.active_poll,
            args.idle_check,
        )
    if args.event_filter:
        log.info("Event name filter: %s", args.event_filter)
    if args.dry_run:
        log.info("DRY RUN — no invites will be accepted.")

    session = spond.Spond(username=username, password=password)
    own_profile_id, own_member_ids = await _get_own_ids(session)

    accepted_ids: set[str] = set()
    consecutive_errors = 0
    was_active = False

    while True:
        sleep_secs = _sleep_duration(
            invite_time, args.pre_window, args.active_poll, args.idle_check
        )
        is_active = invite_time is not None and sleep_secs == args.active_poll

        if is_active and not was_active:
            log.info("Entering active polling window (every %.1fs).", args.active_poll)
        elif not is_active and was_active:
            log.info(
                "Leaving active polling window. Next poll in %.0fs.", sleep_secs
            )
        was_active = is_active

        try:
            count = await poll_and_accept(
                session, own_profile_id, own_member_ids, accepted_ids, args
            )
            if count:
                log.info(
                    "Session total: %d invite(s) accepted.", len(accepted_ids)
                )
            consecutive_errors = 0
        except Exception:
            consecutive_errors += 1
            log.exception("Poll error (%d consecutive).", consecutive_errors)
            if consecutive_errors >= 3:
                log.warning("Recreating session after repeated errors.")
                await session.clientsession.close()
                session = spond.Spond(username=username, password=password)
                consecutive_errors = 0

        await asyncio.sleep(sleep_secs)


async def run_once(args: argparse.Namespace) -> None:
    """Accept all pending invites once and exit."""
    log.info("SpondBot — one-shot mode.")
    if args.dry_run:
        log.info("DRY RUN — no invites will be accepted.")

    session = spond.Spond(username=username, password=password)
    try:
        own_profile_id, own_member_ids = await _get_own_ids(session)
        accepted_ids: set[str] = set()
        count = await poll_and_accept(
            session, own_profile_id, own_member_ids, accepted_ids, args
        )
        log.info("Done. %d invite(s) accepted.", count)
    finally:
        await session.clientsession.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Automatically accept Spond event invites at a scheduled time."
    )
    p.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously (daemon mode)",
    )
    p.add_argument(
        "--event-filter",
        nargs="+",
        metavar="NAME",
        help="Only accept events whose heading contains one of these substrings",
    )
    p.add_argument(
        "--group",
        metavar="GROUP_ID",
        default=None,
        help="Restrict to events from this group",
    )
    p.add_argument(
        "--invite-time",
        metavar="HH:MM",
        default=None,
        help="Local time invites typically go out; triggers rapid polling around this time",
    )
    p.add_argument(
        "--pre-window",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Seconds before invite-time to enter rapid-poll mode (default: 30)",
    )
    p.add_argument(
        "--idle-check",
        type=float,
        default=21600.0,
        metavar="SECONDS",
        help="Poll interval outside the active window in seconds (default: 21600)",
    )
    p.add_argument(
        "--active-poll",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="Poll interval inside the active window in seconds (default: 0.5)",
    )
    p.add_argument(
        "--accept-delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Wait this many seconds after detecting an invite before accepting (default: 0)",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=3,
        metavar="N",
        help="Max acceptance attempts per event (default: 3)",
    )
    p.add_argument(
        "--retry-delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Delay between retry attempts in seconds (default: 1.0)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be accepted without making any changes",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if not username or not password:
        log.error(
            "No credentials found. Set SPOND_USERNAME / SPOND_PASSWORD "
            "or create examples/config.py."
        )
        raise SystemExit(1)

    invite_time: dtime | None = None
    if args.invite_time:
        try:
            invite_time = datetime.strptime(args.invite_time, "%H:%M").time()
        except ValueError:
            log.error(
                "Invalid --invite-time '%s'. Expected HH:MM, e.g. 09:00.",
                args.invite_time,
            )
            raise SystemExit(1)

    if args.watch:
        asyncio.run(run_watch(args, invite_time))
    else:
        asyncio.run(run_once(args))


if __name__ == "__main__":
    main()
