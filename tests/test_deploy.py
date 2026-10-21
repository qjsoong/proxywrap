"""Installer transactions exercise real files with isolated service/core boundaries."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from proxywrap.config import atomic_write, dump, initialize, read_yaml
from proxywrap.deploy import check_ports, deploy
from proxywrap.errors import ProxyWrapError


class FakeService:
    def __init__(self):
        self.running = False
        self.boot = False
        self.calls = []
        self.fail_start = False

    def status(self):
        return self.running

    def enabled(self):
        return self.boot

    def install(self):
        self.calls.append("install")

    def enable(self):
        self.calls.append("enable")
        self.boot = True

    def disable(self):
        self.calls.append("disable")
        self.boot = False

    def start(self):
        self.calls.append("start")
        if self.fail_start:
            raise ProxyWrapError("Simulated startup failure.")
        self.running = True

    def stop(self):
        self.calls.append("stop")
        self.running = False

    def restart(self):
        self.calls.append("restart")
        self.running = True


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "config"
        self.metadata = self.root / "etc" / "settings.json"
        self.settings = {
            "platform": "linux",
            "config_dir": str(self.directory),
            "binary": "/fake/mihomo",
        }
        self.service = FakeService()
        self.url = b"https://example.invalid/subscription?token=private\n"
        self.patch_service = patch("proxywrap.deploy.Service", return_value=self.service)
        self.patch_service.start()
        self.addCleanup(self.patch_service.stop)
        self.patch_core = patch("proxywrap.deploy.validate_core")
        self.patch_core.start()
        self.addCleanup(self.patch_core.stop)
        self.patch_ports = patch("proxywrap.deploy.check_ports")
        self.patch_ports.start()
        self.addCleanup(self.patch_ports.stop)

    def effective(self, settings, apply):
        final = read_yaml(self.directory / "override.yaml")
        final.update(read_yaml(self.directory / "override-platform.yaml"))
        atomic_write(self.directory / "config.yaml", dump(final))
        atomic_write(self.directory / "subscription.yaml", b"rules: [MATCH,DIRECT]\n")
        return True

    def run_deploy(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return deploy(self.settings, settings_path=self.metadata, **kwargs)

    def existing(self):
        initialize(self.directory, "linux", url_bytes=self.url)
        self.effective(self.settings, False)
        self.metadata.parent.mkdir()
        atomic_write(self.metadata, json.dumps(self.settings).encode(), mode=0o644)

    def test_fresh_no_start_installs_and_enables(self):
        with patch("proxywrap.deploy._update", side_effect=self.effective):
            self.run_deploy(url_bytes=self.url)
        self.assertEqual(self.service.calls, ["install", "enable"])
        self.assertTrue(self.service.boot)
        self.assertFalse(self.service.running)
        self.assertEqual(self.metadata.stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.directory / "override.yaml").stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list((self.directory / ".deploy-backups").iterdir())), 1)

    def test_failed_initial_start_restores_packaged_config_and_disables(self):
        self.directory.mkdir()
        original = b"# Package default\nrules: [MATCH,DIRECT]\n"
        (self.directory / "config.yaml").write_bytes(original)
        self.service.fail_start = True
        with patch("proxywrap.deploy._update", side_effect=self.effective):
            with self.assertRaisesRegex(ProxyWrapError, "startup failure"):
                self.run_deploy(url_bytes=self.url, start=True, package_default_config=True)
        self.assertEqual((self.directory / "config.yaml").read_bytes(), original)
        self.assertFalse((self.directory / "override.yaml").exists())
        self.assertFalse(self.metadata.exists())
        self.assertFalse(self.service.boot)

    def test_unmanaged_config_requires_explicit_force(self):
        self.directory.mkdir()
        (self.directory / "config.yaml").write_bytes(b"rules: [MATCH,DIRECT]\n")
        with self.assertRaisesRegex(ProxyWrapError, "unmanaged"):
            self.run_deploy(url_bytes=self.url)
        self.assertEqual(self.service.calls, [])

    def test_reinstall_preserves_running_service_and_credentials(self):
        self.existing()
        self.service.running = True
        self.service.boot = True
        original = (self.directory / "override.yaml").read_bytes()
        with patch("proxywrap.deploy._update") as update:
            self.run_deploy(start=False)
        update.assert_not_called()
        self.assertNotIn("restart", self.service.calls)
        self.assertNotIn("stop", self.service.calls)
        self.assertEqual((self.directory / "override.yaml").read_bytes(), original)

    def test_failed_force_download_restores_url_without_restarting(self):
        self.existing()
        self.service.running = True
        self.service.boot = True
        original = (self.directory / "override.yaml").read_bytes()
        with patch("proxywrap.deploy._update", side_effect=ProxyWrapError("Download failed.")):
            with self.assertRaisesRegex(ProxyWrapError, "Download failed"):
                self.run_deploy(force=True, url_bytes=b"https://new.invalid/private")
        self.assertEqual((self.directory / "subscription.url").read_bytes(), self.url)
        self.assertEqual((self.directory / "override.yaml").read_bytes(), original)
        self.assertEqual(self.service.calls, [])

    def test_changed_url_requires_force(self):
        self.existing()
        with self.assertRaisesRegex(ProxyWrapError, "--force"):
            self.run_deploy(url_bytes=b"https://new.invalid/private")
        self.assertEqual((self.directory / "subscription.url").read_bytes(), self.url)

    def test_existing_metadata_user_agent_is_retained(self):
        self.existing()
        self.settings["user_agent"] = "SpecialClient"
        atomic_write(self.metadata, json.dumps(self.settings).encode(), mode=0o644)
        del self.settings["user_agent"]
        self.run_deploy()
        self.assertEqual(json.loads(self.metadata.read_text())["user_agent"], "SpecialClient")

    def test_force_keeps_local_authentication_and_overrides(self):
        self.existing()
        original = (self.directory / "override.yaml").read_bytes()
        with patch("proxywrap.deploy._update", side_effect=self.effective):
            self.run_deploy(force=True)
        self.assertEqual((self.directory / "override.yaml").read_bytes(), original)
        self.assertTrue((self.directory / ".deploy-backups").exists())

    def test_symlink_configuration_directory_is_rejected(self):
        destination = self.root / "other"
        destination.mkdir()
        self.directory.symlink_to(destination, target_is_directory=True)
        with self.assertRaisesRegex(ProxyWrapError, "symbolic"):
            self.run_deploy(url_bytes=self.url)
        self.assertEqual(list(destination.iterdir()), [])


class InstallerShellTests(unittest.TestCase):
    def shell(self, code, *, env=None):
        project = Path(__file__).resolve().parents[1]
        return subprocess.run(
            ["bash", "-c", 'source "$1/install.sh"; ' + code, "_", str(project)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_architecture_mapping(self):
        result = self.shell("pw_arch x86_64; pw_arch aarch64; pw_arch armv7l")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["amd64-v1", "arm64", "armv7"])
        self.assertNotEqual(self.shell("pw_arch unsupported").returncode, 0)

    def test_help_is_safe_and_has_all_public_options(self):
        result = self.shell("pw_help")
        self.assertEqual(result.returncode, 0)
        for option in ("--url-file", "--start", "--no-start", "--force", "--version", "--help"):
            self.assertIn(option, result.stdout)

    def test_conflicting_start_options_are_rejected(self):
        result = self.shell("pw_parse --start --no-start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only one", result.stderr)

    def test_version_validation_rejects_command_injection_without_echo(self):
        result = self.shell('pw_parse --version "v1.19.32; secret"')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("secret", result.stdout + result.stderr)

    def test_remote_session_detection(self):
        result = self.shell('SSH_CONNECTION="10.0.0.1 111 10.0.0.2 22"; pw_remote')
        self.assertEqual(result.returncode, 0)


class PortTests(unittest.TestCase):
    def test_port_conflict_has_safe_diagnostic(self):
        with (
            tempfile.TemporaryDirectory() as name,
            patch("proxywrap.deploy.socket.socket") as factory,
        ):
            directory = Path(name)
            atomic_write(directory / "config.yaml", dump({"mixed-port": 7890, "allow-lan": False}))
            factory.return_value.__enter__.return_value.bind.side_effect = OSError("Address in use")
            with self.assertRaisesRegex(ProxyWrapError, "another process"):
                check_ports(directory)
