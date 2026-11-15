# Linux offline installation media

Use this workflow when the target cannot reach GitHub or Python/package indexes.
Prepare the archive on a connected Debian-family machine with the **same OS ID,
release and package architecture** as the target. This initial implementation does
not support macOS offline deployment. The target must already run systemd and
have apt, dpkg, Bash and coreutils. Python and its venv dependencies are bundled
as OS packages; they need not be installed on the target beforehand.

## Prepare and transfer

Commit the intended source changes first. On the matching build machine, install
Git, curl, Python 3.10+ and python3-venv, populate APT indexes with `apt-get update`,
then run as an ordinary user:

```sh
bash scripts/build-offline.sh
```

The builder downloads the pinned, digest-verified official Mihomo package, the
complete APT dependency closure using an empty package status database, and
ProxyWrap/PyYAML wheels using the system Python. Use matching, trusted package
repositories. Allow enough space for OS libraries in addition to application
files. The archive contains committed source only, package copyright notices,
and dependency wheels. Bundled components retain their upstream licenses.

The `Linux offline bundle` GitHub Actions workflow also builds Ubuntu 24.04 AMD64
media as a downloadable artifact, on relevant PRs or manual dispatch. A successful
build is followed by installation of OS packages, Mihomo and wheels in a fresh
Ubuntu container with `--network none` and no preinstalled Python. This checks
artifact completeness, not systemd deployment or TUN behavior in a guest.

Copy the archive and its `.sha256` file from `dist/` to a USB drive. On the target,
verify the archive checksum, extract it, enter `proxywrap-offline`, then run:

```sh
bash install.sh --offline-dir ./offline
```

The installer checks target identity and payload checksums before installing OS
packages. APT uses `--no-download`; pip uses `--no-index` and installs the bundled
wheel without building source. Missing dependencies cause an error instead of
falling back to an online source. Linux uses `/usr/bin/python3` for this workflow.
The `--version` option cannot override the bundled core. Existing managed cores
are retained under the same rules as online installation.

## Limits and validation

This is offline **software installation**, not offline proxy configuration or
operation. The subscription endpoint must still be reachable during deployment.
Provider rule sets, geodata or other external resources may require additional
network access when Mihomo validates or starts. Local subscription import and
bundling those provider-specific resources are not implemented. Supply your own
subscription privately; no credentials belong in a public release archive.

The web dashboard is not included in the payload either. On a fresh offline
installation, Mihomo's attempted dashboard download fails and `/ui/` returns
HTTP 404. This does not prevent the core, CLI, or controller API from working.
Dashboard availability requires separately prepared assets or later connectivity.

Checksums detect corruption, not publisher identity. Obtain the archive and
expected checksum through a trusted channel. Packages can update system libraries;
test each release in a disposable matching guest before distributing it.

Before publishing, test with GitHub, package repositories and Python indexes
unreachable, but the subscription endpoint reachable. Verify installation on a
fresh system without Python, missing/corrupted media rejection, service startup,
IPv4/IPv6 routing, DNS and proxy egress following [validation.md](validation.md).
Automated media checks do not replace that guest test.
