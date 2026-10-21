#!/usr/bin/env bash
# Build a source release containing everything needed by install.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist
archive="dist/proxywrap-0.1.0a1.tar.gz"
git archive --format=tar --prefix=proxywrap-0.1.0a1/ HEAD | gzip -n > "$archive"
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$archive" > "$archive.sha256"
else
    shasum -a 256 "$archive" > "$archive.sha256"
fi
printf 'Created %s (committed source only).\n' "$archive"
