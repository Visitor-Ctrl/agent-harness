# Dispatch, heavy-lock, and herdr helpers

These commands require Python 3.11+ and Linux. They execute local trusted
configuration and do not provision services or authenticate accounts.

Copy `config/dispatch.example.toml` and `config/herdr.example.toml` into
`~/.config/agent-harness/` as `dispatch.toml` and `herdr.toml`. Configure ai-pool
with `engines.toml` and `pool.toml` as described in the harness README.

```sh
bin/dispatch start sample --engine codex --model sample-model --effort high \
  --cwd ~/projects/example --task 'Implement the requested change.'
bin/dispatch status
bin/dispatch status sample
bin/dispatch summary sample
bin/dispatch stop sample
bin/dispatch continue sample --engine codex --model sample-model \
  --cwd ~/projects/example --task-file ./next-task.txt
bin/dispatch clean sample
```

`start` and `continue` require an explicit engine and nonempty model. Engines
come from the pool configuration; dispatch has no vendor routing branches.
`--effort` passes through to ai-pool. `--heavy` wraps the entire pool invocation
in heavy-lock, including account failover. The task comes from exactly one of
`--task` or `--task-file`. Global `--config-dir` and `--state-dir` options must
precede the subcommand. `HARNESS_CONFIG_DIR` selects configuration;
`HARNESS_DISPATCH_DIR` selects dispatch state. `HARNESS_STATE_DIR` still selects
the independent pool state and is inherited by ai-pool.

Dispatch state defaults to `~/.local/state/agent-harness/dispatch`. A locked
registry reserves up to `dispatch.max_workers` jobs across concurrent launchers
using that state directory. The default cap is three. Reservations cover both
starting and running jobs. Unknown process identities are marked `lost`;
starting reservations expire after 30 seconds. State directories are trusted,
profile-owned paths. Do not share dispatch state between untrusted users.

Each job contains `meta.json` and a separate randomly named directory per
attempt. Attempts contain `brief.md`, `task.md`, `output.log`, `stderr.log`,
`result.md`, and the worker-written `HANDOVER.md`; detached launch diagnostics
are in `launcher.log`. Metadata records status, model, engine, process identity,
and exit code. Files use private permissions. Prompts and results may contain
sensitive work context, so retain and secure them accordingly.

`templates/brief.md` receives the north star, operating context, state excerpt,
task, stop rule, handover destination, and previous handover. Default context
files are `NORTH-STAR.md`, `AGENTS.md`, and `COORDINATOR-STATE.md` in the job's
working directory. Missing context is explicitly identified in the brief. The
`dispatch` table can override these paths, `template`, `stop_rule`, and
`max_workers`. Relative template/context paths resolve against the job working
directory; a relative task-file resolves against the caller's working directory.
Template fields use Python `str.format`; literal braces must be doubled.

`continue` launches a fresh worker through the pool, with the same engine/model
requirements as start. It requires the preceding attempt's `HANDOVER.md` and
injects its contents into the new brief; it does not resume a vendor session.
Previous attempts remain intact. The engine and model may change between
attempts. An active job cannot be continued or cleaned. `stop` cancels a pending
launch or sends SIGTERM to the running process group. Jobs marked `lost` need
manual inspection for surviving child processes before replacement work starts;
the registry cannot track descendants after an externally killed supervisor.
Engines should honor SIGTERM and must not detach children into other sessions.

Workers must finish with a final `## Summary` section. `summary` prints only
that section, never earlier detail. A later level-one or level-two heading makes
the contract invalid. Successful output without a valid final Summary becomes a
failed job (exit 65), and its result gets a diagnostic Summary directing the
reader to the raw log and handover. Launch failures are exit 64. Other worker
failures preserve their exit code. The `result` schema in engines.toml can
extract a terminal JSON result; otherwise stdout is treated as plain text.
`status` prints JSON. `clean` moves an inactive job and all its attempts into
`archive/`; it does not delete results. Start command success means launch was
submitted, so inspect status for the worker's final result.

Dispatch uses the herdr JSON CLI: workspace list/create, tab create, and pane
run. A tab is labeled `dispatch:<job>`. The worker starts in its own process
group. If herdr is missing or unavailable, dispatch launches the same worker as
a detached local process. Duplicate launches are rejected by attempt identity
under the registry lock. Herdr tabs remain available for inspection; cleaning
job files does not close tabs. Output is captured in attempt logs.

## Heavy work slots

```sh
bin/heavy-lock -- make build
bin/heavy-lock -- bin/heavy-lock -- make test
```

Heavy-lock reads `/etc/agent-harness/heavy-lock.toml`, or the path selected by
`HARNESS_HEAVY_CONFIG` or `--config`. Use `config/heavy-lock.example.toml` as the
starting point. The `heavy_lock` table has `slots` (integer 1–1024, default 2),
`lock_dir` (default `/run/lock/agent-harness`), and positive finite `poll_seconds`
(default 0.2). Every profile must use the same config, slot count, and directory.
An administrator should provision a setgid directory owned by a shared Unix
group (mode 2770), with all intended users in that group. Slot files are created
with explicit mode 0660 regardless of the caller's umask. Existing slot files
must be readable by that group. Recreate runtime directories after reboot through
the host's normal tmpfiles or provisioning mechanism. No installer is run by
these helpers. Do not change the slot count while commands hold locks.

Each slot is a machine-wide `flock`. On contention, stderr logs that the command
is waiting, then logs the acquired slot. The child inherits the lock descriptor,
so killing only the wrapper cannot release a still-running immediate child's
slot. Nested heavy-lock calls validate and reuse `HARNESS_HEAVY_FD`, avoiding
self-deadlock with one slot. The marker is internal; do not set it manually.
A child that closes inherited descriptors cannot retain the lock after the
wrapper dies. Child exit codes propagate (signals use 128 + signal).

## Herdr setup helpers

```sh
bin/herdr-ensure-workspace --cwd ~/projects/example
bin/herdr-ensure-coordinator --cwd ~/projects/example
```

`herdr.toml` configures `command` (argv array), worker `session`, `workspace`,
`cwd`, `coordinator_session`, and optional `coordinator_command` (argv array).
Explicit `--cwd` overrides the config. Helpers print the workspace ID and fail
with exit 64 on CLI/config errors. They ensure a labeled workspace and, for the
coordinator, one labeled coordinator tab. The optional command starts only when
that tab is created; an existing tab is preserved. These helpers do not start a
herdr server or restart a crashed coordinator. Calls are serialized through a
local flock in `~/.local/state/agent-harness/herdr`, overridable through
`HARNESS_HERDR_STATE_DIR`. Dispatch has its own registry lock.

## Offline verification

```sh
python3 -m unittest discover -s harness/tests
```

Fake engines exercise pool routing, briefs, fresh continuations, summary
validation, concurrency, stop, and archival. A fake herdr checks tab execution
and helper idempotence. Heavy-lock tests use real local flocks to verify slot
contention, nested calls, exit status, and configuration validation. No network,
installed vendor CLI, credentials, or running herdr server is required.
