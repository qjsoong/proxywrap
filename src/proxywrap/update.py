"""Serialized subscription transactions with durable recovery and service rollback."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import (MAX_BYTES, atomic_write, check_safety, check_subscription, dump,
                     fsync_dir, merge, parse, read_yaml, validate_url)
from .errors import ProxyWrapError
from .services import Service


class SecureRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, user_agent: str = "ClashMeta") -> bytes:
    validate_url(url)
    if not isinstance(user_agent, str) or not user_agent or any(c in user_agent for c in "\r\n"):
        raise ProxyWrapError("Subscription User-Agent must be a nonempty single line.")
    try:
        request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/yaml"})
        with build_opener(SecureRedirect()).open(request, timeout=30) as response:
            body = response.read(MAX_BYTES + 1)
        if not body or len(body) > MAX_BYTES:
            raise ProxyWrapError("Subscription is empty or exceeds the 10 MiB limit.")
        return body
    except (URLError, OSError, ValueError):
        raise ProxyWrapError("Subscription download failed; check connectivity, URL file and TLS.") from None


@contextmanager
def locked(directory: Path):
    path = directory / ".update.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProxyWrapError("Another configuration operation is in progress.") from None
        yield
    finally:
        os.close(fd)


def validate_core(settings: dict, candidate: Path):
    env = dict(os.environ, SKIP_SYSTEM_IPV6_CHECK="1")
    try:
        result = subprocess.run(
            [settings["binary"], "-t", "-d", settings["config_dir"], "-f", str(candidate)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProxyWrapError("Mihomo configuration check could not complete; check binary and network.")
    if result.returncode:
        # Core diagnostics may contain provider URLs and credentials. Keep them out of normal logs.
        raise ProxyWrapError("Mihomo rejected the candidate configuration. Inspect the subscription "
                             "and overrides privately; the running configuration was preserved.")


def _restore(directory: Path, transaction: Path):
    state = json.loads((transaction / "state.json").read_text())
    for name in ("config.yaml", "subscription.yaml", "override.yaml"):
        if name not in state:
            continue
        if state[name]:
            atomic_write(directory / name, (transaction / name).read_bytes())
        else:
            (directory / name).unlink(missing_ok=True)
    fsync_dir(directory)
    return state


def recover(directory: Path, service, apply: bool):
    transaction = directory / ".transaction"
    if not transaction.exists():
        return
    if not (transaction / "state.json").exists():
        shutil.rmtree(transaction)
        return
    state = _restore(directory, transaction)
    if state.get("was_running") and apply:
        service.restart()
    shutil.rmtree(transaction)
    fsync_dir(directory)


def update(settings: dict, apply=True, edit=None) -> bool:
    """Download and apply. A stopped service stays stopped; edit stages local overrides."""
    directory = Path(settings["config_dir"])
    with locked(directory):
        return _update(settings, apply, edit)


def _update(settings: dict, apply: bool, edit=None) -> bool:
    directory = Path(settings["config_dir"])
    service = Service(settings)
    recover(directory, service, apply)
    url = validate_url((directory / "subscription.url").read_bytes())
    raw = download(url, settings.get("user_agent", "ClashMeta"))
    subscription = parse(raw)
    check_subscription(subscription)
    common = read_yaml(directory / "override.yaml")
    if edit:
        common = edit(common)
    final = merge(merge(subscription, common), read_yaml(directory / "override-platform.yaml"))
    check_safety(final)
    payload = dump(final)
    fd, name = tempfile.mkstemp(prefix=".candidate-", suffix=".yaml", dir=directory)
    candidate = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        validate_core(settings, candidate)
    finally:
        candidate.unlink(missing_ok=True)
    current = directory / "config.yaml"
    changed = not current.exists() or read_yaml(current) != final
    if not changed:
        atomic_write(directory / "subscription.yaml", raw)
        if edit:
            atomic_write(directory / "override.yaml", dump(common))
        return False
    was_running = service.status() if apply else False
    transaction = directory / ".transaction"
    transaction.mkdir(mode=0o700)
    state = {"was_running": was_running}
    for filename in ("config.yaml", "subscription.yaml", "override.yaml"):
        source = directory / filename
        state[filename] = source.exists()
        if source.exists():
            atomic_write(transaction / filename, source.read_bytes())
    atomic_write(transaction / "state.json", json.dumps(state).encode())
    fsync_dir(directory)
    try:
        atomic_write(directory / "subscription.yaml", raw)
        if edit:
            atomic_write(directory / "override.yaml", dump(common))
        atomic_write(current, payload)
        if was_running:
            service.restart()
            if not service.status():
                raise ProxyWrapError("Mihomo did not remain running after activation.")
    except Exception:
        try:
            _restore(directory, transaction)
            if was_running:
                service.restart()
                if not service.status():
                    raise ProxyWrapError("Service did not recover.")
        except Exception:
            raise ProxyWrapError("Activation failed and rollback could not restore service. "
                                 "Previous files remain in .transaction; run stop, then recover.") from None
        shutil.rmtree(transaction)
        raise ProxyWrapError("Activation failed; previous configuration was restored.") from None
    backup = directory / ".previous"
    if backup.exists():
        shutil.rmtree(backup)
    transaction.rename(backup)
    fsync_dir(directory)
    return True
