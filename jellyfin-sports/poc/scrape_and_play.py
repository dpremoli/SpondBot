#!/usr/bin/env python3
"""
Proof-of-concept: scrape live events from footybite.do and extract a stream URL.

Usage:
    pip install -r requirements.txt
    playwright install chromium
    python scrape_and_play.py [footybite_url]

    # To auto-open in mpv/vlc after extraction:
    python scrape_and_play.py --play
"""
import asyncio
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field

from playwright.async_api import async_playwright, Page, Request

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
    print("[warn] playwright-stealth not installed — bot detection may block scraping")
    print("       pip install playwright-stealth")


BASE_URL = "https://www.footybite.do"
M3U8_TIMEOUT = 15  # seconds to wait for m3u8 to appear after opening embed


@dataclass
class StreamOption:
    source: str
    channel: str
    ads: int
    embed_url: str
    m3u8_url: str = ""
    error: str = ""


@dataclass
class Event:
    name: str
    sport: str
    url: str
    streams: list[StreamOption] = field(default_factory=list)


async def apply_stealth(page: Page) -> None:
    if HAS_STEALTH:
        await stealth_async(page)
    else:
        # Minimal manual stealth without the library
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        """)


async def scrape_events(page: Page, base_url: str) -> list[Event]:
    """Scrape the homepage for live/upcoming events."""
    print(f"\n[1] Navigating to {base_url} ...")
    await apply_stealth(page)
    await page.goto(base_url, wait_until="domcontentloaded", timeout=30_000)

    # Wait for event links to appear — footybite renders them as <a> tags
    # with paths like /Team-A-vs-Team-B/12345
    try:
        await page.wait_for_selector("a[href*='-vs-']", timeout=15_000)
    except Exception:
        print("[warn] No '-vs-' links found. Trying fallback selectors...")

    # Dump all hrefs that look like event pages
    hrefs = await page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => [e.href, e.innerText.trim()])"
    )

    events = []
    seen = set()
    for href, text in hrefs:
        if "-vs-" not in href:
            continue
        # Normalise to absolute URL
        if href.startswith("/"):
            href = base_url.rstrip("/") + href
        if href in seen:
            continue
        seen.add(href)

        name = text or href.split("/")[-2].replace("-", " ").title()
        events.append(Event(name=name, sport="football", url=href))

    print(f"[1] Found {len(events)} event(s)")
    for i, ev in enumerate(events[:10]):
        print(f"    [{i}] {ev.name}  →  {ev.url}")
    if len(events) > 10:
        print(f"    ... and {len(events) - 10} more")

    return events


async def scrape_streams(page: Page, event: Event) -> list[StreamOption]:
    """Open an event page and scrape the stream source table."""
    print(f"\n[2] Scraping streams for: {event.name}")
    print(f"    URL: {event.url}")
    await apply_stealth(page)
    await page.goto(event.url, wait_until="domcontentloaded", timeout=30_000)

    # Wait for stream table rows — footybite lists sources in a table or list
    try:
        await page.wait_for_selector("table tr, .stream-row, [class*='stream']", timeout=10_000)
    except Exception:
        print("    [warn] Could not find stream table selector, dumping all links")

    # Try to extract rows: source name, ad count, channel, embed link
    # The page structure varies; we try multiple selectors
    streams = []

    # Strategy 1: look for <a> tags that lead to embed/stream pages
    rows = await page.eval_on_selector_all(
        "table tr",
        """rows => rows.map(r => ({
            text: r.innerText,
            links: Array.from(r.querySelectorAll('a')).map(a => a.href)
        }))"""
    )

    for row in rows:
        text = row.get("text", "").strip()
        links = row.get("links", [])
        if not text or not links:
            continue

        # Parse ad count from text — typically a number surrounded by whitespace
        parts = [p.strip() for p in text.split("\t") if p.strip()]
        source = parts[0] if parts else "unknown"
        ads = 0
        channel = ""
        for part in parts[1:]:
            if part.isdigit():
                ads = int(part)
            elif part:
                channel = part

        embed_url = links[0] if links else ""
        if embed_url:
            streams.append(StreamOption(source=source, channel=channel, ads=ads, embed_url=embed_url))

    if not streams:
        # Fallback: collect all outbound links that look like stream embeds
        all_links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.href, text: e.innerText.trim()}))"
        )
        for item in all_links:
            href = item["href"]
            if any(k in href for k in ["stream", "embed", "watch", "live"]):
                streams.append(StreamOption(
                    source=item["text"] or "unknown",
                    channel="",
                    ads=0,
                    embed_url=href,
                ))

    streams.sort(key=lambda s: s.ads)
    print(f"    Found {len(streams)} stream option(s):")
    for s in streams[:8]:
        print(f"      {s.source:25s} ads={s.ads}  channel={s.channel}  embed={s.embed_url[:60]}")

    return streams


async def extract_m3u8(context, stream: StreamOption) -> str:
    """Open an embed page and intercept the m3u8 URL via network requests."""
    print(f"\n[3] Extracting m3u8 from embed: {stream.embed_url[:80]}")

    found_url: list[str] = []
    page = await context.new_page()
    await apply_stealth(page)

    def on_request(req: Request) -> None:
        url = req.url
        if ".m3u8" in url or "/hls/" in url or "playlist" in url.lower():
            if url not in found_url:
                found_url.append(url)
                print(f"    [intercept] {url[:100]}")

    page.on("request", on_request)

    try:
        await page.goto(stream.embed_url, wait_until="domcontentloaded", timeout=20_000)
        # Give the page time to fire XHR/fetch calls for the stream
        await page.wait_for_timeout(M3U8_TIMEOUT * 1000)
    except Exception as e:
        print(f"    [warn] Page load error: {e}")
    finally:
        await page.close()

    return found_url[0] if found_url else ""


async def main() -> None:
    base_url = BASE_URL
    auto_play = "--play" in sys.argv

    # Allow overriding the URL via CLI
    for arg in sys.argv[1:]:
        if arg.startswith("http"):
            base_url = arg

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            java_script_enabled=True,
        )

        page = await context.new_page()

        # Step 1: scrape event list
        events = await scrape_events(page, base_url)
        if not events:
            print("\n[ERROR] No events found. The site structure may have changed or bot protection is active.")
            print("        Try running with a visible browser: set headless=False in this script.")
            await browser.close()
            return

        # Step 2: scrape streams for the first event
        target = events[0]
        streams = await scrape_streams(page, target)

        if not streams:
            print("\n[ERROR] No streams found for this event.")
            await browser.close()
            return

        # Step 3: extract m3u8 from the best stream (fewest ads)
        best = streams[0]
        m3u8 = await extract_m3u8(context, best)

        await browser.close()

        print("\n" + "=" * 60)
        print("RESULT")
        print("=" * 60)
        print(f"Event  : {target.name}")
        print(f"Source : {best.source}  channel={best.channel}  ads={best.ads}")
        if m3u8:
            print(f"m3u8   : {m3u8}")
            if auto_play:
                # Try mpv first, fall back to vlc
                for player in ["mpv", "vlc"]:
                    try:
                        subprocess.Popen([player, m3u8])
                        print(f"\n[play] Opened in {player}")
                        break
                    except FileNotFoundError:
                        continue
                else:
                    print("\n[play] Neither mpv nor vlc found. Install one to auto-play.")
        else:
            print("m3u8   : NOT FOUND")
            print("\n[hint] The embed page may use a different mechanism.")
            print("       Try setting headless=False to debug visually.")


if __name__ == "__main__":
    asyncio.run(main())
