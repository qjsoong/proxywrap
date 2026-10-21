#!/usr/bin/env python3
"""Serve a complete synthetic subscription and distinguishable HTTP upstream.

Run in a disposable guest; this only binds loopback and writes a private fixture
URL file. The documentation address requests cannot succeed without the fixture
proxy path, so they distinguish interception from ordinary Internet connectivity.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import platform
import subprocess
import threading
import time

SUBSCRIPTION = """dns:
  enable: true
  ipv6: true
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  nameserver:
    - https://1.1.1.1/dns-query
proxies:
  - name: controlled-upstream
    type: http
    server: 127.0.0.1
    port: 18888
proxy-groups:
  - name: Probe
    type: select
    proxies:
      - controlled-upstream
      - DIRECT
      - REJECT
rules:
  - IP-CIDR,192.0.2.123/32,Probe,no-resolve
  - IP-CIDR6,2001:db8::123/128,Probe,no-resolve
  - IP-CIDR,198.19.0.1/32,REJECT,no-resolve
  - MATCH,DIRECT
"""
MARKER = b"PROXYWRAP-CONTROLLED-UPSTREAM\n"


class Handler(BaseHTTPRequestHandler):
    def do_CONNECT(self):
        self.connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        request = self.rfile.readline(8192)
        while self.rfile.readline(8192).strip():
            pass
        if b"slow" in request:
            time.sleep(8)
        response = (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
            + str(len(MARKER)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + MARKER
        )
        self.connection.sendall(response)
        self.close_connection = True

    def do_GET(self):
        if self.server.server_port == 18765:
            body = self.server.subscription.read_bytes()
        else:
            if "slow" in self.path:
                time.sleep(8)
            body = MARKER
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Private guest fixture directory.")
    args = parser.parse_args()
    if os.environ.get("PROXYWRAP_DISPOSABLE_VM") != "1":
        parser.error("Set PROXYWRAP_DISPOSABLE_VM=1 inside a disposable Tart guest.")
    if platform.system() == "Linux":
        identity = subprocess.check_output(["systemd-detect-virt"], text=True).strip()
        expected = "apple"
    elif platform.system() == "Darwin":
        identity = subprocess.check_output(
            ["/usr/sbin/sysctl", "-n", "hw.model"], text=True
        ).strip()
        expected = "VirtualMac"
    else:
        parser.error("Unsupported guest platform.")
    if not identity.startswith(expected):
        parser.error("Expected an Apple Virtualization guest.")
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    subscription = args.directory / "subscription.yaml"
    subscription.write_text(SUBSCRIPTION)
    url = args.directory / "subscription.url"
    url.write_text("http://127.0.0.1:18765/subscription.yaml\n")
    url.chmod(0o600)
    servers = []
    try:
        for port in (18765, 18888):
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            server.subscription = subscription
            servers.append(server)
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print("Synthetic subscription: guest loopback port 18765", flush=True)
        print("Controlled HTTP upstream: guest loopback port 18888", flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.server_close()


if __name__ == "__main__":
    main()
