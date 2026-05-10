# PoC: footybite scraper

Validates that Playwright can bypass bot protection on footybite.do and extract an m3u8 stream URL.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## Run

```bash
# Print events + first m3u8 URL found
python scrape_and_play.py

# Override source URL
python scrape_and_play.py https://www.footybite.do/

# Auto-open stream in mpv/vlc after extraction
python scrape_and_play.py --play
```

## Debugging bot protection

If the script finds no events, set `headless=False` near the top of the script to watch the browser in real time. This helps identify Cloudflare challenges or JS-rendered content that needs extra wait time.
