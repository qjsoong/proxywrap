# Diagnostics and recovery

Keep a console, VM guest agent, or another management path available before
starting TUN remotely. A service can be running while its routes, DNS, or selected
upstream are unusable. ProxyWrap does not promise a global kill switch or repair
unrelated network configuration.

## Inspect and stop

```sh
proxywrap status
sudo proxywrap doctor
sudo proxywrap stop
```

Stopping asks the native service manager to terminate Mihomo gracefully so the
core can remove its network state. It retains boot startup. If connectivity is
lost, use the console rather than repeatedly restarting from a failing SSH
connection. Avoid broad commands that flush system routes, DNS, or firewall rules.

On Linux, the service waits for an IPv4 or IPv6 main-table default route before
starting Mihomo. The pre-start check retries every three seconds without a
systemd start-rate cutoff. With no route, `proxywrap status` reports no running
core; inspect `systemctl status mihomo.service` and `ip route show table main`
plus `ip -6 route show table main` to distinguish this from a configuration error.
Starting TUN is deferred until a default route is present. No external website
is used as a health gate.

Ordinary link disconnection and reconnection should be checked separately from
restarting the network-manager daemon. A full daemon restart or reconfiguration
can flush Mihomo's foreign policy rules while leaving its process alive. After
the network has returned, use `sudo proxywrap restart` to recreate those rules.
ProxyWrap does not alter unrelated network-manager settings to prevent cleanup.

To keep the core off across reboot on Linux:

```sh
sudo systemctl disable --now mihomo.service
```

On macOS:

```sh
sudo proxywrap stop
sudo launchctl disable system/io.github.proxywrap.mihomo
```

If the manager itself is unavailable on macOS, a loaded daemon can be removed
with `sudo launchctl bootout system/io.github.proxywrap.mihomo`. A nonzero result
can mean the job was already absent. Disabling boot startup must be done before
using a reboot as a recovery step; the installer and `--no-start` enable it.

When ready, re-enable Linux boot startup with
`sudo systemctl enable mihomo.service`, then use `sudo proxywrap start`.
On macOS `sudo proxywrap start` re-enables and loads the system daemon.

## Find configuration and logs

| Location | Linux | macOS |
| --- | --- | --- |
| Configuration | `/etc/mihomo` | `$(brew --prefix)/etc/mihomo` |
| Installation metadata | `/usr/local/etc/proxywrap.json` | `/Library/Preferences/io.github.proxywrap.json` |
| Native status | `systemctl status mihomo.service` | `launchctl print system/io.github.proxywrap.mihomo` |
| Core output | `sudo journalctl -u mihomo.service` | private `mihomo.log` inside the configuration directory |

Inspect logs locally: core diagnostics may include provider information. Do not
paste raw logs, subscriptions, or configuration into public reports. If logs
must be collected, use a private directory and redact them before sharing.

Check the generated configuration path, executable, and native definition when a
service will not start. Linux customizations belong in
`/etc/systemd/system/mihomo.service.d/20-local.conf`; ProxyWrap regenerates its
own `10-proxywrap.conf` during installation. Do not edit the package-owned
original unit. macOS uses
`/Library/LaunchDaemons/io.github.proxywrap.mihomo.plist` and a root daemon. Do not
start Homebrew's service alongside it.

## Update failures

Failed downloads, malformed subscriptions, incompatible YAML, or a failed Mihomo
configuration check leave the working files unchanged. Check network access,
the saved URL privately, provider format, and local overrides before retrying.
Do not put the URL on a command line for debugging.

If DNS is explicitly disabled in the subscription while TUN DNS hijacking remains
enabled, ProxyWrap requests a local decision instead of substituting a DNS
template. Resolve that conflict in the overrides with your provider's intended
policy in mind.

An effective configuration change creates a private transaction snapshot before
replacement. On activation failure, ProxyWrap attempts to restore the old
configuration, raw subscription, and any CLI-staged common override, then restart
the previously running service. A failure message describes whether that recovery
completed.

| Private directory | Contents and purpose |
| --- | --- |
| `.previous` | Snapshot before the most recent successful changed subscription update |
| `.transaction` | Pending update or failed rollback; preserved for recovery |
| `.deploy-backups/<UTC timestamp>` | Configuration and metadata snapshot before a completed deployment |
| `.deploy-transaction` | Interrupted installation or failed deployment recovery |

Deployment snapshots also save the native service definitions that ProxyWrap
can create or replace. Rollback restores changed definitions and reloads systemd
before restoring the previous process state. Unchanged package unit files are
left alone.

These directories live inside the configuration directory and may contain
credentials. `.previous` is replaced by the next successful changed update;
deployment archives are retained. Back them up privately if longer history is
required.

An interrupted update is recovered before the next `update`, `start`, or
`restart`. `stop` remains available without triggering recovery. If recovery
fails, stop the service, inspect the private snapshot and overrides, then retry
`sudo proxywrap update` or `sudo proxywrap start` from a console. Recovery may
restart the service that was running when the interrupted transaction began.
Do not remove the transaction directory to bypass the error unless you have
verified and preserved its contents.

If another operation holds the update lock, let it finish or investigate the
owning process. Removing `.update.lock` does not safely release a held file lock
and can allow concurrent writers.

## Manual restoration

Use manual restoration only after stopping the core and preserving the current
private configuration directory. Inspect the snapshot's `state.json` to identify
which files existed; a first-install snapshot may not contain a prior config.
Restore only reviewed files, keeping root ownership and modes `0600` for files
and `0700` for the directory. Never recursively delete the configuration tree.

`config.yaml` is generated. Restore or fix the corresponding local overrides as
well, otherwise the next update can recreate the unwanted configuration. To
validate and regenerate from the current subscription and overrides, use
`sudo proxywrap update` while the service is stopped, then explicitly start it.
If the provider is unavailable, a reviewed previous generated `config.yaml` can
be restored and checked directly with the installed Mihomo binary before start;
obtain the actual binary and configuration paths from installation metadata.

Do not overwrite `subscription.url` merely to test a URL. Replacing the saved URL
through installation requires `--url-file FILE --force`, which creates a backup
and preserves existing overrides and credentials.

## Installation and runtime failures

Rerun the complete release's `install.sh` after resolving the reported problem.
An existing deployment is preserved on ordinary reinstall. `--force` backs up and
reapplies configuration rather than deleting it. A pending `.deploy-transaction`
is restored before another deployment proceeds. OS packages installed before a
failure may remain; they are not removed automatically.

The manager uses versioned dedicated Python environments with a `current` symlink.
Do not edit its installed modules to fix a configuration issue. Reinstall from a
reviewed source bundle if the launcher or runtime is damaged. Subscription
updates deliberately do not upgrade Mihomo; manage binary changes separately.

A port-conflict diagnostic does not authorize terminating the other program.
Identify the listener, choose a different local port or stop the conflicting
service intentionally, then regenerate configuration. TUN startup and graceful
stop cannot prove that all network state is correct; use the separate
[validation procedure](validation.md) for interfaces, routes, DNS, and IPv4/IPv6
egress evidence.

## macOS hostname failures

If a request using a known literal IP works while the corresponding hostname
fails, inspect DNS separately from routes and proxy node reachability:

```sh
scutil --dns
networksetup -listallnetworkservices
```

Use `networksetup -getdnsservers` with the active service name to inspect explicit
settings; DHCP-provided resolvers appear in `scutil --dns`. Record the original
configuration before making changes. Mihomo cannot automatically hijack DNS
addressed to local-network resolvers. With a nested proxy or NAT resolver, answers
may even contain foreign `198.18.0.0/15` fake IPs that are absent from the guest
core's cache.

If required, explicitly select a suitable non-LAN resolver through macOS Network
settings or `networksetup -setdnsservers`, then verify both UDP and TCP DNS,
hostname requests, and core connection evidence. Restore DHCP-based DNS using
the tool's `Empty` setting only if that matches the original configuration.
ProxyWrap leaves these system resolver choices to the administrator and preserves
the provider's subscription DNS. See the recorded validation report for the
specific guest behavior that was exercised.
