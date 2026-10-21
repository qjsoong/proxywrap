# Contributing

ProxyWrap keeps core management small and explicit. Share platform-independent
configuration logic in Python, isolate native commands, and use Bash for the
installation entry point. Read [AGENTS.md](AGENTS.md) before changing the project.

## Development environment

Use Python 3.10 or newer and a dedicated virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check src tests scripts
bash -n install.sh scripts/*.sh
shellcheck install.sh scripts/*.sh
```

Install ShellCheck separately through your OS package manager. PyYAML is the
only runtime Python dependency; Ruff is a development dependency. Do not install
packages into a managed system interpreter. The download integration tests bind
a temporary HTTP server to loopback; permit local sockets in the test environment.
They never install a service or modify system routes or DNS.

CI runs the automated checks on Linux and macOS with Python 3.10 and 3.13 and
runs ShellCheck on Linux. Passing this matrix does not replace deployment tests.

## Code structure

| Path | Responsibility |
| --- | --- |
| `install.sh` | Platform/CPU detection, OS dependencies, verified core installation, private Python runtime, launcher |
| `src/proxywrap/config.py` | YAML parsing, deep merge, safety checks, initialization, secure atomic writes |
| `src/proxywrap/update.py` | Download, validation, locking, durable subscription transactions, rollback |
| `src/proxywrap/services.py` | Native systemd and launchd lifecycle and startup checks |
| `src/proxywrap/deploy.py` | Initial deployment, repeat installation, deployment backups and recovery |
| `src/proxywrap/cli.py` | Public commands and explicit privilege escalation |
| `templates/` | Safe public defaults without live secrets |
| `tests/` | Unit tests and simulated integration tests using temporary directories |
| `scripts/` | Source bundle creation and repeatable guest validation |

Keep the public command `proxywrap` independent of the selected core. Add small
adaptations when another implemented core needs them; avoid speculative plugin
frameworks or dependencies.

## Review requirements

Preserve complete subscriptions and provider DNS. Merge subscription, common
override, then platform override; mappings recurse and sequences replace. Test
unknown upstream fields, unchanged updates, failures, concurrency, and rollback
when modifying this flow. Validate with Mihomo before activating a candidate.

Protect subscription URLs and credentials in storage, subprocess arguments,
exceptions, and logs. Use synthetic subscriptions in tests. Never commit live
configuration, private VM identities, captured logs, generated packages, or
credentials. Do not weaken authentication or expose the controller beyond
loopback as a side effect of unrelated changes.

Use English in source comments, documentation, CLI messages, and commits. Prefer
focused functions and the standard library. Keep tests meaningful: regressions
should exercise observable behavior rather than reproduce implementation details.
Update public documentation and AGENTS.md when behavior or workflows change.

Create small, focused Conventional Commits after reviewing the diff and running
the relevant checks. Do not rewrite shared history or push without authorization.
A pull request should explain the concrete behavior change, validation, and any
remaining platform or network limitations. Remove secrets from diagnostic evidence
before sharing it.

## System deployment tests

Follow [docs/validation.md](docs/validation.md). Use positively identified,
disposable Tart guests cloned from recoverable baselines. Run installers, TUN,
route, DNS, native service, and reboot experiments inside the guests. Do not run
them on a development host as part of the automated suite.

Record unit, simulated integration, Debian guest, and macOS guest results
separately. Verify interfaces, IPv4/IPv6 routes, DNS, core connection evidence,
and actual egress individually. A successful HTTP request or an IPv6 interface
alone is insufficient. State virtualization limits and unexecuted checks plainly.

## Source releases

Commit the intended release contents, then run:

```sh
bash scripts/build-release.sh
```

The script archives committed `HEAD` into `dist/` and writes a SHA256 checksum.
Uncommitted edits are excluded. Test installation from the extracted archive in
clean guests; `install.sh` expects the complete source bundle, not a standalone
downloaded script. Recheck upstream package digests when changing the pinned
Mihomo or Homebrew bootstrap version.

The maintainer must select the project license before publishing an open-source
release. Do not infer license terms from the project's name or its dependencies.
