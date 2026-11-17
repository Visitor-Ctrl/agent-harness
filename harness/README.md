# Agent harness

Version 0.1.0 provides a config-driven account pool for agent CLIs, using Python
3.11+ and the standard library on Linux. It does not sign in or read credential
files. This component runs locally; the remaining harness components are listed
in [HANDOVER.md](HANDOVER.md).

## Setup

Copy `config/engines.example.toml` and `config/pool.example.toml` to
`~/.config/agent-harness/engines.toml` and `pool.toml`. Adjust account directories
and commands to your locally installed CLI versions. Sign in through each CLI
separately using that account's configured environment variable. Account
directories must exist before they can be selected. Example engine commands are
configuration templates; offline tests do not validate installed vendor CLIs.

Run `bin/ai-pool probe --force`, then `bin/ai-pool status`. No installation or
cloud access is needed to run the tests:

```sh
python3 -m unittest discover -s harness/tests -v
```

## Commands

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

## Configuration reference

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
