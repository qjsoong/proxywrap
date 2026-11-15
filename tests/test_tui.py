"""Controller and terminal interaction regressions without host service mutations."""

import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from proxywrap.cli import main
from proxywrap.errors import ProxyWrapError
from proxywrap.tui import Controller, clean, dashboard, run, size, snapshot


class TuiTests(unittest.TestCase):
    def test_idle_mihomo_returns_null_connections(self):
        controller = Mock()
        controller.request.side_effect = [
            {"version": "test"},
            {"proxies": {}},
            {"connections": None},
        ]
        with patch("proxywrap.tui.Service"):
            value = snapshot({}, controller)
        self.assertEqual(value["error"], "")
        self.assertEqual(value["connections"]["connections"], [])

    def test_read_only_launch_does_not_elevate(self):
        with (
            patch("proxywrap.cli.load_settings", return_value={}),
            patch("proxywrap.cli.elevate") as elevate,
            patch("proxywrap.tui.run", return_value=0),
        ):
            self.assertEqual(main(["tui"]), 0)
        elevate.assert_not_called()

    def test_select_uses_elevation_boundary(self):
        with patch("proxywrap.cli.elevate", return_value=0) as elevate:
            self.assertEqual(main(["select", "--", "group", "node"]), 0)
        elevate.assert_called_once_with(["select", "--", "group", "node"])

    def test_selector_validation_and_path_encoding(self):
        controller = Controller()
        controller.request = Mock(
            side_effect=[
                {"proxies": {"a/b 中文": {"type": "Selector", "all": ["node"]}}},
                {},
            ]
        )
        controller.select("a/b 中文", "node")
        self.assertEqual(
            controller.request.call_args.args,
            ("/proxies/a%2Fb%20%E4%B8%AD%E6%96%87", {"name": "node"}),
        )
        controller.request = Mock(return_value={"proxies": {}})
        with self.assertRaises(ProxyWrapError):
            controller.select("removed", "node")
        self.assertEqual(controller.request.call_count, 1)

    def test_http_errors_are_sanitized_and_proxy_environment_is_ignored(self):
        controller = Controller()
        controller.opener.open = Mock(side_effect=URLError("sensitive-token"))
        with self.assertRaisesRegex(ProxyWrapError, "^Controller unavailable") as caught:
            controller.request("/version")
        self.assertNotIn("sensitive-token", str(caught.exception))
        self.assertFalse(
            any(getattr(handler, "proxies", None) for handler in controller.opener.handlers)
        )

    def test_response_shape_and_size_are_bounded(self):
        controller = Controller()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        controller.opener.open = Mock(return_value=response)
        for body in (b"[]", b"invalid", b"x" * (4 * 1024 * 1024 + 1)):
            response.read.return_value = body
            with self.assertRaises(ProxyWrapError):
                controller.request("/version")
        response.read.return_value = json.dumps({"version": "test"}).encode()
        self.assertEqual(controller.request("/version"), {"version": "test"})

    def test_stopped_controller_does_not_retain_stale_connections(self):
        controller = Mock()
        controller.request.side_effect = ProxyWrapError("Controller unavailable.")
        with patch("proxywrap.tui.Service") as service:
            service.return_value.status.return_value = False
            value = snapshot({}, controller)
        self.assertFalse(value["running"])
        self.assertNotIn("connections", value)
        self.assertIn("unavailable", value["error"])

    def test_non_terminal_is_rejected_without_curses(self):
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(ProxyWrapError, "interactive terminal"):
                run({})

    def test_selection_requires_confirmation_and_cancel_has_no_effect(self):
        import queue

        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        # Open nodes, group, choose node, cancel; choose again and confirm, quit.
        screen.getch.side_effect = [ord("2"), 10, 10, ord("n"), 10, ord("y"), ord("q")]
        updates = queue.Queue()
        updates.put({"proxies": {"group": {"type": "Selector", "all": ["node"], "now": "node"}}})
        with (
            patch("proxywrap.tui.curses.curs_set"),
            patch("proxywrap.tui.command", return_value=0) as command,
        ):
            dashboard(screen, updates)
        command.assert_called_once_with(screen, ["select", "--", "group", "node"])

    def test_display_removes_terminal_control_sequences(self):
        self.assertNotIn("\x1b", clean("name\x1b[31m\n"))
        self.assertEqual(size(1024), "1.0 KiB")
        self.assertEqual(size(None), "?")
