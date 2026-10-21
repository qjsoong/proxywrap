import copy
import os
from pathlib import Path
import tempfile
import unittest

from proxywrap.config import (
    COMMON,
    TUN,
    check_safety,
    check_subscription,
    dump,
    initialize,
    merge,
    parse,
    read_yaml,
    validate_url,
)
from proxywrap.errors import ProxyWrapError


class ConfigTests(unittest.TestCase):
    def test_complete_subscription_and_unknown_fields_survive(self):
        subscription = parse(b"""dns:
  enable: true
  nameserver: [https://dns.example/dns-query]
  ipv6: false
proxies: []
proxy-providers: {provider: {type: http, url: https://example.com}}
proxy-groups: [{name: Main, type: select, use: [provider]}]
rules: [MATCH,Main]
rule-providers: {}
future-feature: {nested: [one, two]}
""")
        check_subscription(subscription)
        merged = merge(subscription, COMMON)
        self.assertEqual(merged["dns"], subscription["dns"])
        self.assertEqual(merged["future-feature"], subscription["future-feature"])
        self.assertEqual(merged["proxy-providers"], subscription["proxy-providers"])

    def test_deep_merge_replaces_sequences_and_scalars(self):
        low = {"nested": {"a": 1, "b": [1, 2]}, "untouched": 3, "scalar": 1}
        result = merge(merge(low, {"nested": {"b": [3]}, "scalar": None}), {"nested": {"a": 8}})
        self.assertEqual(result, {"nested": {"a": 8, "b": [3]}, "untouched": 3, "scalar": None})
        self.assertEqual(low["nested"]["b"], [1, 2])

    def test_invalid_sources(self):
        for source in (b"<html>error</html>", b"", b"a: [", b"a: 1\na: 2", b"a: &a [*a]"):
            with self.subTest(source=source), self.assertRaises(ProxyWrapError):
                parse(source)
        for data in ({"message": "denied"}, {"proxies": "bad"}, {"dns": []}):
            with self.assertRaises(ProxyWrapError):
                check_subscription(data)
        check_subscription({"rules": ["MATCH,DIRECT"]})

    def test_yaml_merge_keys_support_explicit_precedence(self):
        value = parse(b"base: &base {a: 1, b: 2}\ncustom: {<<: *base, a: 3}\n")
        self.assertEqual(value["custom"], {"a": 3, "b": 2})

    def test_url_security(self):
        self.assertEqual(
            validate_url(b"https://example.com/x?token=private\n"),
            "https://example.com/x?token=private",
        )
        self.assertTrue(validate_url("http://127.0.0.1:8888/test"))
        for url in (
            "file:///tmp/x",
            "http://example.com",
            "https://u:p@example.com",
            "https://example.com/\nX",
            "https://",
            "https://example.com/\x00token",
        ):
            with self.assertRaises(ProxyWrapError):
                validate_url(url)

    def test_persistent_credentials_and_platform_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            initialize(directory, "linux", b"https://example.com/sub")
            first = read_yaml(directory / "override.yaml")
            initialize(directory, "linux", b"https://example.com/new", force=True)
            self.assertEqual(first, read_yaml(directory / "override.yaml"))
            self.assertGreaterEqual(len(first["authentication"][0].split(":")[1]), 40)
            platform = read_yaml(directory / "override-platform.yaml")["tun"]
            self.assertEqual(platform["stack"], "mips")
            self.assertTrue(platform["auto-redirect"])
            for file in directory.iterdir():
                self.assertEqual(os.stat(file).st_mode & 0o777, 0o600)
            check_safety(merge(first, {"tun": platform}))
        with tempfile.TemporaryDirectory() as tmp:
            initialize(Path(tmp), "darwin", b"https://example.com/sub")
            tun = read_yaml(Path(tmp) / "override-platform.yaml")["tun"]
            self.assertNotIn("auto-redirect", tun)
            self.assertNotIn("device", tun)

    def test_safety_rejects_insecure_or_incompatible_configuration(self):
        safe = merge(COMMON, {"tun": TUN, "authentication": ["proxywrap:" + "x" * 32]})
        for override in (
            {"external-controller": "0.0.0.0:9090"},
            {"authentication": []},
            {"skip-auth-prefixes": ["0.0.0.0/0"]},
            {"ipv6": False},
            {"tun": {"inet6-address": []}},
            {"dns": {"enable": False}},
            {"listeners": [{"type": "http", "port": 1234}]},
        ):
            with self.subTest(override=override), self.assertRaises(ProxyWrapError):
                check_safety(merge(copy.deepcopy(safe), override))

    def test_symlink_refused_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "config"
            directory.mkdir()
            target = Path(tmp) / "private"
            target.write_text("private")
            (directory / "override.yaml").symlink_to(target)
            with self.assertRaises(ProxyWrapError):
                initialize(directory, "linux", b"https://example.com/sub")
            self.assertEqual(target.read_text(), "private")

    def test_roundtrip(self):
        source = {"rules": ["MATCH,DIRECT"], "dns": {"enable": True}}
        self.assertEqual(parse(dump(source)), source)
