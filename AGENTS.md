# ProxyWrap development guide

ProxyWrap is a lightweight wrapper for existing proxy cores. Alpha integrates Mihomo on Debian-family Linux with systemd and macOS with Homebrew and a system LaunchDaemon. Keep common Python logic shared and operating-system commands isolated. Do not build a plugin framework for hypothetical cores.

## Layout

- `install.sh`: Bash bootstrap, dependency installation and platform detection.
- `src/proxywrap/`: CLI, subscription transactions, configuration, native services and deployment.
- `templates/`: public configuration defaults without live credentials.
- `tests/`: unit and simulated integration tests; never modify host networking.
- `scripts/`: repeatable VM checks and release tooling.
- `docs/`: deployment, recovery, security and validation documentation.

## Development

Use Python 3.10 or newer in a dedicated virtual environment. PyYAML is the only runtime dependency. Never install Python packages into a managed system interpreter. Run `python3 -m venv .venv`, `.venv/bin/python -m pip install -e '.[dev]'`, `.venv/bin/python -m unittest discover -s tests -v`, `.venv/bin/ruff check .`, and `bash -n install.sh`. Keep CI synchronized with these commands. Build distributable source bundles with `scripts/build-release.sh`.

## Configuration invariants

Merge complete subscription, common override, then platform override. Recursively merge mappings, replace sequences and scalars, retain unknown fields. Preserve subscription DNS unless the user explicitly overrides it. Generated `config.yaml` is not user-maintained. Always validate candidates with Mihomo before activation. Serialize updates; unchanged effective configuration must not restart the service. Failed downloads and validation must preserve the last working configuration. Failed activation must attempt rollback. Keep top-level IPv6 and explicit TUN IPv6 addresses; set `SKIP_SYSTEM_IPV6_CHECK=1` in both service implementations. Linux uses mips and auto-redirect; macOS uses mips without a fixed device or auto-redirect.

## Security and system boundaries

Never commit subscriptions, tokens, live configuration, credentials, VM identities, logs or private paths. Never put subscription URLs or passwords in process arguments, shell tracing, exceptions or normal logs. Store sensitive files with mode 0600 in a root-controlled configuration directory. LAN proxy access requires strong persistent authentication; the management API listens on loopback only. Do not kill unrelated processes or edit package-owned service files. Prefer a systemd drop-in and prevent competing Homebrew services. Status should work without elevation; explicitly elevate mutations. Do not run Homebrew as root.

## Validation

Automated tests use temporary directories and fake service/core boundaries. Real TUN, routes, DNS, service installation and reboot tests must run inside a positively identified disposable Tart VM. Clone a recoverable baseline before destructive tests. Do not modify the development host's proxy, DNS, routes or services. Verify IPv4 and IPv6 interfaces, routes, DNS, core connection evidence and egress separately. An HTTP success or a VM without native IPv6 does not prove interception or absence of leaks. Report unit, simulated integration, Linux VM and macOS VM results separately; never call an unexecuted check passed.

## Contributions

Use English in source comments, CLI output, documentation and commit messages. Keep functions focused, prefer the standard library and add meaningful regression tests. Review diffs for secrets and run relevant checks before small focused Conventional Commits. Do not rewrite history, push, or create remote repositories without authorization. The maintainer selects the project license; do not invent license terms.
