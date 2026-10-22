#!/usr/bin/env bash
# Install from an extracted ProxyWrap source release. Homebrew always runs as its user.

pw_die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
pw_info() { printf '%s\n' "$*"; }
pw_root() {
    if [[ "$(id -u)" -eq 0 ]]; then "$@"; else sudo -- "$@"; fi
}
pw_help() {
    cat <<'HELP'
Usage: ./install.sh [options]

Install Mihomo and ProxyWrap from this release directory.
  --url-file FILE   Read the complete subscription URL from a private file
  --no-start        Configure and enable boot startup without starting now
  --start           Start now, including over SSH (may interrupt the connection)
  --force           Back up and redeploy; preserve local overrides and credentials
  --version VERSION Select an official Mihomo release on Linux (default v1.19.32)
  --help            Show this help

Subscription input is hidden. Existing subscription URLs are retained by default.
Local installs start by default. SSH installs do not start by default, but enable
startup at the next boot. Run this script without sudo on macOS.
HELP
}
pw_parse() {
    PW_URL_FILE='' PW_START=auto PW_FORCE=0 PW_VERSION=v1.19.32 PW_VERSION_SET=0
    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --help|-h) pw_help; return 10 ;;
            --url-file)
                [[ "$#" -ge 2 && -n "$2" ]] || pw_die '--url-file requires a file.'
                PW_URL_FILE="$2"; shift ;;
            --version)
                [[ "$#" -ge 2 && "$2" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
                    pw_die '--version requires a stable version such as v1.19.32.'
                PW_VERSION="v${2#v}"; PW_VERSION_SET=1; shift ;;
            --start)
                [[ "$PW_START" != no ]] || pw_die 'Use only one of --start and --no-start.'
                PW_START=yes ;;
            --no-start)
                [[ "$PW_START" != yes ]] || pw_die 'Use only one of --start and --no-start.'
                PW_START=no ;;
            --force) PW_FORCE=1 ;;
            *) pw_die 'Unknown option. Run ./install.sh --help.' ;;
        esac
        shift
    done
}
pw_arch() {
    case "$1" in
        x86_64|amd64) printf 'amd64-v1\n' ;;
        aarch64|arm64) printf 'arm64\n' ;;
        armv7l|armv7|armhf) printf 'armv7\n' ;;
        *) return 1 ;;
    esac
}
pw_remote() { [[ -n "${SSH_CONNECTION:-}${SSH_CLIENT:-}${SSH_TTY:-}" ]]; }
pw_download() {
    curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
        --connect-timeout 15 --max-time 180 --retry 2 --retry-delay 2 "$1" -o "$2"
}
pw_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}
pw_mihomo_digest() {
    case "$PW_VERSION/$PW_ARCH" in
        v1.19.32/amd64-v1) printf 'cdba6557ad571a543f9cbccb4163fd10f060ec80fa0c67f8326f919203dc7d35\n' ;;
        v1.19.32/arm64) printf '0f089b621ff61084a370ab3209ea022a266fa71bc84cc0b01469a1ca25c3b28e\n' ;;
        v1.19.32/armv7) printf '1198229b160ea9fed182a5f932f0182e1415e859cd8dbd708849f5b047f90b6c\n' ;;
        *)
            pw_download "https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/$PW_VERSION" \
                "$PW_TMP/release.json" || pw_die 'Cannot fetch official release metadata.'
            "$PW_PYTHON" - "$PW_TMP/release.json" "$PW_ASSET" <<'PY'
import json
import re
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    release = json.load(stream)
for asset in release.get("assets", []):
    if asset.get("name") == sys.argv[2]:
        digest = asset.get("digest", "")
        if isinstance(digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            print(digest.split(":", 1)[1])
            sys.exit(0)
sys.exit("No compatible official asset with a SHA256 digest; select another version.")
PY
            ;;
    esac
}
pw_packaged_default() {
    local expected actual
    expected="$(dpkg-query -W -f='${Conffiles}' mihomo 2>/dev/null |
        awk '$1 == "/etc/mihomo/config.yaml" {print $2}')" || return 1
    [[ "$expected" =~ ^[a-f0-9]{32}$ ]] || return 1
    actual="$(pw_root md5sum /etc/mihomo/config.yaml 2>/dev/null | awk '{print $1}')" || return 1
    [[ "$actual" == "$expected" ]]
}
pw_package_binary() {
    dpkg-query -L mihomo 2>/dev/null | awk '/\/bin\/mihomo$/ {print; exit}'
}
pw_install_linux() {
    [[ -r /etc/os-release ]] || pw_die 'Cannot identify this Linux distribution.'
    # os-release is an operating-system-owned shell assignment file.
    # shellcheck source=/dev/null
    . /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in
        *' debian '*|*' ubuntu '*|*' raspbian '*) ;;
        *) pw_die 'Only Debian-family Linux distributions are supported.' ;;
    esac
    if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
        pw_die 'A running systemd installation is required.'
    fi
    PW_ARCH="$(pw_arch "$(dpkg --print-architecture)")" ||
        pw_die 'Unsupported Debian package architecture.'
    if [[ "$PW_ARCH" == armv7 ]]; then
        case "$(uname -m)" in armv7*|armv8*|aarch64) ;;
            *) pw_die 'The official ARMv7 package requires an ARMv7 or newer processor.' ;;
        esac
    fi
    pw_info 'Installing required Debian packages.'
    pw_root apt-get update
    pw_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
        ca-certificates curl python3 python3-venv iproute2 nftables
    PW_PYTHON="$(command -v python3)"
    PW_CONFIG=/etc/mihomo
    PW_PACKAGE_DEFAULT=0
    PW_BINARY="$(command -v mihomo || true)"
    if [[ -n "$PW_BINARY" && "$PW_VERSION_SET" -eq 0 ]]; then
        PW_BINARY="$(pw_package_binary || true)"
        [[ -n "$PW_BINARY" && -x "$PW_BINARY" ]] ||
            pw_die 'An unmanaged Mihomo binary exists. Review it before installing the official package.'
        if pw_packaged_default; then PW_PACKAGE_DEFAULT=1; fi
        pw_info 'Keeping the existing official Mihomo package; subscription updates never upgrade it.'
        return
    fi
    if systemctl is-active --quiet mihomo.service; then
        pw_die 'Stop Mihomo before changing its binary version; then rerun the installer.'
    fi
    PW_ASSET="mihomo-linux-$PW_ARCH-$PW_VERSION.deb"
    local digest
    digest="$(pw_mihomo_digest)" || pw_die 'Official package integrity metadata is unavailable.'
    pw_info "Downloading official Mihomo $PW_VERSION ($PW_ARCH)."
    pw_download "https://github.com/MetaCubeX/mihomo/releases/download/$PW_VERSION/$PW_ASSET" \
        "$PW_TMP/mihomo.deb" || pw_die 'Mihomo download failed; check network access to GitHub.'
    [[ "$(pw_sha256 "$PW_TMP/mihomo.deb")" == "$digest" ]] ||
        pw_die 'Mihomo package checksum did not match; refusing installation.'
    # A temporary mask prevents package scripts from starting TUN before configuration.
    local unit_state
    unit_state="$(systemctl is-enabled mihomo.service 2>/dev/null || true)"
    case "$unit_state" in
        masked|masked-runtime) PW_MASKED=0 ;;
        *) pw_root systemctl mask --runtime mihomo.service; PW_MASKED=1 ;;
    esac
    if [[ ! -e "$PW_CONFIG/config.yaml" ]]; then PW_PACKAGE_DEFAULT=1; fi
    pw_root dpkg -i "$PW_TMP/mihomo.deb"
    if [[ "$PW_MASKED" -eq 1 ]]; then
        pw_root systemctl unmask --runtime mihomo.service
        PW_MASKED=0
    fi
    PW_BINARY="$(pw_package_binary || true)"
    [[ -n "$PW_BINARY" ]] || pw_die 'The official package did not install a Mihomo executable.'
}
pw_find_brew() {
    local directory path_file
    if command -v brew >/dev/null 2>&1; then command -v brew; return; fi
    for path_file in /etc/paths /etc/paths.d/*; do
        [[ -f "$path_file" ]] || continue
        while IFS= read -r directory; do
            if [[ "$directory" = /* && -x "$directory/brew" ]]; then
                printf '%s/brew\n' "$directory"; return
            fi
        done < "$path_file"
    done
    return 1
}
pw_install_macos() {
    [[ "$(id -u)" -ne 0 ]] ||
        pw_die 'Run ./install.sh as your ordinary Homebrew user, without sudo.'
    [[ "$PW_VERSION_SET" -eq 0 ]] || pw_die '--version is supported only on Linux.'
    case "$(uname -m)" in arm64|x86_64) ;; *) pw_die 'Unsupported macOS CPU architecture.' ;; esac
    PW_BREW="$(pw_find_brew || true)"
    if [[ -z "$PW_BREW" ]]; then
        pw_info 'Installing Homebrew using a pinned official bootstrap script.'
        pw_download \
            'https://raw.githubusercontent.com/Homebrew/install/a2fe82ef3a9ec7a5c72a559c09676256d6f354f6/install.sh' \
            "$PW_TMP/homebrew-install.sh" || pw_die 'Cannot download the Homebrew bootstrap.'
        [[ "$(pw_sha256 "$PW_TMP/homebrew-install.sh")" == \
            5f333bbe53bc490e51e7ccb1df8779b3dd6ee73a1a7379efda216edb08ccb148 ]] ||
            pw_die 'Homebrew bootstrap checksum did not match.'
        /bin/bash "$PW_TMP/homebrew-install.sh"
        PW_BREW="$(pw_find_brew || true)"
    fi
    [[ -n "$PW_BREW" ]] || pw_die 'Homebrew was not found in PATH or the system path registry.'
    HOMEBREW_NO_INSTALL_UPGRADE=1 "$PW_BREW" install mihomo python
    local brew_prefix mihomo_prefix python_prefix
    brew_prefix="$("$PW_BREW" --prefix)"
    mihomo_prefix="$("$PW_BREW" --prefix mihomo)"
    python_prefix="$("$PW_BREW" --prefix python)"
    PW_BINARY="$mihomo_prefix/bin/mihomo"
    PW_PYTHON="$python_prefix/libexec/bin/python3"
    [[ -x "$PW_PYTHON" ]] || PW_PYTHON="$python_prefix/bin/python3"
    PW_PACKAGE_DEFAULT=0
    PW_CONFIG="$brew_prefix/etc/mihomo"
    if [[ -f "$mihomo_prefix/.bottle/etc/mihomo/config.yaml" ]] &&
        pw_root cmp -s "$PW_CONFIG/config.yaml" "$mihomo_prefix/.bottle/etc/mihomo/config.yaml"; then
        PW_PACKAGE_DEFAULT=1
    fi
    [[ -x "$PW_PYTHON" && -x "$PW_BINARY" ]] || pw_die 'Homebrew installed paths are incomplete.'
}
pw_cleanup() {
    local code=$?
    if [[ "${PW_MASKED:-0}" -eq 1 ]]; then
        pw_root systemctl unmask --runtime mihomo.service >/dev/null 2>&1 || true
    fi
    if [[ -n "${PW_NEW_RUNTIME:-}" && "${PW_COMMITTED:-0}" -ne 1 ]]; then
        pw_root rm -rf -- "$PW_NEW_RUNTIME"
    fi
    if [[ -n "${PW_TMP:-}" ]]; then rm -rf -- "$PW_TMP"; fi
    if [[ "$code" -ne 0 ]]; then
        printf '%s\n' 'Installation did not complete. Existing configuration was retained or restored.' >&2
        printf '%s\n' 'Installed OS dependencies may remain. Resolve the reported error and rerun.' >&2
    fi
}
pw_install_app() {
    local app_root=/usr/local/lib/proxywrap settings_file=/usr/local/etc/proxywrap.json
    if [[ "$PW_PLATFORM" == darwin ]]; then
        app_root='/Library/Application Support/ProxyWrap'
        settings_file=/Library/Preferences/io.github.proxywrap.json
    fi
    pw_root install -d -m 0755 "$app_root" "$app_root/releases" /usr/local/bin
    pw_root chown root "$app_root" "$app_root/releases"
    [[ ! -e "$app_root/current" || -L "$app_root/current" ]] ||
        pw_die 'The existing application current path is not a managed symlink.'
    if [[ -e /usr/local/bin/proxywrap ]]; then
        pw_root "$PW_PYTHON" -c \
            'import pathlib,sys; p=pathlib.Path(sys.argv[1]); sys.exit(0 if "ProxyWrap managed launcher" in p.read_text() else 1)' \
            /usr/local/bin/proxywrap || pw_die 'Refusing to replace an unrelated proxywrap command.'
    fi
    PW_NEW_RUNTIME="$(pw_root mktemp -d "$app_root/releases/alpha.XXXXXXXX")"
    pw_root chmod 0755 "$PW_NEW_RUNTIME"
    pw_root "$PW_PYTHON" -m venv "$PW_NEW_RUNTIME"
    pw_root "$PW_NEW_RUNTIME/bin/python" -I -m pip install --disable-pip-version-check \
        --no-cache-dir "$PW_SOURCE"
    local -a args
    args=(--platform "$PW_PLATFORM" --config-dir "$PW_CONFIG" --binary "$PW_BINARY"
        --settings-file "$settings_file")
    [[ "$PW_PACKAGE_DEFAULT" -eq 0 ]] || args+=(--package-default-config)
    [[ -z "$PW_URL_FILE" ]] || args+=(--url-file "$PW_URL_FILE")
    [[ "$PW_FORCE" -eq 0 ]] || args+=(--force)
    [[ "$PW_REMOTE" -eq 0 ]] || args+=(--remote)
    if [[ "$PW_START" == yes ]]; then args+=(--start); else args+=(--no-start); fi
    if [[ "$PW_PLATFORM" == darwin ]]; then
        args+=(--brew "$PW_BREW" --brew-uid "$(id -u)" --brew-user "$(id -un)")
    fi
    pw_root "$PW_NEW_RUNTIME/bin/python" -I -m proxywrap.deploy "${args[@]}"
    # Publish only after configuration and native service deployment have succeeded.
    pw_root ln -s "$PW_NEW_RUNTIME" "$app_root/current.next.$$"
    pw_root "$PW_PYTHON" -c 'import os,sys; os.replace(sys.argv[1], sys.argv[2])' \
        "$app_root/current.next.$$" "$app_root/current"
    PW_COMMITTED=1
    {
        printf '%s\n' '#!/bin/sh' '# ProxyWrap managed launcher'
        printf 'exec "%s/current/bin/python" -I -m proxywrap "$@"\n' "$app_root"
    } > "$PW_TMP/proxywrap"
    pw_root install -m 0755 "$PW_TMP/proxywrap" "/usr/local/bin/proxywrap.next.$$"
    pw_root "$PW_PYTHON" -c 'import os,sys; os.replace(sys.argv[1], sys.argv[2])' \
        "/usr/local/bin/proxywrap.next.$$" /usr/local/bin/proxywrap
    PW_COMMITTED=1
    pw_info 'ProxyWrap installed. Use proxywrap status and sudo proxywrap update.'
}
pw_main() {
    set -euo pipefail
    # Disable inherited tracing before handling sensitive input paths.
    set +x
    local parse_result=0
    pw_parse "$@" || parse_result=$?
    [[ "$parse_result" -ne 10 ]] || return 0
    [[ "$parse_result" -eq 0 ]] || return "$parse_result"
    PW_SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
    [[ -f "$PW_SOURCE/pyproject.toml" && -d "$PW_SOURCE/src/proxywrap" ]] ||
        pw_die 'Extract the complete ProxyWrap release archive, then run its install.sh.'
    PW_REMOTE=0
    if pw_remote; then
        PW_REMOTE=1
        pw_info 'Remote session detected. TUN startup can interrupt this connection.'
    fi
    if [[ "$PW_START" == auto ]]; then
        if [[ "$PW_REMOTE" -eq 1 ]]; then PW_START=no; else PW_START=yes; fi
    fi
    if [[ "$PW_START" == yes ]]; then
        pw_info 'TUN will start after validation and may change routes and DNS.'
    else
        pw_info 'TUN will not be started now. Boot startup will still be enabled.'
    fi
    PW_TMP="$(mktemp -d "${TMPDIR:-/tmp}/proxywrap-install.XXXXXXXX")"
    chmod 0700 "$PW_TMP"
    PW_MASKED=0 PW_COMMITTED=0 PW_NEW_RUNTIME=
    trap pw_cleanup EXIT
    case "$(uname -s)" in
        Linux) PW_PLATFORM=linux ;;
        Darwin) PW_PLATFORM=darwin ;;
        *) pw_die 'ProxyWrap Alpha supports Debian-family Linux and macOS.' ;;
    esac
    if [[ "$(id -u)" -ne 0 ]]; then
        command -v sudo >/dev/null 2>&1 || pw_die 'sudo is required for system installation.'
        sudo -v
    fi
    if [[ "$PW_PLATFORM" == linux ]]; then pw_install_linux; else pw_install_macos; fi
    "$PW_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' ||
        pw_die 'Python 3.10 or newer is required.'
    pw_install_app
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then pw_main "$@"; fi
