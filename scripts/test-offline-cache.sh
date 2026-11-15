#!/usr/bin/env bash
# Exercise real sudo/APT ownership and EXIT cleanup in a disposable Linux guest.
set -euo pipefail
[[ "${PROXYWRAP_DISPOSABLE_VM:-}" == 1 && "$(id -u)" -ne 0 &&
    "$(systemd-detect-virt 2>/dev/null || true)" == apple ]] || {
    echo 'Run as an ordinary sudo-capable user in a disposable Tart Linux guest.' >&2
    exit 2
}
[[ $# -eq 1 && -d "$1/debs" ]] || { echo 'Usage: test-offline-cache.sh PAYLOAD' >&2; exit 2; }
source_dir=$(cd "$(dirname "$0")/.." && pwd -P)
payload=$(cd "$1" && pwd -P)
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
mkdir "$scratch/invalid" "$scratch/invalid/debs"
printf 'intentionally invalid package\n' > "$scratch/invalid/debs/invalid.deb"
for mode in success failure; do
    selected=$payload
    [[ "$mode" == success ]] || selected="$scratch/invalid"
    status=0
    bash -c '
        set -euo pipefail
        source "$1/install.sh"
        PW_OFFLINE_DIR=$2 PW_TMP=$3
        mkdir -m 0700 "$PW_TMP"
        trap pw_cleanup EXIT
        pw_install_offline_deps
    ' test "$source_dir" "$selected" "$scratch/$mode" || status=$?
    if [[ "$mode" == success ]]; then
        [[ "$status" -eq 0 ]] || { echo 'Successful APT installation returned failure.' >&2; exit 1; }
    else
        [[ "$status" -ne 0 ]] || { echo 'Invalid package unexpectedly succeeded.' >&2; exit 1; }
    fi
    [[ ! -e "$scratch/$mode" ]] || { echo "APT cache survived $mode cleanup." >&2; exit 1; }
    echo "Offline APT cache cleanup after $mode: passed"
done
