"""
Textual TUI for YouTube Music.
"""
from __future__ import annotations

from dataclasses import dataclass

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
)

from yui.browser import TYPE_ICONS, SearchResult, YTMBrowser


@dataclass
class ListState:
    mode: str
    label: str
    results: list[SearchResult]
    status: str


class YuiApp(App):
    TITLE = "yui"

    CSS = """
    #now-playing {
        height: auto;
        border: round $primary;
        padding: 1 2;
        margin: 1 1 0 1;
    }
    #track-title  { text-style: bold; width: 100%; }
    #track-artist { color: $text-muted; width: 100%; margin-bottom: 1; }
    #track-progress { width: 100%; }
    #time-row     { width: 100%; height: 1; }
    #current-time { width: 1fr; }
    #track-duration { width: 1fr; text-align: right; }

    #search-input { margin: 1 1 0 1; height: 3; }

    #results-area {
        height: 1fr;
        border: round $surface-lighten-1;
        margin: 1 1 0 1;
    }
    #results-label {
        background: $surface-lighten-1;
        width: 100%;
        padding: 0 2;
        text-style: bold;
        height: 1;
    }
    #results-list { height: 1fr; }
    #results-list ListItem { padding: 0 2; }

    #bottom-row { height: 1; margin: 0 1; }
    #status-bar {
        width: 1fr;
        background: $surface-darken-1;
        padding: 0 2;
        color: $text-muted;
    }
    #vol-label {
        width: 12;
        background: $surface-darken-1;
        padding: 0 2;
        text-align: right;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("space", "play_pause", "Play/Pause"),
        Binding("l", "next_track", "Next"),
        Binding("h", "prev_track", "Prev"),
        Binding("up,k", "cursor_up", "Up", show=False),
        Binding("down,j", "cursor_down", "Down", show=False),
        Binding("slash,s", "focus_search", "Search"),
        Binding("r", "show_recent", "Recent"),
        Binding("escape", "go_back", "Back", show=False),
        Binding("plus,equal", "vol_up", "Vol+"),
        Binding("minus", "vol_down", "Vol-"),
        Binding("L", "toggle_like", "Like"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, visible: bool = False):
        super().__init__()
        self.browser = YTMBrowser(visible=visible)
        self._mode: str = "queue"
        self._current_results: list[SearchResult] = []
        self._back_stack: list[ListState] = []
        self._volume: int = 50
        self._queue_sig: str = ""  # signature to detect queue changes

    # ------------------------------------------------------------------ compose

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="now-playing"):
            yield Label("No track playing", id="track-title")
            yield Label("", id="track-artist")
            yield ProgressBar(total=100, show_percentage=False, id="track-progress")
            with Horizontal(id="time-row"):
                yield Label("0:00", id="current-time")
                yield Label("0:00", id="track-duration")
        yield Input(placeholder="/ or s to search…", id="search-input")
        with Vertical(id="results-area"):
            yield Label("Queue", id="results-label")
            yield ListView(id="results-list")
        with Horizontal(id="bottom-row"):
            yield Label("Starting…", id="status-bar")
            yield Label("Vol: --", id="vol-label")
        yield Footer()

    # ------------------------------------------------------------------ mount

    async def on_mount(self) -> None:
        self._set_status("Launching Brave…")
        self.query_one("#results-list", ListView).focus()
        self._init_browser()

    @work
    async def _init_browser(self) -> None:
        """Start the browser in the background so the TUI renders immediately."""
        try:
            await self.browser.start()
        except Exception as e:
            self._set_status(f"Browser error: {e}")
            return

        if not await self.browser.is_logged_in():
            self._set_status("Not logged in — run: uv run yui --login")
        else:
            self._set_status("Ready")

        self._volume = await self.browser.get_volume()
        self._update_vol_label()

        self.set_interval(1.0, self._refresh_track)
        self.set_interval(10.0, self._refresh_queue)

        # Show recent history while waiting for the queue
        await self._show_recent_silently()
        # Then try to load the queue right away
        await self._refresh_queue(force=True)

    # ------------------------------------------------------------------ polling

    async def _refresh_track(self) -> None:
        try:
            track = await self.browser.get_track_info()
            icon = "▶" if track.is_playing else "II"
            title = f"{icon}  {track.title}" if track.title else "No track playing"
            self.query_one("#track-title", Label).update(title)
            self.query_one("#track-artist", Label).update(track.artist)
            self.query_one("#track-progress", ProgressBar).progress = int(track.progress * 100)
            self.query_one("#current-time", Label).update(track.current_time)
            self.query_one("#track-duration", Label).update(track.duration)
        except Exception:
            pass

    async def _refresh_queue(self, force: bool = False) -> None:
        if self._mode != "queue":
            return
        try:
            queue = await self.browser.get_queue()
            if not queue:
                return

            # Only redraw if the queue actually changed
            sig = "|".join(f"{q['title']}:{q.get('selected', False)}" for q in queue)
            if not force and sig == self._queue_sig:
                return
            self._queue_sig = sig

            selected = next((i for i, q in enumerate(queue) if q.get("selected")), -1)
            items = [
                f"{q['title']}  —  {q['artist']}" if q.get("artist") else q["title"]
                for q in queue
            ]
            self.query_one("#results-label", Label).update("Queue")
            await self._set_list_items(items, selected)
        except Exception:
            pass

    # ------------------------------------------------------------------ actions

    async def action_play_pause(self) -> None:
        await self.browser.play_pause()

    async def action_next_track(self) -> None:
        await self.browser.next_track()

    async def action_prev_track(self) -> None:
        await self.browser.prev_track()

    def action_cursor_down(self) -> None:
        self.query_one("#results-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#results-list", ListView).action_cursor_up()

    async def action_vol_up(self) -> None:
        self._volume = min(100, self._volume + 5)
        await self.browser.set_volume(self._volume)
        self._update_vol_label()

    async def action_vol_down(self) -> None:
        self._volume = max(0, self._volume - 5)
        await self.browser.set_volume(self._volume)
        self._update_vol_label()

    async def action_toggle_like(self) -> None:
        await self.browser.toggle_like()

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    async def action_show_recent(self) -> None:
        history = self.browser.load_history()
        if not history:
            self._set_status("No history yet.")
            return
        self._back_stack.clear()
        self._current_results = history
        self._mode = "recent"
        self.query_one("#results-label", Label).update("Recent")
        await self._set_list_items([self._fmt(r) for r in history])
        self._set_status(f"{len(history)} recent items  —  Enter to play/browse, Esc for queue")
        self.query_one("#results-list", ListView).focus()

    def action_go_back(self) -> None:
        search = self.query_one("#search-input", Input)
        if search.has_focus:
            search.value = ""
            self.query_one("#results-list", ListView).focus()
            return
        if self._back_stack:
            self._restore_state(self._back_stack.pop())
        else:
            # Go back to queue
            self._mode = "queue"
            self._queue_sig = ""  # force redraw
            self.query_one("#results-label", Label).update("Queue")
            self._set_status("Queue")
            self._do_set_list([])
        self.query_one("#results-list", ListView).focus()

    # ------------------------------------------------------------------ events

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        event.input.value = ""
        if not query:
            self.query_one("#results-list", ListView).focus()
            return
        self._back_stack.clear()
        self._do_search(query)

    @work
    async def _do_search(self, query: str) -> None:
        self._set_status(f"Searching: {query}…")
        results = await self.browser.search(query)
        self._current_results = results
        self._mode = "search"
        self.query_one("#results-label", Label).update(f'Results for "{query}"')
        if results:
            await self._set_list_items([self._fmt(r) for r in results])
            self._set_status(f"{len(results)} results  —  Enter to open, Esc to go back")
        else:
            await self._set_list_items(["No results found."])
            self._set_status("No results.")
        self.query_one("#results-list", ListView).focus()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None or idx >= len(self._current_results):
            return
        if self._mode == "queue":
            return

        result = self._current_results[idx]
        if result.kind in ("album", "playlist"):
            self._open_collection(result)
        elif result.kind == "artist":
            self._set_status("Artist pages not supported yet.")
        else:
            self._play(result)

    @work
    async def _open_collection(self, result: SearchResult) -> None:
        self._back_stack.append(ListState(
            mode=self._mode,
            label=str(self.query_one("#results-label", Label).renderable),
            results=list(self._current_results),
            status=str(self.query_one("#status-bar", Label).renderable),
        ))
        icon = TYPE_ICONS.get(result.kind, "♪")
        self._set_status(f"Loading {result.kind}: {result.title}…")
        tracks = await self.browser.get_page_tracks(result.href)

        if not tracks:
            self._set_status(f"No tracks found in {result.title}.")
            self._back_stack.pop()
            return

        self.browser.save_to_history(result)
        self._current_results = tracks
        self._mode = "browse"
        self.query_one("#results-label", Label).update(f"{icon} {result.title}")
        await self._set_list_items([self._fmt(t) for t in tracks])
        self._set_status(f"{len(tracks)} tracks  —  Enter to play, Esc to go back")
        self.query_one("#results-list", ListView).focus()

    @work
    async def _play(self, result: SearchResult) -> None:
        self._set_status(f"Loading: {result.title}…")
        await self.browser.play_result(result)
        self.browser.save_to_history(result)
        self._mode = "queue"
        self._back_stack.clear()
        self._queue_sig = ""
        self._set_status(f"Playing: {result.title}")

    # ------------------------------------------------------------------ cleanup

    async def on_unmount(self) -> None:
        await self.browser.close()

    # ------------------------------------------------------------------ helpers

    async def _show_recent_silently(self) -> None:
        """Populate the list with recent history if the queue is still empty."""
        if self._mode != "queue":
            return
        history = self.browser.load_history()
        if not history:
            return
        self._current_results = history
        self._mode = "recent"
        self.query_one("#results-label", Label).update("Recent")
        await self._set_list_items([self._fmt(r) for r in history])
        self._set_status(f"{len(history)} recent items  —  r to refresh, / to search")

    def _restore_state(self, state: ListState) -> None:
        self._mode = state.mode
        self._current_results = state.results
        self.query_one("#results-label", Label).update(state.label)
        self._set_status(state.status)
        self._do_set_list([self._fmt(r) for r in state.results])

    @work
    async def _do_set_list(self, items: list[str]) -> None:
        await self._set_list_items(items)

    def _fmt(self, r: SearchResult) -> str:
        icon = TYPE_ICONS.get(r.kind, "♪")
        label = f"{icon} {r.title}"
        if r.subtitle:
            label += f"  —  {r.subtitle}"
        return label

    async def _set_list_items(self, items: list[str], selected_idx: int = -1) -> None:
        lv = self.query_one("#results-list", ListView)
        await lv.clear()
        for i, text in enumerate(items):
            prefix = "▶ " if i == selected_idx else "  "
            await lv.append(ListItem(Label(prefix + text)))

    def _update_vol_label(self) -> None:
        self.query_one("#vol-label", Label).update(f"Vol: {self._volume}%")

    def _set_status(self, msg: str) -> None:
        try:
            self.query_one("#status-bar", Label).update(msg)
        except Exception:
            pass
