"""Simulated native-service tests; no host services or routes are changed."""

from __future__ import annotations

import errno
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from proxywrap.errors import ProxyWrapError
from proxywrap.services import Service, _systemd_argument


class NativeManager:
    """Small command boundary double, separate from actual service files."""

    def __init__(self):
        self.calls = []
        self.running = False
        self.loaded = False
        self.enabled = True
        self.load_state = "loaded"
        self.brew_jobs = set()
        self.disabled = set()
        self.pid = "123"

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        code, output = 0, ""
        if args[0] == "systemctl":
            if args[1:3] == ["show", "--property=LoadState"]:
                output = self.load_state + "\n"
            elif args[1] == "show":
                output = "ActiveState=" + ("active" if self.running else "inactive")
                output += "\nMainPID=" + (self.pid if self.running else "0") + "\n"
            elif args[1] == "start":
                self.running = True
            elif args[1] == "stop":
                self.running = False
            elif args[1] == "enable":
                self.enabled = True
            elif args[1] == "disable":
                self.enabled = False
            elif args[1] == "is-enabled":
                code = 0 if self.enabled else 1
        elif args[0] == "launchctl":
            target = args[-1]
            if args[1] == "print-disabled":
                output = 'disabled services = { "io.github.proxywrap.mihomo" => '
                output += ("false" if self.enabled else "true") + " }"
            elif args[1] == "print" and "homebrew.mxcl.mihomo" in target:
                code = 0 if target in self.brew_jobs else 113
            elif args[1] == "print":
                code = 0 if self.loaded else 113
                if self.loaded:
                    output = "state = " + ("running" if self.running else "waiting") + "\n"
                    if self.running:
                        output += f"pid = {self.pid}\n"
            elif args[1] == "disable":
                self.disabled.add(target)
                if "homebrew.mxcl.mihomo" not in target:
                    self.enabled = False
            elif args[1] == "enable":
                self.enabled = True
            elif args[1] == "bootout" and "homebrew.mxcl.mihomo" in target:
                self.brew_jobs.discard(target)
            elif args[1] == "bootout":
                self.loaded = self.running = False
            elif args[1] in ("bootstrap", "kickstart"):
                self.loaded = self.running = True
        return subprocess.CompletedProcess(args, code, stdout=output, stderr="")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / "config"
        self.config.mkdir()
        (self.config / "config.yaml").write_text("mixed-port: 7890\n")
        self.manager = NativeManager()
        self.sleeps = []
        self.settings = {
            "platform": "linux",
            "config_dir": str(self.config),
            "binary": str(self.root / "bin" / "mihomo"),
        }
        self.root_patch = patch("proxywrap.services.os.geteuid", return_value=0)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.chown_patch = patch("proxywrap.services.os.fchown")
        self.chown_patch.start()
        self.addCleanup(self.chown_patch.stop)

    def service(self, platform="linux"):
        self.settings["platform"] = platform
        return Service(
            self.settings,
            runner=self.manager,
            sleeper=self.sleeps.append,
            systemd_dir=self.root / "systemd",
            launchd_dir=self.root / "launchd",
        )

    def test_linux_reuses_existing_service_with_dropin(self):
        service = self.service()
        self.manager.running = True
        service.install()
        self.assertFalse((service.systemd_dir / "mihomo.service").exists())
        dropin = service.systemd_dir / "mihomo.service.d" / "10-proxywrap.conf"
        content = dropin.read_text()
        self.assertIn("Environment=SKIP_SYSTEM_IPV6_CHECK=1\n", content)
        self.assertIn("ExecStart=\nExecStart=", content)
        self.assertIn(str(self.config), content)
        self.assertIn("Restart=on-failure", content)
        self.assertIn("CAP_NET_ADMIN CAP_NET_RAW CAP_NET_BIND_SERVICE", content)
        self.assertEqual(dropin.stat().st_mode & 0o777, 0o644)
        self.assertTrue(self.manager.running)
        self.assertEqual(self.manager.calls[-1], ["systemctl", "daemon-reload"])

    def test_linux_fallback_only_when_no_service_exists(self):
        self.manager.load_state = "not-found"
        service = self.service()
        service.install()
        content = (service.systemd_dir / "mihomo.service").read_text()
        self.assertIn("WantedBy=multi-user.target", content)
        self.assertIn("After=network.target", content)
        self.assertFalse(self.manager.running)

    def test_linux_masked_service_is_not_silently_unmasked(self):
        self.manager.load_state = "masked"
        with self.assertRaisesRegex(ProxyWrapError, "masked"):
            self.service().install()
        self.assertEqual(len(self.manager.calls), 1)

    def test_linux_invalid_manager_state_does_not_write_units(self):
        self.manager.load_state = ""
        service = self.service()
        with self.assertRaisesRegex(ProxyWrapError, "systemd"):
            service.install()
        self.assertFalse(service.systemd_dir.exists())

    def test_systemd_arguments_preserve_spaces_and_literal_specifiers(self):
        self.assertEqual(_systemd_argument('/path/a $%"\\b'), '"/path/a $$%%\\"\\\\b"')

    def test_macos_plist_has_root_tun_and_boot_environment(self):
        service = self.service("darwin")
        service.install()
        plist = plistlib.loads(service.plist_path.read_bytes())
        self.assertEqual(
            plist["ProgramArguments"], [self.settings["binary"], "-d", str(self.config)]
        )
        self.assertEqual(plist["UserName"], "root")
        self.assertEqual(plist["EnvironmentVariables"], {"SKIP_SYSTEM_IPV6_CHECK": "1"})
        self.assertTrue(plist["KeepAlive"])
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["ExitTimeOut"], 20)
        self.assertEqual(service.plist_path.stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.config / "mihomo.log").stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.manager.running)
        self.assertFalse(any(args[1] == "bootstrap" for args in self.manager.calls))

    @patch.dict("os.environ", {"SUDO_UID": "501"})
    def test_macos_retires_only_known_competing_brew_jobs(self):
        self.manager.brew_jobs = {
            "system/homebrew.mxcl.mihomo",
            "gui/501/homebrew.mxcl.mihomo",
            "gui/501/homebrew.mxcl.other",
        }
        self.service("darwin").install()
        self.assertEqual(self.manager.brew_jobs, {"gui/501/homebrew.mxcl.other"})
        self.assertIn("user/501/homebrew.mxcl.mihomo", self.manager.disabled)
        self.assertFalse(any(args[0] == "brew" for args in self.manager.calls))

    def test_enable_and_disable_do_not_change_running_state(self):
        for platform in ("linux", "darwin"):
            service = self.service(platform)
            self.manager.running = True
            service.disable()
            self.assertFalse(self.manager.enabled)
            self.assertTrue(self.manager.running)
            service.enable()
            self.assertTrue(self.manager.enabled)
            self.assertTrue(self.manager.running)

    def test_status_does_not_read_private_config_or_need_root(self):
        self.manager.running = True
        self.config.rename(self.root / "private")
        with patch("proxywrap.services.os.geteuid", return_value=501):
            self.assertTrue(self.service().status())

    def test_mutations_require_root(self):
        service = self.service()
        with patch("proxywrap.services.os.geteuid", return_value=501):
            for method in (
                service.install,
                service.enable,
                service.disable,
                service.start,
                service.stop,
                service.restart,
            ):
                with self.subTest(method=method.__name__):
                    with self.assertRaisesRegex(ProxyWrapError, "sudo"):
                        method()
        self.assertEqual(self.manager.calls, [])

    def test_start_waits_for_same_pid_to_remain_running(self):
        service = self.service()
        with patch.object(service, "_check_ports"):
            service.start()
        self.assertTrue(self.manager.running)
        self.assertEqual(len(self.sleeps), service.STARTUP_SAMPLES - 1)

    def test_existing_running_service_is_not_restarted(self):
        service = self.service()
        self.manager.running = True
        with patch.object(service, "_check_ports") as ports:
            service.start()
        ports.assert_not_called()
        self.assertFalse(any(args[1] == "start" for args in self.manager.calls))

    def test_repeated_crashes_do_not_pass_stabilization(self):
        service = self.service()
        with patch.object(service, "_state", side_effect=[(True, str(i)) for i in range(25)]):
            with self.assertRaisesRegex(ProxyWrapError, "remain running"):
                service._wait_running()

    def test_macos_restart_uses_graceful_bootout_before_bootstrap(self):
        service = self.service("darwin")
        self.manager.loaded = self.manager.running = True
        with patch.object(service, "_check_ports"):
            service.restart()
        actions = [args[1] for args in self.manager.calls]
        self.assertLess(actions.index("bootout"), actions.index("bootstrap"))
        self.assertFalse(any("-k" in args for args in self.manager.calls))
        self.assertTrue(self.manager.running)

    def test_macos_stop_preserves_boot_activation(self):
        service = self.service("darwin")
        self.manager.loaded = self.manager.running = True
        service.stop()
        self.assertFalse(self.manager.loaded)
        self.assertFalse(self.manager.running)
        self.assertTrue(self.manager.enabled)

    def test_linux_enabled_uses_native_unprivileged_query(self):
        service = self.service()
        self.assertTrue(service.enabled())
        self.manager.enabled = False
        self.assertFalse(service.enabled())

    def test_macos_enabled_requires_plist_and_respects_disabled_override(self):
        service = self.service("darwin")
        self.assertFalse(service.enabled())
        service.install()
        self.assertTrue(service.enabled())
        service.disable()
        self.assertFalse(service.enabled())

    def test_conflicting_port_does_not_start_or_kill_a_process(self):
        service = self.service()
        probe = MagicMock()
        probe.__enter__.return_value.bind.side_effect = OSError(errno.EADDRINUSE, "secret-token")
        with patch("proxywrap.services.socket.socket", return_value=probe):
            with self.assertRaisesRegex(ProxyWrapError, "port 7890") as error:
                service.start()
        self.assertNotIn("secret-token", str(error.exception))
        self.assertFalse(any(args[1] == "start" for args in self.manager.calls))

    def test_manager_failures_do_not_leak_command_output(self):
        service = self.service()
        service._runner = lambda args, **kwargs: subprocess.CompletedProcess(
            args, 1, stdout="https://secret.invalid/token", stderr="password=secret"
        )
        with self.assertRaises(ProxyWrapError) as error:
            service.enable()
        self.assertNotIn("secret", str(error.exception))
        self.assertIn("exit 1", str(error.exception))

    def test_invalid_paths_and_unit_names_are_rejected(self):
        for field, value in (
            ("binary", "relative"),
            ("config_dir", "/tmp/a\nExecStart=bad"),
            ("service_name", "../another.service"),
        ):
            settings = dict(self.settings, **{field: value})
            with self.subTest(field=field):
                with self.assertRaises(ProxyWrapError):
                    Service(settings)


if __name__ == "__main__":
    unittest.main()
