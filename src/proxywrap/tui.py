"""Offline terminal dashboard for the managed loopback controller."""

import curses
import json
import queue
import subprocess
import sys
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .errors import ProxyWrapError
from .services import Service


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Controller:
    def __init__(self):
        # Never send controller requests through inherited HTTP proxy settings.
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, data=None):
        request = Request(
            "http://127.0.0.1:9090" + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="GET" if data is None else "PUT",
        )
        try:
            with self.opener.open(request, timeout=1) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError
            value = json.loads(raw) if raw else {}
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (HTTPError, URLError, OSError, ValueError):
            raise ProxyWrapError("Controller unavailable or returned invalid data.") from None

    def select(self, group, node):
        groups = self.request("/proxies").get("proxies", {})
        if not isinstance(groups, dict):
            raise ProxyWrapError("Controller returned invalid proxy groups.")
        entry = groups.get(group, {})
        nodes = entry.get("all", []) if isinstance(entry, dict) else []
        if (
            not isinstance(entry, dict)
            or entry.get("type") != "Selector"
            or not isinstance(nodes, list)
            or node not in nodes
        ):
            raise ProxyWrapError("The selected group or node is no longer available.")
        self.request("/proxies/" + quote(group, safe=""), {"name": node})


def snapshot(settings, controller):
    result = {"running": Service(settings).status(), "error": ""}
    try:
        result["version"] = controller.request("/version").get("version", "unknown")
        result["proxies"] = controller.request("/proxies").get("proxies", {})
        result["connections"] = controller.request("/connections")
        proxies = result["proxies"]
        connections = result["connections"]
        if not isinstance(proxies, dict) or not isinstance(connections, dict):
            raise ValueError
        for name, entry in proxies.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise ValueError
            nodes = entry.get("all", [])
            if not isinstance(nodes, list) or not all(isinstance(node, str) for node in nodes):
                raise ValueError
        active = connections.get("connections")
        if active is None:
            active = []
            connections["connections"] = active
        if not isinstance(active, list):
            raise ValueError
        for entry in active:
            if not isinstance(entry, dict) or not isinstance(entry.get("metadata", {}), dict):
                raise ValueError
            chains = entry.get("chains", [])
            if not isinstance(chains, list) or not all(isinstance(chain, str) for chain in chains):
                raise ValueError
    except ProxyWrapError as error:
        return {"running": result["running"], "error": str(error)}
    except ValueError:
        return {
            "running": result["running"],
            "error": "Controller returned invalid dashboard data.",
        }
    return result


def clean(value):
    return "".join(c if c.isprintable() else " " for c in str(value))


def size(value):
    try:
        value = max(0, float(value))
    except (ValueError, TypeError):
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


def command(screen, args):
    # Leave curses before sudo prompts; reuse CLI locking, validation and recovery.
    curses.def_prog_mode()
    curses.endwin()
    try:
        code = subprocess.call([sys.executable, "-I", "-m", "proxywrap", *args])
        input("Press Enter to return to the dashboard.")
        return code
    finally:
        curses.reset_prog_mode()
        screen.clear()


def run(settings):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ProxyWrapError("The terminal dashboard requires an interactive terminal.")
    updates = queue.Queue(maxsize=1)
    stop = threading.Event()

    def poll():
        controller = Controller()
        while not stop.is_set():
            try:
                value = snapshot(settings, controller)
            except (OSError, ProxyWrapError):
                value = {"error": "Unable to read service state."}
            try:
                updates.get_nowait()
            except queue.Empty:
                pass
            updates.put_nowait(value)
            stop.wait(2)

    worker = threading.Thread(target=poll, daemon=True)
    worker.start()
    try:
        curses.wrapper(dashboard, updates)
    except curses.error:
        raise ProxyWrapError(
            "Unable to initialize the terminal; check TERM and terminal size."
        ) from None
    finally:
        stop.set()
        worker.join(timeout=0.2)
    return 0


def dashboard(screen, updates):
    screen.keypad(True)
    screen.timeout(200)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    state = {}
    tab, selected, offset = 1, 0, 0
    group = None
    message = ""
    pending = None
    while True:
        try:
            state = updates.get_nowait()
        except queue.Empty:
            pass
        height, width = screen.getmaxyx()
        screen.erase()

        def draw(row, text, reverse=False):
            if 0 <= row < height - 1 and width > 1:
                try:
                    screen.addnstr(
                        row, 0, clean(text), width - 1, curses.A_REVERSE if reverse else 0
                    )
                except curses.error:
                    pass

        draw(0, "ProxyWrap | 1 Status  2 Nodes  3 Connections | q Quit", True)
        draw(1, "Service: " + ("running" if state.get("running") else "stopped/unknown"))
        items = []
        lines = []
        proxies = state.get("proxies", {})
        if not isinstance(proxies, dict):
            proxies = {}
        if tab == 1:
            connections = state.get("connections", {})
            lines = [
                "Core: " + str(state.get("version", "unavailable")),
                "Uploaded: " + size(connections.get("uploadTotal", 0)),
                "Downloaded: " + size(connections.get("downloadTotal", 0)),
                "Active connections: " + str(len(connections.get("connections") or [])),
                "",
                "s Start | x Stop | r Restart | u Update subscription",
                "Service actions and node changes request sudo when needed.",
                "TUN operations may interrupt SSH. Confirm with y; cancel with any other key.",
                "Traffic totals belong to the current core process, not billing usage.",
            ]
        elif tab == 2:
            if group is not None and group not in proxies:
                group = None
            if group is None:
                items = sorted(
                    name
                    for name, entry in proxies.items()
                    if isinstance(entry, dict) and entry.get("type") == "Selector"
                )
                lines = [f"{name} -> {proxies[name].get('now', '?')}" for name in items]
            else:
                entry = proxies[group]
                items = entry.get("all", [])
                lines = [("* " if node == entry.get("now") else "  ") + node for node in items]
            draw(2, f"{group or 'Selector groups'} | Enter: choose | Esc: back")
        else:
            for connection in state.get("connections", {}).get("connections") or []:
                metadata = connection.get("metadata", {})
                lines.append(
                    f"{metadata.get('network', '?')} {metadata.get('host') or metadata.get('destinationIP', '?')}:"
                    f"{metadata.get('destinationPort', '?')} | {' > '.join(connection.get('chains') or [])}"
                )
            draw(2, "Active destinations and proxy chains | Up/Down: scroll")
        selected = min(max(0, selected), max(0, len(lines) - 1))
        visible = max(1, height - 7)
        offset = min(offset, selected)
        if selected >= offset + visible:
            offset = selected - visible + 1
        for index, line in enumerate(lines[offset : offset + visible], offset):
            draw(3 + index - offset, line, tab == 2 and selected == index)
        if not lines and tab != 1:
            draw(3, "No entries. Start the core or check controller availability.")
        draw(height - 3, state.get("error") or message)
        draw(
            height - 2,
            f"Confirm {pending[0]}? [y/any other key]"
            if pending
            else "Up/Down: navigate | 1/2/3: switch view",
        )
        screen.refresh()
        key = screen.getch()
        if key == -1:
            continue
        if key == curses.KEY_RESIZE:
            continue
        if pending:
            if key == ord("y"):
                code = command(screen, pending)
                message = (
                    "Operation completed." if code == 0 else "Operation failed; review CLI output."
                )
            pending = None
        elif key in (ord("q"), 3):
            return
        elif key in (ord("1"), ord("2"), ord("3")):
            tab, selected, offset = int(chr(key)), 0, 0
            group = None
        elif key in (curses.KEY_DOWN, ord("j")):
            selected += 1
        elif key in (curses.KEY_UP, ord("k")):
            selected -= 1
        elif key == 27:
            group, selected, offset = None, 0, 0
        elif tab == 2 and key in (10, 13, curses.KEY_ENTER) and items:
            if group is None:
                group, selected, offset = items[selected], 0, 0
            else:
                pending = ["select", "--", group, items[selected]]
        elif tab == 1 and key in map(ord, "sxru"):
            pending = [{"s": "start", "x": "stop", "r": "restart", "u": "update"}[chr(key)]]
