"""
yui IPC client — subclass of BraveClient that reconstructs YTM-specific
dataclasses from the plain dicts returned over the wire.
"""
from __future__ import annotations

import dataclasses
import json

from brave_tui import BraveClient

from yui.browser import HISTORY_FILE, HISTORY_MAX, SearchResult, TrackInfo
from yui.daemon import SOCKET_PATH


class YTMClient(BraveClient):
    """Drop-in async replacement for YTMBrowser that talks to the yui daemon."""

    def __init__(self) -> None:
        super().__init__(socket_path=SOCKET_PATH)

    # Methods that return dataclasses need explicit overrides so callers get
    # typed objects back.  Everything else (play_pause, set_volume, get_queue,
    # etc.) is handled automatically by BraveClient.__getattr__.

    async def get_track_info(self) -> TrackInfo:
        return TrackInfo(**await self._call("get_track_info"))

    async def search(self, query: str) -> list[SearchResult]:
        return [SearchResult(**i) for i in await self._call("search", query=query)]

    async def get_page_tracks(self, url: str) -> list[SearchResult]:
        return [SearchResult(**i) for i in await self._call("get_page_tracks", url=url)]

    async def get_artist_items(self, url: str) -> list[SearchResult]:
        return [SearchResult(**i) for i in await self._call("get_artist_items", url=url)]

    async def set_volume(self, level: int) -> None:
        await self._call("set_volume", level=level)

    async def find_artist_url(self, name: str) -> str:
        return await self._call("find_artist_url", name=name)

    async def remove_from_queue(self, indices: list[int]) -> None:
        await self._call("remove_from_queue", indices=indices)

    async def add_to_queue(self, indices: list[int]) -> None:
        await self._call("add_to_queue", indices=indices)

    async def move_queue_items(self, indices: list[int], direction: int) -> None:
        await self._call("move_queue_items", indices=indices, direction=direction)

    async def play_queue_item(self, index: int) -> None:
        await self._call("play_queue_item", index=index)

    async def play_result(self, result: SearchResult) -> None:
        await self._call("play_result", result=dataclasses.asdict(result))

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
        HISTORY_FILE.write_text(json.dumps(existing[:HISTORY_MAX], indent=2))
