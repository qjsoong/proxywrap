# Validation

Run the automated checks described in [CONTRIBUTING.md](../CONTRIBUTING.md).
They exercise configuration transactions with temporary directories and simulated
service/core boundaries. They do not validate real routing or service privileges.

## Disposable Tart guests

Create a copy of a known clean, stopped guest before deployment. Keep the source
guest untouched so failed route, DNS, service and reboot experiments can be
discarded. Set `TART_NO_AUTO_PRUNE=1` while cloning to prevent unrelated VM pruning.
Use a named clone and positively verify the guest OS and virtualization identity
before any privileged command. Never run the installer or network mutations on
the development host as part of validation.

The following commands use names selected by the tester:

```sh
TART_NO_AUTO_PRUNE=1 tart clone "$BASELINE_VM" "$TEST_VM"
tart run --no-graphics --no-clipboard "$TEST_VM"
tart exec "$TEST_VM" uname -a
```

Transfer a source bundle to the guest using SSH or a read-only Tart directory
share, then run the installer **inside the guest**. Use a synthetic full Mihomo
subscription served by a guest-local HTTP server for reproducible installation
and failure tests. Keep any private provider subscription outside the repository
and pass it using `--url-file`. Start with `--no-start` to inspect the generated
configuration and native service, then explicitly start inside the disposable
guest. Repeat with a clean clone and the default local installation path.

The included fixture provides a DNS-enabled full subscription, a selector group,
a reject rule and a controlled HTTP upstream. Run it inside the guest:

```sh
PROXYWRAP_DISPOSABLE_VM=1 python3 scripts/vm-fixture.py /tmp/proxywrap-fixture
./install.sh --url-file /tmp/proxywrap-fixture/subscription.url --no-start
sudo proxywrap start
curl --noproxy '*' --max-time 10 http://192.0.2.123/proxywrap-probe
curl --noproxy '*' -g --max-time 10 'http://[2001:db8::123]/proxywrap-probe'
```

Keep the fixture process running in a separate session. Both requests must return
`PROXYWRAP-CONTROLLED-UPSTREAM`. Documentation-only destinations cannot directly
reach this server. The marker therefore verifies the configured proxy path, but
does not establish encrypted remote-provider compatibility or global IPv6 egress.
Use `/slow` to retain a connection long enough to inspect the core connection API.
With the fixture and Mihomo running, use `PROXYWRAP_DISPOSABLE_VM=1 python3
scripts/vm-network-check.py` inside the guest to assert both controlled proxy
paths, correlate intercepted inbounds with the connection API, and test UDP/TCP
DNS interception over both address families. It does not change networking.

Collect read-only baseline and deployed evidence inside the guest:

```sh
PROXYWRAP_DISPOSABLE_VM=1 scripts/vm-check.sh --baseline
PROXYWRAP_DISPOSABLE_VM=1 scripts/vm-check.sh --deployed
```

The script refuses to run without explicit acknowledgement and a recognized
Apple Virtualization Linux guest or VirtualMac identity. Its output includes
network addresses; review evidence before sharing it. It does not read live
configuration or credentials. API and dashboard checks assume the default
loopback controller address.

## Acceptance procedure

1. Confirm the baseline has no Mihomo package, service or ProxyWrap configuration.
   Install dependencies and Mihomo through the normal platform installer.
2. Verify the original package-owned systemd unit is retained and a drop-in is
   used; on macOS verify one root LaunchDaemon and no competing Homebrew service.
   Check enablement, root execution and `SKIP_SYSTEM_IPV6_CHECK=1`.
3. Verify the TUN interface, explicit IPv6 address, IPv4 and IPv6 policy routes,
   API loopback binding, dashboard assets, and permission-restricted files.
4. Test the mixed proxy from another guest or the host using explicit proxy
   requests. Unauthenticated non-loopback requests must be rejected; authorized
   requests must work. Obtain the password inside the guest and transmit it via
   protected stdin or a temporary mode-0600 file, never command arguments or logs.
5. Use a controlled upstream proxy with distinguishable egress. Compare direct,
   explicit proxy and TUN traffic. Correlate packet capture or Mihomo connection
   API records with test requests. Test IPv4 and IPv6 separately. A `DIRECT`
   fixture can prove core interception but cannot prove external proxy egress.
6. Verify UDP and TCP DNS against known external destinations separately from
   local-network resolvers. Preserve the subscription DNS configuration.
7. Update an unchanged fixture and compare the core PID. Change a local override
   and confirm one validated activation. Serve invalid YAML or stop the fixture
   server and verify hashes, PID and service availability remain unchanged.
8. Exercise an activation failure in a cloned guest and verify rollback, then
   repeat installation and `--force`; compare credential hashes without printing
   credentials. Retain and inspect backup permissions.
9. Reboot the guest and verify automatic service startup without user login.
   Kill the core process and verify automatic restart.
10. In a recoverable clone, boot without external networking, then reconnect;
    repeat after removing non-loopback IPv6 addresses. Linux should wait for a
    usable default route before starting the core. With an IPv4-only default
    route, confirm TUN IPv6 still initializes. Inspect routes and core traffic
    after network restoration. Repeat a
    disconnect/reconnect cycle. Use Tart's guest agent or console for recovery.

macOS cannot automatically hijack ordinary DNS requests addressed to the local
network. Test and document the active resolver behavior rather than silently
replacing a provider's DNS configuration. See the [Mihomo TUN documentation](https://wiki.metacubex.one/en/config/inbound/tun/).

## Recorded validation

Validation results below are evidence for the listed guest environments only.
They do not establish behavior on every physical network or supported CPU.

| Environment | Baseline | Deployment result |
| --- | --- | --- |
| Debian 13.7, ARM64, kernel 6.12 | Python 3.13; no Mihomo package or service | Official Mihomo 1.19.32 installed and exercised |
| macOS 26.6.2, Apple Silicon | Homebrew present; system Python 3.9.6; Homebrew Python 3.15 | Homebrew Mihomo 1.19.33 installed and exercised |

The Debian guest has passed these real system checks:

- Fresh installation, original package systemd unit retained with a drop-in,
  system startup enabled, and `--no-start` leaves TUN absent until explicit start.
- Root-controlled configuration directory, mode-0600 runtime files, persistent
  credentials, unchanged provider DNS, loopback-only API and dashboard HTTP 200.
- TUN IPv4 and explicit IPv6 addresses, policy routes, controlled proxy marker
  requests over IPv4 and IPv6, live intercepted connection records, and UDP/TCP
  DNS interception over both address families.
- Loopback proxy access without credentials; independent LAN client rejected
  without authentication and accepted with authentication.
- Unchanged update preserves the core PID. Failed download, malformed
  subscription and actual Mihomo rejection preserve the running configuration
  and PID. Concurrent updates are rejected. A real listener conflict during
  activation rolls back to a running previous configuration.
- Local override changes activate successfully. Reinstallation preserves the
  existing running service; force redeployment preserves credentials and creates
  private backups. Credential rotation and allowed LAN CIDR updates work through
  the public CLI. A forced redeployment with an unreachable subscription restores
  configuration, credentials, the manager runtime and the running core PID; its
  failed temporary runtime is removed.
- REST selector changes survive service restart, and the node delay endpoint
  returns a measurement. An abnormal core exit triggers systemd restart.
- An actual reboot starts the service with no logged-in users. A physical guest
  link disconnect/reconnect preserves policy rules and recovers IPv4/IPv6
  controlled traffic and external HTTPS without a core restart.
- A separate offline reboot with no physical-interface addresses leaves the core
  waiting for a usable main-table default route. When networking returns,
  systemd automatically starts the core with complete IPv4/IPv6 rules and TUN
  addresses. All controlled proxy and DNS checks then pass without a manual core
  restart.
- With physical IPv6 disabled and only an IPv4 default route available, the core
  still initializes the explicit TUN IPv6 address and controlled IPv6 proxy path.
  Physical IPv6 and router advertisements were restored afterward.

The final 89-test automated source suite also passed inside Debian using the
installed Python 3.13 virtual environment. These automated results are separate
from the actual system checks above.

The macOS guest has passed these real system checks:

- Installation through the existing Homebrew installation, dynamically detected
  paths, Python 3.15 and an isolated application environment with PyYAML 6.0.3.
- An enabled system LaunchDaemon with root-controlled configuration, mode-0600
  runtime files, loopback API, TUN IPv6 address, and graceful lifecycle commands.
- Controlled IPv4/IPv6 proxy paths, matching TUN connection API records, and
  UDP/TCP DNS interception over both address families to non-LAN destinations.
- Non-loopback LAN requests rejected without authentication and accepted with
  authentication; dashboard HTTP 200; REST selector, delay and connection
  operations; and selector persistence across restart.
- Unchanged updates retain the PID. A real listener conflict after successful
  Mihomo candidate validation triggers rollback to a running old configuration.
  Reinstallation and force redeployment retain the running PID and credentials.
  An installation download failure preserves the previous deployment.
- LaunchDaemon `KeepAlive` restarts the core after an abnormal exit. An actual
  offline reboot initializes TUN IPv6 without an underlay address; restoring the
  guest network recovers controlled traffic without restarting the core.

The full 89-test automated suite also passed inside the macOS guest using the
installed Python 3.15 virtual environment.

The macOS NAT resolver returned synthetic addresses in the same `198.18.0.0/16`
range as the guest fixture. Ordinary hostname HTTPS requests then failed with TUN
active, although a request explicitly resolved to a native destination IPv4
address succeeded. This demonstrates the upstream limitation that macOS cannot
automatically intercept LAN-addressed DNS, combined with overlapping synthetic
address pools. An explicit **guest-only test** DNS setting using a non-LAN
resolver and a guest resolver-cache flush restored normal hostname HTTPS. The
fixture's subscription DNS remained unchanged. The macOS test guest retains that
explicit resolver setting; the installer does not silently change OS resolvers.
Networks with LAN DNS or another proxy's fake-IP pool require equivalent explicit
resolver planning and independent validation.

Both disposable guests use Tart's shared NAT networking. Baseline interfaces have
IPv4, IPv6 ULA addresses and an IPv6 default route. An IPv6 HTTP request from the
Debian guest succeeded before deployment. This does not prove that a guest has
native globally routable IPv6, and does not validate IPv6 proxy interception.
Native global IPv6 egress, real provider compatibility and additional physical
LAN environments require separate evidence. Unexecuted checks must remain
explicitly marked pending or unverified.

The dashboard has been verified through served assets and its REST operations;
interactive browser rendering has not been exercised. Fresh Homebrew bootstrap
on a guest without Homebrew, Intel macOS, AMD64 Linux and ARMv7 Linux have not
been deployed in this validation run. Deterministic fixtures do not establish
compatibility with a particular subscription provider or encrypted remote proxy
server.

Restarting or fully reconfiguring `systemd-networkd` can remove foreign routing
policy rules while Mihomo remains running. In this guest, ordinary carrier
disconnect/reconnect preserved those rules, but `networkctl reconfigure` removed
them. After administrator-driven network-manager reconfiguration, inspect the
rules and run `sudo proxywrap restart` when necessary. The wrapper does not change
unrelated network-manager settings to retain foreign rules.
