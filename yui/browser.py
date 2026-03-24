"""
Brave browser controller for YouTube Music.
Uses Xvfb (virtual display) instead of headless so YouTube Music works fully.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

BRAVE_PATH = "/usr/bin/brave-browser"
PROFILE_DIR = Path.home() / ".config" / "yui" / "browser-profile"
YTM_URL = "https://music.youtube.com"

TYPE_ICONS = {
    "song": "♪",
    "album": "💿",
    "playlist": "≡",
    "artist": "👤",
    "video": "▶",
}


@dataclass
class TrackInfo:
    title: str = ""
    artist: str = ""
    is_playing: bool = False
    progress: float = 0.0  # 0.0–1.0
    current_time: str = "0:00"
    duration: str = "0:00"


@dataclass
class SearchResult:
    title: str
    subtitle: str
    href: str
    kind: str = "song"  # song | album | playlist | artist | video


class YTMBrowser:
    def __init__(self, visible: bool = False):
        """
        visible=False: run on a virtual Xvfb display (invisible but fully functional).
        visible=True:  run on the real display (for first-time login).
        """
        self.visible = visible
        self._playwright = None
        self._context = None
        self._page = None
        self._xvfb: subprocess.Popen | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        # Remove stale lock files that block launching after a crash
        for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            (PROFILE_DIR / lock).unlink(missing_ok=True)

        if not self.visible:
            self._start_xvfb()

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,  # never true headless — use Xvfb instead
            executable_path=BRAVE_PATH,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-default-apps",
                "--autoplay-policy=no-user-gesture-required",
                "--no-sandbox",
            ],
            ignore_default_args=[
            "--enable-automation",
            "--disable-extensions",
            "--disable-component-extensions-with-background-pages",
            "--disable-component-update",
        ],
        )

        # Reuse an existing YTM tab if Brave restored one (e.g. music already playing).
        # Otherwise navigate the first available page, or open a new one.
        pages = self._context.pages
        self._page = next(
            (p for p in pages if p.url.startswith(YTM_URL)),
            None,
        )
        if self._page is None:
            self._page = pages[0] if pages else await self._context.new_page()
            await self._page.goto(YTM_URL, wait_until="domcontentloaded")
            await self._page.wait_for_timeout(2000)

        await self._page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = { runtime: {} };"
        )

        await self._handle_consent()

    def _start_xvfb(self) -> None:
        """Start a virtual X display, auto-allocating a free display number."""
        try:
            r_fd, w_fd = os.pipe()
            self._xvfb = subprocess.Popen(
                ["Xvfb", "-displayfd", str(w_fd), "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(w_fd,),
            )
            os.close(w_fd)
            display_num = os.read(r_fd, 16).decode().strip()
            os.close(r_fd)
            os.environ["DISPLAY"] = f":{display_num}"
        except FileNotFoundError:
            pass  # Xvfb not installed, fall back to existing DISPLAY

    async def _handle_consent(self) -> None:
        """Auto-accept the Google/YouTube cookie consent page if present."""
        if "consent.youtube.com" not in self._page.url:
            return
        try:
            await self._page.click("button:has-text('Accept all')", timeout=5000)
            await self._page.wait_for_url(f"{YTM_URL}/**", timeout=10000)
            await self._page.wait_for_timeout(1000)
        except Exception:
            pass

    async def is_logged_in(self) -> bool:
        try:
            btn = await self._page.query_selector('[aria-label="Sign in"]')
            return btn is None
        except Exception:
            return False

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        if self._xvfb:
            self._xvfb.terminate()

    # ------------------------------------------------------------------ track info

    async def get_track_info(self) -> TrackInfo:
        try:
            data = await self._page.evaluate("""
                () => {
                    const sel = (q) => document.querySelector(q);
                    const title =
                        sel('ytmusic-player-bar .title.ytmusic-player-bar')?.textContent?.trim() ||
                        sel('.content-info-wrapper .title')?.textContent?.trim() || '';
                    const artist =
                        sel('ytmusic-player-bar .subtitle a.yt-formatted-string')?.textContent?.trim() ||
                        sel('ytmusic-player-bar .byline')?.textContent?.trim() || '';
                    const playBtn = sel('#play-pause-button');
                    const isPlaying = playBtn?.getAttribute('aria-label') === 'Pause';
                    const bar = sel('#progress-bar');
                    const cur = parseFloat(bar?.value || 0);
                    const dur = parseFloat(bar?.max || 0);
                    const fmt = (s) => {
                        const m = Math.floor(s / 60);
                        return `${m}:${Math.floor(s % 60).toString().padStart(2,'0')}`;
                    };
                    return { title, artist, isPlaying,
                             progress: dur > 0 ? cur / dur : 0,
                             currentTime: fmt(cur), duration: fmt(dur) };
                }
            """)
            return TrackInfo(
                title=data.get("title", ""),
                artist=data.get("artist", ""),
                is_playing=data.get("isPlaying", False),
                progress=data.get("progress", 0.0),
                current_time=data.get("currentTime", "0:00"),
                duration=data.get("duration", "0:00"),
            )
        except Exception:
            return TrackInfo()

    # ------------------------------------------------------------------ controls

    async def play_pause(self) -> None:
        try:
            await self._page.click("#play-pause-button")
        except Exception:
            pass

    async def next_track(self) -> None:
        try:
            await self._page.click(".next-button")
        except Exception:
            pass

    async def prev_track(self) -> None:
        try:
            await self._page.click(".previous-button")
        except Exception:
            pass

    async def get_volume(self) -> int:
        try:
            v = await self._page.evaluate(
                "() => parseInt(document.querySelector('#volume-slider')?.value ?? 50)"
            )
            return int(v)
        except Exception:
            return 50

    async def set_volume(self, level: int) -> None:
        level = max(0, min(100, level))
        try:
            await self._page.evaluate(f"""
                () => {{
                    const s = document.querySelector('#volume-slider');
                    if (!s) return;
                    s.value = {level};
                    s.dispatchEvent(new Event('input', {{bubbles: true}}));
                    s.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
            """)
        except Exception:
            pass

    async def toggle_like(self) -> None:
        try:
            await self._page.click("#like-button-renderer")
        except Exception:
            pass

    # ------------------------------------------------------------------ search

    async def search(self, query: str) -> list[SearchResult]:
        try:
            encoded = query.replace(" ", "+")
            await self._page.goto(f"{YTM_URL}/search?q={encoded}", wait_until="domcontentloaded")
            await self._page.wait_for_timeout(2000)

            items = await self._page.evaluate("""
                () => {
                    const kindFromHref = (href) => {
                        if (!href)                          return 'song';
                        if (href.includes('watch?v='))      return 'song';
                        if (href.includes('/channel/') ||
                            href.includes('/browse/UC'))    return 'artist';
                        if (href.includes('playlist?list=') ||
                            href.includes('/browse/RDCLAK') ||
                            href.includes('/browse/VL'))    return 'playlist';
                        if (href.includes('/browse/'))      return 'album';
                        return 'song';
                    };
                    return Array.from(
                        document.querySelectorAll('ytmusic-responsive-list-item-renderer')
                    ).slice(0, 30).map(row => {
                        const a        = row.querySelector('a.main-link, a.yt-simple-endpoint');
                        const href     = a?.href || '';
                        const title    = row.querySelector('.title')?.textContent?.trim() || '';
                        const subtitle = row.querySelector('.subtitle')?.textContent?.trim() || '';
                        return { title, subtitle, href, kind: kindFromHref(href) };
                    }).filter(r => r.title);
                }
            """)
            return [SearchResult(**i) for i in items]
        except Exception:
            return []

    # ------------------------------------------------------------------ browse

    async def get_page_tracks(self, url: str) -> list[SearchResult]:
        """Navigate to an album/playlist page and return its track list."""
        try:
            await self._page.goto(url, wait_until="commit", timeout=15000)
            await self._page.wait_for_timeout(2000)

            items = await self._page.evaluate("""
                () => {
                    // Gather rows from any shelf variant on the page
                    let rows = Array.from(document.querySelectorAll(
                        'ytmusic-shelf-renderer ytmusic-responsive-list-item-renderer,' +
                        'ytmusic-playlist-shelf-renderer ytmusic-responsive-list-item-renderer,' +
                        'ytmusic-music-shelf-renderer ytmusic-responsive-list-item-renderer'
                    ));
                    if (!rows.length)
                        rows = Array.from(document.querySelectorAll('ytmusic-responsive-list-item-renderer'));

                    return rows.map(row => {
                        const title    = row.querySelector('.title')?.textContent?.trim() || '';
                        const subtitle = row.querySelector('.subtitle')?.textContent?.trim() || '';
                        const a        = row.querySelector('a[href*="watch"]') ||
                                         row.querySelector('a.main-link');
                        const href     = a?.href || '';
                        return { title, subtitle, href, kind: 'song' };
                    }).filter(r => r.title);
                }
            """)
            return [SearchResult(**i) for i in items]
        except Exception:
            return []

    # ------------------------------------------------------------------ play

    async def play_result(self, result: SearchResult) -> None:
        """Navigate to a search result and start playback."""
        if not result.href:
            return
        try:
            await self._page.goto(result.href, wait_until="commit", timeout=15000)
            await self._page.wait_for_timeout(2000)

            if result.kind in ("album", "playlist"):
                for selector in [
                    '[aria-label="Play"]',
                    "ytmusic-play-button-renderer",
                    ".play-button-shape button",
                ]:
                    try:
                        await self._page.click(selector, timeout=3000)
                        break
                    except Exception:
                        continue
        except Exception:
            pass

    # ------------------------------------------------------------------ queue

    async def get_queue(self) -> list[dict]:
        try:
            return await self._page.evaluate("""
                () => Array.from(document.querySelectorAll('ytmusic-player-queue-item'))
                    .slice(0, 30)
                    .map(item => ({
                        title:    item.querySelector('.song-title')?.textContent?.trim() || '',
                        artist:   item.querySelector('.byline')?.textContent?.trim() || '',
                        selected: item.hasAttribute('selected'),
                    }))
            """)
        except Exception:
            return []
