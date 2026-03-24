"""
Brave browser controller for YouTube Music.
Uses Xvfb (virtual display) instead of headless so YouTube Music works fully.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

BRAVE_PATH = "/usr/bin/brave-browser"
PROFILE_DIR = Path.home() / ".config" / "yui" / "browser-profile"
HISTORY_FILE = Path.home() / ".config" / "yui" / "history.json"
YTM_URL = "https://music.youtube.com"
HISTORY_MAX = 50

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
    progress: float = 0.0
    current_time: str = "0:00"
    duration: str = "0:00"


@dataclass
class SearchResult:
    title: str
    subtitle: str
    href: str
    kind: str = "song"


class YTMBrowser:
    def __init__(self, visible: bool = False):
        self.visible = visible
        self._playwright = None
        self._context = None
        self._page = None
        self._xvfb: subprocess.Popen | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._remove_stale_locks()

        if not self.visible:
            self._start_xvfb()

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
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

        pages = self._context.pages
        self._page = next((p for p in pages if p.url.startswith(YTM_URL)), None)

        if self._page is None:
            self._page = pages[0] if pages else await self._context.new_page()
            await self._page.goto(YTM_URL, wait_until="domcontentloaded", timeout=20000)
            await self._page.wait_for_timeout(2000)
        else:
            # Restored tab — wait for it to finish loading, but don't block forever
            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass

        try:
            await self._page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                "window.chrome = { runtime: {} };"
            )
        except Exception:
            pass

        await self._handle_consent()

    def _remove_stale_locks(self) -> None:
        """Remove SingletonLock only if the owning process is no longer alive."""
        lock = PROFILE_DIR / "SingletonLock"
        if not lock.exists():
            return
        try:
            # SingletonLock is a symlink: hostname-pid
            target = os.readlink(lock)
            pid = int(target.split("-")[-1])
            os.kill(pid, 0)  # signal 0 = check existence only
            # Process is alive — do NOT remove the lock, raise instead
            raise RuntimeError(
                f"Brave is already running (pid {pid}). "
                "Close it before starting yui."
            )
        except (ValueError, OSError):
            # Process is dead or link is malformed — safe to clean up
            for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                (PROFILE_DIR / name).unlink(missing_ok=True)

    def _start_xvfb(self) -> None:
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
            pass

    async def _handle_consent(self) -> None:
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

    # ------------------------------------------------------------------ history

    def load_history(self) -> list[SearchResult]:
        try:
            return [SearchResult(**i) for i in json.loads(HISTORY_FILE.read_text())]
        except Exception:
            return []

    def save_to_history(self, result: SearchResult) -> None:
        history = self.load_history()
        history = [h for h in history if h.href != result.href]
        history.insert(0, result)
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps([dataclasses.asdict(h) for h in history[:HISTORY_MAX]]))

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
            v = await self._page.locator("#volume-slider").evaluate(
                "el => parseInt(el.value ?? 50)"
            )
            return int(v)
        except Exception:
            return 50

    async def set_volume(self, level: int) -> None:
        level = max(0, min(100, level))
        try:
            await self._page.locator("#volume-slider").evaluate(f"""el => {{
                el.value = {level};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}""")
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
                        if (!href)                           return 'song';
                        if (href.includes('watch?v='))       return 'song';
                        if (href.includes('/channel/') ||
                            href.includes('/browse/UC'))     return 'artist';
                        if (href.includes('playlist?list=') ||
                            href.includes('/browse/RDCLAK') ||
                            href.includes('/browse/VL'))     return 'playlist';
                        if (href.includes('/browse/'))       return 'album';
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
        try:
            await self._page.goto(url, wait_until="commit", timeout=15000)
            await self._page.wait_for_timeout(2000)

            items = await self._page.evaluate("""
                () => {
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
                        const a        = row.querySelector('a[href*="watch"]') || row.querySelector('a.main-link');
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
        if not result.href:
            return
        try:
            await self._page.goto(result.href, wait_until="commit", timeout=15000)
            await self._page.wait_for_timeout(2000)

            if result.kind in ("album", "playlist"):
                for selector in ['[aria-label="Play"]', "ytmusic-play-button-renderer", ".play-button-shape button"]:
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
                    .slice(0, 50)
                    .map(item => ({
                        title:    item.querySelector('.song-title')?.textContent?.trim() || '',
                        artist:   item.querySelector('.byline')?.textContent?.trim() || '',
                        selected: item.hasAttribute('selected'),
                    }))
            """)
        except Exception:
            return []
