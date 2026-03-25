from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from yui.daemon import SOCKET_PATH, is_running

TRAY_PID_PATH = Path.home() / ".config" / "yui" / "tray.pid"


def _is_tray_running() -> bool:
    if not TRAY_PID_PATH.exists():
        return False
    try:
        pid = int(TRAY_PID_PATH.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


def _start_daemon() -> None:
    subprocess.Popen(
        [sys.executable, "-m", "yui", "--daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait up to 5 s for the socket to appear (browser may still be loading).
    for _ in range(10):
        if SOCKET_PATH.exists():
            return
        time.sleep(0.5)


def _start_tray() -> None:
    """Spawn the tray as a background process (non-blocking)."""
    if _is_tray_running():
        return
    # Pass the venv Python path so the tray can spawn subprocesses correctly
    # even after re-exec'ing under system python3 to access gi/AppIndicator3.
    env = {**os.environ, "YUI_PYTHON": sys.executable}
    subprocess.Popen(
        [sys.executable, "-m", "yui", "--tray"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run_tray() -> None:
    """Run the tray in the current process (blocking). Handles gi import fallback."""
    TRAY_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRAY_PID_PATH.write_text(str(os.getpid()))
    try:
        try:
            import gi  # noqa: F401
        except ImportError:
            # uv uses its own Python which lacks gi/AppIndicator3 (system package).
            # Re-exec with the system Python, injecting the project root so yui is importable.
            project_root = str(Path(__file__).parent.parent)
            os.execvp(
                "python3",
                ["python3", "-c",
                 f"import sys; sys.path.insert(0, {project_root!r});"
                 f"import os; from pathlib import Path;"
                 f"p = Path.home() / '.config' / 'yui' / 'tray.pid';"
                 f"p.write_text(str(os.getpid()));"
                 "from yui.tray import run_tray; run_tray()"],
            )
            return  # unreachable
        from yui.tray import run_tray
        run_tray()
    finally:
        TRAY_PID_PATH.unlink(missing_ok=True)


def main() -> None:
    p = argparse.ArgumentParser(prog="yui", description="YouTube Music TUI")
    p.add_argument("--daemon", action="store_true", help="Run as background daemon")
    p.add_argument("--tray",   action="store_true", help="Run as system tray icon")
    p.add_argument("--login",  action="store_true", help="First-time login (opens visible browser, no daemon)")
    args = p.parse_args()

    if args.daemon:
        asyncio.run(_run_daemon())
        return

    if args.tray:
        _run_tray()
        return

    if args.login:
        from yui.tui import YuiApp
        from yui.browser import YTMBrowser
        YuiApp(browser=YTMBrowser()).run()
        return

    # Default: ensure daemon + tray are running, then show TUI.
    if not is_running():
        _start_daemon()
    _start_tray()
    from yui.client import YTMClient
    from yui.tui import YuiApp
    YuiApp(browser=YTMClient(), loading_msg="Connecting to daemon…").run()


async def _run_daemon() -> None:
    from yui.daemon import run_daemon
    await run_daemon()


if __name__ == "__main__":
    main()
