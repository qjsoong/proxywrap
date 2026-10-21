"""Install validated configuration and native services without exposing credentials."""

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import shutil
import socket
import sys

from .config import atomic_write, check_safety, initialize, read_yaml, validate_url
from .errors import ProxyWrapError
from .services import Service
from .update import _update, locked, validate_core

SETTINGS_PATH = Path(
    "/Library/Preferences/io.github.proxywrap.json"
    if sys.platform == "darwin"
    else "/usr/local/etc/proxywrap.json"
)
CONFIG_FILES = (
    "config.yaml",
    "subscription.yaml",
    "subscription.url",
    "override.yaml",
    "override-platform.yaml",
)


def check_ports(directory: Path):
    """Diagnose TCP listener conflicts before starting an inactive core."""
    config = read_yaml(Path(directory) / "config.yaml")
    listeners = []
    mixed = config.get("mixed-port", 0)
    if mixed:
        host = config.get("bind-address", "*") if config.get("allow-lan") else "127.0.0.1"
        listeners.append(("mixed-port", "0.0.0.0" if host == "*" else host, mixed))
    controller = config.get("external-controller", "")
    if controller:
        host, port = controller.rsplit(":", 1)
        listeners.append(("external-controller", host.strip("[]"), port))
    for label, host, port in listeners:
        try:
            port = int(port)
            if not 0 < port < 65536:
                raise ValueError
            host = host.strip("[]")
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((host, port))
        except (OSError, ValueError, TypeError):
            raise ProxyWrapError(
                f"Cannot bind {label} listener. Check its address/port and other running services; "
                "ProxyWrap will not stop another process."
            ) from None


def _snapshot(directory: Path, settings_path: Path, service: Service) -> Path:
    transaction = directory / ".deploy-transaction"
    transaction.mkdir(mode=0o700)
    state = {"was_running": service.status(), "was_enabled": service.enabled(), "files": {}}
    for filename in CONFIG_FILES:
        source = directory / filename
        if source.is_symlink():
            shutil.rmtree(transaction)
            raise ProxyWrapError("Configuration files must not be symbolic links.")
        state["files"][filename] = source.exists()
        if source.exists():
            atomic_write(transaction / filename, source.read_bytes())
    state["settings_existed"] = settings_path.exists()
    if settings_path.exists():
        atomic_write(transaction / "settings.json", settings_path.read_bytes())
    atomic_write(transaction / "state.json", json.dumps(state).encode())
    return transaction


def _restore(directory: Path, transaction: Path, settings_path: Path, service: Service):
    state_file = transaction / "state.json"
    if not state_file.exists():
        shutil.rmtree(transaction)
        return
    state = json.loads(state_file.read_text())
    current = directory / "config.yaml"
    old_config = transaction / "config.yaml"
    config_changed = current.exists() != state["files"]["config.yaml"]
    if current.exists() and old_config.exists():
        config_changed = current.read_bytes() != old_config.read_bytes()
    for filename, existed in state["files"].items():
        target = directory / filename
        if existed:
            atomic_write(target, (transaction / filename).read_bytes())
        else:
            target.unlink(missing_ok=True)
    if state["settings_existed"]:
        atomic_write(settings_path, (transaction / "settings.json").read_bytes(), mode=0o644)
    else:
        settings_path.unlink(missing_ok=True)
    if state["was_running"] and (config_changed or not service.status()):
        service.restart()
    elif not state["was_running"] and service.status():
        service.stop()
    if not state["was_enabled"]:
        service.disable()
    shutil.rmtree(transaction)


def _archive(directory: Path, transaction: Path):
    backups = directory / ".deploy-backups"
    backups.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    transaction.rename(backups / stamp)


def deploy(
    settings: dict,
    *,
    url_bytes=None,
    start=False,
    force=False,
    settings_path: Path = SETTINGS_PATH,
    package_default_config=False,
):
    """Preserve edits on redeploy and restore known files if activation fails."""
    directory = Path(settings["config_dir"])
    settings_path = Path(settings_path)
    if directory.is_symlink() or settings_path.is_symlink():
        raise ProxyWrapError("Deployment paths must not be symbolic links.")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    settings_path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    os.chmod(directory, 0o700)
    service = Service(settings)
    with locked(directory):
        pending = directory / ".deploy-transaction"
        if pending.exists():
            try:
                _restore(directory, pending, settings_path, service)
            except Exception:
                raise ProxyWrapError(
                    "A previous installation needs recovery. Stop Mihomo and inspect the private "
                    ".deploy-transaction backup before retrying."
                ) from None
        old_settings = None
        if settings_path.exists():
            try:
                old_settings = json.loads(settings_path.read_text())
            except (ValueError, OSError):
                raise ProxyWrapError(
                    "Existing ProxyWrap settings could not be read safely."
                ) from None
            for key in ("platform", "config_dir", "binary", "service_name"):
                if key in settings and key in old_settings and old_settings[key] != settings[key]:
                    raise ProxyWrapError(
                        "Existing installation paths differ. Restore the original paths before "
                        "redeploying; automatic migration is not supported."
                    )
            # Preserve administrator settings, including subscription User-Agent.
            settings = dict(old_settings, **settings)
            service = Service(settings)
        elif (directory / "config.yaml").exists() and not force and not package_default_config:
            raise ProxyWrapError(
                "An unmanaged Mihomo configuration already exists. Review it, then use --force "
                "to back it up before installing ProxyWrap."
            )
        saved_url = directory / "subscription.url"
        if url_bytes is not None:
            new_url = validate_url(url_bytes)
            if saved_url.exists() and validate_url(saved_url.read_bytes()) != new_url and not force:
                raise ProxyWrapError("Use --force to back up and replace a saved subscription URL.")
        existing = old_settings is not None and (directory / "config.yaml").exists()
        transaction = _snapshot(directory, settings_path, service)
        try:
            initialize(directory, settings["platform"], url_bytes=url_bytes, force=force)
            if existing and not force:
                # Reinstalling the manager does not implicitly update a working subscription.
                check_safety(read_yaml(directory / "config.yaml"))
                validate_core(settings, directory / "config.yaml")
            else:
                _update(settings, apply=service.status())
            service.install()
            service.enable()
            if start and not service.status():
                check_ports(directory)
                service.start()
                if not service.status():
                    raise ProxyWrapError("Mihomo did not remain running after installation.")
            atomic_write(
                settings_path, (json.dumps(settings, indent=2) + "\n").encode(), mode=0o644
            )
            _archive(directory, transaction)
        except Exception as error:
            try:
                _restore(directory, transaction, settings_path, service)
            except Exception:
                raise ProxyWrapError(
                    "Installation failed and service recovery needs attention. Previous files "
                    "remain in the private .deploy-transaction directory."
                ) from None
            if isinstance(error, ProxyWrapError):
                raise error
            raise ProxyWrapError(
                "Installation failed; previous configuration was restored."
            ) from None
    print("Native service boot startup: enabled.")
    print("Native service: " + ("running." if service.status() else "stopped."))
    if not start:
        print(
            "No startup was requested; an existing running service is preserved. "
            "Boot startup remains enabled. Use sudo proxywrap start when ready."
        )
    print("Dashboard: http://127.0.0.1:9090/ui (available while Mihomo is running).")
    print("Use sudo proxywrap auth show to inspect LAN credentials privately.")


def _read_input(args, config_dir: Path):
    if args.url_file:
        try:
            with Path(args.url_file).open("rb") as stream:
                result = stream.read(16 * 1024 + 1)
            if len(result) > 16 * 1024:
                raise ProxyWrapError("Subscription URL file exceeds the 16 KiB limit.")
            return result
        except OSError:
            raise ProxyWrapError("Cannot read the subscription URL file.") from None
    if (config_dir / "subscription.url").exists():
        return None
    if not sys.stdin.isatty():
        raise ProxyWrapError("Provide --url-file for unattended installation.")
    try:
        return getpass.getpass("Complete subscription URL (hidden): ").encode()
    except (EOFError, KeyboardInterrupt):
        raise ProxyWrapError("Subscription input cancelled.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Internal ProxyWrap deployment entry point.")
    parser.add_argument("--platform", required=True, choices=("linux", "darwin"))
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--url-file")
    parser.add_argument("--settings-file", default=str(SETTINGS_PATH))
    parser.add_argument("--package-default-config", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--remote", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--start", action="store_true")
    group.add_argument("--no-start", action="store_true")
    parser.add_argument("--brew")
    parser.add_argument("--brew-uid", type=int)
    parser.add_argument("--brew-user")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ProxyWrapError("Deployment requires administrator privileges; use ./install.sh.")
        config_dir = Path(args.config_dir)
        binary = Path(args.binary)
        if not config_dir.is_absolute() or not binary.is_absolute() or not binary.is_file():
            raise ProxyWrapError(
                "Configuration and Mihomo executable paths must be absolute and valid."
            )
        settings = {"platform": args.platform, "config_dir": str(config_dir), "binary": str(binary)}
        for key in ("brew", "brew_uid", "brew_user"):
            value = getattr(args, key)
            if value is not None:
                settings[key] = value
        if args.remote and args.start:
            print("Starting TUN over a remote connection may interrupt this session.")
        start = args.start or (not args.no_start and not args.remote)
        deploy(
            settings,
            url_bytes=_read_input(args, config_dir),
            start=start,
            force=args.force,
            settings_path=Path(args.settings_file),
            package_default_config=args.package_default_config,
        )
        return 0
    except (ProxyWrapError, OSError) as error:
        message = (
            str(error) if isinstance(error, ProxyWrapError) else "Deployment file operation failed."
        )
        print("Error: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
