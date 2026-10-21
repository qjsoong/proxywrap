"""Native, system-level Mihomo service management.

No command output from the core or service manager is included in exceptions:
service diagnostics can contain subscription URLs and credentials.
"""

from __future__ import annotations

import errno
import os
import plistlib
import pwd
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import yaml

from .errors import ProxyWrapError

NETWORK_GATE = (
    "PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH; "
    "for family in -4 -6; do "
    'if ip "$family" route show table main default 2>/dev/null | '
    "grep -Ev '(^| )linkdown( |$)| dev (lo|mihomo)( |$)' | "
    "grep -Eq '^default( .*)? dev [^ ]+'; then exit 0; fi; "
    "done; exit 1"
)


def _systemd_argument(value: str) -> str:
    """Quote one ExecStart argument without specifier or variable expansion."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped.replace("%", "%%").replace("$", "$$") + '"'


def _absolute_path(value: object, description: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise ProxyWrapError(f"An absolute {description} path is required.")
    path = Path(value)
    if not path.is_absolute() or any(char in str(path) for char in "\n\r\0"):
        raise ProxyWrapError(f"An absolute {description} path is required.")
    return path


class Service:
    """Manage a single Mihomo service; callers elevate before mutations.

    ``settings`` contains platform (linux/darwin), config_dir and binary.
    macOS optionally records brew_uid or brew_user to retire that user's
    Homebrew service before installing the independent system daemon.
    The injectable paths and runner are for isolated tests, not CLI options.
    """

    STARTUP_SAMPLES = 5
    STARTUP_ATTEMPTS = 25
    POLL_INTERVAL = 0.5

    def __init__(
        self,
        settings: dict,
        *,
        runner=None,
        sleeper=None,
        systemd_dir: Path = Path("/etc/systemd/system"),
        launchd_dir: Path = Path("/Library/LaunchDaemons"),
    ):
        self.platform = settings.get("platform")
        if self.platform not in ("linux", "darwin"):
            raise ProxyWrapError("Only Debian-family Linux and macOS are supported.")
        self.config_dir = _absolute_path(settings.get("config_dir"), "configuration directory")
        self.binary = _absolute_path(settings.get("binary"), "Mihomo binary")
        default = "mihomo.service" if self.platform == "linux" else "io.github.proxywrap.mihomo"
        self.name = settings.get("service_name", default)
        pattern = r"[A-Za-z0-9_.@-]+\.service" if self.platform == "linux" else r"[A-Za-z0-9_.-]+"
        if not isinstance(self.name, str) or not re.fullmatch(pattern, self.name):
            raise ProxyWrapError("The configured service name is invalid.")
        self.settings = settings
        self.systemd_dir = Path(systemd_dir)
        self.launchd_dir = Path(launchd_dir)
        self.plist_path = self.launchd_dir / f"{self.name}.plist"
        self.target = f"system/{self.name}"
        self._runner = runner or subprocess.run
        self._sleep = sleeper or time.sleep

    @staticmethod
    def _require_root() -> None:
        if os.geteuid() != 0:
            raise ProxyWrapError("This operation requires administrator privileges; use sudo.")

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
        try:
            result = self._runner(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=dict(os.environ, PATH="/usr/bin:/bin:/usr/sbin:/sbin"),
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ProxyWrapError(
                f"Unable to run {Path(args[0]).name}; check the native service manager."
            ) from None
        if check and result.returncode != 0:
            raise ProxyWrapError(
                f"The native service manager could not {args[1]} Mihomo "
                f"(exit {result.returncode}). Inspect its protected logs locally."
            )
        return result

    @staticmethod
    def _write(path: Path, content: bytes, mode: int = 0o644) -> None:
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".proxywrap-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                os.fchmod(handle.fileno(), mode)
                if os.geteuid() == 0:
                    os.fchown(handle.fileno(), 0, 0)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def install(self) -> None:
        """Write native service definitions without starting or restarting TUN."""
        self._require_root()
        if self.platform == "linux":
            self._install_systemd()
        else:
            self._install_launchd()

    def definition_paths(self) -> list[Path]:
        """Return only definitions this manager may create or replace."""
        if self.platform == "linux":
            return [
                self.systemd_dir / self.name,
                self.systemd_dir / f"{self.name}.d" / "10-proxywrap.conf",
            ]
        return [self.plist_path]

    def reload_definition(self) -> None:
        """Reload restored Linux definitions without changing process state."""
        self._require_root()
        if self.platform == "linux":
            self._run(["systemctl", "daemon-reload"])

    def preflight_adoption(self, managed: bool) -> None:
        """Require explicit stop before adopting a different running deployment."""
        self._require_root()
        running, pid = self._state()
        if running and not managed:
            raise ProxyWrapError(
                "An unmanaged Mihomo service is running. Stop it explicitly before "
                "installing ProxyWrap; --force does not authorize stopping it."
            )
        if self.platform == "linux":
            if not running and pid.isdecimal() and int(pid) > 0:
                raise ProxyWrapError(
                    "Mihomo is changing service state. Wait for it to settle or stop it "
                    "explicitly before redeploying."
                )
            if running:
                try:
                    raw = (Path("/proc") / pid / "cmdline").read_bytes()
                    arguments = [os.fsdecode(item) for item in raw.rstrip(b"\0").split(b"\0")]
                except OSError:
                    raise ProxyWrapError(
                        "Cannot verify the running Mihomo command. Stop it explicitly "
                        "before redeploying."
                    ) from None
                if arguments != [str(self.binary), "-d", str(self.config_dir)]:
                    raise ProxyWrapError(
                        "Running Mihomo uses different service arguments. Stop it "
                        "explicitly before redeploying with the recorded paths."
                    )
        else:
            for target in self._brew_targets():
                if self._run(["launchctl", "print", target], check=False).returncode == 0:
                    raise ProxyWrapError(
                        "A Homebrew Mihomo service is loaded. Stop it explicitly before "
                        "installing ProxyWrap to preserve recovery of the previous service."
                    )

    def _install_systemd(self) -> None:
        result = self._run(
            ["systemctl", "show", "--property=LoadState", "--value", self.name], check=False
        )
        load_state = result.stdout.strip()
        if load_state not in ("loaded", "not-found", "masked"):
            raise ProxyWrapError("systemd could not inspect Mihomo; confirm systemd is running.")
        if load_state == "masked":
            raise ProxyWrapError(
                "Mihomo is masked in systemd; unmask it explicitly before installation."
            )
        if load_state == "not-found":
            unit = (
                "# Managed by ProxyWrap.\n"
                "[Unit]\nDescription=Mihomo Proxy\nAfter=network.target\n\n"
                "[Service]\nType=simple\n\n"
                "[Install]\nWantedBy=multi-user.target\n"
            )
            self._write(self.systemd_dir / self.name, unit.encode())
        arguments = " ".join(
            _systemd_argument(str(item)) for item in (self.binary, "-d", self.config_dir)
        )
        gate = " ".join(_systemd_argument(item) for item in ("/bin/sh", "-ec", NETWORK_GATE))
        dropin = (
            "# Managed by ProxyWrap.\n[Unit]\n"
            "Wants=network-online.target\nAfter=network-online.target\n"
            "StartLimitIntervalSec=0\n\n[Service]\n"
            "Environment=SKIP_SYSTEM_IPV6_CHECK=1\n"
            "User=root\nGroup=root\n"
            f"ExecStartPre={gate}\n"
            "ExecStart=\n"
            f"ExecStart={arguments}\n"
            "Restart=on-failure\nRestartSec=3\nTimeoutStopSec=20\nLimitNOFILE=1048576\n"
            "UMask=0077\n"
            "CapabilityBoundingSet=\n"
            "CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW CAP_NET_BIND_SERVICE\n"
            "AmbientCapabilities=\n"
            "AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW CAP_NET_BIND_SERVICE\n"
            "NoNewPrivileges=true\n"
        )
        self._write(self.systemd_dir / f"{self.name}.d" / "10-proxywrap.conf", dropin.encode())
        self._run(["systemctl", "daemon-reload"])

    def _brew_uids(self) -> set[int]:
        uids: set[int] = set()
        for value in (self.settings.get("brew_uid"), os.environ.get("SUDO_UID")):
            if value is not None:
                try:
                    uid = int(value)
                except (TypeError, ValueError):
                    raise ProxyWrapError("The Homebrew user ID is invalid.") from None
                if uid > 0:
                    uids.add(uid)
        user = self.settings.get("brew_user")
        if user:
            try:
                uids.add(pwd.getpwnam(user).pw_uid)
            except (KeyError, TypeError):
                raise ProxyWrapError("The Homebrew installation user does not exist.") from None
        return uids

    def _brew_targets(self) -> list[str]:
        domains = ["system"]
        for uid in sorted(self._brew_uids()):
            domains.extend((f"gui/{uid}", f"user/{uid}"))
        return [f"{domain}/homebrew.mxcl.mihomo" for domain in domains]

    def _retire_brew_services(self) -> None:
        """Disable only Mihomo's known Homebrew jobs, never unrelated processes."""
        for target in self._brew_targets():
            present = self._run(["launchctl", "print", target], check=False).returncode == 0
            # A persistent disable also prevents an unloaded Brew plist from
            # launching on the next login or reboot. Absent user domains can fail.
            disabled = self._run(["launchctl", "disable", target], check=False)
            if present:
                if disabled.returncode:
                    raise ProxyWrapError("Unable to disable the competing Homebrew Mihomo service.")
                self._run(["launchctl", "bootout", target])
                if self._run(["launchctl", "print", target], check=False).returncode == 0:
                    raise ProxyWrapError("The competing Homebrew Mihomo service is still loaded.")
            elif target.startswith("system/") and disabled.returncode:
                raise ProxyWrapError("Unable to disable the system Homebrew Mihomo service.")

    def _install_launchd(self) -> None:
        self._retire_brew_services()
        # Core output can contain provider data. Keep it in the private
        # configuration directory instead of public Homebrew log files.
        log_path = self.config_dir / "mihomo.log"
        log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
        )
        try:
            os.fchmod(descriptor, 0o600)
            if os.geteuid() == 0:
                os.fchown(descriptor, 0, 0)
        finally:
            os.close(descriptor)
        plist = {
            "Label": self.name,
            "ProgramArguments": [str(self.binary), "-d", str(self.config_dir)],
            "WorkingDirectory": str(self.config_dir),
            "UserName": "root",
            "GroupName": "wheel",
            "EnvironmentVariables": {"SKIP_SYSTEM_IPV6_CHECK": "1"},
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 3,
            "ExitTimeOut": 20,
            "Umask": 0o077,
            "StandardOutPath": str(log_path),
            "StandardErrorPath": str(log_path),
        }
        self._write(self.plist_path, plistlib.dumps(plist))

    def enable(self) -> None:
        """Enable boot activation without starting a currently stopped core."""
        self._require_root()
        if self.platform == "linux":
            self._run(["systemctl", "enable", self.name])
        else:
            self._run(["launchctl", "enable", self.target])

    def enabled(self) -> bool:
        if self.platform == "linux":
            return (
                self._run(["systemctl", "is-enabled", "--quiet", self.name], check=False).returncode
                == 0
            )
        result = self._run(["launchctl", "print-disabled", "system"], check=False)
        if result.returncode or not self.plist_path.is_file():
            return False
        match = re.search(r'"' + re.escape(self.name) + r'"\s*=>\s*(true|false)', result.stdout)
        return match is None or match.group(1) == "false"

    def disable(self) -> None:
        """Disable future boot activation without stopping a running core."""
        self._require_root()
        if self.platform == "linux":
            self._run(["systemctl", "disable", self.name])
        else:
            self._run(["launchctl", "disable", self.target])

    def _state(self) -> tuple[bool, str]:
        if self.platform == "linux":
            result = self._run(
                ["systemctl", "show", "--property=ActiveState,MainPID", self.name], check=False
            )
            fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            pid = fields.get("MainPID", "0")
            return result.returncode == 0 and fields.get(
                "ActiveState"
            ) == "active" and pid != "0", pid
        result = self._run(["launchctl", "print", self.target], check=False)
        pid = re.search(r"^\s*pid = (\d+)\s*$", result.stdout, re.MULTILINE)
        running = re.search(r"^\s*state = running\s*$", result.stdout, re.MULTILINE)
        return result.returncode == 0 and bool(running and pid), pid.group(1) if pid else "0"

    def status(self) -> bool:
        """Read native running state without reading private configuration."""
        return self._state()[0]

    def _check_ports(self) -> None:
        try:
            config = yaml.safe_load((self.config_dir / "config.yaml").read_text())
        except (OSError, ValueError, yaml.YAMLError):
            raise ProxyWrapError(
                "Cannot inspect generated config.yaml before service startup."
            ) from None
        if not isinstance(config, dict):
            raise ProxyWrapError("Generated config.yaml must be a YAML mapping.")
        listeners: set[tuple[str, int, int]] = set()
        host = config.get("bind-address", "*")
        host = "0.0.0.0" if host == "*" else str(host).strip("[]")
        if config.get("allow-lan") is False:
            host = "127.0.0.1"
        for key in ("mixed-port", "port", "socks-port", "redir-port", "tproxy-port"):
            port = config.get(key)
            if isinstance(port, int) and not isinstance(port, bool) and 0 < port <= 65535:
                addresses = [str(host)]
                if host == "0.0.0.0" and config.get("ipv6", False) and socket.has_ipv6:
                    addresses.append("::")
                for address in addresses:
                    listeners.add((address, port, socket.SOCK_STREAM))
                    if key in ("mixed-port", "socks-port", "tproxy-port"):
                        listeners.add((address, port, socket.SOCK_DGRAM))
        dns = config.get("dns")
        dns_listen = dns.get("listen") if isinstance(dns, dict) and dns.get("enable") else None
        for value in (config.get("external-controller"), dns_listen):
            if isinstance(value, str) and ":" in value:
                address, _, raw_port = value.rpartition(":")
                if raw_port.isdecimal() and 0 < int(raw_port) <= 65535:
                    listeners.add((address.strip("[]"), int(raw_port), socket.SOCK_STREAM))
                    if value == dns_listen:
                        listeners.add((address.strip("[]"), int(raw_port), socket.SOCK_DGRAM))
        for address, port, kind in tuple(listeners):
            # BSD permits some wildcard/specific listener combinations. Probe
            # loopback explicitly so an existing local listener is diagnosed.
            if address == "0.0.0.0":
                listeners.add(("127.0.0.1", port, kind))
            elif address == "::":
                listeners.add(("::1", port, kind))
        for address, port, kind in sorted(listeners):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            try:
                with socket.socket(family, kind) as probe:
                    if kind == socket.SOCK_STREAM:
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind((address, port))
                    if kind == socket.SOCK_STREAM:
                        # BSD can permit overlapping binds with SO_REUSEADDR;
                        # listening is what detects the competing live socket.
                        probe.listen(1)
            except OSError as error:
                if error.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
                    continue
                protocol = "TCP" if kind == socket.SOCK_STREAM else "UDP"
                raise ProxyWrapError(
                    f"{protocol} port {port} is unavailable. Stop the conflicting listener or edit "
                    "the local override, then run proxywrap update. No process was terminated."
                ) from None

    def _wait_running(self) -> None:
        previous = None
        stable = 0
        for _ in range(self.STARTUP_ATTEMPTS):
            running, pid = self._state()
            stable = stable + 1 if running and pid == previous else int(running)
            previous = pid if running else None
            if stable >= self.STARTUP_SAMPLES:
                return
            self._sleep(self.POLL_INTERVAL)
        raise ProxyWrapError(
            "Mihomo did not remain running after startup. Inspect the protected core logs "
            "and native service status; network interception has not been verified."
        )

    def start(self) -> None:
        self._require_root()
        if self.status():
            return
        self._check_ports()
        if self.platform == "linux":
            self._run(["systemctl", "start", self.name])
        else:
            self._retire_brew_services()
            self._run(["launchctl", "enable", self.target])
            loaded = self._run(["launchctl", "print", self.target], check=False).returncode == 0
            if not loaded:
                self._run(["launchctl", "bootstrap", "system", str(self.plist_path)])
            else:
                self._run(["launchctl", "kickstart", self.target])
        self._wait_running()

    def stop(self) -> None:
        """Request a native graceful stop, preserving boot activation."""
        self._require_root()
        if self.platform == "linux":
            result = self._run(["systemctl", "stop", self.name], check=False)
            if result.returncode:
                state = self._run(
                    ["systemctl", "show", "--property=LoadState", "--value", self.name],
                    check=False,
                )
                if state.stdout.strip() != "not-found":
                    raise ProxyWrapError(
                        "The native service manager could not stop Mihomo. "
                        "Inspect its protected logs locally."
                    )
        elif self._run(["launchctl", "print", self.target], check=False).returncode == 0:
            self._run(["launchctl", "bootout", self.target])
        if self.status():
            raise ProxyWrapError("Mihomo is still running after the stop request.")

    def restart(self) -> None:
        # A graceful stop lets the core remove its own TUN routes and DNS state.
        self.stop()
        self.start()
