#!/usr/bin/env python3
"""
SpondBot - automatically accept Spond event invites.

Usage:
    python auto_accept_bot.py [--time HH:MM[:SS]] [--group GROUP_ID] [--dry-run] [--once]
    python auto_accept_bot.py --watch --event-filter TEXT --invite-time HH:MM [options]

Credentials are read from a `config.py` file (copy `config.py.sample` and fill
it in), or from the environment variables SPOND_USERNAME and SPOND_PASSWORD.

Modes:
  Daemon (default): runs continuously, accepting all pending invites once per
                    day at --time.
  One-shot (--once): accepts all pending invites immediately and exits.
  Watch (--watch):   two-phase smart poller —
                      • Idle phase  : checks every --idle-check seconds that a
                        matching event exists; sleeps the remainder.
                      • Active phase: starts --pre-window seconds before
                        --invite-time; polls every --active-poll seconds until
                        the invite appears in the user's unansweredIds.
                      • Accept phase: waits --accept-delay seconds, calls
                        accept, re-fetches to confirm, retries up to
                        --max-retries times with --retry-delay spacing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _local_now() -> datetime:
    return datetime.now()


async def _get_own_ids(session: spond.Spond) -> tuple[str, set[str]]:
    """Return (profile_id, set_of_member_ids) for the logged-in user."""
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
    event_filter: str | None = None,
) -> int:
    """Find and accept every pending invite for the current user."""
    now = _utcnow().replace(tzinfo=None)
    events = await session.get_events(group_id=group_id, min_start=now)

    if not events:
        log.info("No upcoming events found.")
        return 0

    log.info("Scanning %d upcoming event(s) for pending invites ...", len(events))
    accepted_count = 0
    own_ids = own_member_ids | {own_profile_id}

    for event in events:
        heading = event.get("heading", "<no heading>")
        if event_filter and event_filter.lower() not in heading.lower():
            continue

        unanswered: list[str] = event.get("responses", {}).get("unansweredIds", [])
        matching_ids = own_ids & set(unanswered)
        if not matching_ids:
            continue

        event_start: str = event.get("startTimestamp", "?")
        event_id: str = event["id"]
        user_id = next(iter(matching_ids))

        if dry_run:
            log.info(
                "[DRY RUN] Would accept: '%s' (starts %s, event id: %s)",
                heading,
                event_start,
                event_id,
            )
        else:
            log.info("Accepting invite for: '%s' (starts %s)", heading, event_start)
            try:
                await session.change_response(event_id, user_id, {"accepted": True})
                log.info("  -> Accepted successfully.")
                accepted_count += 1
            except Exception:
                log.exception("  -> Failed to accept '%s'.", heading)

    if not dry_run and accepted_count == 0:
        log.info("No pending invites found.")

    return accepted_count


async def _accept_with_retry(
    session: spond.Spond,
    event_id: str,
    user_id: str,
    event_name: str,
    group_id: str | None,
    own_ids: set[str],
    max_retries: int,
    retry_delay: float,
    dry_run: bool,
) -> bool:
    """Call accept, re-fetch to verify, retry until confirmed or retries exhausted.

    With hundreds of members accepting simultaneously, Spond's API can lag.
    We send the request, then re-fetch the event to check acceptedIds before
    declaring success. If not confirmed, we retry.
    """
    if dry_run:
        log.info("[DRY RUN] Would accept: '%s'", event_name)
        return True

    for attempt in range(1, max_retries + 1):
        log.info("Accept attempt %d/%d for '%s' ...", attempt, max_retries, event_name)

        try:
            await session.change_response(event_id, user_id, {"accepted": True})
        except Exception:
            log.warning(
                "Attempt %d/%d: API call raised an exception.",
                attempt,
                max_retries,
                exc_info=True,
            )
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
            continue

        # Verify the acceptance was registered.
        try:
            events = await session.get_events(group_id=group_id)
            for ev in events or []:
                if ev["id"] != event_id:
                    continue
                accepted_ids = set(ev.get("responses", {}).get("acceptedIds", []))
                if own_ids & accepted_ids:
                    log.info(
                        "Confirmed accepted '%s' on attempt %d.", event_name, attempt
                    )
                    return True
                log.warning(
                    "Attempt %d/%d: response sent but not yet confirmed "
                    "(unanswered=%d, accepted=%d). Retrying in %.1fs ...",
                    attempt,
                    max_retries,
                    len(ev.get("responses", {}).get("unansweredIds", [])),
                    len(accepted_ids),
                    retry_delay,
                )
                break
            else:
                log.warning(
                    "Attempt %d/%d: event not found during verification.",
                    attempt,
                    max_retries,
                )
        except Exception:
            log.warning(
                "Attempt %d/%d: verification fetch failed.",
                attempt,
                max_retries,
                exc_info=True,
            )

        if attempt < max_retries:
            await asyncio.sleep(retry_delay)

    log.error(
        "Failed to confirm acceptance of '%s' after %d attempts.", event_name, max_retries
    )
    return False


def _seconds_until(target: dtime) -> float:
    """Seconds until the next wall-clock occurrence of *target* (today or tomorrow)."""
    now = _local_now()
    candidate = datetime.combine(now.date(), target)
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


def _active_window_status(
    invite_time: dtime, pre_window: float, post_window: float = 3600.0
) -> tuple[bool, float]:
    """Return (in_active_window, secs_until_next_window_start).

    The active window runs from (invite_time - pre_window) to
    (invite_time + post_window) each day.
    """
    now = _local_now()
    window_start = datetime.combine(now.date(), invite_time) - timedelta(seconds=pre_window)
    window_end = datetime.combine(now.date(), invite_time) + timedelta(seconds=post_window)

    if window_start <= now <= window_end:
        return True, 0.0

    if now < window_start:
        return False, (window_start - now).total_seconds()

    # Past today's window — return time until tomorrow's window.
    tomorrow_start = window_start + timedelta(days=1)
    return False, (tomorrow_start - now).total_seconds()


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
            event_filter=args.event_filter,
        )
        log.info("Done. %d invite(s) accepted.", count)
    finally:
        await session.clientsession.close()


async def run_daemon(args: argparse.Namespace, accept_time: dtime) -> None:
    """Run forever, accepting invites once per day at *accept_time*."""
    log.info(
        "SpondBot - daemon mode. Will accept pending invites daily at %s.",
        accept_time.strftime("%H:%M:%S"),
    )
    if args.dry_run:
        log.info("DRY RUN: no invites will actually be accepted.")
    if args.group:
        log.info("Filtering to group ID: %s", args.group)
    if args.event_filter:
        log.info("Filtering to events containing: '%s'", args.event_filter)

    while True:
        wait_secs = _seconds_until(accept_time)
        next_run = _local_now() + timedelta(seconds=wait_secs)
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
                event_filter=args.event_filter,
            )
        except Exception:
            log.exception("Unexpected error during acceptance run.")
        finally:
            await session.clientsession.close()

        await asyncio.sleep(61)


async def run_watch(args: argparse.Namespace) -> None:
    """Two-phase smart poller.

    Idle phase  — polls every --idle-check seconds to confirm the target event
                  exists.  When --invite-time is set, the loop sleeps at most
                  until (invite_time - pre_window) so the active phase starts
                  on time.

    Active phase — polls every --active-poll seconds, starting --pre-window
                   seconds before --invite-time.  Continues until invite is
                   detected or the window closes (1 h after invite-time).

    Accept phase — on first detection: waits --accept-delay seconds, calls
                   change_response, re-fetches to verify, retries up to
                   --max-retries times with --retry-delay spacing.
    """
    event_filter: str = args.event_filter or ""
    invite_time: dtime | None = args.invite_time
    pre_window: float = args.pre_window
    idle_check: float = args.idle_check
    active_poll: float = args.active_poll
    accept_delay: float = args.accept_delay
    max_retries: int = args.max_retries
    retry_delay: float = args.retry_delay

    log.info("SpondBot - watch mode.")
    log.info("  Event filter : '%s'", event_filter or "<any>")
    log.info("  Group ID     : %s", args.group or "<all groups>")
    if invite_time:
        log.info(
            "  Invite time  : %s  (active window starts %ds before)",
            invite_time.strftime("%H:%M:%S"),
            int(pre_window),
        )
    else:
        log.info(
            "  Invite time  : not set — polling every %.0fs continuously.", idle_check
        )
    log.info(
        "  Accept delay : %.3fs | Max retries: %d | Retry delay: %.1fs",
        accept_delay,
        max_retries,
        retry_delay,
    )
    if args.dry_run:
        log.info("  DRY RUN: no invites will actually be accepted.")

    session = spond.Spond(username=username, password=password)
    own_profile_id, own_member_ids = await _get_own_ids(session)
    own_ids = own_member_ids | {own_profile_id}

    # Event IDs we have already processed (accepted or exhausted retries).
    processed: set[str] = set()

    try:
        while True:
            # ---------------------------------------------------------------
            # Determine poll interval for this cycle.
            # ---------------------------------------------------------------
            if invite_time is not None:
                in_window, secs_to_window = _active_window_status(invite_time, pre_window)
                if in_window:
                    poll_interval = active_poll
                else:
                    # Sleep until the active window opens, capped at idle_check.
                    poll_interval = min(idle_check, secs_to_window)
                    log.info(
                        "Idle. Active window opens in %.0f s. Next check in %.0f s.",
                        secs_to_window,
                        poll_interval,
                    )
            else:
                in_window = True  # always poll at active rate when no invite-time given
                poll_interval = idle_check

            # ---------------------------------------------------------------
            # Poll Spond.
            # ---------------------------------------------------------------
            try:
                now = _utcnow().replace(tzinfo=None)
                events = await session.get_events(group_id=args.group, min_start=now)

                log.info("Poll: %d event(s) returned.", len(events or []))
                for event in events or []:
                    heading = event.get("heading", "<no heading>")
                    start = event.get("startTimestamp", "?")

                    if event_filter and event_filter.lower() not in heading.lower():
                        log.info("  SKIP (name filter) : '%s'", heading)
                        continue
                    if event.get("cancelled"):
                        log.info("  SKIP (cancelled)   : '%s'", heading)
                        continue

                    event_id: str = event["id"]
                    if event_id in processed:
                        log.info("  SKIP (processed)   : '%s'", heading)
                        continue

                    responses = event.get("responses", {})
                    unanswered = set(responses.get("unansweredIds", []))
                    accepted  = set(responses.get("acceptedIds", []))
                    declined  = set(responses.get("declinedIds", []))
                    matching_ids = own_ids & unanswered

                    if own_ids & accepted:
                        log.info("  SKIP (already accepted): '%s' (starts %s)", heading, start)
                        processed.add(event_id)
                        continue
                    if own_ids & declined:
                        log.info("  SKIP (already declined): '%s' (starts %s)", heading, start)
                        processed.add(event_id)
                        continue

                    if not matching_ids:
                        log.info(
                            "  WAITING (no invite yet): '%s' (starts %s) — "
                            "accepted=%d declined=%d unanswered=%d",
                            heading, start,
                            len(accepted), len(declined), len(unanswered),
                        )
                        continue

                    log.info(
                        "  PENDING invite found : '%s' (starts %s) — "
                        "accepted=%d declined=%d unanswered=%d",
                        heading, start,
                        len(accepted), len(declined), len(unanswered),
                    )

                    # -------------------------------------------------------
                    # Invite detected.
                    # -------------------------------------------------------
                    detected_at = _local_now()
                    log.info(
                        "Invite detected for '%s' at %s — waiting %.3fs before accepting.",
                        heading,
                        detected_at.strftime("%H:%M:%S.%f")[:-3],
                        accept_delay,
                    )

                    await asyncio.sleep(accept_delay)

                    user_id = next(iter(matching_ids))
                    ok = await _accept_with_retry(
                        session,
                        event_id,
                        user_id,
                        heading,
                        args.group,
                        own_ids,
                        max_retries,
                        retry_delay,
                        args.dry_run,
                    )
                    processed.add(event_id)
                    if not ok:
                        log.error(
                            "Giving up on '%s' after exhausting all retries.", heading
                        )

            except Exception:
                log.exception("Error during poll cycle — will retry on next interval.")

            await asyncio.sleep(poll_interval)
    finally:
        await session.clientsession.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_time(value: str) -> dtime:
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Invalid time '{value}'. Expected HH:MM or HH:MM:SS."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatically accept Spond event invites.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--time",
        default="08:00",
        metavar="HH:MM[:SS]",
        type=_parse_time,
        help="Daemon mode: time of day to accept pending invites (default: 08:00)",
    )
    parser.add_argument(
        "--group",
        metavar="GROUP_ID",
        default=None,
        help="Only consider events from this group",
    )
    parser.add_argument(
        "--event-filter",
        metavar="TEXT",
        default=None,
        help="Only accept invites for events whose heading contains TEXT (case-insensitive)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be accepted without making any changes",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Accept pending invites immediately and exit",
    )
    # Watch mode
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Smart watch mode: idle polling + rapid burst around invite time",
    )
    parser.add_argument(
        "--invite-time",
        metavar="HH:MM[:SS]",
        type=_parse_time,
        default=None,
        help=(
            "Watch mode: expected time when invites go out. "
            "Enables active-phase polling starting --pre-window seconds before this time."
        ),
    )
    parser.add_argument(
        "--pre-window",
        type=float,
        default=60.0,
        metavar="SECS",
        help="Watch mode: seconds before --invite-time to switch to rapid polling (default: 60)",
    )
    parser.add_argument(
        "--idle-check",
        type=float,
        default=21600.0,
        metavar="SECS",
        help="Watch mode: seconds between idle polls — 21600 = 6 h (default: 21600)",
    )
    parser.add_argument(
        "--active-poll",
        type=float,
        default=5.0,
        metavar="SECS",
        help="Watch mode: seconds between polls during the active window (default: 5)",
    )
    parser.add_argument(
        "--accept-delay",
        type=float,
        default=0.7,
        metavar="SECS",
        help="Watch mode: seconds to wait after invite detected before accepting (default: 0.7)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=10,
        metavar="N",
        help="Watch mode: max accept+verify attempts per invite (default: 10)",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=1.0,
        metavar="SECS",
        help="Watch mode: seconds between retry attempts (default: 1.0)",
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

    if args.once:
        asyncio.run(run_once(args))
        return

    if args.watch:
        asyncio.run(run_watch(args))
        return

    asyncio.run(run_daemon(args, args.time))


if __name__ == "__main__":
    main()
