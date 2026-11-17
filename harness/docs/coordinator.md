# Coordinator supervision

The coordinator runs bounded CLI turns through ai-pool in an existing herdr pane.
It uses the same engine definitions, models, account slots and health transitions
as dispatch. It never provisions accounts or services. Copy
[`coordinator.example.toml`](../config/coordinator.example.toml) to
`~/.config/agent-harness/coordinator.toml`, then set the engine, model, project
`cwd`, existing pane ID and herdr session name (`remote_name`). Herdr's command
comes from `herdr.toml`. Its pane-read CLI must support `--raw` plain text output.

Start `coordinator` as the foreground command in that pane, or use it as the
`coordinator_command` argv in herdr.toml when creating the coordinator tab.
Set `pane` to that tab's actual pane ID before launching. The helper cannot infer
an ID in the configuration file. Do not start another launcher in the same pane.
The supervisor streams output into the pane and keeps its identity throughout
failover and rotation. It never recreates the tab or renames the herdr session.

```sh
bin/coordinator
bin/coordinator --once
bin/coordinator-watch
bin/coordinator-rotate
bin/coordinator-health
```

Every command accepts `--config-dir` (default `HARNESS_CONFIG_DIR`, otherwise
`~/.config/agent-harness`) and `--state-dir` (default `HARNESS_COORDINATOR_DIR`,
otherwise `~/.local/state/agent-harness/coordinator`). `HARNESS_STATE_DIR` still
selects the independent ai-pool state. Use a separate coordinator directory for
each engine/project/pane/session combination. A change to that identity requires
a new state directory. A lifetime flock rejects a second launcher. Control state
is locked and atomically replaced; corrupt JSON fails closed.

## Account reservation and failover

In pool.toml, add `reserved_for = "coordinator"` to an account dedicated to the
coordinator. Ordinary `ai-pool run` and `healthy` default to role `worker` and
exclude reserved accounts, including when explicitly named with `--account`.
`run --role coordinator` and `healthy --role coordinator` include shared accounts
and accounts reserved for that role. Status and probes still cover all accounts;
status JSON exposes `reserved_for`. Reservations are scheduling policy within a
trusted profile, not a privilege boundary against callers choosing another role.

The launcher prefers eligible coordinator-reserved accounts, then shared ones,
using licensing state, priority and last-limit time within those groups. It pins
each attempt through ai-pool so its active-account record is exact. Pool slot
locks remain authoritative if another process takes a slot after selection.
Usage, licensing and authentication failures recorded by ai-pool move the loop
to another eligible account. No account available means exit 75 with `--once`,
otherwise a retry after `retry_seconds`. Other failures stop the launcher for
inspection. A successful turn waits `turn_delay_seconds` before the next turn.

## Pane watcher

The optional `coordinator-watch.timer` runs every two minutes. The watcher reads
only the configured pane's latest 120 lines, capped at 64 KiB. It compares that
snapshot with the pre-attempt baseline and previous read, matching only newly
observed output against the engine's `patterns.limit` expressions. An unchanged
pane does not repeat an alert. It records the active account's cooling hold in
the pool using the configured duration or a recognized retry duration/epoch.
The control record retains hold metadata and a SHA-256 evidence fingerprint.

The launcher observes the restart request, terminates its own live ai-pool
process, lets the pool reap the engine process group, then selects another
account. The watcher never signals a PID read from a state file. The same pool
session ID is retained when the engine supports explicit session assignment,
resuming transferred transcripts where available. Without that capability,
failover starts with a fresh state/handover prompt.

Pane matching is a heuristic. Narrow limit patterns to provider notices; output
quoting a matching notice can trigger cooling. Notices that appear and disappear
between reads can be missed. A crashing or unavailable pane reader fails the
watch command; it does not mark an account unhealthy. The private control record
contains a bounded pane snapshot while an attempt is active. Protect it like
agent history; it can contain work context. Snapshots are removed when a turn
ends normally or is interrupted. A machine/process crash can leave private
stale state until the next launcher starts.

## Rotation and checkpoints

Each prompt directs the coordinator to read the approved objective and profile
rules, preserve completed work, do one bounded turn, then update
COORDINATOR-STATE.md and write a handover. Prompts include the state file and the
newest regular, nonsymlink handover matched by `handover_glob`, selected by mtime
then filename. Each document is limited to 128 KiB; oversized checkpoints stop
execution instead of being silently truncated. Keep secrets out of checkpoints.

`rotate_turns` counts successful turns, and `rotate_bytes` counts combined output
bytes since rotation. Zero disables the corresponding threshold. At the next
turn boundary, either threshold or `coordinator-rotate` requests a new session.
Both a state file and a handover must exist before rotating. Missing checkpoints
stop the launcher until an operator or worker repairs them. A manual request is
queued; it never interrupts an active turn. Rotation injects the newest state
and handover while keeping the pane and remote-control session name.

With engine `session_flag` and `resume_flag`, the supervisor assigns a UUID and
lets ai-pool resume a matching transcript. Engines without explicit session
assignment run fresh CLI sessions each turn and receive the same checkpoint
prompt. The threshold still resets the logical session counters. No vendor
session discovery is guessed. Checkpoint persistence is an agent instruction;
the harness cannot prove that a handover describes every external side effect.

## Daily health

`coordinator-health.timer` schedules a daily check with up to 15 minutes of
random delay. The check counts available verified worker/coordinator accounts,
checks failed system and user units, requires configured guard files to be
root-owned regular files without group/other write permission, and verifies the
configured broker path is a Unix socket. It reports both success and failures
through notify.toml. It does not request secrets or prove that the broker can
fetch from Vault. File checks do not establish that every client has active
hooks; retain the guard's installation acceptance checks.

The defaults require two free worker accounts and one free coordinator-eligible
account; a fully occupied pool can produce a capacity warning. Configure these
thresholds in `[health]` and list the actual installed guard launcher and policy.
Install the service/timer files in the profile's user unit directory through the
release installation workflow, then enable the two timers explicitly. This
component does not activate services itself.

Exit codes are 0 for a completed command/turn, 1 for health findings, 64 for
configuration/checkpoint/inspection failures, 75 for no usable account with
`--once`, and 130 for interruption. Delivery failure causes a failed health run.
Other engine errors preserve ai-pool's exit code. Offline verification uses fake
CLIs and sockets in [`test_coordinator.py`](../tests/test_coordinator.py).
