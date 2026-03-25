"""
Brave browser controller for YouTube Music.
Uses Xvfb (virtual display) instead of headless so YouTube Music works fully.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

def _find_brave() -> str:
    """Return the path to the Brave browser executable, or raise RuntimeError."""
    # Honour explicit override first.
    if env := os.environ.get("BRAVE_PATH"):
        return env
    candidates = [
        "/usr/bin/brave-browser",
        "/usr/bin/brave",
        "/usr/local/bin/brave-browser",
        "/usr/local/bin/brave",
        "/opt/brave.com/brave/brave",
        "/opt/brave/brave",
        # Flatpak
        "/var/lib/flatpak/exports/bin/com.brave.Browser",
        os.path.expanduser("~/.local/share/flatpak/exports/bin/com.brave.Browser"),
        # Snap
        "/snap/bin/brave",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    # Last resort: search PATH
    if found := shutil.which("brave-browser") or shutil.which("brave"):
        return found
    raise RuntimeError(
        "Brave browser not found. Install it from https://brave.com/linux/ "
        "or set the BRAVE_PATH environment variable."
    )
REAL_PROFILE_DIR = Path.home() / ".config" / "BraveSoftware" / "Brave-Browser"
PROFILE_DIR = Path.home() / ".config" / "yui" / "browser-profile"
HISTORY_FILE = Path.home() / ".config" / "yui" / "history.json"
YTM_URL = "https://music.youtube.com"
HISTORY_MAX = 50

TYPE_ICONS = {
    "song":     "\uf001",  # nf-fa-music
    "album":    "\uf51f",  # nf-fa-compact_disc
    "playlist": "\uf0ca",  # nf-fa-list_ul
    "artist":   "\uf007",  # nf-fa-user
    "video":    "\uf03d",  # nf-fa-video_camera
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
    def __init__(self):
        self._playwright = None
        self._context = None
        self._page = None
        self._search_page = None  # separate page for search/browse so player keeps running
        self._xvfb: subprocess.Popen | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._sync_profile()
        self._remove_stale_locks(PROFILE_DIR)

        self._start_xvfb()

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            executable_path=_find_brave(),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-default-apps",
                "--autoplay-policy=no-user-gesture-required",
                "--no-sandbox",
                "--restore-last-session",
            ],
            ignore_default_args=[
                "--enable-automation",
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
                "--disable-component-update",
            ],
        )

        # Wait briefly for Brave to restore previous session tabs before inspecting URLs
        await asyncio.sleep(2)
        pages = self._context.pages
        self._page = next((p for p in pages if p.url.startswith(YTM_URL)), None)

        if self._page is None:
            self._page = pages[0] if pages else await self._context.new_page()
            await self._page.goto(YTM_URL, wait_until="domcontentloaded", timeout=20000)
            try:
                await self._page.wait_for_selector("ytmusic-player-bar", timeout=8000)
            except Exception:
                pass
        else:
            # Restored tab — wait for it to finish loading, but don't block forever
            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass

        self._page.set_default_timeout(5000)

        # Pre-create search page so the first search has no page-creation overhead
        self._search_page = await self._context.new_page()
        self._search_page.set_default_timeout(5000)

        try:
            await self._page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                "window.chrome = { runtime: {} };"
            )
        except Exception:
            pass

        await self._handle_consent()
        # Open queue panel at startup so items are in the DOM for all future get_queue() calls
        await self._open_queue_panel()

    def _sync_profile(self) -> None:
        """Copy the Default directory from the real Brave profile to yui's profile.

        Only runs when real Brave is not currently open (safe to read its files).
        Skips files that would cause conflicts (locks, singletons, crash data).
        """
        if not REAL_PROFILE_DIR.exists():
            return
        # Check if real Brave is running via its SingletonLock
        lock = REAL_PROFILE_DIR / "SingletonLock"
        if lock.exists():
            try:
                pid = int(os.readlink(lock).split("-")[-1])
                os.kill(pid, 0)
                return  # Brave is running — don't touch its files
            except (ValueError, OSError):
                pass  # stale lock, safe to proceed

        src = REAL_PROFILE_DIR / "Default"
        dst = PROFILE_DIR / "Default"
        if not src.exists():
            return

        skip = {
            "SingletonLock", "SingletonCookie", "SingletonSocket",
            "lockfile", "LOCK", "LOG", "LOG.old",
        }
        skip_dirs = {"GPUCache", "Code Cache", "DawnGraphiteCache", "DawnWebGPUCache"}

        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name in skip or item.name in skip_dirs:
                continue
            d = dst / item.name
            try:
                if item.is_dir():
                    if d.exists():
                        shutil.rmtree(d)
                    shutil.copytree(item, d)
                else:
                    shutil.copy2(item, d)
            except Exception:
                pass

    def _remove_stale_locks(self, profile: Path) -> None:
        """Remove SingletonLock only if the owning process is no longer alive."""
        lock = profile / "SingletonLock"
        if not lock.exists():
            return
        try:
            target = os.readlink(lock)
            pid = int(target.split("-")[-1])
            os.kill(pid, 0)  # signal 0 = just check existence
            raise RuntimeError(
                f"Brave is already running (pid {pid}). "
                "Close it before starting yui."
            )
        except (ValueError, OSError):
            for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                (profile / name).unlink(missing_ok=True)

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
                const inner = el.shadowRoot?.querySelector('#input') || el.shadowRoot?.querySelector('input');
                if (inner) {{
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(inner, {level});
                    inner.dispatchEvent(new Event('input', {{bubbles: true, composed: true}}));
                    inner.dispatchEvent(new Event('change', {{bubbles: true, composed: true}}));
                }}
            }}""")
        except Exception:
            pass

    async def toggle_like(self) -> None:
        try:
            await self._page.click("#like-button-renderer")
        except Exception:
            pass

    # ------------------------------------------------------------------ search

    async def _get_search_page(self):
        """Return the dedicated search/browse page, creating it if needed."""
        if self._search_page is None or self._search_page.is_closed():
            self._search_page = await self._context.new_page()
            self._search_page.set_default_timeout(5000)
        return self._search_page

    async def search(self, query: str) -> list[SearchResult]:
        try:
            page = await self._get_search_page()
            encoded = quote_plus(query)
            await page.goto(f"{YTM_URL}/search?q={encoded}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector("ytmusic-responsive-list-item-renderer", timeout=8000)

            items = await page.evaluate("""
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
            page = await self._get_search_page()
            await page.goto(url, wait_until="commit", timeout=15000)
            await page.wait_for_selector("ytmusic-responsive-list-item-renderer", timeout=8000)

            items = await page.evaluate("""
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

    async def get_artist_items(self, url: str) -> list[SearchResult]:
        """Fetch albums, singles, playlists and top songs from an artist page."""
        page = await self._get_search_page()
        try:
            await page.goto(url, wait_until="commit", timeout=15000)
            await page.wait_for_selector(
                "ytmusic-two-row-item-renderer, ytmusic-responsive-list-item-renderer",
                timeout=8000,
            )
        except Exception:
            return []
        try:
            items = await page.evaluate("""
                () => {
                    const kindFromHref = (href) => {
                        if (!href || href.includes('watch?v=')) return 'song';
                        if (href.includes('playlist?list=') ||
                            href.includes('/browse/RDCLAK') ||
                            href.includes('/browse/VL'))     return 'playlist';
                        if (href.includes('/browse/'))       return 'album';
                        return 'song';
                    };
                    const seen = new Set();
                    const results = [];
                    const add = (title, subtitle, href, kind) => {
                        if (!title || seen.has(href)) return;
                        seen.add(href);
                        results.push({ title, subtitle, href, kind });
                    };
                    // Carousel shelves: albums, singles, playlists
                    document.querySelectorAll('ytmusic-two-row-item-renderer').forEach(el => {
                        const title    = el.querySelector('.title')?.textContent?.trim() || '';
                        const subtitle = el.querySelector('.subtitle')?.textContent?.trim() || '';
                        const a        = el.querySelector('a.main-link, a.yt-simple-endpoint');
                        add(title, subtitle, a?.href || '', kindFromHref(a?.href));
                    });
                    // List shelves: top songs
                    document.querySelectorAll('ytmusic-responsive-list-item-renderer').forEach(el => {
                        const title    = el.querySelector('.title')?.textContent?.trim() || '';
                        const subtitle = el.querySelector('.subtitle')?.textContent?.trim() || '';
                        const a        = el.querySelector('a[href*="watch"]') || el.querySelector('a.main-link');
                        add(title, subtitle, a?.href || '', kindFromHref(a?.href));
                    });
                    return results;
                }
            """)
            return [SearchResult(**i) for i in items]
        except Exception:
            return []

    async def find_artist_url(self, name: str) -> str:
        """Search for an artist by name and return their page URL, or '' if not found."""
        results = await self.search(name)
        for r in results:
            if r.kind == "artist":
                return r.href
        return ""

    # ------------------------------------------------------------------ play

    async def play_result(self, result: SearchResult) -> None:
        if not result.href:
            return
        try:
            await self._page.goto(result.href, wait_until="commit", timeout=15000)
            await self._page.wait_for_selector("#play-pause-button, ytmusic-play-button-renderer", timeout=8000)

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

    async def _open_queue_panel(self) -> None:
        """Open the queue panel if not already open, wait for items to be in the DOM."""
        try:
            # If items are already present the panel is already open — don't toggle it closed
            items = await self._page.query_selector_all("ytmusic-player-queue-item")
            if items:
                return
            await self._page.click("#queue-button", timeout=3000)
            await self._page.wait_for_selector("ytmusic-player-queue-item", timeout=5000)
        except Exception:
            pass

    async def _ensure_queue_panel_open(self) -> None:
        """Re-open queue panel if items are missing (e.g. after navigation)."""
        try:
            items = await self._page.query_selector_all("ytmusic-player-queue-item")
            if not items:
                await self._open_queue_panel()
        except Exception:
            pass

    async def remove_from_queue(self, indices: list[int]) -> None:
        """Remove queue items at the given indices (processed back-to-front)."""
        await self._ensure_queue_panel_open()
        for idx in sorted(indices, reverse=True):
            try:
                items = await self._page.query_selector_all("ytmusic-player-queue-item")
                if idx >= len(items):
                    continue
                item = items[idx]
                await item.scroll_into_view_if_needed()
                # Try dedicated more-button first, fall back to right-click
                btn = await item.query_selector(
                    "ytmusic-menu-renderer #button, .more-button button, "
                    "[aria-label='More actions'], [aria-label='More options']"
                )
                if btn:
                    await btn.click()
                else:
                    await item.click(button="right")
                # Menu text varies by locale; try both common labels
                for label in ("Remove from queue", "Remove from Queue"):
                    try:
                        await self._page.click(
                            f"ytmusic-menu-service-item-renderer:has-text('{label}')",
                            timeout=1500,
                        )
                        break
                    except Exception:
                        continue
                await self._page.wait_for_timeout(150)
            except Exception:
                pass

    async def add_to_queue(self, indices: list[int]) -> None:
        """Add search/browse result rows at the given indices to the player queue."""
        page = await self._get_search_page()
        for idx in indices:
            try:
                rows = await page.query_selector_all("ytmusic-responsive-list-item-renderer")
                if idx >= len(rows):
                    continue
                row = rows[idx]
                await row.scroll_into_view_if_needed()
                await row.hover()
                btn = await row.query_selector(".more-button button, [aria-label='More actions']")
                if not btn:
                    continue
                await btn.click()
                await page.click(
                    "ytmusic-menu-service-item-renderer:has-text('Add to queue')",
                    timeout=2000,
                )
                await page.wait_for_timeout(150)
            except Exception:
                pass

    async def move_queue_items(self, indices: list[int], direction: int) -> None:
        """Move a contiguous block of queue items up (-1) or down (+1) one position."""
        await self._ensure_queue_panel_open()
        try:
            if direction == -1:
                for idx in sorted(indices):
                    if idx == 0:
                        continue
                    items = await self._page.query_selector_all("ytmusic-player-queue-item")
                    if idx < len(items) and idx - 1 >= 0:
                        await self._page.drag_and_drop(
                            f"ytmusic-player-queue-item:nth-child({idx + 1})",
                            f"ytmusic-player-queue-item:nth-child({idx})",
                        )
                        await self._page.wait_for_timeout(100)
            else:
                for idx in sorted(indices, reverse=True):
                    items = await self._page.query_selector_all("ytmusic-player-queue-item")
                    if idx + 1 < len(items):
                        await self._page.drag_and_drop(
                            f"ytmusic-player-queue-item:nth-child({idx + 1})",
                            f"ytmusic-player-queue-item:nth-child({idx + 2})",
                        )
                        await self._page.wait_for_timeout(100)
        except Exception:
            pass

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

