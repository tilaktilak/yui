"""
yui system tray icon — uses AppIndicator3 for native GTK appearance and menus.

Left-click  → opens a terminal running the TUI
Right-click → menu with Open / Restart daemon / Quit
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time

import gi
gi.require_version("AppIndicator3", "0.1")
gi.require_version("Gtk", "3.0")
from gi.repository import AppIndicator3, Gtk

from yui.daemon import PID_PATH, SOCKET_PATH, is_running

# App icon names to try from the active GTK theme, in preference order.
_ICON_NAMES = ["gnome-music", "music-app", "rhythmbox", "audio-x-generic"]

# Use the venv Python passed by __main__.py, so subprocesses work even after
# we re-exec under system python3 (needed for gi/AppIndicator3).
_YUI_PYTHON = os.environ.get("YUI_PYTHON", sys.executable)


def _find_terminal_cmd() -> list[str]:
    """Return a command list that opens a terminal running yui."""
    yui = f"{_YUI_PYTHON} -m yui"
    env_term = os.environ.get("TERMINAL", "")
    candidates = ([env_term] if env_term else []) + [
        "kitty", "alacritty", "wezterm", "foot",
        "gnome-terminal", "xfce4-terminal", "xterm",
    ]
    for term in candidates:
        if not shutil.which(term):
            continue
        match term:
            case "kitty":        return ["kitty", "--", "sh", "-c", yui]
            case "alacritty":    return ["alacritty", "-e", "sh", "-c", yui]
            case "wezterm":      return ["wezterm", "start", "--", "sh", "-c", yui]
            case "foot":         return ["foot", "sh", "-c", yui]
            case "gnome-terminal": return ["gnome-terminal", "--", "sh", "-c", yui]
            case "xfce4-terminal": return ["xfce4-terminal", "-e", yui]
            case _:              return [term, "-e", yui]
    return ["xterm", "-e", yui]


def _open_tui(*_) -> None:
    try:
        subprocess.Popen(_find_terminal_cmd())
    except Exception as e:
        print(f"[yui tray] failed to open terminal: {e}", flush=True)


def _kill_daemon() -> None:
    if is_running():
        try:
            pid = int(PID_PATH.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def _restart_daemon(*_) -> None:
    def _do() -> None:
        _kill_daemon()
        for _ in range(20):
            if not is_running() and not SOCKET_PATH.exists():
                break
            time.sleep(0.3)
        subprocess.Popen(
            [_YUI_PYTHON, "-m", "yui", "--daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    threading.Thread(target=_do, daemon=True).start()


def _quit(*_) -> None:
    _kill_daemon()
    Gtk.main_quit()


def _build_menu() -> Gtk.Menu:
    menu = Gtk.Menu()

    item_open = Gtk.MenuItem(label="Open yui")
    item_open.connect("activate", _open_tui)
    menu.append(item_open)

    item_restart = Gtk.MenuItem(label="Restart daemon")
    item_restart.connect("activate", _restart_daemon)
    menu.append(item_restart)

    menu.append(Gtk.SeparatorMenuItem())

    item_quit = Gtk.MenuItem(label="Quit")
    item_quit.connect("activate", _quit)
    menu.append(item_quit)

    menu.show_all()
    return menu


def run_tray() -> None:
    """Start the tray icon, launching the daemon first if needed."""
    if not is_running():
        subprocess.Popen(
            [_YUI_PYTHON, "-m", "yui", "--daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Pick the best icon name available in the active GTK theme.
    theme = Gtk.IconTheme.get_default()
    icon_name = next((n for n in _ICON_NAMES if theme.has_icon(n)), "audio-x-generic")

    indicator = AppIndicator3.Indicator.new(
        "yui",
        icon_name,
        AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
    indicator.set_menu(_build_menu())

    Gtk.main()
