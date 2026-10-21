"""Public management commands with an explicit privilege boundary."""

import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from . import __version__
from .config import read_yaml
from .errors import ProxyWrapError
from .services import Service
from .update import locked, recover, update

SETTINGS = (
    Path("/Library/Preferences/io.github.proxywrap.json")
    if sys.platform == "darwin"
    else Path("/usr/local/etc/proxywrap.json")
)


def load_settings() -> dict:
    try:
        metadata = SETTINGS.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or SETTINGS.is_symlink():
            raise ProxyWrapError(
                "Installation settings must be root-owned and not writable by others."
            )
        value = json.loads(SETTINGS.read_text())
        if value["platform"] not in ("linux", "darwin"):
            raise ValueError
        for name in ("config_dir", "binary"):
            if not Path(value[name]).is_absolute():
                raise ValueError
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise ProxyWrapError(
            "Installation settings unavailable or invalid; run install.sh first."
        ) from None


def parser():
    result = argparse.ArgumentParser(description="Manage the ProxyWrap system proxy service.")
    result.add_argument("--version", action="version", version=f"ProxyWrap {__version__}")
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("update", "status", "start", "stop", "restart", "doctor"):
        commands.add_parser(
            name,
            help={
                "update": "Download, merge, validate and apply the complete subscription.",
                "status": "Show service state without administrator privileges.",
                "start": "Start the system service (TUN may affect remote connections).",
                "stop": "Stop the system service and allow Mihomo to restore networking.",
                "restart": "Restart the system service.",
                "doctor": "Check installation files and service state.",
            }[name],
        )
    auth = commands.add_parser("auth", help="Inspect or replace LAN credentials.")
    auth_commands = auth.add_subparsers(dest="action", required=True)
    auth_commands.add_parser(
        "show", help="Explicitly print saved credentials (administrator only)."
    )
    reset = auth_commands.add_parser("reset", help="Set a hidden password and apply configuration.")
    group = reset.add_mutually_exclusive_group()
    group.add_argument("--generate", action="store_true", help="Generate a secure random password.")
    group.add_argument("--password-file", type=Path, help="Read the password from a private file.")
    lan = commands.add_parser("lan", help="Replace the allowed LAN client networks.")
    lan.add_argument("networks", nargs="+", help="CIDR networks; loopback is always retained.")
    return result


def elevate(argv):
    if os.geteuid() == 0:
        return None
    print("Administrator privileges are required for this operation.", file=sys.stderr)
    # Isolated mode prevents the working directory or PYTHONPATH from injecting root code.
    try:
        return subprocess.call(
            ["/usr/bin/sudo", "--", sys.executable, "-I", "-m", "proxywrap", *argv]
        )
    except OSError:
        raise ProxyWrapError("Unable to request privileges; run the installed command using sudo.")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = parser().parse_args(argv)
    try:
        if args.command not in ("status", "doctor"):
            code = elevate(argv)
            if code is not None:
                return code
        settings = load_settings()
        service = Service(settings)
        directory = Path(settings["config_dir"])
        pending_deploy = directory / ".deploy-transaction"
        if args.command not in ("status", "doctor", "stop") and pending_deploy.exists():
            raise ProxyWrapError(
                "An interrupted installation needs recovery; rerun install.sh first."
            )
        if args.command in ("status", "doctor"):
            running = service.status()
            print("Service: " + ("running" if running else "stopped"))
            if os.geteuid() == 0 and pending_deploy.exists():
                print("Installation recovery pending; rerun install.sh.")
            print("Dashboard: http://127.0.0.1:9090/ui")
            if args.command == "doctor":
                print(
                    "Core binary: "
                    + ("present" if Path(settings["binary"]).is_file() else "missing")
                )
                print("Configuration directory: " + str(directory))
                if os.geteuid() == 0:
                    for name in (
                        "subscription.url",
                        "subscription.yaml",
                        "override.yaml",
                        "override-platform.yaml",
                        "config.yaml",
                    ):
                        file = directory / name
                        good = (
                            file.is_file()
                            and not file.is_symlink()
                            and not file.stat().st_mode & 0o077
                        )
                        print(f"{name}: " + ("present, private" if good else "missing or insecure"))
                else:
                    print("Run sudo proxywrap doctor to check private configuration permissions.")
                if settings["platform"] == "darwin":
                    print(
                        "macOS LAN DNS can bypass TUN; review resolvers and overlapping fake-IP ranges."
                    )
                print("Service state does not prove DNS, route or egress correctness.")
            return 0 if running else 3
        if args.command == "update":
            changed = update(settings)
            print(
                "Configuration updated."
                if changed
                else "Configuration unchanged; no restart needed."
            )
        elif args.command in ("start", "stop", "restart"):
            with locked(directory):
                if args.command != "stop" and pending_deploy.exists():
                    raise ProxyWrapError(
                        "An interrupted installation needs recovery; rerun install.sh first."
                    )
                if args.command != "stop":
                    recover(directory, service, apply=True)
                getattr(service, args.command)()
            print(f"Service operation completed: {args.command}.")
        elif args.command == "auth":
            if args.action == "show":
                for entry in read_yaml(directory / "config.yaml").get("authentication", []):
                    print(entry)
            else:
                if args.generate:
                    password = secrets.token_urlsafe(32)
                elif args.password_file:
                    password = args.password_file.read_text().strip()
                else:
                    password = getpass.getpass("New LAN password (at least 16 characters): ")
                    if password != getpass.getpass("Confirm password: "):
                        raise ProxyWrapError("Passwords do not match.")
                if len(password) < 16 or any(
                    not (c.isascii() and (c.isalnum() or c in "_-")) for c in password
                ):
                    raise ProxyWrapError(
                        "Use at least 16 ASCII letters, digits, underscores or hyphens."
                    )

                def set_auth(common):
                    common["authentication"] = ["proxywrap:" + password]
                    return common

                update(settings, edit=set_auth)
                print("LAN credentials replaced. Use sudo proxywrap auth show to retrieve them.")
        elif args.command == "lan":
            try:
                networks = [str(ipaddress.ip_network(n, strict=False)) for n in args.networks]
            except ValueError:
                raise ProxyWrapError("Allowed clients must be valid CIDR networks.") from None

            def set_lan(common):
                common["lan-allowed-ips"] = list(
                    dict.fromkeys(["127.0.0.0/8", "::1/128", *networks])
                )
                return common

            update(settings, edit=set_lan)
            print("Allowed LAN client networks updated.")
        return 0
    except (ProxyWrapError, OSError) as error:
        message = (
            str(error)
            if isinstance(error, ProxyWrapError)
            else "File or system operation failed; check permissions and installation."
        )
        print("Error: " + message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Operation interrupted; recovery will run before the next update/start.",
            file=sys.stderr,
        )
        return 130
