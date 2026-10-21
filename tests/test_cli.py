from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from proxywrap.cli import elevate, main
from proxywrap.errors import ProxyWrapError


class CliTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.status.return_value = True
        self.settings = {
            "platform": "linux",
            "config_dir": "/fake/config",
            "binary": "/fake/mihomo",
        }
        patch("proxywrap.cli.load_settings", return_value=self.settings).start()
        patch("proxywrap.cli.Service", return_value=self.service).start()
        self.addCleanup(patch.stopall)

    def test_status_does_not_elevate(self):
        with patch("proxywrap.cli.elevate") as sudo, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["status"]), 0)
        sudo.assert_not_called()
        self.assertIn("running", output.getvalue())

    def test_stopped_status_has_distinct_exit_code(self):
        self.service.status.return_value = False
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["status"]), 3)

    def test_mutation_requests_isolated_installed_python(self):
        with (
            patch("os.geteuid", return_value=1000),
            patch("subprocess.call", return_value=0) as call,
        ):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(elevate(["update"]), 0)
        args = call.call_args.args[0]
        self.assertEqual(args[:2], ["/usr/bin/sudo", "--"])
        self.assertEqual(args[3:], ["-I", "-m", "proxywrap", "update"])

    def test_auth_file_not_in_output_or_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "password"
            file.write_text("private_password_12345")
            with (
                patch("proxywrap.cli.elevate", return_value=None),
                patch("proxywrap.cli.update") as update,
            ):
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["auth", "reset", "--password-file", str(file)]), 0)
                edited = update.call_args.kwargs["edit"]({})
                self.assertEqual(edited["authentication"], ["proxywrap:private_password_12345"])
                self.assertNotIn("private_password_12345", output.getvalue())

    def test_lan_invalid_network_is_sanitized(self):
        with (
            patch("proxywrap.cli.elevate", return_value=None),
            redirect_stderr(io.StringIO()) as output,
        ):
            self.assertEqual(main(["lan", "token-sensitive-invalid"]), 1)
        self.assertNotIn("token-sensitive-invalid", output.getvalue())

    def test_update_safe_error(self):
        with patch("proxywrap.cli.elevate", return_value=None):
            with patch("proxywrap.cli.update", side_effect=ProxyWrapError("Subscription failed.")):
                with redirect_stderr(io.StringIO()) as output:
                    self.assertEqual(main(["update"]), 1)
                self.assertIn("Subscription failed.", output.getvalue())
