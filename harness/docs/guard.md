# Guard hooks

`bin/guard-hook` accepts one Claude Code hook event as JSON on stdin. It handles
PreToolUse, PostToolUse, SessionStart, Stop, SubagentStart, SubagentStop and
UserPromptSubmit. Python 3.11+ and Git are required; no network or vendor Python
packages are used. Run it directly so its isolated Python shebang is honored.

## Policy and installation

Copy `config/guard.example.toml`, set the profile/root, and review the arrays for
that profile. The development launcher accepts `--config PATH`, then
`HARNESS_GUARD_CONFIG`, then `~/.config/agent-harness/guard.toml`. With no policy
file it uses built-in generic defaults. An explicitly missing or malformed
policy fails closed. All keys live under `[guard]`; unknown keys and invalid
types are errors. Arrays replace defaults rather than extending them.

Review the generated settings without privileges or writes:

```sh
harness/bin/guard-install --dry-run --config /path/to/reviewed-guard.toml
```

An administrator installs a reviewed, trusted source checkout:

```sh
sudo harness/bin/guard-install \
  --config /path/to/reviewed-guard.toml \
  --destination /opt/agent-harness/guard-v1 \
  --output /opt/agent-harness/settings.template.json
```

The installer copies only the guard package, launcher and policy. It sets root
ownership, directory/executable mode 0755 and data mode 0644. It rejects existing
destinations and symlinked or untrusted destination ancestors. Use a new version
directory for upgrades. The output settings template contains all seven event
hooks, bypassPermissions and native deny rules; distribute it to the accounts
through the administrator's settings deployment workflow. Existing account
settings are never silently edited. The template's existing permissions are
preserved, deny entries are merged and its hooks block is replaced completely.
The native deny list intentionally errs on the side of denying reads of env
examples and public SSH files; customize the reviewed settings if needed.

Installed commands name the root-owned policy explicitly, so an environment
variable cannot replace it. Policy edits and hook deployment are owner managed.
Do not grant agents sudo access to the installer or a writable parent of the
installed tree. This component does not install itself or change active hooks
when tests run.

## Checks

- Secret-producing commands, env dumps, secret variable printing and secret-file
  readers cannot send values to the terminal. Pipelines are followed through
  filters, tee, substitutions, process substitutions and nested shells. Direct
  consumers, file redirects, names/counts, hashes and presence checks can be
  allowed. Redirecting to a protected credential store remains blocked.
- Credential stores cannot be deleted, moved, overwritten or logged out.
  Cleanup scripts may be inspected using configured readers, Git inspection or
  `sed -n`. A reader combined with a runner or copy command anywhere in the same
  invocation loses that exemption, including `cat script | bash`, `&&`, sudo
  wrappers and substitutions. An explicit HOME or configured credential env
  pointing at an existing directory beneath the system temporary directory can
  isolate a cleanup test. An explicit path to a persistent store stays blocked.
- Git staging must name paths. Hook bypasses, force pushes, wildcard pushes,
  protected-branch pushes/deletions, protected hard resets and forced clean are
  denied. Feature branch force-with-lease follows the input policy and is
  permitted; branch deletion emits a warning. Nonstandard Git directory options
  on mutations require a simpler `git -C` command.
- Configured deploy command globs require a clean tree, including staged and
  untracked changes, and normally HEAD equal to the configured local deploy ref.
  Separate deployments from commands that could change the checked state.
  This is a local ref check; fetching, validation and deployment scripts remain
  responsible for freshness and the repository's release checks.
- Shell writes and file tools cannot change guarded paths, guard installations,
  settings or the selected policy. Configured denied paths also block reads.
  Path comparisons normalize `..`, HOME and symlinks. Recursive deletion of an
  ancestor of protected data is denied.
- `dispatch` start/continue invocations and Agent/Task tools require an explicit
  model. Read-only dispatch lifecycle queries are exempt.

PreToolUse blocks return exit 2 with fixed rule messages, warnings return JSON
additionalContext, and allowed calls return zero without output. Invalid JSON
and internal PreToolUse errors also return 2, without echoing the input or
exception. Non-enforcement event errors return 1. Stop asks once for unpushed
commits to be pushed or explained, then reports remaining work when
stop_hook_active is set, preventing a stop loop.

## Configuration reference

| Key | Meaning |
| --- | --- |
| `profile`, `root` | Display identity and profile context boundary |
| `guarded_paths` | Owner-managed files/directories; writes denied |
| `guard_paths` | Guard installation, configuration and settings paths |
| `denied_paths` | Paths that cannot be accessed, including other profile roots |
| `secret_patterns` | Python regular expressions over canonical absolute paths |
| `secret_exemptions` | Glob exemptions such as example files and public keys |
| `credential_paths` | Persistent stores protected from writes and deletion |
| `credential_readers` | Names allowed to inspect credential cleanup scripts |
| `credential_scripts` | Globs identifying cleanup scripts, including Git object paths |
| `credential_logout_commands` | Command prefixes that invalidate persistent sign-ins |
| `credential_sandbox_env` | Explicit temporary-directory overrides for logout tests |
| `secret_commands` | Shell command globs that produce secret values |
| `protected_branches` | Branch names protected from direct pushes and hard resets |
| `deploy_commands` | Command globs requiring the clean-tree gate |
| `deploy_ref`, `require_deploy_ref` | Local release ref and optional exact HEAD check |
| `require_model` | Require explicit dispatch and subagent models |
| `state_dir` | Private JSONL lifecycle/audit directory |
| `context_files` | Ordered relative north-star candidates; approved name first |
| `context_limit` | Context character cap, 256 through 65536 |

Path policies expand `~`, `$HOME`, `${HOME}`, `${HARNESS_ROOT}` and `${PROFILE}`;
arbitrary environment expansion is deliberately excluded. Paths may contain
globs. Relative policy paths are relative to each command's working directory.
Credential directories in the example cover common CLI stores; add each engine
and account store used by your deployment.

## Context and audit

SessionStart finds the nearest project marker within the configured profile
boundary, then supplies profile/project, north star or a missing-objective flag,
the latest handover and coordinator state. Markdown discovery is bounded to two
directory levels and skips hidden and symlinked entries. Configurable north-star
candidates can include nested layouts. Individual file reads and combined output
are capped. Coordinator state uses its Now/Current state section, caps its body
at 25 lines and warns after six hours; ISO timestamps with explicit timezones
are honored, otherwise file mtime is used. Common sensitive assignments and PEM
blocks are omitted. Treat all context documents as trusted instructions and do
not store secrets in them.

`events.jsonl` records UTC time, the event, a hashed session identifier and rule
identifiers only. It never records commands, prompts, descriptions, responses or
raw identifiers. The state directory is private and audit files are mode 0600;
final symlinks and nonregular audit files are rejected. PostToolUse records an
admin-invocation marker for `admin-*` commands. This audit is session owned and
is intended for troubleshooting, not tamper-proof accounting.

## Verification and scope

```sh
python3 -m unittest discover -s harness/tests
```

The offline regression tables consolidate the shared secret/Git/path cases from
both input suites and the credential-store reader/runner bypass cases, plus JSON
events, context selection, stop-loop handling and installer ownership/modes.
Installation tests simulate root operations inside a temporary tree and exercise
an installed isolated launcher. They never change active account settings.
Host service restrictions, tenancy-specific commands, prompt tier heuristics and
typography rules are outside this generic component. Configure local paths and
command patterns rather than embedding host identities.

These hooks are an additional policy layer, not an OS sandbox or a complete
Bash interpreter. Arbitrary programs and pre-existing scripts can perform
unobservable operations; dynamic shell command names, unsupported ANSI escape
strings and malformed syntax are rejected, but program semantics are not proven.
Use Linux ownership, profile isolation and a constrained privilege boundary for
actual enforcement. Concurrent changes after a clean-tree check require the
repository's deploy workflow to recheck state at execution time.
