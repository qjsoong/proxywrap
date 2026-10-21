# Security and trust boundaries

ProxyWrap configures a privileged proxy core that can change routes and handle
machine traffic. Install only a source bundle, Mihomo build, dashboard, and
subscription provider you trust. Validate deployment in a recoverable environment
before relying on it remotely.

## Credentials and private configuration

The configuration directory is root-owned and mode `0700`. Subscription URLs,
raw subscriptions, overrides, and generated configuration use mode `0600`.
Backups contain the same sensitive data and require the same protection. The
installer preserves an existing generated LAN password across reinstalls.

Interactive subscription and password input is hidden. Unattended operations
read private files. Do not place secrets in shell commands, environment variables,
issue reports, screenshots, or terminal recordings. `sudo proxywrap auth show`
explicitly displays effective credentials and should be run only in a private
terminal. Delete unneeded input files securely according to your storage policy.

ProxyWrap sanitizes download, validation, and service-manager errors. Mihomo's
own logs and dashboard can still contain provider addresses, traffic metadata,
or other sensitive details. Inspect them privately and review before sharing.
On macOS core output goes to private `mihomo.log` in the configuration directory;
on Linux it follows native service logging. Control access to journal-reading
groups and manage log retention according to your environment.

Installation metadata is root-owned and publicly readable so status queries do
not require elevation. It must not contain credentials, private URLs, or tokens.
The optional subscription User-Agent belongs there; the subscription URL does not.

## LAN proxy

The mixed HTTP/SOCKS listener uses port `7890` by default. The generated
`proxywrap` password contains 256 bits of randomness using URL-safe characters.
Only loopback clients may bypass authentication. Allowed non-loopback clients
must supply credentials through Mihomo's native authentication mechanism.

Default allowed networks are private IPv4 ranges, IPv6 ULA/link-local ranges, and
loopback. A global IPv6 address assigned to a LAN client is not covered merely
because the client is on the same network. Add its actual client prefix with
`proxywrap lan`, preferably restricting the list to the networks you use.
Authentication and address restrictions are separate controls.

HTTP/SOCKS proxy authentication does **not** encrypt the client-to-proxy link.
On an untrusted network, credentials, destination metadata, and unencrypted
application traffic may be exposed. Use a trusted LAN or an encrypted connection
such as SSH. Do not forward the proxy port to the public internet. ProxyWrap does
not configure unrelated host firewall policies or router port forwards.

See [Mihomo's authentication and LAN settings](https://wiki.metacubex.one/en/config/general/)
for the underlying core behavior.

## Management API and dashboard

The REST controller binds to `127.0.0.1:9090`. LAN proxy access does not expose it.
Extra controller listeners and additional proxy listeners/tunnels are rejected
by ProxyWrap's safety checks. MetaCubeXD is served through this local controller
and operates independently of the CLI process.

There is no generated API secret by default. Any application or user able to
access the host's loopback interface can control the core, inspect connections,
and change node selections. Proxy authentication does not protect the REST API.
Treat local users and software as part of the trusted management boundary. If
needed, configure Mihomo's `secret` in `override.yaml`, apply an update, and enter
that secret in the dashboard. Keep the API on loopback even when adding a secret.

Remote management should use an authenticated SSH connection with a forward
bound to the client's loopback address:

```sh
ssh -N -L 127.0.0.1:9090:127.0.0.1:9090 user@server
```

Dashboard JavaScript is fetched from the official MetaCubeXD distribution URL
configured in the override. It is a separate upstream supply-chain dependency;
administrators can replace that URL or maintain reviewed local assets.

## Privileges and installed software

Status and basic diagnostics are unprivileged. Configuration writes and service
operations explicitly request elevation. The system core runs as root to create
TUN interfaces and routes. Linux limits capabilities to network administration,
raw sockets, and privileged port binding; verify native service restrictions
on each deployment environment and consult the recorded validation results.

Homebrew installation and package commands run as the ordinary Homebrew user.
The macOS system daemon executes the installed Homebrew Mihomo binary as root.
The manager also relies on the Homebrew Python interpreter. Anyone who can
replace these executables or write to their Homebrew installation can
therefore influence privileged execution. Use a Homebrew prefix controlled only
by trusted administrators. This follows the trust assumption described in
[Homebrew's installation documentation](https://docs.brew.sh/Installation).

Do not start a competing Homebrew Mihomo service or another core on the same
ports. ProxyWrap disables the known Homebrew jobs for the installing user and
system after requiring loaded jobs to be stopped explicitly. Running unmanaged
services must also be stopped before adoption; `--force` does not stop them.
ProxyWrap diagnoses occupied ports and never terminates an unrelated listener.

## Routing and availability

ProxyWrap is not a global kill switch. With `strict-route: false`, route
exclusions, network changes, service shutdown, and failures can allow traffic to
leave outside the proxy. A subscription may intentionally route traffic DIRECT.
Even strict routing is not a substitute for testing the actual network policy.

Top-level IPv6, DNS AAAA behavior, TUN IPv6 addresses, system routes, and upstream
node support are distinct. Enabling one does not prove the others. Both services
set `SKIP_SYSTEM_IPV6_CHECK=1` for early IPv6 initialization; that does not ensure
later route recovery. Linux additionally defers core startup until a non-core
main-table default route exists and retries while offline; this is a local route
check rather than proof of working internet access. A full network-manager daemon
restart can remove foreign policy rules and require an explicit
`sudo proxywrap restart` after connectivity returns. macOS cannot automatically
hijack ordinary DNS requests addressed to local-network resolvers. These details follow
[Mihomo's TUN behavior](https://wiki.metacubex.one/en/config/inbound/tun/).

Inspect the active macOS system resolver explicitly with `scutil --dns` and
`networksetup -getdnsservers` for the selected network service. In nested proxy
or NAT environments, a local resolver may return fake IPs belonging to another
core; those addresses cannot be resolved through the guest core's own fake-IP
cache. This can break hostname traffic even when literal-IP traffic works.
Choose an appropriate resolver outside the local network through macOS tools
when required, recording the previous settings so they can be restored.
ProxyWrap does not silently change system DNS. Preserving provider DNS in the
subscription alone does not establish that system DNS requests reach that core.

Check [validation](validation.md) for tested scenarios. Keep console access and
read the [recovery procedure](recovery.md) before applying network changes over
SSH. `--no-start` still enables activation at the next boot.
