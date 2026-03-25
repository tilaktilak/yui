"""
yui daemon — background process that owns the browser and serves IPC clients.

Socket:  ~/.config/yui/daemon.sock
PID:     ~/.config/yui/daemon.pid
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import signal
from pathlib import Path

from yui.browser import SearchResult, YTMBrowser

SOCKET_PATH = Path.home() / ".config" / "yui" / "daemon.sock"
PID_PATH    = Path.home() / ".config" / "yui" / "daemon.pid"


def is_running() -> bool:
    """Return True if the daemon process is alive."""
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


async def run_daemon() -> None:
    """Start the daemon: create socket, launch browser, serve clients."""
    if is_running():
        print("[yui daemon] already running", flush=True)
        return

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()))
    SOCKET_PATH.unlink(missing_ok=True)

    browser = YTMBrowser()
    ready   = asyncio.Event()

    async def start_browser() -> None:
        try:
            await browser.start()
            print("[yui daemon] browser ready", flush=True)
        except Exception as e:
            print(f"[yui daemon] browser error: {e}", flush=True)
        finally:
            ready.set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Each incoming command waits for the browser to be ready first.
        await ready.wait()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    continue
                resp = await _dispatch(browser, req)
                writer.write(json.dumps(resp).encode() + b"\n")
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    loop.add_signal_handler(signal.SIGINT,  stop.set)

    # Create socket BEFORE starting browser so clients can connect immediately.
    server = await asyncio.start_unix_server(handle, path=str(SOCKET_PATH))
    asyncio.create_task(start_browser())
    print(f"[yui daemon] listening on {SOCKET_PATH}", flush=True)

    async with server:
        await stop.wait()

    print("[yui daemon] shutting down…", flush=True)
    await browser.close()
    for p in (SOCKET_PATH, PID_PATH):
        p.unlink(missing_ok=True)


async def _dispatch(browser: YTMBrowser, req: dict) -> dict:
    cmd = req.get("cmd", "")
    try:
        match cmd:
            case "ping":
                return {"result": "pong"}
            case "is_logged_in":
                return {"result": await browser.is_logged_in()}
            case "get_volume":
                return {"result": await browser.get_volume()}
            case "set_volume":
                await browser.set_volume(req["level"])
                return {"result": None}
            case "get_track_info":
                return {"result": dataclasses.asdict(await browser.get_track_info())}
            case "play_pause":
                await browser.play_pause()
                return {"result": None}
            case "next_track":
                await browser.next_track()
                return {"result": None}
            case "prev_track":
                await browser.prev_track()
                return {"result": None}
            case "toggle_like":
                await browser.toggle_like()
                return {"result": None}
            case "get_queue":
                return {"result": await browser.get_queue()}
            case "play_queue_item":
                await browser.play_queue_item(req["index"])
                return {"result": None}
            case "search":
                r = await browser.search(req["query"])
                return {"result": [dataclasses.asdict(x) for x in r]}
            case "get_page_tracks":
                r = await browser.get_page_tracks(req["url"])
                return {"result": [dataclasses.asdict(x) for x in r]}
            case "get_artist_items":
                r = await browser.get_artist_items(req["url"])
                return {"result": [dataclasses.asdict(x) for x in r]}
            case "find_artist_url":
                return {"result": await browser.find_artist_url(req["name"])}
            case "play_result":
                await browser.play_result(SearchResult(**req["result"]))
                return {"result": None}
            case "add_to_queue":
                await browser.add_to_queue(req["indices"])
                return {"result": None}
            case "remove_from_queue":
                await browser.remove_from_queue(req["indices"])
                return {"result": None}
            case "move_queue_items":
                await browser.move_queue_items(req["indices"], req["direction"])
                return {"result": None}
            case "load_history":
                return {"result": [dataclasses.asdict(x) for x in browser.load_history()]}
            case "save_to_history":
                browser.save_to_history(SearchResult(**req["result"]))
                return {"result": None}
            case "shutdown":
                asyncio.get_running_loop().call_soon(
                    lambda: os.kill(os.getpid(), signal.SIGTERM)
                )
                return {"result": None}
            case _:
                return {"error": f"unknown command: {cmd!r}"}
    except Exception as e:
        return {"error": str(e)}
