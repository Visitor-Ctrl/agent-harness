# Harness implementation handover

## Done

- Version 0.1.0: `bin/ai-pool`, `lib/agent_harness/pool.py`, example engine and
  account TOML, command/config reference in `README.md`, and 19 offline tests.
- Engine behavior is declared in configuration. The implementation contains no
  engine-specific routing branches. Examples cover Claude, Codex, and Gemini.
- Pool supports priority/slots, persisted health, cooling, signed-out and
  unlicensed states, failover, resume transcript transfer, manual controls,
  health probes, retry hints, and transition events.
- Changes live on branch `feat/generic-pool`; this session stops at the account
  pool component boundary.

## Evidence

- `python3 -m unittest discover -s harness/tests -v`: 19 tests passed.
- Fake CLI tests run without network access, authentication, or real accounts.
- Tests cover an additional engine defined only in TOML, live output, retained
  child locks after wrapper death, interruption cleanup, slot saturation,
  draining, inconclusive probes, stale successes, zero-exit auth/limit notices,
  result-event classification, transcript/sidecar transfer, symbolic-link
  rejection, private file modes, and retry marker isolation.
- Required case-insensitive identifier scan and IPv4 scan: no matches.
- `git diff --check`: clean.

## Left

- `dispatch`: start/continue/status/summary/stop/clean, required engine/model,
  configurable concurrency, heavy-lock, templated brief, result ending in
  `## Summary`, herdr tabs. Add its offline tests next.
- Coordinator launcher/watch/rotate and daily health check.
- Herdr session helpers and example configuration.
- Configurable merged guard, rule set, shell parser, context injection,
  credential-store protection, root-owned installer, and ported hook tests.
- Configurable heavy-lock.
- OCI instance-principal Unix-socket secret broker, peer UID and allow-list
  enforcement, audit log, secret-run, service, and offline tests.
- Generic notify backends and watchers, user units/timers, broker system unit.
- Machine/profile/brief/handover/settings templates.
- Versioned `/opt/agent-harness/<version>` installer, current symlink,
  per-profile config, and Linux-user/profile/slice provisioning command.
- Expand README as each component lands. An approved project NORTH-STAR.md is
  still required from the owner.

## Limitations

- Vendor CLI definitions are examples, validated syntactically and with fake
  engines only; check flags and directory layouts against installed versions.
- Pool is for batch jobs: piped input is buffered until EOF. Output verdicts use
  bounded tails. Time-of-day reset notices currently fall back to cool_seconds;
  epoch and relative-duration reset notices are parsed.
- Resume transfer requires an engine transcript glob and a stable session ID.
  Existing agent history is shared only among accounts for the same configured
  engine/profile. This is not an exactly-once side-effect guarantee.
- Config/state paths and account roots are trusted profile-owned directories.
  Transcript copies reject static symbolic links but do not implement an openat
  directory-FD walk to defend against concurrent hostile directory replacement.
- There is no packaging, installer, deployment, or service activation yet.

## Next step

Implement `bin/dispatch` and its library under `lib/agent_harness/` using the pool
CLI documented in `README.md`. Put integration tests in `tests/test_dispatch.py`
with fake pool, herdr, and heavy-lock executables; never contact real services.
Keep generic documentation and example data; commit only the new component's
paths, run its tests and the existing pool suite, and refresh this handover.
