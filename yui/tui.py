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
    LoadingIndicator,
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
    Screen { layers: base overlay; }

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
    #loading-overlay {
        layer: overlay;
        width: 100%;
        height: 100%;
        align: center middle;
        background: $background 85%;
    }
    #loading-overlay LoadingIndicator {
        width: 10;
        height: 3;
    }
    #loading-overlay Label {
        color: $text-muted;
        text-style: bold;
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
        Binding("q", "toggle_view", "Queue/Results"),
        Binding("escape", "go_back", "Back", show=False),
        Binding("plus,equal", "vol_up", "Vol+"),
        Binding("minus", "vol_down", "Vol-"),
        Binding("L", "toggle_like", "Like"),
        Binding("ctrl+d", "page_down", "Page↓", show=False),
        Binding("ctrl+u", "page_up", "Page↑", show=False),
        Binding("G", "go_bottom", "Bottom", show=False),
        Binding("V", "visual_mode", "Visual", show=False),
        Binding("d", "delete_selected", "Delete", show=False),
        Binding("p", "queue_selected", "Add to queue", show=False),
        Binding("J", "move_down", "Move↓", show=False),
        Binding("K", "move_up", "Move↑", show=False),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, browser=None, loading_msg: str = "Launching Brave…"):
        super().__init__()
        self._loading_msg = loading_msg
        self.browser = browser or YTMBrowser()
        self._mode: str = "queue"
        self._current_results: list[SearchResult] = []
        self._back_stack: list[ListState] = []
        self._volume: int = 50
        self._queue_sig: str = ""
        self._current_label: str = "Queue"
        self._current_status: str = ""
        self._saved_view: ListState | None = None
        # list rendering state
        self._list_items: list[str] = []
        self._playing_idx: int = -1
        # raw queue data (title/artist dicts) for artist lookup from queue view
        self._queue_data: list[dict] = []
        # ga key sequence state
        self._g_pending: bool = False
        # visual mode state
        self._visual_mode: bool = False
        self._visual_anchor: int = 0
        self._visual_end: int = 0
        self._pre_visual_status: str = ""
        # search spinner state
        self._spinner_handle = None
        self._spinner_idx: int = 0

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
        with Vertical(id="loading-overlay"):
            yield LoadingIndicator()
            yield Label(self._loading_msg)

    # ------------------------------------------------------------------ mount

    async def on_mount(self) -> None:
        self._set_status("")
        self.query_one("#results-list", ListView).focus()
        self._init_browser()

    @work
    async def _init_browser(self) -> None:
        """Start/connect the browser then hide the loading overlay."""
        try:
            await self.browser.start()
            logged_in = await self.browser.is_logged_in()
        except Exception as e:
            self.query_one("#loading-overlay").display = False
            self._set_status(f"Browser error: {e}")
            return

        self.query_one("#loading-overlay").display = False

        if not logged_in:
            self._set_status("Not logged in — run: yui --login")
        else:
            self._set_status("Ready")

        self._volume = await self.browser.get_volume()
        self._update_vol_label()

        self.set_interval(1.0, self._refresh_track)
        self.set_interval(10.0, self._refresh_queue)

        await self._show_recent_silently()
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
        if self._mode != "queue" or self._visual_mode:
            return
        try:
            queue = await self.browser.get_queue()
            if not queue:
                return
            if self._mode != "queue" or self._visual_mode:
                return

            sig = "|".join(f"{q['title']}:{q.get('selected', False)}" for q in queue)
            if not force and sig == self._queue_sig:
                return
            self._queue_sig = sig
            self._queue_data = queue

            selected = next((i for i, q in enumerate(queue) if q.get("selected")), -1)
            items = [
                f"{q['title']}  —  {q['artist']}" if q.get("artist") else q["title"]
                for q in queue
            ]
            self._set_label("Queue")
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
        lv = self.query_one("#results-list", ListView)
        lv.action_cursor_down()
        if self._visual_mode:
            self._visual_end = min(len(self._list_items) - 1, (lv.index or 0))
            self._redraw_list()

    def action_cursor_up(self) -> None:
        lv = self.query_one("#results-list", ListView)
        lv.action_cursor_up()
        if self._visual_mode:
            self._visual_end = max(0, (lv.index or 0))
            self._redraw_list()

    def action_page_down(self) -> None:
        lv = self.query_one("#results-list", ListView)
        self._move_cursor((lv.index or 0) + 10)

    def action_page_up(self) -> None:
        lv = self.query_one("#results-list", ListView)
        self._move_cursor((lv.index or 0) - 10)

    def action_go_bottom(self) -> None:
        self._move_cursor(len(self._list_items) - 1)

    def _move_cursor(self, idx: int) -> None:
        lv = self.query_one("#results-list", ListView)
        idx = max(0, min(idx, len(self._list_items) - 1))
        lv.index = idx
        if self._visual_mode:
            self._visual_end = idx
            self._redraw_list()

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

    def on_key(self, event) -> None:
        """Handle multi-key sequences (gg = top, ga = go to artist)."""
        if self.query_one("#search-input", Input).has_focus:
            self._g_pending = False
            return
        if self._g_pending:
            self._g_pending = False
            if event.key == "g":
                event.stop()
                self._move_cursor(0)
            elif event.key == "a":
                event.stop()
                self._go_to_artist()
            return
        if event.key == "g":
            self._g_pending = True
            event.stop()

    async def action_show_recent(self) -> None:
        history = self.browser.load_history()
        if not history:
            self._set_status("No history yet.")
            return
        self._back_stack.clear()
        self._current_results = history
        self._mode = "recent"
        self._set_label("Recent")
        await self._set_list_items([self._fmt(r) for r in history])
        self._set_status(f"{len(history)} recent items  —  Enter to play/browse, Esc for queue")
        self.query_one("#results-list", ListView).focus()

    def action_toggle_view(self) -> None:
        if self._visual_mode:
            self._exit_visual_mode()
            return
        if self._mode == "queue":
            if self._saved_view:
                self._restore_state(self._saved_view)
        else:
            self._saved_view = ListState(
                mode=self._mode,
                label=self._current_label,
                results=list(self._current_results),
                status=self._current_status,
            )
            self._mode = "queue"
            self._queue_sig = ""
            self._set_label("Queue")
            self._set_status("Queue")
            self._do_set_list([])
        self.query_one("#results-list", ListView).focus()

    def action_go_back(self) -> None:
        if self._visual_mode:
            self._exit_visual_mode()
            return
        search = self.query_one("#search-input", Input)
        if search.has_focus:
            search.value = ""
            self.query_one("#results-list", ListView).focus()
            return
        if self._back_stack:
            self._restore_state(self._back_stack.pop())
        else:
            self._mode = "queue"
            self._queue_sig = ""
            self._set_label("Queue")
            self._set_status("Queue")
            self._do_set_list([])
        self.query_one("#results-list", ListView).focus()

    def action_visual_mode(self) -> None:
        if self._visual_mode:
            self._exit_visual_mode()
            return
        lv = self.query_one("#results-list", ListView)
        if not self._list_items:
            return
        self._visual_anchor = lv.index or 0
        self._visual_end = self._visual_anchor
        self._pre_visual_status = self._current_status
        self._visual_mode = True
        self._redraw_list()
        if self._mode == "queue":
            self._set_status("VISUAL  j/k extend  d delete  J/K move  Esc cancel")
        else:
            self._set_status("VISUAL  j/k extend  p add-to-queue  Esc cancel")

    @work
    async def action_delete_selected(self) -> None:
        if not self._visual_mode or self._mode != "queue":
            return
        indices = self._visual_indices()
        await self.browser.remove_from_queue(indices)
        self._exit_visual_mode()
        self._queue_sig = ""

    @work
    async def action_queue_selected(self) -> None:
        if self._mode == "queue":
            return
        lv = self.query_one("#results-list", ListView)
        indices = self._visual_indices() if self._visual_mode else (
            [lv.index] if lv.index is not None else []
        )
        results = [self._current_results[i] for i in indices if i < len(self._current_results)]
        valid = [r for r in results if r.href and r.kind != "artist"]
        if valid:
            self._set_status(f"Adding {len(valid)} item(s) to queue…")
            await self.browser.add_to_queue(indices)
            self._set_status(f"Added {len(valid)} item(s) to queue")
        self._exit_visual_mode()

    @work
    async def action_move_up(self) -> None:
        if not self._visual_mode or self._mode != "queue":
            return
        indices = self._visual_indices()
        if indices[0] == 0:
            return
        await self.browser.move_queue_items(indices, -1)
        self._visual_anchor -= 1
        self._visual_end -= 1
        self._queue_sig = ""
        await self._refresh_queue(force=True)
        self._visual_mode = True  # restore after refresh cleared it
        self._redraw_list()

    @work
    async def action_move_down(self) -> None:
        if not self._visual_mode or self._mode != "queue":
            return
        indices = self._visual_indices()
        if indices[-1] >= len(self._list_items) - 1:
            return
        await self.browser.move_queue_items(indices, 1)
        self._visual_anchor += 1
        self._visual_end += 1
        self._queue_sig = ""
        await self._refresh_queue(force=True)
        self._visual_mode = True
        self._redraw_list()

    # ------------------------------------------------------------------ events

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        event.input.value = ""
        if not query:
            self.query_one("#results-list", ListView).focus()
            return
        self._back_stack.clear()
        self._do_search(query)

    _SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _start_spinner(self) -> None:
        self._spinner_idx = 0
        self._spinner_handle = self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self) -> None:
        self._spinner_idx = (self._spinner_idx + 1) % len(self._SPINNER_FRAMES)
        self.query_one("#search-input", Input).placeholder = (
            self._SPINNER_FRAMES[self._spinner_idx] + " Searching…"
        )

    def _stop_spinner(self) -> None:
        if self._spinner_handle:
            self._spinner_handle.stop()
            self._spinner_handle = None
        self.query_one("#search-input", Input).placeholder = "/ or s to search…"

    @work
    async def _do_search(self, query: str) -> None:
        self._start_spinner()
        self._set_status(f"Searching: {query}…")
        results = await self.browser.search(query)
        self._stop_spinner()
        self._current_results = results
        self._mode = "search"
        self._set_label(f'Results for "{query}"')
        if results:
            await self._set_list_items([self._fmt(r) for r in results])
            self._set_status(f"{len(results)} results  —  Enter to open, Esc to go back")
        else:
            await self._set_list_items(["No results found."])
            self._set_status("No results.")
        self.query_one("#results-list", ListView).focus()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self._visual_mode:
            return
        idx = event.list_view.index
        if idx is None:
            return
        if self._mode == "queue":
            if idx < len(self._queue_data):
                self._play_queue_item(idx)
            return
        if idx >= len(self._current_results):
            return

        result = self._current_results[idx]
        if result.kind in ("album", "playlist"):
            self._open_collection(result)
        elif result.kind == "artist":
            self._open_artist(result)
        else:
            self._play(result)

    @work
    async def _go_to_artist(self) -> None:
        lv = self.query_one("#results-list", ListView)
        idx = lv.index
        if idx is None:
            return

        # Resolve artist name from current context
        artist_name = ""
        artist_url = ""
        if self._mode == "queue":
            if idx < len(self._queue_data):
                artist_name = self._queue_data[idx].get("artist", "")
        else:
            if idx < len(self._current_results):
                r = self._current_results[idx]
                if r.kind == "artist":
                    artist_url = r.href
                else:
                    # subtitle is usually "Artist • Album • Year"
                    artist_name = r.subtitle.split("•")[0].strip() if r.subtitle else ""

        if not artist_url:
            if not artist_name:
                self._set_status("No artist info for this item.")
                return
            self._set_status(f"Finding artist: {artist_name}…")
            artist_url = await self.browser.find_artist_url(artist_name)
            if not artist_url:
                self._set_status(f"Artist not found: {artist_name}")
                return

        fake_result = SearchResult(title=artist_name or "Artist", subtitle="", href=artist_url, kind="artist")
        self._open_artist(fake_result)

    @work
    async def _open_artist(self, result: SearchResult) -> None:
        self._back_stack.append(ListState(
            mode=self._mode,
            label=self._current_label,
            results=list(self._current_results),
            status=self._current_status,
        ))
        self._set_status(f"Loading artist: {result.title}…")
        items = await self.browser.get_artist_items(result.href)

        if not items:
            self._set_status(f"No content found for {result.title}.")
            self._back_stack.pop()
            return

        self.browser.save_to_history(result)
        self._current_results = items
        self._mode = "browse"
        icon = TYPE_ICONS["artist"]
        self._set_label(f"{icon} {result.title}")
        await self._set_list_items([self._fmt(r) for r in items])
        self._set_status(f"{len(items)} items  —  Enter to open, Esc to go back")
        self.query_one("#results-list", ListView).focus()

    @work
    async def _open_collection(self, result: SearchResult) -> None:
        self._back_stack.append(ListState(
            mode=self._mode,
            label=self._current_label,
            results=list(self._current_results),
            status=self._current_status,
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
        self._set_label(f"{icon} {result.title}")
        await self._set_list_items([self._fmt(t) for t in tracks])
        self._set_status(f"{len(tracks)} tracks  —  Enter to play, Esc to go back")
        self.query_one("#results-list", ListView).focus()

    @work
    async def _play_queue_item(self, index: int) -> None:
        title = self._queue_data[index].get("title", "") if index < len(self._queue_data) else ""
        self._set_status(f"Playing: {title}…" if title else "Playing…")
        await self.browser.play_queue_item(index)
        self._queue_sig = ""

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
        if self._mode != "queue":
            return
        history = self.browser.load_history()
        if not history:
            return
        self._current_results = history
        self._mode = "recent"
        self._set_label("Recent")
        await self._set_list_items([self._fmt(r) for r in history])
        self._set_status(f"{len(history)} recent items  —  r to refresh, / to search")

    def _restore_state(self, state: ListState) -> None:
        self._mode = state.mode
        self._current_results = state.results
        self._set_label(state.label)
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
        self._list_items = items
        self._playing_idx = selected_idx
        lv = self.query_one("#results-list", ListView)
        await lv.clear()
        for i, text in enumerate(items):
            if not lv.is_attached:
                return
            prefix = "▶ " if i == selected_idx else "  "
            try:
                await lv.append(ListItem(Label(prefix + text)))
            except Exception:
                return

    def _redraw_list(self) -> None:
        """Update list item prefixes in-place (preserves cursor position)."""
        selected_set = set(self._visual_indices()) if self._visual_mode else set()
        lv = self.query_one("#results-list", ListView)
        for i, item in enumerate(lv.children):
            if i >= len(self._list_items):
                break
            text = self._list_items[i]
            if i in selected_set:
                prefix = "▪ "
            elif i == self._playing_idx:
                prefix = "▶ "
            else:
                prefix = "  "
            item.query_one(Label).update(prefix + text)

    def _visual_indices(self) -> list[int]:
        lo = min(self._visual_anchor, self._visual_end)
        hi = max(self._visual_anchor, self._visual_end)
        return list(range(lo, hi + 1))

    def _exit_visual_mode(self) -> None:
        self._visual_mode = False
        self._redraw_list()
        self._set_status(self._pre_visual_status)

    def _update_vol_label(self) -> None:
        self.query_one("#vol-label", Label).update(f"Vol: {self._volume}%")

    def _set_status(self, msg: str) -> None:
        self._current_status = msg
        try:
            self.query_one("#status-bar", Label).update(msg)
        except Exception:
            pass

    def _set_label(self, text: str) -> None:
        self._current_label = text
        self.query_one("#results-label", Label).update(text)
