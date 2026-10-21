#!/usr/bin/env python3
"""Check the controlled fixture through real guest TUN and DNS interception."""

import http.client
import json
import os
import platform
import socket
import struct
import subprocess
import threading
import time
from urllib.request import urlopen

MARKER = b"PROXYWRAP-CONTROLLED-UPSTREAM\n"


def require_guest():
    if os.environ.get("PROXYWRAP_DISPOSABLE_VM") != "1":
        raise SystemExit("Set PROXYWRAP_DISPOSABLE_VM=1 inside a disposable Tart guest.")
    if platform.system() == "Linux":
        command = ["systemd-detect-virt"]
        expected = "apple"
    elif platform.system() == "Darwin":
        command = ["/usr/sbin/sysctl", "-n", "hw.model"]
        expected = "VirtualMac"
    else:
        raise SystemExit("Unsupported guest platform.")
    identity = subprocess.check_output(command, text=True).strip()
    if not identity.startswith(expected):
        raise SystemExit("Expected a positively identified Apple Virtualization guest.")


def request(address, path="/proxywrap-probe"):
    host = f"[{address}]" if ":" in address else address
    client = http.client.HTTPConnection(host, timeout=15)
    try:
        client.request("GET", path)
        response = client.getresponse()
        assert response.status == 200 and response.read() == MARKER, "Unexpected upstream response"
    finally:
        client.close()


def dns(address, tcp):
    # Documentation-only destinations cannot provide a real external resolver.
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    query = struct.pack("!6H", 0x5A17, 0x0100, 1, 0, 0, 0)
    query += b"\x07example\x03com\x00" + struct.pack("!2H", 1, 1)
    with socket.socket(family, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as channel:
        channel.settimeout(8)
        if tcp:
            channel.connect((address, 53))
            channel.sendall(struct.pack("!H", len(query)) + query)
            size = struct.unpack("!H", receive_exact(channel, 2))[0]
            response = receive_exact(channel, size)
        else:
            channel.sendto(query, (address, 53))
            response = channel.recv(4096)
    header = struct.unpack("!6H", response[:12])
    assert header[0] == 0x5A17 and header[3] > 0, "DNS query did not receive an answer"


def receive_exact(channel, size):
    result = bytearray()
    while len(result) < size:
        part = channel.recv(size - len(result))
        if not part:
            raise AssertionError("Truncated TCP DNS response")
        result.extend(part)
    return bytes(result)


def main():
    require_guest()
    for address in ("192.0.2.123", "2001:db8::123"):
        request(address)
        print(f"Controlled TUN {'IPv6' if ':' in address else 'IPv4'} proxy path: passed")
        errors = []

        def slow():
            try:
                request(address, "/slow")
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=slow)
        worker.start()
        time.sleep(1)
        with urlopen("http://127.0.0.1:9090/connections", timeout=5) as response:
            connections = json.load(response).get("connections", [])
        matches = [
            c
            for c in connections
            if "controlled-upstream" in c.get("chains", [])
            and c.get("metadata", {}).get("destinationIP") == address
        ]
        assert any(
            str(c.get("metadata", {}).get("type", "")).lower() in ("tun", "redir") for c in matches
        ), "No controlled TUN/auto-redirect connection evidence"
        worker.join()
        assert not errors, "Controlled slow request failed"
        print("Core connection API confirms intercepted inbound and controlled proxy chain")
    for address in ("192.0.2.53", "2001:db8::53"):
        for tcp in (False, True):
            dns(address, tcp)
            print(
                f"{'TCP' if tcp else 'UDP'} DNS hijack over {'IPv6' if ':' in address else 'IPv4'}: passed"
            )
    print("Global IPv6 egress and encrypted remote providers require separate validation.")


if __name__ == "__main__":
    main()
