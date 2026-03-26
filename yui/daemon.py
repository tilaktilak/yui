"""
yui daemon — thin wrapper around brave_tui.Daemon for YouTube Music.

Socket:  ~/.config/yui/daemon.sock
PID:     ~/.config/yui/daemon.pid
"""
from __future__ import annotations

from pathlib import Path

from brave_tui import Daemon, is_daemon_running

SOCKET_PATH = Path.home() / ".config" / "yui" / "daemon.sock"
PID_PATH    = Path.home() / ".config" / "yui" / "daemon.pid"


def is_running() -> bool:
    """Return True if the yui daemon process is alive."""
    return is_daemon_running(PID_PATH)


async def run_daemon() -> None:
    """Start the daemon: create socket, launch browser, serve clients."""
    from yui.browser import YTMBrowser
    await Daemon(YTMBrowser(), SOCKET_PATH, PID_PATH).run()
