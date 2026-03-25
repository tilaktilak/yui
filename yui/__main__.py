from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time

from yui.daemon import SOCKET_PATH, is_running


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


def _run_tray() -> None:
    try:
        import gi  # noqa: F401
    except ImportError:
        # uv uses its own Python which lacks gi/AppIndicator3 (system package).
        # Re-exec with the system Python, injecting the project root so yui is importable.
        import os
        from pathlib import Path
        project_root = str(Path(__file__).parent.parent)
        os.execvp(
            "python3",
            ["python3", "-c",
             f"import sys; sys.path.insert(0, {project_root!r});"
             "from yui.tray import run_tray; run_tray()"],
        )
        return  # unreachable
    from yui.tray import run_tray
    run_tray()


def main() -> None:
    p = argparse.ArgumentParser(prog="yui", description="YouTube Music TUI")
    p.add_argument("--daemon", action="store_true", help="Run as background daemon")
    p.add_argument("--login",  action="store_true", help="First-time login (opens visible browser, no daemon)")
    args = p.parse_args()

    if args.daemon:
        asyncio.run(_run_daemon())
        return

    if args.login:
        from yui.tui import YuiApp
        from yui.browser import YTMBrowser
        YuiApp(browser=YTMBrowser()).run()
        return

    # Default: tray if daemon not running, TUI if it already is.
    if is_running():
        from yui.client import YTMClient
        from yui.tui import YuiApp
        YuiApp(browser=YTMClient(), loading_msg="Connecting to daemon…").run()
    else:
        _start_daemon()
        _run_tray()


async def _run_daemon() -> None:
    from yui.daemon import run_daemon
    await run_daemon()


if __name__ == "__main__":
    main()
