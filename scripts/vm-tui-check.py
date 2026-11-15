#!/usr/bin/env python3
"""Exercise installed TUI in a real PTY using the running disposable VM fixture."""

import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time
from urllib.request import ProxyHandler, build_opener

if (
    os.environ.get("PROXYWRAP_DISPOSABLE_VM") != "1"
    or os.geteuid() == 0
    or subprocess.check_output(["systemd-detect-virt"], text=True).strip() != "apple"
):
    raise SystemExit(
        "Run as an ordinary user in a disposable Tart Linux guest with PROXYWRAP_DISPOSABLE_VM=1."
    )
api = build_opener(ProxyHandler({}))


def groups():
    with api.open("http://127.0.0.1:9090/proxies", timeout=2) as response:
        return json.load(response)["proxies"]


master, slave = pty.openpty()
original = termios.tcgetattr(slave)
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
process = subprocess.Popen(
    ["/usr/local/bin/proxywrap", "tui"],
    stdin=slave,
    stdout=slave,
    stderr=slave,
    env={**os.environ, "TERM": "xterm-256color"},
)
output = bytearray()


def collect(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if select.select([master], [], [], 0.1)[0]:
            output.extend(os.read(master, 65536))


def send(keys, wait=1):
    os.write(master, keys)
    collect(wait)


try:
    collect(4)
    assert b"ProxyWrap" in output and b"running" in output
    names = sorted(name for name, value in groups().items() if value.get("type") == "Selector")
    send(b"2" + b"j" * names.index("Probe") + b"\n")
    nodes = groups()["Probe"]["all"]
    send(b"j" * nodes.index("DIRECT") + b"\nny", 0.5)
    assert groups()["Probe"]["now"] == "controlled-upstream", "cancel changed selection"
    send(b"\ny", 3)
    assert groups()["Probe"]["now"] == "DIRECT", "confirmed selection failed"
    send(b"\n", 1)
    send(b"k" * nodes.index("DIRECT") + b"\ny", 3)
    assert groups()["Probe"]["now"] == "controlled-upstream", "restore failed"
    send(b"\n3", 1)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 8, 32, 0, 0))
    process.send_signal(signal.SIGWINCH)
    collect(0.5)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    process.send_signal(signal.SIGWINCH)
    send(b"1xn", 1)
    send(b"xy", 4)
    assert subprocess.call(["systemctl", "is-active", "--quiet", "mihomo"]) != 0
    send(b"\n", 1)
    send(b"sy", 5)
    assert subprocess.call(["systemctl", "is-active", "--quiet", "mihomo"]) == 0
    send(b"\n", 1)
    probe = subprocess.Popen(
        ["curl", "--noproxy", "*", "--max-time", "15", "-s", "http://192.0.2.123/slow"],
        stdout=subprocess.DEVNULL,
    )
    output.clear()
    send(b"3", 3)
    assert b"192.0.2.123" in output and b"controlled-upstream" in output
    probe.wait(timeout=15)
    send(b"q", 1)
    assert process.wait(timeout=5) == 0
    assert termios.tcgetattr(slave) == original, "terminal modes not restored"
    print(
        "PASS: real PTY render, cancelled/confirmed sudo node switching, restoration, views, resize, cancelled stop, confirmed stop/start, live connections and clean terminal exit"
    )
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    os.close(master)
    os.close(slave)
