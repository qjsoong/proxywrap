from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from proxywrap.config import atomic_write, initialize, read_yaml
from proxywrap.errors import ProxyWrapError
from proxywrap.update import locked, recover, update

RAW = b'dns: {enable: true, nameserver: [1.1.1.1]}\nrules: ["MATCH,DIRECT"]\nnew: yes\n'


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        initialize(self.directory, "linux", b"https://example.com/sub?token=must-not-log")
        self.settings = {
            "platform": "linux",
            "config_dir": self.temp.name,
            "binary": "/fake/mihomo",
        }
        self.service = Mock()
        self.service.status.return_value = True
        self.mock_service = patch("proxywrap.update.Service", return_value=self.service).start()
        self.mock_download = patch("proxywrap.update.download", return_value=RAW).start()
        self.mock_core = patch("proxywrap.update.validate_core").start()
        self.addCleanup(patch.stopall)

    def prime(self):
        update(self.settings, apply=False)
        self.service.reset_mock()
        return (self.directory / "config.yaml").read_bytes()

    def test_install_complete_and_unchanged_does_not_restart(self):
        self.prime()
        self.assertEqual(
            read_yaml(self.directory / "config.yaml")["dns"]["nameserver"], ["1.1.1.1"]
        )
        self.assertFalse(update(self.settings))
        self.service.restart.assert_not_called()
        self.mock_download.return_value = RAW + b"# new comment\n"
        self.assertFalse(update(self.settings))
        self.assertTrue(
            (self.directory / "subscription.yaml").read_bytes().endswith(b"# new comment\n")
        )

    def test_changed_local_override_restarts_and_retains_upstream_fields(self):
        self.prime()
        with (self.directory / "override.yaml").open("ab") as stream:
            stream.write(b"log-level: warning\n")
        self.assertTrue(update(self.settings))
        self.service.restart.assert_called_once()
        self.assertIn("new", read_yaml(self.directory / "config.yaml"))
        self.assertTrue((self.directory / ".previous/config.yaml").exists())

    def test_download_invalid_merge_and_validation_failures_preserve_files(self):
        before = self.prime()
        original = (self.directory / "subscription.yaml").read_bytes()
        for bad in (b"unauthorized", b"proxies: invalid", b"dns: {enable: false}"):
            self.mock_download.return_value = bad
            with self.assertRaises(ProxyWrapError):
                update(self.settings)
            self.assertEqual((self.directory / "config.yaml").read_bytes(), before)
            self.assertEqual((self.directory / "subscription.yaml").read_bytes(), original)
        self.mock_download.side_effect = ProxyWrapError("Subscription download failed.")
        with self.assertRaises(ProxyWrapError):
            update(self.settings)
        self.mock_download.side_effect = None
        self.mock_download.return_value = RAW
        self.mock_core.side_effect = ProxyWrapError("Core rejected candidate.")
        with self.assertRaises(ProxyWrapError):
            update(self.settings)
        self.assertEqual((self.directory / "config.yaml").read_bytes(), before)
        self.service.restart.assert_not_called()

    def test_restart_failure_rolls_back_config_subscription_and_auth(self):
        before = self.prime()
        auth_before = (self.directory / "override.yaml").read_bytes()
        self.service.restart.side_effect = [ProxyWrapError("start failed"), None]
        with self.assertRaisesRegex(ProxyWrapError, "previous configuration was restored"):
            update(self.settings, edit=lambda c: dict(c, authentication=["proxywrap:" + "y" * 32]))
        self.assertEqual((self.directory / "config.yaml").read_bytes(), before)
        self.assertEqual((self.directory / "override.yaml").read_bytes(), auth_before)
        self.assertEqual(self.service.restart.call_count, 2)
        self.assertFalse((self.directory / ".transaction").exists())

    def test_failed_rollback_leaves_recoverable_journal(self):
        before = self.prime()
        self.mock_download.return_value = RAW + b"other: true\n"
        self.service.restart.side_effect = ProxyWrapError("broken")
        with self.assertRaisesRegex(ProxyWrapError, "rollback could not"):
            update(self.settings)
        self.assertTrue((self.directory / ".transaction/state.json").exists())
        self.service.restart.side_effect = None
        recover(self.directory, self.service, True)
        self.assertEqual((self.directory / "config.yaml").read_bytes(), before)

    def test_platform_override_cannot_silently_defeat_credential_reset(self):
        self.prime()
        with (self.directory / "override-platform.yaml").open("ab") as stream:
            stream.write(b"authentication: [proxywrap:platform_password_123456]\n")
        with self.assertRaisesRegex(ProxyWrapError, "higher-priority"):
            update(self.settings, edit=lambda c: dict(c, authentication=["proxywrap:" + "y" * 32]))
        self.service.restart.assert_not_called()

    def test_stopped_service_stays_stopped(self):
        self.prime()
        self.service.status.return_value = False
        self.mock_download.return_value = RAW + b"other: true\n"
        update(self.settings)
        self.service.restart.assert_not_called()

    def test_interrupted_deploy_blocks_regular_update(self):
        self.prime()
        (self.directory / ".deploy-transaction").mkdir()
        with self.assertRaisesRegex(ProxyWrapError, "rerun install.sh"):
            update(self.settings)
        self.service.restart.assert_not_called()

    def test_inactive_apply_cannot_discard_active_recovery(self):
        before = self.prime()
        transaction = self.directory / ".transaction"
        transaction.mkdir()
        atomic_write(transaction / "config.yaml", before)
        atomic_write(
            transaction / "state.json",
            json.dumps({"config.yaml": True, "was_running": True}).encode(),
        )
        atomic_write(self.directory / "config.yaml", b"candidate: true")
        with self.assertRaisesRegex(ProxyWrapError, "needs recovery"):
            recover(self.directory, self.service, False)
        self.assertTrue(transaction.exists())
        self.assertEqual((self.directory / "config.yaml").read_bytes(), b"candidate: true")

    def test_parallel_update_rejected(self):
        entered = threading.Event()
        release = threading.Event()

        def hold():
            with locked(self.directory):
                entered.set()
                release.wait(5)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold)
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaisesRegex(ProxyWrapError, "in progress"):
                    update(self.settings)
            finally:
                release.set()
            future.result()

    def test_interrupted_transaction_recovered(self):
        before = self.prime()
        transaction = self.directory / ".transaction"
        transaction.mkdir()
        atomic_write(transaction / "config.yaml", before)
        atomic_write(
            transaction / "state.json",
            json.dumps(
                {"config.yaml": True, "subscription.yaml": False, "was_running": True}
            ).encode(),
        )
        atomic_write(self.directory / "config.yaml", b"bad: true")
        recover(self.directory, self.service, True)
        self.assertEqual((self.directory / "config.yaml").read_bytes(), before)
        self.service.restart.assert_called_once()
