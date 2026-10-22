# ProxyWrap

A lightweight, cross-platform wrapper for proxy cores.

ProxyWrap Alpha installs and manages [Mihomo](https://github.com/MetaCubeX/mihomo)
on Debian-family Linux and macOS. It combines complete subscription downloads,
editable local overrides, authenticated LAN access, IPv4/IPv6 TUN configuration,
native system services, and a small `proxywrap` CLI. Mihomo handles proxy protocols
and traffic; [MetaCubeXD](https://github.com/MetaCubeX/metacubexd) provides the web
dashboard independently of the CLI.

See [validation](docs/validation.md) for the environments and behaviors actually
tested. Read [security](docs/security.md) before enabling TUN on a remote machine.

## Requirements

| Platform | Requirements |
| --- | --- |
| Debian, Ubuntu, Raspberry Pi OS, compatible Debian derivatives | systemd, Python 3.10+, AMD64 or ARM64; ARMv7 when the selected upstream release provides a compatible `.deb` |
| macOS | Apple Silicon or Intel where Homebrew can install Mihomo and Python; administrator access for the system daemon |

Installation needs network access to the OS package repositories, GitHub, Python
package index, the subscription server, and Homebrew on macOS. Homebrew support
and available bottles vary by macOS version and CPU; consult its
[installation requirements](https://docs.brew.sh/Installation).

## Install

Extract a complete ProxyWrap source release, enter its directory, and run:

```sh
./install.sh
```

Run this as your ordinary user on macOS. The installer invokes Homebrew as that
user and requests `sudo` only for privileged deployment. On Linux it requests
`sudo` when needed and also supports execution from a root shell.

Enter the **complete Clash/Mihomo subscription URL** when prompted; input is
hidden. For unattended installation, place the URL in a private file using a
trusted editor, restrict it with `chmod 600`, and use:

```sh
./install.sh --url-file /path/to/subscription.url --no-start
```

Do not paste subscription URLs into shell commands. The file path is passed as
an argument; its contents are read privately.

| Option | Behavior |
| --- | --- |
| `--no-start` | Configure and enable boot startup without starting a stopped service now |
| `--start` | Explicitly start after validation, including in an SSH session |
| `--force` | Back up and redeploy; retain local overrides and existing credentials |
| `--url-file FILE` | Read the complete URL from a file; replacing a saved URL requires `--force` |
| `--version VERSION` | Select an official Mihomo release on Linux |
| `--help` | Show installer help |

Local installation starts TUN by default. SSH installation defaults to
`--no-start`, because route changes can interrupt the connection. **Boot startup
is enabled in both cases: the next reboot can activate TUN.** Keep a console or
other recovery path available before starting. `--no-start` does not stop an
already running service; a forced redeployment can apply changes to that service.

Fresh Linux installs use the official Mihomo `v1.19.32` `.deb`, checked against a
SHA256 digest. Other selected releases require an upstream asset digest. An
existing official package binary is retained unless `--version` is supplied;
an unmanaged executable found in `PATH` is rejected. Stop the service before
changing versions. macOS installs Mihomo through Homebrew without implicitly
upgrading an existing core and detects its actual prefix. Python dependencies
live in a dedicated application virtual environment on both platforms.

Repeating installation preserves local configuration, credentials, and a running
deployment. It does not implicitly refresh an existing subscription. Use the
update command when configuration should change.

Before adopting an existing unmanaged Mihomo deployment, stop its service
explicitly. Loaded Homebrew Mihomo jobs must also be stopped before installation.
`--force` does not authorize stopping those services. A managed Linux service
whose actual running arguments differ from the recorded paths must be stopped
before redeployment so the new definition can take effect consistently.

## Manage the service

```sh
proxywrap status
proxywrap doctor
sudo proxywrap start
sudo proxywrap stop
sudo proxywrap restart
sudo proxywrap update
```

Status and basic diagnostics work without elevation. Mutating commands request
administrator privileges if invoked without `sudo`. `sudo proxywrap doctor` also
checks private configuration file permissions. A stopped service returns exit
status `3` from `status` and `doctor`; operation failures return `1`.

`update` downloads the complete subscription, merges local settings, validates
with Mihomo, and replaces files atomically under an exclusive lock. An unchanged
effective configuration does not restart the core. A running service is restarted
only after validation; a stopped service stays stopped. Failed activation attempts
to restore the previous files and service. Subscription updates do not upgrade
the binary, and no recurring subscription update is scheduled.

## Configuration

Linux stores configuration in `/etc/mihomo`. macOS uses the detected
`$(brew --prefix)/etc/mihomo` directory. These files contain credentials and are
root-owned, mode `0600`, inside a private directory.

| File | Purpose |
| --- | --- |
| `subscription.url` | Complete subscription URL, potentially including a private token |
| `subscription.yaml` | Original complete configuration returned by the provider |
| `override.yaml` | User-maintained common settings and persistent LAN credentials |
| `override-platform.yaml` | User-maintained platform settings, including TUN |
| `config.yaml` | Generated effective configuration; do not edit directly |

Merge priority is subscription, common override, then platform override. Mappings
merge recursively, sequences and scalars are replaced, and untouched fields,
including unknown upstream additions, survive. The provider's DNS configuration
is preserved unless you explicitly override it. An incompatible structure or an
explicitly disabled DNS service with active DNS hijacking is reported for review.

Edit local overrides with an administrator-capable editor, then run
`sudo proxywrap update`. Keep authentication and allowed-client settings in the
common override so the CLI can maintain them. A conflicting higher-priority
platform override is diagnosed rather than silently ignoring a CLI change.

Defaults include mixed HTTP/SOCKS port `7890`, rule mode, IPv6, authenticated LAN
access, loopback controller `127.0.0.1:9090`, and persistent node selections.
Additional subscription listeners and tunnels are disabled by the local defaults;
Additional listeners and tunnels must remain disabled in Alpha.

Both platforms use the `mips` TUN stack, automatic routes, interface detection,
`strict-route: false`, `fdfe:dcba:9876::1/126`, and ordinary UDP/TCP DNS hijacking.
Linux additionally uses the `mihomo` device and `auto-redirect`. macOS lets the
system allocate an `utun` device and does not use `auto-redirect`. Both services
set `SKIP_SYSTEM_IPV6_CHECK=1` to permit IPv6 TUN initialization before external
IPv6 becomes available. See [Mihomo's TUN documentation](https://wiki.metacubex.one/en/config/inbound/tun/)
for platform behavior, including macOS local-network DNS limitations.

Linux waits for an IPv4 or IPv6 main-table default route before launching the
core. It uses `network-online.target` plus a native pre-start check and retries
while the route is absent; offline boot therefore does not initialize TUN
prematurely. IPv4-only connectivity still permits startup and IPv6 TUN creation.
This check establishes a route's presence, not internet connectivity. A full
network-manager daemon restart can remove foreign policy rules; run
`sudo proxywrap restart` if that happens after network recovery.

On macOS, review the active system resolver before relying on transparent DNS.
Mihomo cannot automatically intercept DNS requests addressed to a LAN resolver.
In nested proxy or NAT environments, that resolver can return another proxy's
fake IPs, which the guest core cannot resolve from its own cache. A successful
literal-IP request does not establish working hostname access. Inspect
`scutil --dns` and use macOS network settings or `networksetup` to explicitly
choose a suitable resolver outside the local network if required. The installer
does not silently rewrite system DNS or replace the provider's DNS configuration.

Installation metadata lives in `/usr/local/etc/proxywrap.json` on Linux and
`/Library/Preferences/io.github.proxywrap.json` on macOS. It is root-owned and
readable for status queries; never store tokens or passwords there. To change the
subscription User-Agent, add a JSON `"user_agent": "ClashMeta"` setting with a
privileged editor, preserving valid JSON. The default is `ClashMeta`. Installation
paths are recorded in this file and should not be changed as a migration method.

## LAN clients and credentials

The installer generates a persistent random password for user `proxywrap`.
Loopback clients are exempt from proxy authentication; other allowed clients
must authenticate.

```sh
sudo proxywrap auth show
sudo proxywrap auth reset
sudo proxywrap auth reset --generate
sudo proxywrap auth reset --password-file /path/to/private-password
sudo proxywrap lan 192.168.1.0/24 fd00:1234::/64
```

`auth show` intentionally prints credentials: use it only in a private terminal.
Interactive password reset is hidden and accepts at least 16 ASCII letters,
digits, underscores, or hyphens. Generated passwords contain 256 bits of
randomness. Credential and LAN changes use the same validated update transaction
and therefore require access to the subscription server.

LAN clients use the server's reachable address on port `7890` with the displayed
credentials. Allowed clients default to private IPv4 networks, IPv6 ULA/link-local
networks, and loopback. `lan` replaces that list and retains loopback. Add your
actual global IPv6 client prefix explicitly if needed; avoid granting arbitrary
internet clients access. Authentication does not encrypt the client-to-proxy
connection. Use a trusted LAN or an encrypted transport such as SSH.

## Web dashboard

While Mihomo is running, open <http://127.0.0.1:9090/ui>. Mihomo downloads the
configured MetaCubeXD assets from the official project. The dashboard supports
node selection, latency tests, traffic, and connection inspection; closing it
does not stop the service. Node selections are persisted by Mihomo.

For a remote server, forward only to the loopback API:

```sh
ssh -N -L 127.0.0.1:9090:127.0.0.1:9090 user@server
```

Then open the same local URL. The API is loopback-only and has no default secret,
so local applications and users can administer the core. See the
[security guide](docs/security.md) for this trust boundary and optional API auth.

## Files and native services

| Component | Linux | macOS |
| --- | --- | --- |
| Manager | `/usr/local/lib/proxywrap/current` | `/Library/Application Support/ProxyWrap/current` |
| CLI launcher | `/usr/local/bin/proxywrap` | `/usr/local/bin/proxywrap` |
| Service | `mihomo.service` | `system/io.github.proxywrap.mihomo` |
| Service customization | `/etc/systemd/system/mihomo.service.d/10-proxywrap.conf` | `/Library/LaunchDaemons/io.github.proxywrap.mihomo.plist` |

Linux reuses the package service with a drop-in; a fallback unit is created only
when no service exists. macOS uses a root LaunchDaemon and disables the known
Homebrew Mihomo jobs for the installing user and system to avoid duplicate cores.
Do not run `brew services start mihomo` alongside ProxyWrap.

The managed `10-proxywrap.conf` and LaunchDaemon plist are regenerated during
installation. Keep additional Linux service customizations in a separate
`20-local.conf` drop-in, preserving the required environment and configuration
path. Do not edit the package-owned original unit.

Service startup checks confirm that the native process remains running. They do
not establish correct routes, DNS behavior, proxy egress, or absence of leaks.
ProxyWrap does not provide a global kill switch. See [recovery](docs/recovery.md)
and the separate [network validation procedure](docs/validation.md).

## Development and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture, checks, and release bundle
creation. ProxyWrap is licensed under the [Apache License 2.0](LICENSE).
Upstream components retain their own licenses.
