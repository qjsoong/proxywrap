#!/usr/bin/env bash
# Prepare transfer media on a connected Debian-family machine matching the target.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=install.sh
source ./install.sh
[[ "$(uname -s)" == Linux ]] || pw_die 'Build Linux media on a matching Linux system.'
# shellcheck source=/dev/null
source /etc/os-release
case " ${ID:-} ${ID_LIKE:-} " in
    *' debian '*|*' ubuntu '*) ;;
    *) pw_die 'A Debian-family build system is required.' ;;
esac
command -v python3 >/dev/null || pw_die 'Install python3 and python3-venv on the build machine.'
PW_VERSION=v1.19.32
PW_ARCH="$(pw_arch "$(dpkg --print-architecture)")"
PW_TMP="$(mktemp -d)"
trap 'rm -rf -- "$PW_TMP"' EXIT
payload="$PW_TMP/proxywrap-offline/offline"
mkdir -p "$payload/debs/partial" "$payload/wheels" dist
# Archive committed source only, like build-release.sh. Never copy local secrets.
git archive HEAD | tar -x -C "$PW_TMP/proxywrap-offline"
printf '%s\n' "$ID" "$VERSION_ID" "$(dpkg --print-architecture)" > "$payload/target"
PW_ASSET="mihomo-linux-$PW_ARCH-$PW_VERSION.deb"
pw_download "https://github.com/MetaCubeX/mihomo/releases/download/$PW_VERSION/$PW_ASSET" \
    "$payload/mihomo.deb"
[[ "$(pw_sha256 "$payload/mihomo.deb")" == "$(pw_mihomo_digest)" ]] ||
    pw_die 'Upstream Mihomo checksum mismatch.'
# An empty dpkg status resolves the entire dependency closure, even if installed
# on the builder. APT indexes must already be populated (run apt-get update).
apt-get -o Dir::State::status=/dev/null -o Dir::Cache::archives="$payload/debs" \
    -o Debug::NoLocking=1 --download-only --no-install-recommends -y install \
    ca-certificates python3 python3-venv iproute2 nftables "$payload/mihomo.deb"
rm -rf "$payload/debs/partial" "$payload/debs/lock"
rm -f "$payload"/debs/mihomo*.deb
/usr/bin/python3 -m venv "$PW_TMP/build-env"
"$PW_TMP/build-env/bin/python" -m pip wheel --wheel-dir "$payload/wheels" \
    "$PW_TMP/proxywrap-offline"
cp "$payload/mihomo.deb" "$PW_TMP/core.deb"
mkdir "$payload/licenses"
dpkg-deb --fsys-tarfile "$PW_TMP/core.deb" | tar -x -C "$payload/licenses" \
    --wildcards './usr/share/doc/*/copyright'
cat > "$PW_TMP/proxywrap-offline/INSTALL-OFFLINE.txt" <<'HELP'
Run: bash install.sh --offline-dir ./offline
Use only on the distribution, version and architecture recorded in offline/target.
A running systemd installation is required. No apt, GitHub or pip downloads occur.
Subscription retrieval and upstream rule/geo resources still require connectivity.
The offline bundle contains public software only; provide your own subscription.
Verify the external archive checksum from a trusted source before extraction.
Checksums detect corruption; they are not publisher signatures.
HELP
(cd "$payload" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + | sort > "$PW_TMP/SHA256SUMS")
mv "$PW_TMP/SHA256SUMS" "$payload/SHA256SUMS"
archive="dist/proxywrap-offline-$ID-$VERSION_ID-$(dpkg --print-architecture).tar.gz"
tar -czf "$archive" -C "$PW_TMP" proxywrap-offline
sha256sum "$archive" > "$archive.sha256"
printf 'Created %s from committed HEAD. Test in a disposable guest before publishing.\n' "$archive"
