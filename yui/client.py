"""
yui IPC client — connects to the daemon and exposes the same API as YTMBrowser.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

from yui.browser import HISTORY_FILE, SearchResult, TrackInfo
from yui.daemon import SOCKET_PATH


class YTMClient:
    """Drop-in async replacement for YTMBrowser that talks to the daemon."""

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Connect to the daemon (retries while it starts up)."""
        for _ in range(30):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    str(SOCKET_PATH)
                )
                return
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(0.5)
        raise RuntimeError(f"Cannot connect to yui daemon at {SOCKET_PATH}")

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------ IPC

    async def _call(self, cmd: str, **kwargs):
        async with self._lock:
            req = {"cmd": cmd, **kwargs}
            self._writer.write(json.dumps(req).encode() + b"\n")
            await self._writer.drain()
            line = await self._reader.readline()
            resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp.get("result")

    # ------------------------------------------------------------------ same API as YTMBrowser

    async def is_logged_in(self) -> bool:
        return await self._call("is_logged_in")

    async def get_volume(self) -> int:
        return await self._call("get_volume")

    async def set_volume(self, level: int) -> None:
        await self._call("set_volume", level=level)

    async def get_track_info(self) -> TrackInfo:
        return TrackInfo(**await self._call("get_track_info"))

    async def play_pause(self) -> None:
        await self._call("play_pause")

    async def next_track(self) -> None:
        await self._call("next_track")

    async def prev_track(self) -> None:
        await self._call("prev_track")

    async def toggle_like(self) -> None:
        await self._call("toggle_like")

    async def get_queue(self) -> list[dict]:
        return await self._call("get_queue")

    async def search(self, query: str) -> list[SearchResult]:
        return [SearchResult(**i) for i in await self._call("search", query=query)]

    async def get_page_tracks(self, url: str) -> list[SearchResult]:
        return [SearchResult(**i) for i in await self._call("get_page_tracks", url=url)]

    async def get_artist_items(self, url: str) -> list[SearchResult]:
        return [SearchResult(**i) for i in await self._call("get_artist_items", url=url)]

    async def find_artist_url(self, name: str) -> str:
        return await self._call("find_artist_url", name=name)

    async def play_result(self, result: SearchResult) -> None:
        await self._call("play_result", result=dataclasses.asdict(result))

    async def add_to_queue(self, indices: list[int]) -> None:
        await self._call("add_to_queue", indices=indices)

    async def remove_from_queue(self, indices: list[int]) -> None:
        await self._call("remove_from_queue", indices=indices)

    async def move_queue_items(self, indices: list[int], direction: int) -> None:
        await self._call("move_queue_items", indices=indices, direction=direction)

    # History is file-based — read/write directly (no IPC round-trip needed).

    def load_history(self) -> list[SearchResult]:
        try:
            return [SearchResult(**i) for i in json.loads(HISTORY_FILE.read_text())]
        except Exception:
            return []

    def save_to_history(self, result: SearchResult) -> None:
        try:
            existing: list[dict] = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
        except Exception:
            existing = []
        d = dataclasses.asdict(result)
        existing = [e for e in existing if e.get("href") != d.get("href")]
        existing.insert(0, d)
        from yui.browser import HISTORY_MAX
        HISTORY_FILE.write_text(json.dumps(existing[:HISTORY_MAX], indent=2))
