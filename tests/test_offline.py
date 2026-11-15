"""Offline media validation runs without privileges or host network changes."""

import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "debs").mkdir()
        (self.root / "wheels").mkdir()
        (self.root / "target").write_text("debian\n12\namd64\n")
        (self.root / "mihomo.deb").write_bytes(b"test fixture")
        entries = []
        for name in ("mihomo.deb", "target"):
            digest = hashlib.sha256((self.root / name).read_bytes()).hexdigest()
            entries.append(f"{digest}  ./{name}\n")
        (self.root / "SHA256SUMS").write_text("".join(sorted(entries)))

    def check(self, *, platform="linux", version="12", selected="0"):
        # macOS provides shasum instead of sha256sum; keep the production API.
        script = r"""
source ./install.sh
set -euo pipefail
if ! command -v sha256sum >/dev/null; then
    sha256sum() { local -a args=(); for arg in "$@"; do
        [[ "$arg" == --strict ]] || args+=("$arg"); done
        shasum -a 256 "${args[@]}"; }
fi
dpkg() { printf 'amd64\n'; }
PW_OFFLINE_DIR="$1" PW_PLATFORM="$2" VERSION_ID="$3" PW_VERSION_SET="$4"
PW_TMP="$1"/scratch ID=debian
PW_TMP="$(mktemp -d)"
trap 'rm -rf -- "$PW_TMP"' EXIT
pw_offline_check
"""
        return subprocess.run(
            ["bash", "-c", script, "test", str(self.root), platform, version, selected],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_valid_media(self):
        self.assertEqual(self.check().returncode, 0)

    def test_corrupted_core(self):
        (self.root / "mihomo.deb").write_bytes(b"changed")
        self.assertNotEqual(self.check().returncode, 0)

    def test_unlisted_package(self):
        (self.root / "debs" / "extra.deb").write_bytes(b"extra")
        self.assertNotEqual(self.check().returncode, 0)

    def test_missing_payload(self):
        shutil.rmtree(self.root / "wheels")
        self.assertNotEqual(self.check().returncode, 0)

    def test_wrong_target(self):
        self.assertNotEqual(self.check(version="13").returncode, 0)

    def test_macos_rejected(self):
        self.assertNotEqual(self.check(platform="darwin").returncode, 0)

    def test_version_override_rejected(self):
        self.assertNotEqual(self.check(selected="1").returncode, 0)
