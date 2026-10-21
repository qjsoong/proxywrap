"""Transparent YAML configuration, persistent credentials and secure file writes."""

import copy
import ipaddress
import os
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlsplit

import yaml

from .errors import ProxyWrapError

MAX_BYTES = 10 * 1024 * 1024
DASHBOARD_URL = "https://github.com/MetaCubeX/metacubexd/archive/refs/heads/gh-pages.zip"
COMMON = {
    "mixed-port": 7890,
    "allow-lan": True,
    "bind-address": "*",
    "mode": "rule",
    "ipv6": True,
    "external-controller": "127.0.0.1:9090",
    "external-ui": "ui",
    "external-ui-url": DASHBOARD_URL,
    "profile": {"store-selected": True},
    "skip-auth-prefixes": ["127.0.0.1/8", "::1/128"],
    "lan-allowed-ips": [
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
        "fe80::/10",
    ],
    "lan-disallowed-ips": [],
    "external-controller-tls": "",
    "external-controller-unix": "",
    "external-controller-pipe": "",
    "listeners": [],
    "tunnels": [],
    "port": 0,
    "socks-port": 0,
    "redir-port": 0,
    "tproxy-port": 0,
    "secret": "",
    "external-controller-cors": {
        "allow-origins": ["http://127.0.0.1:9090", "http://localhost:9090"],
        "allow-private-network": False,
    },
}
TUN = {
    "enable": True,
    "stack": "mips",
    "auto-route": True,
    "auto-detect-interface": True,
    "strict-route": False,
    "inet6-address": ["fdfe:dcba:9876::1/126"],
    "dns-hijack": ["any:53", "tcp://any:53"],
}


class UniqueLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate keys rather than silently discarding settings."""


def _mapping(loader, node, deep=False):
    explicit = set()
    for key_node, _ in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            continue
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in explicit:
            raise ProxyWrapError("YAML mapping keys must be unique strings.")
        explicit.add(key)
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ProxyWrapError("YAML mapping keys must be unique strings.")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse(data: bytes | str) -> dict:
    if len(data) > MAX_BYTES:
        raise ProxyWrapError("Configuration exceeds the 10 MiB limit.")
    try:
        value = yaml.load(data, Loader=UniqueLoader)
        budget = [100000]

        def bounded(item, depth=0):
            budget[0] -= 1
            if depth > 60 or budget[0] < 0:
                raise ProxyWrapError("YAML nesting or alias expansion exceeds the safe limit.")
            if isinstance(item, dict):
                for child in item.values():
                    bounded(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    bounded(child, depth + 1)

        bounded(value)
    except (yaml.YAMLError, ValueError, RecursionError, UnicodeError):
        raise ProxyWrapError("Invalid YAML; inspect the source privately for details.") from None
    if not isinstance(value, dict) or not value:
        raise ProxyWrapError("Expected a nonempty Clash/Mihomo YAML mapping.")
    return value


def read_yaml(path: Path) -> dict:
    return parse(path.read_bytes())


def dump(value: dict) -> bytes:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode()


def merge(low: dict, high: dict) -> dict:
    result = copy.deepcopy(low)
    for key, value in high.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def check_subscription(value: dict):
    fields = {
        "dns": dict,
        "proxies": list,
        "proxy-providers": dict,
        "proxy-groups": list,
        "rules": list,
        "rule-providers": dict,
    }
    if not any(key in value for key in fields):
        raise ProxyWrapError("Subscription has no recognized Clash/Mihomo configuration fields.")
    for key, kind in fields.items():
        if key in value and not isinstance(value[key], kind):
            raise ProxyWrapError(f"Subscription field '{key}' has an incompatible structure.")


def check_safety(value: dict):
    controller = value.get("external-controller", "")
    try:
        host, port = controller.rsplit(":", 1)
        if not ipaddress.ip_address(host.strip("[]")).is_loopback or not 0 < int(port) < 65536:
            raise ValueError
    except (ValueError, AttributeError):
        raise ProxyWrapError("external-controller must use a loopback IP address and valid port.")
    if any(
        value.get(k)
        for k in ("external-controller-tls", "external-controller-unix", "external-controller-pipe")
    ):
        raise ProxyWrapError(
            "Additional controller listeners are not supported by ProxyWrap Alpha."
        )
    if value.get("listeners") or value.get("tunnels"):
        raise ProxyWrapError("Additional listeners/tunnels require a separate security review.")
    if value.get("allow-lan"):
        auth = value.get("authentication")
        if not isinstance(auth, list) or not auth:
            raise ProxyWrapError("LAN access requires authentication in override.yaml.")
        for entry in auth:
            if not isinstance(entry, str) or ":" not in entry or len(entry.split(":", 1)[1]) < 16:
                raise ProxyWrapError(
                    "LAN authentication passwords must contain at least 16 characters."
                )
    for field in ("skip-auth-prefixes", "lan-allowed-ips", "lan-disallowed-ips"):
        if not isinstance(value.get(field, []), list):
            raise ProxyWrapError(f"{field} must be a sequence of CIDR networks.")
    for prefix in value.get("skip-auth-prefixes", []):
        try:
            if not ipaddress.ip_network(prefix, strict=False).is_loopback:
                raise ValueError
        except (ValueError, TypeError):
            raise ProxyWrapError("Only loopback clients may skip proxy authentication.") from None
    for prefix in value.get("lan-allowed-ips", []):
        try:
            ipaddress.ip_network(prefix, strict=False)
        except (ValueError, TypeError):
            raise ProxyWrapError("lan-allowed-ips must contain valid client networks.") from None
    dns = value.get("dns", {})
    if not isinstance(dns, dict):
        raise ProxyWrapError("dns must be a mapping.")
    tun = value.get("tun", {})
    if not isinstance(tun, dict):
        raise ProxyWrapError("tun must be a mapping.")
    if tun.get("enable"):
        if value.get("ipv6") is not True or not tun.get("inet6-address"):
            raise ProxyWrapError("TUN requires ipv6: true and explicit inet6-address entries.")
        if not isinstance(tun["inet6-address"], list):
            raise ProxyWrapError("TUN inet6-address must be a sequence.")
        for address in tun["inet6-address"]:
            try:
                if ipaddress.ip_interface(address).version != 6:
                    raise ValueError
            except (ValueError, TypeError):
                raise ProxyWrapError(
                    "TUN inet6-address must contain IPv6 interface addresses."
                ) from None
        if dns.get("enable") is False and tun.get("dns-hijack"):
            raise ProxyWrapError(
                "Subscription disables DNS but TUN DNS hijacking is enabled; "
                "review DNS explicitly in your local override."
            )


def validate_url(data: bytes | str) -> str:
    try:
        url = data.decode().strip() if isinstance(data, bytes) else data.strip()
        parts = urlsplit(url)
        if (
            parts.scheme not in ("https", "http")
            or not parts.hostname
            or parts.username
            or parts.password
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
        ):
            raise ValueError
        if parts.scheme == "http" and not ipaddress.ip_address(parts.hostname).is_loopback:
            raise ValueError
        return url
    except (ValueError, UnicodeError):
        raise ProxyWrapError(
            "Subscription must be an HTTPS URL (HTTP is allowed only on loopback)."
        )


def fsync_dir(path: Path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes, mode=0o600):
    fd, name = tempfile.mkstemp(prefix=".proxywrap-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        fsync_dir(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def initialize(directory: Path, platform: str, url_bytes=None, force=False):
    directory = Path(directory)
    if directory.is_symlink():
        raise ProxyWrapError("Configuration directory must not be a symbolic link.")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    if os.geteuid() == 0:
        os.chown(directory, 0, 0)
    for name in (
        "subscription.url",
        "subscription.yaml",
        "override.yaml",
        "override-platform.yaml",
        "config.yaml",
    ):
        if (directory / name).is_symlink():
            raise ProxyWrapError("Configuration files must not be symbolic links.")
    if platform not in ("linux", "darwin"):
        raise ProxyWrapError("Unsupported platform.")
    if url_bytes is not None:
        url = validate_url(url_bytes)
        if not (directory / "subscription.url").exists() or force:
            atomic_write(directory / "subscription.url", (url + "\n").encode())
    if not (directory / "subscription.url").exists():
        raise ProxyWrapError("No subscription URL saved; provide --url-file or interactive input.")
    if not (directory / "override.yaml").exists():
        common = copy.deepcopy(COMMON)
        common["authentication"] = ["proxywrap:" + secrets.token_urlsafe(32)]
        atomic_write(directory / "override.yaml", dump(common))
    if not (directory / "override-platform.yaml").exists():
        tun = copy.deepcopy(TUN)
        if platform == "linux":
            tun.update({"device": "mihomo", "auto-redirect": True})
        elif platform != "darwin":
            raise ProxyWrapError("Unsupported platform.")
        atomic_write(directory / "override-platform.yaml", dump({"tun": tun}))
    for name in (
        "subscription.url",
        "subscription.yaml",
        "override.yaml",
        "override-platform.yaml",
        "config.yaml",
    ):
        file = directory / name
        if file.is_symlink():
            raise ProxyWrapError("Configuration files must not be symbolic links.")
        if file.exists():
            if os.geteuid() == 0:
                os.chown(file, 0, 0)
            os.chmod(file, 0o600)
