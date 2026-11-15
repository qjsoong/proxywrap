#!/usr/bin/env bash
# CI smoke check only: never deploy services or TUN on the host.
set -euo pipefail
[[ -f /.dockerenv && "${PW_OFFLINE_CONTAINER_TEST:-}" == 1 ]] || {
    echo 'Run only inside a disposable offline-test Docker container.' >&2
    exit 1
}
cd /media/proxywrap-offline
[[ "$(id -u)" -ne 0 ]] || { echo 'Run as an ordinary sudo-capable user.' >&2; exit 1; }
sudo -n true
# Verify the artifact, including the real upstream license layout that once
# broke packaging after every unit test had passed.
(cd offline && sha256sum --check --strict SHA256SUMS)
test -s offline/licenses/usr/share/licenses/mihomo/LICENSE
if command -v python3; then
    echo "The test image must not have Python preinstalled." >&2
    exit 1
fi
# Prevent any package-maintainer scripts from starting services in the container.
printf '#!/bin/sh\nexit 101\n' | sudo tee /usr/sbin/policy-rc.d >/dev/null
sudo chmod 0755 /usr/sbin/policy-rc.d
# There is no external network, no prepopulated APT cache and no Python runtime.
# Exercise the same dependency-install function as the real installer.
# shellcheck source=install.sh
source ./install.sh
PW_OFFLINE_DIR=/media/proxywrap-offline/offline
PW_TMP="$(mktemp -d)"
trap 'rm -rf -- "$PW_TMP"' EXIT
pw_install_offline_deps
sudo dpkg -i offline/mihomo.deb
/usr/bin/python3 -m venv /tmp/runtime
/tmp/runtime/bin/python -I -m pip install --disable-pip-version-check \
    --no-index --find-links offline/wheels offline/wheels/proxywrap-*.whl
/tmp/runtime/bin/python -I -c 'import proxywrap, yaml'
/tmp/runtime/bin/python -I -m proxywrap --help
/usr/bin/mihomo -v
