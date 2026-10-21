"""Credential-safe failures at untrusted input and metadata boundaries."""

import copy
from http.client import InvalidURL
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

from proxywrap.cli import load_settings
from proxywrap.config import COMMON, TUN, check_safety, merge, parse, validate_url
from proxywrap.errors import ProxyWrapError
from proxywrap.update import SecureRedirect, download


class InputSecurityTests(unittest.TestCase):
    def test_malformed_security_fields_fail_without_echoing_values(self):
        safe = merge(COMMON, {"tun": TUN, "authentication": ["proxywrap:" + "x" * 32]})
        for change in (
            {"external-controller": None},
            {"external-controller": ["private-token"]},
            {"authentication": {"private-token": "not-a-list"}},
            {"skip-auth-prefixes": None},
            {"skip-auth-prefixes": ["private-token"]},
            {"lan-allowed-ips": None},
            {"lan-allowed-ips": ["private-token"]},
            {"lan-disallowed-ips": "private-token"},
            {"dns": None},
            {"tun": ["private-token"]},
            {"tun": {"inet6-address": "private-token"}},
            {"tun": {"inet6-address": ["private-token"]}},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ProxyWrapError) as result:
                    check_safety(merge(copy.deepcopy(safe), change))
                self.assertNotIn("private-token", str(result.exception))

    def test_yaml_python_constructor_is_never_executed(self):
        source = b'!!python/object/apply:os.system ["private-token"]'
        with patch("os.system") as execute:
            with self.assertRaises(ProxyWrapError) as result:
                parse(source)
        execute.assert_not_called()
        self.assertNotIn("private-token", str(result.exception))

    def test_url_controls_are_rejected_before_request_creation(self):
        with patch("proxywrap.update.Request") as request:
            for control in ("\0", "\t", "\n", "\r", "\x1f", "\x7f"):
                with self.subTest(control=repr(control)):
                    with self.assertRaises(ProxyWrapError) as result:
                        download("https://example.invalid/private-token" + control + "suffix")
                    self.assertNotIn("private-token", str(result.exception))
        request.assert_not_called()

    def test_invalid_http_client_error_is_sanitized(self):
        opener = Mock()
        opener.open.side_effect = InvalidURL("https://example.invalid/?token=private-token")
        with patch("proxywrap.update.build_opener", return_value=opener):
            with self.assertRaises(ProxyWrapError) as result:
                download("https://example.invalid/subscription")
        self.assertNotIn("private-token", str(result.exception))
        self.assertNotIn("example.invalid", str(result.exception))

    def test_user_agent_header_injection_is_rejected(self):
        with patch("proxywrap.update.build_opener") as opener:
            with self.assertRaises(ProxyWrapError) as result:
                download("https://example.invalid/subscription", "ClashMeta\r\nprivate-token")
        opener.assert_not_called()
        self.assertNotIn("private-token", str(result.exception))

    def test_https_redirect_cannot_downgrade_to_public_http(self):
        response = io.BytesIO(b"private-token")
        request = Request("https://example.invalid/subscription")
        with self.assertRaises(ProxyWrapError) as result:
            SecureRedirect().redirect_request(
                request, response, 302, "Found", {}, "http://public.invalid/private-token"
            )
        self.assertTrue(response.closed)
        self.assertNotIn("private-token", str(result.exception))

    def test_embedded_url_credentials_are_rejected(self):
        for url in (
            "https://user:private-token@example.invalid/subscription",
            "https://private-token@example.invalid/subscription",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ProxyWrapError) as result:
                    validate_url(url)
                self.assertNotIn("private-token", str(result.exception))


class MetadataSecurityTests(unittest.TestCase):
    def test_untrusted_settings_are_rejected_before_reading(self):
        for owner, mode, symlink in ((501, 0o644, False), (0, 0o666, False), (0, 0o644, True)):
            with self.subTest(owner=owner, mode=oct(mode), symlink=symlink):
                metadata = Mock()
                metadata.stat.return_value = SimpleNamespace(st_uid=owner, st_mode=mode)
                metadata.is_symlink.return_value = symlink
                with patch("proxywrap.cli.SETTINGS", metadata):
                    with self.assertRaises(ProxyWrapError):
                        load_settings()
                metadata.read_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
