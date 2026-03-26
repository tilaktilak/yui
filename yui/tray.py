"""
yui system tray — configures brave_tui.Tray for YouTube Music.
"""
from __future__ import annotations

import os
import sys

from brave_tui import Tray

from yui.daemon import PID_PATH, SOCKET_PATH

# Use the venv Python passed by __main__.py, so subprocesses work even after
# we re-exec under system python3 (needed for gi/AppIndicator3).
_YUI_PYTHON = os.environ.get("YUI_PYTHON", sys.executable)


def run_tray() -> None:
    Tray(
        app_id="yui",
        app_name="yui",
        open_cmd=[_YUI_PYTHON, "-m", "yui"],
        daemon_cmd=[_YUI_PYTHON, "-m", "yui", "--daemon"],
        socket_path=SOCKET_PATH,
        pid_path=PID_PATH,
        icon_names=["gnome-music", "music-app", "rhythmbox", "audio-x-generic"],
    ).run()
