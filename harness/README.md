# Agent harness command and configuration reference

A versioned, config-driven harness for batch AI CLIs on Linux with Python 3.11+
and the standard library. It includes account routing, dispatch, checkpointed
coordinators, guard hooks, shared heavy-work slots, an OCI secret broker,
notifications and watchers. Engines are declared in TOML; adding a compatible
batch engine requires no Python changes. Herdr, AI CLIs, systemd and the OCI CLI
are external dependencies for their respective components.

Start with [installation and first dispatch](../docs/06-harness-install.md),
[engine setup](../docs/07-ai-engines.md) and [messaging](../docs/09-agent-messaging.md).
The harness does not sign in, provision cloud accounts, activate services or
establish host isolation merely by being installed. Use the infrastructure
guide's live acceptance checks before unattended work.

## Installation and administration

Run from the repository root:

```sh
python3 -m unittest discover -s harness/tests
harness/install.sh --dry-run
sudo harness/install.sh
harness/bin/harness profile add research --root /srv/projects --dry-run
harness/bin/harness profile list
harness/bin/harness doctor
harness/bin/harness version
```

`install.sh` runs tests first, refuses failed checks/existing releases and copies
VERSION into `/opt/agent-harness/<version>` root-owned before atomically switching
`current`. `--prefix PATH` chooses an alternate root-owned prefix. It installs
system/user unit files and reloads the system manager; activation/restarts are
explicit operator steps. `--rollback VERSION` validates/tests an existing
protected release, restores its supplied units and switches `current` back.
It does not undo configuration/state migrations or delete obsolete units.

`harness profile add NAME` needs administrator privileges except `--dry-run`.
It creates a new Linux user/home, project directory (both 0700), agent-heavy group
membership, a systemd user-slice drop-in, config skeleton and draft instructions.
Options are `--root` (HARNESS_ROOT, otherwise `/srv/projects`), `--memory-high`
(default 8G), `--cpu-weight` (100) and `--tasks-max` (4096). Existing users and
destinations are refused. `profile list` reads the administrator-owned inventory
in `/etc/agent-harness/profiles`. `doctor` checks Python, Git, herdr, systemctl and
core config TOML syntax; exit 1 reports findings. It does not probe accounts or
prove active hooks/isolation. Administration errors return 64.

Examples and templates are proposals: remove unused backends/accounts, fill all
placeholders and obtain owner approval for the project NORTH-STAR.md. Set up
shared heavy-lock permissions, broker policy and active client hooks separately.
Config files select executable commands and must stay within the profile's trust
boundary. Source checkouts used by administrators must be reviewed and trusted.

Commands below use `bin/` relative to this directory. For installation, add
`/opt/agent-harness/current/bin` to PATH. CLI `--help` lists exact options.

## Account pool commands

```sh
bin/ai-pool run --engine claude --model sample-model --effort high -- task
bin/ai-pool run --engine claude --session unique-session-id -- task
bin/ai-pool run --engine codex --resume existing-session-id -- task
bin/ai-pool status --json
bin/ai-pool healthy --engine claude --names
bin/ai-pool probe --engine claude --account acct-a --force
bin/ai-pool off --engine claude acct-a
bin/ai-pool on --engine claude acct-a
bin/ai-pool drain --engine claude acct-a
bin/ai-pool cool --engine claude acct-a --seconds 3600
```

Engine CLIs can impose stricter session identifier formats; for example, use a
UUID for a new Claude session. `run` accepts `--account` to pin selection; it
never changes engines automatically. Options after `--` pass through unchanged.
Supply session identifiers through pool options to enable transcript transfer;
passing a vendor resume flag after `--` bypasses this feature. A new engine must
support explicit session assignment to use `--session`; otherwise use `--resume`
with a session created by that engine.

Selection prefers lower numeric priority, then the least recently limited
account, then configuration order. Unlicensed accounts due for a recheck follow
ordinary candidates. Unknown accounts may run but are not reported as healthy.
`healthy` counts only verified successful accounts with free slots, an existing
config directory, and no manual override or inconclusive latest probe.

`off` prevents new work; `drain` prevents new work and becomes off after existing
slots empty. Neither interrupts running jobs. `on` removes the manual override
and probes immediately. Cooling expires to unknown, unlicensed accounts become
eligible after the recheck interval, and signed-out accounts require a new probe
or sign-in. Probes can recover signed-out accounts; `--force` bypasses timers,
while off/draining accounts always remain skipped. An inconclusive probe retains
the previous verdict and marks it uncertain. Successful work started before a
newer failure cannot clear that failure.

Output streams live with bounded in-memory classification tails. Standard input
is buffered until EOF for replay during failover; use this command for batch
jobs. An inherited terminal remains attached on the initial attempt. Failed
authentication, licensing, and usage limits try another account. Other failures
return the child status without replay. Terminal result events can confirm
success even if a CLI subsequently crashes. Only configured short anchored
notices can override exit zero, so output quoting rate-limit logs remains a
successful answer. All retry attempts' output appears in the output stream;
consumers should use the final process exit code to determine the outcome.

With `--session` or `--resume`, configured transcript files and regular sidecars
are synchronized to the chosen account, keeping newer destination files and
using private file modes. Symbolic links are skipped at the source and rejected
at the destination. Only accounts configured for that engine are searched.
Existing transcripts trigger resume flags and a continuation instruction on
stdin that tells the agent to inspect the checkpoint and preserve completed
work. Extra CLI arguments and original input are retained. Session files are
sensitive agent history; accounts within a profile must share a trust boundary.
The pool cannot guarantee exactly-once external side effects across retries.

Exit codes: 0 for success, 64 for invalid configuration/launch errors, 75 when no
eligible free account remains, 130 for interrupted execution, otherwise the
child's status (signals use 128 + signal). On 75, stderr includes
`AI_POOL_RETRY_AT=<epoch>`; if `AI_POOL_RETRY_FILE` is set, that epoch is also
written atomically to the specified existing parent directory. This variable is
removed from the child's environment. The retry timestamp is a hint: disabled,
signed-out, and missing-directory accounts need intervention.

## Coordinator commands

`bin/coordinator` supervises bounded turns in one existing herdr pane;
`bin/coordinator-watch` detects configured limit notices; `bin/coordinator-rotate`
queues a fresh session at a checkpoint boundary; `bin/coordinator-health` reports
pool capacity, failed units, installed guard files and the broker socket. See the
[coordinator reference](docs/coordinator.md) for configuration, account
reservation, timers, checkpoint behavior and offline checks.

## Pool and engine configuration

Global `--config-dir` and `--state-dir` must precede the subcommand. Their defaults
can be overridden by `HARNESS_CONFIG_DIR` and `HARNESS_STATE_DIR`. Default state
is `~/.local/state/agent-harness/pool/`: `state.json`, `state.lock`, `events.log`,
and `slots/`. State updates use `flock` and atomic replacement. A corrupt state
file causes a closed failure rather than resetting manual disables. Slot locks
are inherited by the immediate child so killing the wrapper does not release a
still-running child's slot. State and event files have mode 0600. Events are
JSON lines with UTC time, engine/account key, and old/new state; command output,
arguments, prompts, and credential values are never logged there.

`pool.toml` has `[pool]` and `[[accounts]]` blocks:

| Pool setting | Default | Meaning |
| --- | --- | --- |
| `cool_seconds` | 3600 | Fallback usage-limit cooldown |
| `probe_interval` | 1800 | Minimum seconds between routine probes |
| `unlicensed_recheck` | 1500 | Recheck interval for license failures |
| `probe_timeout` | 90 | Maximum seconds for a health probe |
| `slot_wait` | 0 | Maximum seconds to wait for occupied eligible slots |

All intervals are finite nonnegative seconds. Explicit `cool --seconds` must be
positive. A limit notice containing `retry in N seconds/minutes/hours` or a future
`|<epoch>` overrides the default cooldown. Other reset formats use the default.

Each account requires `name`, `engine`, and `config_dir`. Optional `priority`
defaults to 0 and `slots` defaults to 1 (integer, 1–1024). `reserved_for` optionally
names a scheduling role: ordinary `run` and `healthy` use role `worker` and
exclude other roles' reservations; `--role coordinator` includes shared and
coordinator-reserved accounts. Status/probes cover every account. Names are lowercase
letters, digits, and hyphens. Account names are unique within an engine. Paths
expand `~`; variables such as `$HOME` are not expanded. Account config roots may
contain engine-specific subdirectories; for Gemini, `GEMINI_CLI_HOME` selects a
home root whose `.gemini` subdirectory contains configuration and session data.

`engines.toml` defines `[engines.<name>]` blocks:

| Field | Purpose |
| --- | --- |
| `command` | Required base argv for batch work |
| `config_env` | Required environment variable selecting the account directory |
| `probe_command` | Required argv for a minimal authenticated health check |
| `model_flag` | Optional argv template containing `{model}` |
| `effort_flag` | Optional argv template containing `{effort}` |
| `session_flag` | Optional argv template containing `{session}` for a new session |
| `resume_flag` | Optional argv template containing `{session}` for an existing session |
| `resume_command` | Optional alternate base argv when resuming |
| `transcript_glob` | Optional relative glob containing `{session}`; never a credential glob |
| `continue_prompt` | Optional continuation instruction prepended to stdin on resume |
| `stream_flags` | Optional array of argv sequences enabling line-JSON interpretation |

Commands execute directly without a shell. Missing model/effort/resume support
causes a usage error when that option is requested. Use absolute executable paths
where appropriate. Config files are trusted executable policy and must remain
under the profile owner's control.

`[engines.<name>.patterns]` contains case-insensitive Python regex arrays:
`limit`, `auth`, and `unlicensed` classify failed calls; `notice_limit`,
`notice_auth`, and `notice_unlicensed` recognize short notices even on exit zero.
Anchor notice patterns to prevent replaying successful output that mentions an
error. Authentication maps to signed-out. Match priority is auth, unlicensed,
then limit. Inconclusive errors preserve the previous health verdict.

Optional `[engines.<name>.result]` configures a line-JSON terminal event schema:
`type_key`, `type_value`, `text_key`, `error_key`, `success_key`, `success_value`.
It is applied only when a configured `stream_flags` sequence is present. Raw
progress events without a terminal result are not searched for error patterns;
stderr and the exit status remain evidence. Plain text containing JSON is never
interpreted as protocol events.

## Add an engine

Add `[engines.example]` with its command, config selector, probe argv, optional
flags, and error patterns. Add one or more `[[accounts]]` entries referencing
`engine = "example"`. Create and authenticate the account directories, then run
`bin/ai-pool probe --engine example --force`. No Python changes are needed. Probes
may consume quota; choose a small probe and an appropriate interval. The offline
suite runs an engine named `custom` to verify this configuration boundary.


## Dispatch and herdr

```sh
bin/dispatch start sample --engine codex --model sample-model \
  --cwd ~/projects/example --task 'Complete one bounded change and write a handover.'
bin/dispatch continue sample --engine codex --model sample-model --task-file ./next.txt
bin/dispatch status sample
bin/dispatch summary sample
bin/dispatch stop sample
bin/dispatch clean sample
bin/herdr-ensure-workspace --cwd ~/projects/example
bin/herdr-ensure-coordinator --cwd ~/projects/example
bin/heavy-lock -- make build
```

`start`/`continue` require engine, model and exactly one task source; `--effort`
passes through and `--heavy` wraps pool execution in the shared lock. Continue
requires the preceding attempt handover and launches a fresh worker. Summary
prints the final `## Summary` only; a missing/invalid Summary fails otherwise
successful work with exit 65. Status returns JSON; stop cancels a job process
group; clean archives inactive jobs. Launch success is submission, not completion.
Herdr gets a labeled tab; if unavailable the worker runs detached locally.

The default worker cap is three. Attempts retain brief, task, logs, result and
HANDOVER.md under private dispatch state. Lost process identities need operator
inspection. Heavy-lock uses machine-wide flock slots, supports nested calls and
propagates the child status. Herdr helpers ensure workspaces/coordinator tabs;
they do not start a server or infer the coordinator pane ID. See
[dispatch, heavy-lock and herdr details](docs/dispatch.md).

## Guard hooks

`bin/guard-hook` consumes one Claude Code JSON hook event. `bin/guard-install
--dry-run --config PATH` reviews generated settings; a privileged invocation
with `--destination PATH --output PATH` copies only the reviewed guard package,
launcher and explicit policy into a new root-owned destination. Distribute its
settings through the administrator-owned client configuration workflow.

PreToolUse rejects secret exposure, credential-store damage, unsafe Git
operations, guarded paths, configured dangerous commands, dispatch without a
model and deployments outside the local clean-ref gate. SessionStart injects
bounded project/state/handover context. Lifecycle audit stores rule identifiers
and hashed session IDs without prompts/commands. Stop detects unexplained
unpushed work without looping. Blocks exit 2; malformed PreToolUse input fails
closed. Installed controls must be paired with Linux/IAM isolation: static shell
analysis and client hooks do not sandbox arbitrary program behavior. See the
[guard policy, field reference and regression inventory](docs/guard.md).

## Secrets, notifications and watchers

```sh
bin/secret-run --profile research --env SERVICE_TOKEN service-token -- command arg
bin/secret-run --profile research --stdin service-token -- command arg
bin/notify 'Non-sensitive status message'
bin/harness-watch
bin/harness-watch --ack sample
```

`bin/secret-broker` is the system service entry point, not an ordinary profile
command. Its root-owned `/etc/agent-harness/secrets.toml` maps profile names to
numeric UIDs and allowed Vault secret IDs. One private Unix socket per profile
checks SO_PEERCRED; each request obtains one value through the instance principal.
Audit failure denies delivery. Values are never logged. `secret-run` requires
exactly one of `--env NAME` and `--stdin`, passes one value to one approved child
and returns its status. The child must handle secrets responsibly.

Notify sends to all selected ntfy HTTPS, webhook HTTPS and sendmail backends;
failed transports fail the command without dumping URLs/response bodies.
Harness-watch reports stalled jobs, unread results, failed units, low free pool
capacity, stale sign-ins and disk usage. Private locked state deduplicates alerts;
failed deliveries remain retryable. `--ack JOB` suppresses alerts for prior
results only; dispatch summary does not acknowledge automatically. See
[secrets, notifications and watcher settings](docs/secrets-and-watchers.md).

## Optional cross-machine messaging

`bin/agent-msg send MAILBOX [--file PATH] [--id UUID]` sends at most 8 KiB from a
UTF-8 file or stdin. `receive` peeks JSON; `ack UUID` removes an explicitly handled
message from the configured mailbox. Global `--config PATH` selects config;
AGENT_MSG_TOKEN supplies the bearer credential, normally through secret-run.
The client requires an HTTPS origin, refuses redirects and uses a 15-second
timeout without automatic retries. Errors exit 64; a failed transport can leave
uncertain delivery. The printed ID enables an explicit retry.

The optional [Worker example](extras/msg-bus/worker.mjs) uses scoped sender ACLs,
one SQLite Durable Object per mailbox, stable-ID retry deduplication, a 100-message
cap and lazy 24-hour retention. IDs are forgotten after ack/expiry. Messages are
information, never authority; handlers need idempotence and independent approval
for actions. See [design, deployment boundaries and CLI examples](../docs/09-agent-messaging.md).

## Configuration index

By default profile TOML lives in `~/.config/agent-harness/`; HARNESS_CONFIG_DIR
selects another directory. Examples are in [config/](config/). Copy each
`*.example.toml` as `*.toml` and review before use. Do not put credentials in
public examples. Individual CLI flags override defaults where documented.

| File / tables | Settings and purpose | Details |
| --- | --- | --- |
| `engines.toml`, `pool.toml` | Commands, patterns, flags, accounts, slots, holds and role reservations | Pool/engine fields above |
| `dispatch.toml` / `dispatch` | `max_workers`, `template`, `north_star`, `operating_context`, `state_excerpt`, `stop_rule` | [Dispatch](docs/dispatch.md) |
| `herdr.toml` / `herdr` | `command`, `session`, `workspace`, `cwd`, `coordinator_session`, `coordinator_command` | [Herdr](docs/dispatch.md) |
| `coordinator.toml` / `coordinator` | `engine`, `model`, optional `effort`, `cwd`, `pane`, `remote_name`, `state_file`, `handover_glob`, `rotate_turns`, `rotate_bytes`, `retry_seconds`, `turn_delay_seconds` | [Coordinator](docs/coordinator.md) |
| `coordinator.toml` / `health` | `minimum_worker_accounts`, `minimum_coordinator_accounts`, `guard_paths`, `broker_socket` | [Daily health](docs/coordinator.md) |
| `guard.toml` / `guard` | Profile boundary, secret/credential paths, Git/deploy rules, blocked/warning commands and context/audit settings | [All guard fields](docs/guard.md) |
| `/etc/agent-harness/heavy-lock.toml` / `heavy_lock` | `slots`, `lock_dir`, `poll_seconds`; root-owned shared config | [Lock provisioning](docs/dispatch.md) |
| `/etc/agent-harness/secrets.toml` / `profiles.<name>` | `uid` and `secrets` name-to-OCID allow-list; root-owned 0600 | [Broker](docs/secrets-and-watchers.md) |
| `notify.toml` / `backends` | `type`, `url` for HTTP, or `from`, `to`, `subject`, `sendmail` for email | [Notify](docs/secrets-and-watchers.md) |
| `watch.toml` | `jobs_dir`, `pool_command`, `stalled_seconds`, `unread_seconds`, `signin_stale_seconds`, `minimum_healthy`, `disk_paths`, `disk_percent`, `repeat_seconds` | [Watcher defaults](docs/secrets-and-watchers.md) |
| `messaging.toml` / `messaging` | HTTPS origin `url`, own `mailbox`; credentials from environment | [Mailbox](../docs/09-agent-messaging.md) |

Default private state is under `~/.local/state/agent-harness/`: pool in `pool/`,
dispatch in `dispatch/`, coordinator in `coordinator/`, herdr in `herdr/`,
watcher in `watch.json`. Overrides are HARNESS_STATE_DIR for pool and watcher,
HARNESS_DISPATCH_DIR, HARNESS_COORDINATOR_DIR and HARNESS_HERDR_STATE_DIR.
Dispatch/pool global config/state options precede subcommands. Coordinator
commands accept config/state options directly. Heavy-lock uses
HARNESS_HEAVY_CONFIG or `--config`; guard uses HARNESS_GUARD_CONFIG or `--config`
for development, while installed settings pin the reviewed policy explicitly.
Notify/agent-msg accept `--config`; harness-watch accepts `--config` and `--state`.
Secret-run selects HARNESS_PROFILE or `--profile`, or an explicit `--socket`.
Consult each linked component for private-file modes and failure semantics.

## Units and templates

The release installs `systemd/secret-broker.service` and user unit/timer pairs:
ai-pool-probe (30 minutes), harness-watch (10 minutes), coordinator-watch
(2 minutes), coordinator-health (daily). Configure first, then explicitly reload
the user manager and enable selected timers. Installation never enables them.
The broker requires the reviewed root policy and chapter 04 isolation gates.

[templates/](templates/) contains machine rules, profile CLAUDE.md/AGENTS.md,
COORDINATOR-STATE.md, HANDOVER.md, draft NORTH-STAR.md, a Python-format worker
brief and client settings. Adopt concrete profile paths and model IDs. Set the
same worker cap in policy and dispatch config. Authors never review their own
work; preserve checkpoints and launch fresh workers at boundaries.

## Verification

From the repository root:

```sh
python3 -m unittest discover -s harness/tests
node harness/extras/msg-bus/test-worker.mjs
```

Python tests use fake CLIs, temporary files, real local flocks and local sockets;
installer tests simulate privileged operations without changing host users or
services. The optional Node test uses a fake SQL store. These checks do not
exercise live AI flags, deployed mailboxes, Vault, active timers, host isolation
or provider billing. Keep live acceptance evidence in your private runbook.
