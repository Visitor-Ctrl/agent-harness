# 07 — AI engines and account capacity

The harness routes work by configuration, with no engine-specific routing branch.
Use [installation](06-harness-install.md) first and keep config private to its
Linux profile. Commands are trusted argv arrays, executed without a shell. Review
CLI flags against the installed engine version before use. The supplied Claude,
Codex and Gemini blocks are examples, not a compatibility promise.

## Engine definition

Copy [engines.example.toml](../harness/config/engines.example.toml) to
`~/.config/agent-harness/engines.toml`. Each `[engines.<name>]` declares `command`,
`config_env` and `probe_command`. Optional argv templates contain `{model}`,
`{effort}` or `{session}` for `model_flag`, `effort_flag`, `session_flag` and
`resume_flag`. `resume_command` can replace the base command on resume.
`transcript_glob` locates session history relative to the account directory;
never select credentials. `continue_prompt` provides a continuation instruction.
See the [full engine field reference](../harness/README.md).

A fourth engine requires only config, assuming it supports the batch interface:

```toml
[engines.custom]
command = ["custom-agent", "run"]
config_env = "CUSTOM_AGENT_HOME"
model_flag = ["--model", "{model}"]
probe_command = ["custom-agent", "check"]
[engines.custom.patterns]
limit = ['capacity exhausted']
auth = ['sign in required']
unlicensed = ['subscription inactive']
notice_limit = ['^capacity exhausted$']
```

Add a matching `[[accounts]]` block to pool.toml and create/sign in its private
config directory using `CUSTOM_AGENT_HOME`. Validate the probe, a successful
batch call and each failure classification in an isolated test account before
using failover. No Python changes are required. Missing flag support produces
a usage error when requested. Dispatch requires an explicit engine and model;
model classes in policy must be replaced with actual engine model IDs.

Patterns are case-insensitive Python regex arrays. Failed calls use `limit`,
`auth` and `unlicensed`; narrow anchored `notice_*` patterns may classify short
notices even with exit zero. Keep them specific: matching quoted output can
replay work. Optional line-JSON `result` fields and `stream_flags` distinguish
terminal results from progress events. Never infer success merely from output
that mentions success. Pool retries can repeat external side effects: make
release operations independently authorized and idempotent.

## Accounts, priorities and reservations

[pool.example.toml](../harness/config/pool.example.toml) shows multiple engines.
Each account has a name, engine, config_dir, numeric priority (lower first), and
slots (default one). Ties prefer the least recently limited account, then config
order. The directory must already exist. Sign-in and subscriptions are handled
by the CLI/operator, independently for each Linux profile. Account names are
unique within an engine; all accounts of an engine within one profile share a
trust boundary for transcript transfer.

```toml
[[accounts]]
name = "acct-coordinator"
engine = "custom"
config_dir = "~/.local/share/agent-harness/accounts/custom/acct-coordinator"
slots = 1
priority = 0
reserved_for = "coordinator"
```

Ordinary workers exclude reservations for another role. The coordinator prefers
its reserved account and then shared accounts. A role is scheduling policy, not
an OS security boundary. Configure enough subscription capacity for the worker
cap plus coordinator activity; slots do not increase provider quotas.
`healthy --role coordinator` includes shared and coordinator-reserved capacity;
normal `healthy` reports free verified worker accounts only.

`ai-pool status --json` shows health and overrides. `probe --force` performs a
real provider call, which may consume quota. `off` and `drain` stop new work
without killing running children; drain becomes off when slots empty. `on`
removes an override and probes. `cool --seconds N` records a usage hold. Usage
limits cool; auth errors become signed-out; licensing failures are rechecked
after their configured interval. Cooling expires to unknown. Unknown accounts
may run but are not advertised as healthy. An inconclusive probe is not evidence
of healthy capacity. Exit 75 means no eligible free account, with a retry-time
hint rather than a guarantee that sign-in will repair itself.

The pool buffers piped stdin for replay and transfers regular transcript files
when explicit session/resume options are used. Symlinks are refused at the
destination and skipped at the source. New-session support is optional: engines
without it receive fresh coordinator checkpoint prompts each turn. Dispatch
continuations always start fresh from the prior handover. See
[coordinator semantics](../harness/docs/coordinator.md).

## Model policy and alerts

| Task tier | Model class | Example responsibility | Review gate |
| --- | --- | --- | --- |
| Routine | Fast / economical | Bounded docs and inspection | Independent worker |
| Standard | General coding | Implementation and focused verification | Independent worker |
| Complex | Strong reasoning | Security, concurrency, architecture and releases | Independent specialist and owner release gate |

Choose current supported model IDs in profile policy; no hardcoded model routing
is implied by this table. The [profile templates](../harness/templates/AGENTS.md)
require explicit model selection, at most three workers by default, fresh
handovers, and an independent reviewer. The author never reviews their own work.

`harness-watch` warns about fewer than two free verified worker accounts by
default, stale health observations and signed-out accounts. The daily
`coordinator-health` check separately requires worker and coordinator capacity.
Configure thresholds and notify backends before enabling timers. A fully busy
healthy pool can warn because counts represent free capacity. Use reservations
and worker caps to leave room for supervision; never evade provider quotas by
adding slots. A notification is information for the operator, not permission
to purchase capacity, switch trust profiles or modify credentials.
