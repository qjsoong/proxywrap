#!/usr/bin/env bash
# Read-only evidence collection inside a disposable virtualization guest.
set -euo pipefail
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

usage() {
  cat <<'EOF'
Usage: PROXYWRAP_DISPOSABLE_VM=1 scripts/vm-check.sh [--baseline|--deployed]

Run inside a disposable Tart guest, after preserving a recoverable baseline.
This script reads services, interfaces, routes and the loopback Mihomo API.
It does not change services or networking and does not read credentials.
EOF
}

mode=deployed
case "${1:---deployed}" in
  --baseline) mode=baseline ;;
  --deployed) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { usage >&2; exit 2; }
[[ "${PROXYWRAP_DISPOSABLE_VM:-}" == 1 ]] || {
  echo "Refusing to run without PROXYWRAP_DISPOSABLE_VM=1." >&2
  exit 2
}

platform=$(uname -s)
case "$platform" in
  Linux)
    [[ "$(systemd-detect-virt 2>/dev/null || true)" == apple ]] || {
      echo "Expected an Apple Virtualization Linux guest." >&2; exit 2;
    }
    ;;
  Darwin)
    [[ "$(sysctl -n hw.model)" == VirtualMac* ]] || {
      echo "Expected a VirtualMac guest." >&2; exit 2;
    }
    ;;
  *) echo "Unsupported guest platform." >&2; exit 2 ;;
esac

echo "Guest platform: $platform $(uname -m)"
echo "Evidence time: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
if [[ "$platform" == Linux ]]; then
  cat /etc/os-release
  ip -brief address
  ip -4 rule
  ip -6 rule
  ip -4 route show table all
  ip -6 route show table all
  if [[ "$mode" == deployed ]]; then
    systemctl is-enabled mihomo.service
    systemctl is-active mihomo.service
    systemctl show mihomo.service -p FragmentPath -p DropInPaths -p MainPID
    ip address show dev mihomo
    ip -6 address show dev mihomo | grep -F 'fdfe:dcba:9876::1/126'
  fi
else
  sw_vers
  ifconfig
  netstat -nr -f inet
  netstat -nr -f inet6
  scutil --dns
  if [[ "$mode" == deployed ]]; then
    proxywrap status
    ifconfig | grep -F 'fdfe:dcba:9876::1'
  fi
fi

if [[ "$mode" == deployed ]]; then
  proxywrap status
  curl --fail --silent --show-error --max-time 5 http://127.0.0.1:9090/version
  echo
  curl --fail --silent --show-error --max-time 10 \
    --output /dev/null --write-out 'Dashboard HTTP status: %{http_code}\n' \
    http://127.0.0.1:9090/ui/
fi
echo "Read-only checks completed. Inspect routes and independently verify traffic evidence."
echo "These checks do not prove proxy egress, DNS interception, or absence of leaks."
