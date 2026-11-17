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
| `blocked_commands` | Globs over normalized command name and arguments; matching operations block |
| `warning_commands` | Globs over normalized command name and arguments; matching operations warn |

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
Generated programs through `xargs`, executable `find` actions, piped shell
programs, shell script substitutions, shell function declarations and `env -S`
are rejected conservatively. Use explicit commands or reviewed script files.
BusyBox applets and the common POSIX shells (including `ash`) are inspected.
Python inline programs support literal paths and constant string concatenation;
dynamic execution and computed open paths require reviewed scripts. Other
interpreters receive heuristic path/write/env checks, not semantic validation.
Use Linux ownership, profile isolation and a constrained privilege boundary for
actual enforcement. Concurrent changes after a clean-tree check require the
repository's deploy workflow to recheck state at execution time.

## Input case review inventory

Each static policy table row is counted once, including both sides of profile conditionals and duplicates across suites. Suite A and B identify the reviewed snapshots without private names; line numbers refer to those input snapshots. Category (a) is applicable and ported; (b) is an intentionally omitted integration or host workflow.

Policy rows: 455 reviewed; 341 category (a), 114 category (b). All applicable rows are in `test_guard_ported.py`.

Adaptations: names and paths use generic fixtures. Clean deploys allow silently. Remote token transfer uses a remote consumer. Temporary credential test directories must exist. Destructive host commands remain applicable.

| Case | Input line | Category | Disposition |
| --- | --- | --- | --- |
| suite-a-001 | 59 | a | ported |
| suite-a-002 | 60 | a | ported |
| suite-a-003 | 61 | a | ported |
| suite-a-004 | 62 | a | ported |
| suite-a-005 | 63 | a | ported |
| suite-a-006 | 64 | a | ported |
| suite-a-007 | 65 | a | ported |
| suite-a-008 | 66 | b | Vendor or engine tenancy policy |
| suite-a-009 | 67 | a | ported |
| suite-a-010 | 68 | a | ported |
| suite-a-011 | 69 | a | ported |
| suite-a-012 | 70 | a | ported |
| suite-a-013 | 71 | a | ported |
| suite-a-014 | 72 | a | ported |
| suite-a-015 | 73 | a | ported |
| suite-a-016 | 74 | a | ported |
| suite-a-017 | 75 | a | ported |
| suite-a-018 | 76 | a | ported |
| suite-a-019 | 77 | a | ported |
| suite-a-020 | 78 | a | ported |
| suite-a-021 | 79 | a | ported |
| suite-a-022 | 80 | a | ported |
| suite-a-023 | 81 | a | ported |
| suite-a-024 | 82 | a | ported |
| suite-a-025 | 83 | a | ported |
| suite-a-026 | 85 | a | ported |
| suite-a-027 | 86 | a | ported |
| suite-a-028 | 87 | a | ported |
| suite-a-029 | 88 | b | Vendor or engine tenancy policy |
| suite-a-030 | 89 | a | ported |
| suite-a-031 | 90 | a | ported |
| suite-a-032 | 91 | a | ported |
| suite-a-033 | 92 | a | ported |
| suite-a-034 | 93 | a | ported |
| suite-a-035 | 94 | a | ported |
| suite-a-036 | 95 | a | ported |
| suite-a-037 | 96 | a | ported |
| suite-a-038 | 97 | a | ported |
| suite-a-039 | 98 | a | ported |
| suite-a-040 | 99 | a | ported |
| suite-a-041 | 100 | a | ported |
| suite-a-042 | 101 | a | ported |
| suite-a-043 | 102 | a | ported |
| suite-a-044 | 103 | a | ported |
| suite-a-045 | 104 | a | ported |
| suite-a-046 | 105 | a | ported |
| suite-a-047 | 106 | a | ported |
| suite-a-048 | 107 | a | ported |
| suite-a-049 | 108 | a | ported |
| suite-a-050 | 109 | a | ported |
| suite-a-051 | 110 | a | ported |
| suite-a-052 | 112 | a | ported |
| suite-a-053 | 113 | a | ported |
| suite-a-054 | 114 | a | ported |
| suite-a-055 | 115 | a | ported |
| suite-a-056 | 116 | a | ported |
| suite-a-057 | 117 | a | ported |
| suite-a-058 | 118 | a | ported |
| suite-a-059 | 119 | a | ported |
| suite-a-060 | 120 | a | ported |
| suite-a-061 | 121 | a | ported |
| suite-a-062 | 122 | a | ported |
| suite-a-063 | 123 | a | ported |
| suite-a-064 | 124 | a | ported |
| suite-a-065 | 125 | a | ported |
| suite-a-066 | 126 | a | ported |
| suite-a-067 | 127 | a | ported |
| suite-a-068 | 128 | a | ported |
| suite-a-069 | 129 | a | ported |
| suite-a-070 | 130 | a | ported |
| suite-a-071 | 131 | a | ported |
| suite-a-072 | 132 | a | ported |
| suite-a-073 | 133 | a | ported |
| suite-a-074 | 134 | a | ported |
| suite-a-075 | 136 | a | ported |
| suite-a-076 | 137 | a | ported |
| suite-a-077 | 138 | a | ported |
| suite-a-078 | 139 | a | ported |
| suite-a-079 | 140 | a | ported |
| suite-a-080 | 141 | a | ported |
| suite-a-081 | 142 | a | ported |
| suite-a-082 | 143 | a | ported |
| suite-a-083 | 144 | a | ported |
| suite-a-084 | 145 | a | ported |
| suite-a-085 | 146 | a | ported |
| suite-a-086 | 147 | a | ported |
| suite-a-087 | 148 | a | ported |
| suite-a-088 | 149 | a | ported |
| suite-a-089 | 150 | a | ported |
| suite-a-090 | 151 | a | ported |
| suite-a-091 | 152 | a | ported |
| suite-a-092 | 153 | a | ported |
| suite-a-093 | 154 | a | ported |
| suite-a-094 | 155 | a | ported |
| suite-a-095 | 156 | a | ported |
| suite-a-096 | 157 | a | ported |
| suite-a-097 | 158 | a | ported |
| suite-a-098 | 159 | a | ported |
| suite-a-099 | 160 | a | ported |
| suite-a-100 | 161 | a | ported |
| suite-a-101 | 162 | a | ported |
| suite-a-102 | 163 | a | ported |
| suite-a-103 | 164 | a | ported |
| suite-a-104 | 165 | a | ported |
| suite-a-105 | 166 | a | ported |
| suite-a-106 | 167 | a | ported |
| suite-a-107 | 169 | a | ported |
| suite-a-108 | 170 | a | ported |
| suite-a-109 | 171 | a | ported |
| suite-a-110 | 172 | a | ported |
| suite-a-111 | 173 | b | Vendor API or project operation policy |
| suite-a-112 | 174 | b | Vendor API or project operation policy |
| suite-a-113 | 175 | b | Vendor API or project operation policy |
| suite-a-114 | 176 | b | Vendor API or project operation policy |
| suite-a-115 | 177 | b | Vendor API or project operation policy |
| suite-a-116 | 178 | a | ported |
| suite-a-117 | 179 | b | Vendor API or project operation policy |
| suite-a-118 | 180 | b | Vendor API or project operation policy |
| suite-a-119 | 181 | a | ported |
| suite-a-120 | 182 | a | ported |
| suite-a-121 | 183 | a | ported |
| suite-a-122 | 184 | b | Vendor API or project operation policy |
| suite-a-123 | 185 | b | Vendor API or project operation policy |
| suite-a-124 | 186 | a | ported |
| suite-a-125 | 187 | a | ported |
| suite-a-126 | 188 | b | Vendor API or project operation policy |
| suite-a-127 | 189 | b | Vendor API or project operation policy |
| suite-a-128 | 191 | a | ported |
| suite-a-129 | 192 | a | ported |
| suite-a-130 | 193 | a | ported |
| suite-a-131 | 194 | a | ported |
| suite-a-132 | 195 | a | ported |
| suite-a-133 | 196 | a | ported |
| suite-a-134 | 197 | a | ported |
| suite-a-135 | 198 | a | ported |
| suite-a-136 | 200 | a | ported |
| suite-a-137 | 201 | a | ported |
| suite-a-138 | 202 | a | ported |
| suite-a-139 | 203 | a | ported |
| suite-a-140 | 204 | a | ported |
| suite-a-141 | 205 | a | ported |
| suite-a-142 | 207 | a | ported |
| suite-a-143 | 208 | a | ported |
| suite-a-144 | 209 | a | ported |
| suite-a-145 | 210 | a | ported |
| suite-a-146 | 211 | a | ported |
| suite-a-147 | 212 | a | ported |
| suite-a-148 | 213 | a | ported |
| suite-a-149 | 214 | a | ported |
| suite-a-150 | 215 | a | ported |
| suite-a-151 | 216 | a | ported |
| suite-a-152 | 217 | a | ported |
| suite-a-153 | 218 | a | ported |
| suite-a-154 | 219 | a | ported |
| suite-a-155 | 220 | a | ported |
| suite-a-156 | 223 | b | Vendor or engine tenancy policy |
| suite-a-157 | 223 | b | Vendor or engine tenancy policy |
| suite-a-158 | 223 | b | Vendor or engine tenancy policy |
| suite-a-159 | 225 | b | Vendor or engine tenancy policy |
| suite-a-160 | 228 | b | Vendor or engine tenancy policy |
| suite-a-161 | 228 | b | Vendor or engine tenancy policy |
| suite-a-162 | 228 | b | Vendor or engine tenancy policy |
| suite-a-163 | 229 | b | Vendor or engine tenancy policy |
| suite-a-164 | 229 | b | Vendor or engine tenancy policy |
| suite-a-165 | 229 | b | Vendor or engine tenancy policy |
| suite-a-166 | 230 | b | Vendor or engine tenancy policy |
| suite-a-167 | 230 | b | Vendor or engine tenancy policy |
| suite-a-168 | 231 | b | Vendor or engine tenancy policy |
| suite-a-169 | 231 | b | Vendor or engine tenancy policy |
| suite-a-170 | 232 | b | Vendor or engine tenancy policy |
| suite-a-171 | 232 | b | Vendor or engine tenancy policy |
| suite-a-172 | 232 | b | Vendor or engine tenancy policy |
| suite-a-173 | 233 | b | Vendor or engine tenancy policy |
| suite-a-174 | 233 | b | Vendor or engine tenancy policy |
| suite-a-175 | 234 | b | Vendor or engine tenancy policy |
| suite-a-176 | 238 | b | Vendor or engine tenancy policy |
| suite-a-177 | 238 | b | Vendor or engine tenancy policy |
| suite-a-178 | 238 | b | Vendor or engine tenancy policy |
| suite-a-179 | 238 | b | Vendor or engine tenancy policy |
| suite-a-180 | 239 | b | Vendor or engine tenancy policy |
| suite-a-181 | 239 | b | Vendor or engine tenancy policy |
| suite-a-182 | 240 | a | ported |
| suite-a-183 | 242 | b | Vendor or engine tenancy policy |
| suite-a-184 | 242 | b | Vendor or engine tenancy policy |
| suite-a-185 | 242 | b | Vendor or engine tenancy policy |
| suite-a-186 | 243 | b | Vendor or engine tenancy policy |
| suite-a-187 | 243 | b | Vendor or engine tenancy policy |
| suite-a-188 | 244 | b | Vendor or engine tenancy policy |
| suite-a-189 | 245 | b | Vendor or engine tenancy policy |
| suite-a-190 | 246 | b | Vendor or engine tenancy policy |
| suite-a-191 | 247 | b | Vendor or engine tenancy policy |
| suite-a-192 | 247 | b | Vendor or engine tenancy policy |
| suite-a-193 | 247 | b | Vendor or engine tenancy policy |
| suite-a-194 | 248 | b | Vendor or engine tenancy policy |
| suite-a-195 | 249 | b | Vendor or engine tenancy policy |
| suite-a-196 | 250 | b | Vendor or engine tenancy policy |
| suite-a-197 | 253 | a | ported |
| suite-a-198 | 254 | a | ported |
| suite-a-199 | 255 | a | ported |
| suite-a-200 | 256 | a | ported |
| suite-a-201 | 257 | a | ported |
| suite-a-202 | 258 | b | Prose typography policy intentionally omitted |
| suite-a-203 | 259 | b | Prose typography policy intentionally omitted |
| suite-a-204 | 260 | b | Prose typography policy intentionally omitted |
| suite-a-205 | 261 | a | ported |
| suite-a-206 | 262 | a | ported |
| suite-a-207 | 263 | a | ported |
| suite-a-208 | 264 | a | ported |
| suite-a-209 | 265 | a | ported |
| suite-a-210 | 267 | a | ported |
| suite-a-211 | 269 | a | ported |
| suite-a-212 | 270 | a | ported |
| suite-a-213 | 271 | a | ported |
| suite-b-001 | 58 | a | ported |
| suite-b-002 | 59 | a | ported |
| suite-b-003 | 60 | a | ported |
| suite-b-004 | 62 | a | ported |
| suite-b-005 | 63 | a | ported |
| suite-b-006 | 64 | a | ported |
| suite-b-007 | 65 | a | ported |
| suite-b-008 | 66 | a | ported |
| suite-b-009 | 68 | a | ported |
| suite-b-010 | 69 | a | ported |
| suite-b-011 | 70 | a | ported |
| suite-b-012 | 71 | a | ported |
| suite-b-013 | 72 | a | ported |
| suite-b-014 | 73 | a | ported |
| suite-b-015 | 74 | a | ported |
| suite-b-016 | 75 | a | ported |
| suite-b-017 | 76 | a | ported |
| suite-b-018 | 78 | a | ported |
| suite-b-019 | 79 | a | ported |
| suite-b-020 | 80 | a | ported |
| suite-b-021 | 81 | a | ported |
| suite-b-022 | 82 | b | Vendor or engine tenancy policy |
| suite-b-023 | 83 | a | ported |
| suite-b-024 | 84 | a | ported |
| suite-b-025 | 85 | a | ported |
| suite-b-026 | 86 | a | ported |
| suite-b-027 | 87 | b | Vendor or engine tenancy policy |
| suite-b-028 | 88 | a | ported |
| suite-b-029 | 89 | a | ported |
| suite-b-030 | 90 | a | ported |
| suite-b-031 | 92 | a | ported |
| suite-b-032 | 93 | a | ported |
| suite-b-033 | 94 | a | ported |
| suite-b-034 | 95 | a | ported |
| suite-b-035 | 96 | a | ported |
| suite-b-036 | 97 | a | ported |
| suite-b-037 | 98 | a | ported |
| suite-b-038 | 99 | b | Vendor or engine tenancy policy |
| suite-b-039 | 100 | a | ported |
| suite-b-040 | 101 | a | ported |
| suite-b-041 | 102 | a | ported |
| suite-b-042 | 103 | a | ported |
| suite-b-043 | 104 | a | ported |
| suite-b-044 | 105 | a | ported |
| suite-b-045 | 106 | a | ported |
| suite-b-046 | 107 | a | ported |
| suite-b-047 | 108 | a | ported |
| suite-b-048 | 109 | a | ported |
| suite-b-049 | 110 | a | ported |
| suite-b-050 | 111 | a | ported |
| suite-b-051 | 112 | a | ported |
| suite-b-052 | 113 | a | ported |
| suite-b-053 | 114 | a | ported |
| suite-b-054 | 115 | a | ported |
| suite-b-055 | 116 | a | ported |
| suite-b-056 | 118 | a | ported |
| suite-b-057 | 119 | a | ported |
| suite-b-058 | 120 | a | ported |
| suite-b-059 | 121 | b | Vendor or engine tenancy policy |
| suite-b-060 | 122 | a | ported |
| suite-b-061 | 123 | a | ported |
| suite-b-062 | 124 | a | ported |
| suite-b-063 | 125 | a | ported |
| suite-b-064 | 126 | a | ported |
| suite-b-065 | 127 | a | ported |
| suite-b-066 | 128 | a | ported |
| suite-b-067 | 129 | a | ported |
| suite-b-068 | 130 | a | ported |
| suite-b-069 | 131 | a | ported |
| suite-b-070 | 132 | a | ported |
| suite-b-071 | 133 | a | ported |
| suite-b-072 | 134 | a | ported |
| suite-b-073 | 135 | a | ported |
| suite-b-074 | 136 | a | ported |
| suite-b-075 | 137 | a | ported |
| suite-b-076 | 138 | a | ported |
| suite-b-077 | 139 | a | ported |
| suite-b-078 | 140 | a | ported |
| suite-b-079 | 141 | a | ported |
| suite-b-080 | 142 | a | ported |
| suite-b-081 | 143 | a | ported |
| suite-b-082 | 145 | a | ported |
| suite-b-083 | 146 | a | ported |
| suite-b-084 | 147 | a | ported |
| suite-b-085 | 148 | a | ported |
| suite-b-086 | 149 | a | ported |
| suite-b-087 | 150 | a | ported |
| suite-b-088 | 151 | a | ported |
| suite-b-089 | 152 | a | ported |
| suite-b-090 | 153 | a | ported |
| suite-b-091 | 154 | a | ported |
| suite-b-092 | 155 | a | ported |
| suite-b-093 | 156 | a | ported |
| suite-b-094 | 157 | a | ported |
| suite-b-095 | 158 | a | ported |
| suite-b-096 | 159 | a | ported |
| suite-b-097 | 160 | a | ported |
| suite-b-098 | 161 | a | ported |
| suite-b-099 | 162 | a | ported |
| suite-b-100 | 163 | a | ported |
| suite-b-101 | 164 | a | ported |
| suite-b-102 | 165 | a | ported |
| suite-b-103 | 166 | a | ported |
| suite-b-104 | 167 | a | ported |
| suite-b-105 | 169 | a | ported |
| suite-b-106 | 170 | a | ported |
| suite-b-107 | 171 | a | ported |
| suite-b-108 | 172 | a | ported |
| suite-b-109 | 173 | a | ported |
| suite-b-110 | 174 | a | ported |
| suite-b-111 | 175 | a | ported |
| suite-b-112 | 176 | a | ported |
| suite-b-113 | 177 | a | ported |
| suite-b-114 | 178 | a | ported |
| suite-b-115 | 179 | a | ported |
| suite-b-116 | 180 | a | ported |
| suite-b-117 | 181 | a | ported |
| suite-b-118 | 182 | a | ported |
| suite-b-119 | 183 | a | ported |
| suite-b-120 | 184 | a | ported |
| suite-b-121 | 185 | a | ported |
| suite-b-122 | 186 | a | ported |
| suite-b-123 | 187 | a | ported |
| suite-b-124 | 188 | a | ported |
| suite-b-125 | 189 | a | ported |
| suite-b-126 | 190 | a | ported |
| suite-b-127 | 191 | a | ported |
| suite-b-128 | 192 | a | ported |
| suite-b-129 | 193 | a | ported |
| suite-b-130 | 194 | a | ported |
| suite-b-131 | 195 | a | ported |
| suite-b-132 | 196 | a | ported |
| suite-b-133 | 197 | a | ported |
| suite-b-134 | 198 | a | ported |
| suite-b-135 | 199 | a | ported |
| suite-b-136 | 200 | a | ported |
| suite-b-137 | 202 | a | ported |
| suite-b-138 | 203 | a | ported |
| suite-b-139 | 204 | a | ported |
| suite-b-140 | 205 | a | ported |
| suite-b-141 | 206 | b | Vendor API or project operation policy |
| suite-b-142 | 207 | b | Vendor API or project operation policy |
| suite-b-143 | 208 | b | Vendor API or project operation policy |
| suite-b-144 | 209 | b | Vendor API or project operation policy |
| suite-b-145 | 210 | b | Vendor API or project operation policy |
| suite-b-146 | 211 | a | ported |
| suite-b-147 | 212 | b | Vendor API or project operation policy |
| suite-b-148 | 213 | b | Vendor API or project operation policy |
| suite-b-149 | 214 | a | ported |
| suite-b-150 | 215 | a | ported |
| suite-b-151 | 216 | a | ported |
| suite-b-152 | 217 | b | Vendor API or project operation policy |
| suite-b-153 | 218 | b | Vendor API or project operation policy |
| suite-b-154 | 219 | a | ported |
| suite-b-155 | 220 | a | ported |
| suite-b-156 | 221 | b | Vendor API or project operation policy |
| suite-b-157 | 222 | b | Vendor API or project operation policy |
| suite-b-158 | 224 | a | ported |
| suite-b-159 | 225 | a | ported |
| suite-b-160 | 226 | a | ported |
| suite-b-161 | 228 | a | ported |
| suite-b-162 | 229 | a | ported |
| suite-b-163 | 230 | a | ported |
| suite-b-164 | 231 | a | ported |
| suite-b-165 | 232 | a | ported |
| suite-b-166 | 233 | a | ported |
| suite-b-167 | 235 | a | ported |
| suite-b-168 | 236 | a | ported |
| suite-b-169 | 237 | a | ported |
| suite-b-170 | 238 | a | ported |
| suite-b-171 | 239 | a | ported |
| suite-b-172 | 240 | a | ported |
| suite-b-173 | 241 | a | ported |
| suite-b-174 | 242 | a | ported |
| suite-b-175 | 243 | a | ported |
| suite-b-176 | 244 | a | ported |
| suite-b-177 | 245 | a | ported |
| suite-b-178 | 246 | a | ported |
| suite-b-179 | 247 | a | ported |
| suite-b-180 | 248 | a | ported |
| suite-b-181 | 251 | b | Vendor or engine tenancy policy |
| suite-b-182 | 251 | b | Vendor or engine tenancy policy |
| suite-b-183 | 251 | b | Vendor or engine tenancy policy |
| suite-b-184 | 253 | b | Vendor or engine tenancy policy |
| suite-b-185 | 256 | b | Vendor or engine tenancy policy |
| suite-b-186 | 256 | b | Vendor or engine tenancy policy |
| suite-b-187 | 256 | b | Vendor or engine tenancy policy |
| suite-b-188 | 257 | b | Vendor or engine tenancy policy |
| suite-b-189 | 257 | b | Vendor or engine tenancy policy |
| suite-b-190 | 257 | b | Vendor or engine tenancy policy |
| suite-b-191 | 258 | b | Vendor or engine tenancy policy |
| suite-b-192 | 258 | b | Vendor or engine tenancy policy |
| suite-b-193 | 259 | b | Vendor or engine tenancy policy |
| suite-b-194 | 259 | b | Vendor or engine tenancy policy |
| suite-b-195 | 260 | b | Vendor or engine tenancy policy |
| suite-b-196 | 260 | b | Vendor or engine tenancy policy |
| suite-b-197 | 260 | b | Vendor or engine tenancy policy |
| suite-b-198 | 261 | b | Vendor or engine tenancy policy |
| suite-b-199 | 261 | b | Vendor or engine tenancy policy |
| suite-b-200 | 262 | b | Vendor or engine tenancy policy |
| suite-b-201 | 266 | b | Vendor or engine tenancy policy |
| suite-b-202 | 266 | b | Vendor or engine tenancy policy |
| suite-b-203 | 266 | b | Vendor or engine tenancy policy |
| suite-b-204 | 266 | b | Vendor or engine tenancy policy |
| suite-b-205 | 267 | b | Vendor or engine tenancy policy |
| suite-b-206 | 267 | b | Vendor or engine tenancy policy |
| suite-b-207 | 268 | a | ported |
| suite-b-208 | 270 | b | Vendor or engine tenancy policy |
| suite-b-209 | 270 | b | Vendor or engine tenancy policy |
| suite-b-210 | 270 | b | Vendor or engine tenancy policy |
| suite-b-211 | 271 | b | Vendor or engine tenancy policy |
| suite-b-212 | 271 | b | Vendor or engine tenancy policy |
| suite-b-213 | 272 | b | Vendor or engine tenancy policy |
| suite-b-214 | 273 | b | Vendor or engine tenancy policy |
| suite-b-215 | 274 | b | Vendor or engine tenancy policy |
| suite-b-216 | 275 | b | Vendor or engine tenancy policy |
| suite-b-217 | 275 | b | Vendor or engine tenancy policy |
| suite-b-218 | 275 | b | Vendor or engine tenancy policy |
| suite-b-219 | 276 | b | Vendor or engine tenancy policy |
| suite-b-220 | 277 | b | Vendor or engine tenancy policy |
| suite-b-221 | 278 | b | Vendor or engine tenancy policy |
| suite-b-222 | 279 | a | ported |
| suite-b-223 | 279 | a | ported |
| suite-b-224 | 282 | a | ported |
| suite-b-225 | 283 | a | ported |
| suite-b-226 | 284 | a | ported |
| suite-b-227 | 285 | a | ported |
| suite-b-228 | 286 | a | ported |
| suite-b-229 | 287 | a | ported |
| suite-b-230 | 288 | a | ported |
| suite-b-231 | 289 | b | Prose typography policy intentionally omitted |
| suite-b-232 | 290 | b | Prose typography policy intentionally omitted |
| suite-b-233 | 291 | b | Prose typography policy intentionally omitted |
| suite-b-234 | 292 | a | ported |
| suite-b-235 | 293 | a | ported |
| suite-b-236 | 294 | a | ported |
| suite-b-237 | 295 | a | ported |
| suite-b-238 | 296 | a | ported |
| suite-b-239 | 298 | a | ported |
| suite-b-240 | 300 | a | ported |
| suite-b-241 | 301 | a | ported |
| suite-b-242 | 302 | a | ported |

Every remaining event/protocol check was reviewed as well. Malformed JSON now fails closed, missing models block, and session records use a private audit hash. Coordinator role/tier heuristics are intentionally omitted. The two audit-isolation probes use configured state and trusted explicit configuration.

| Case | Input line | Category | Disposition |
| --- | --- | --- | --- |
| suite-a-event-01 | 322 | a | ported with generic protocol |
| suite-a-event-02 | 324 | a | ported with generic protocol |
| suite-a-event-03 | 326 | a | ported with generic protocol |
| suite-a-event-04 | 329 | a | ported with generic protocol |
| suite-a-event-05 | 330 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-06 | 332 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-07 | 335 | a | ported with generic protocol |
| suite-a-event-08 | 339 | a | ported with generic protocol |
| suite-a-event-09 | 343 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-10 | 345 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-11 | 346 | a | ported with generic protocol |
| suite-a-event-12 | 347 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-13 | 349 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-14 | 351 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-15 | 353 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-16 | 355 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-17 | 357 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-18 | 359 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-19 | 363 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-20 | 367 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-21 | 371 | a | ported with generic protocol |
| suite-a-event-22 | 375 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-a-event-23 | 377 | a | ported with generic protocol |
| suite-a-event-24 | 379 | a | ported with generic protocol |
| suite-a-event-25 | 382 | a | ported with generic protocol |
| suite-a-event-26 | 384 | a | ported with generic protocol |
| suite-a-event-27 | 386 | a | ported with generic protocol |
| suite-a-event-28 | 387 | a | ported with generic protocol |
| suite-a-event-extra-01 | 402 | a | ported with isolated audit and trusted config |
| suite-a-event-extra-02 | 409 | a | ported with isolated audit and trusted config |
| suite-a-event-extra-03 | 415 | a | ported with isolated audit and trusted config |
| suite-a-event-extra-04 | 421 | a | ported with isolated audit and trusted config |
| suite-b-event-01 | 340 | a | ported with generic protocol |
| suite-b-event-02 | 342 | a | ported with generic protocol |
| suite-b-event-03 | 344 | a | ported with generic protocol |
| suite-b-event-04 | 347 | a | ported with generic protocol |
| suite-b-event-05 | 348 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-b-event-06 | 350 | b | Coordinator role or prompt tier workflow intentionally omitted |
| suite-b-event-07 | 353 | a | ported with generic protocol |
| suite-b-event-08 | 357 | a | ported with generic protocol |
| suite-b-event-09 | 361 | a | ported with generic protocol |
| suite-b-event-10 | 363 | a | ported with generic protocol |
| suite-b-event-11 | 366 | a | ported with generic protocol |
| suite-b-event-12 | 368 | a | ported with generic protocol |
| suite-b-event-13 | 370 | a | ported with generic protocol |
| suite-b-event-14 | 371 | a | ported with generic protocol |
| suite-b-event-extra-01 | 386 | a | ported with isolated audit and trusted config |
| suite-b-event-extra-02 | 393 | a | ported with isolated audit and trusted config |

## Security review results

Across both snapshots, **503 checks were classified: 373 applicable (a),
130 intentionally omitted (b)**. All applicable checks are ported: 341 policy
rows and 32 protocol/lifecycle cases, with duplicates retained for traceability.
The omissions cover vendor tenancy tools/APIs, endpoint products, engine bans,
project-specific operations, prose typography, coordinator role/rotation and
prompt-tier workflows. Generic host destruction and service controls are retained
using configurable command patterns. No private input command is shipped.

Against the tracked pre-change implementation, 34 of the final 341 policy rows
failed, and both merge-warning protocol rows lacked their expected warnings.
The first exploratory run reported 28 failures before fixture corrections and
before the host-service cases were added. The final adversarial table has 44
command probes plus two symlink probes; 23 probes failed their expectations
against the tracked baseline. One attempted descriptor bypass is correctly
allowed because its last redirect writes stdout to a file.

Fixed gaps include generated shell stdin, `xargs` and executable `find`, BusyBox
applets, absolute secret-command names, output process substitutions, inline
interpreter env dumps, concatenated Python secret paths and encoded execution,
combined Git force flags, attached `git -C`, case-insensitive hook configuration,
Git aliases/config environment injection, and `env -S`. Runtime-generated shell
scripts and unsupported function declarations fail closed. All supplied sandbox
overrides must now qualify as temporary; one cannot mask a persistent override.

The probes never execute dangerous commands; tests call the analyzer and use
isolated Git/audit fixtures. Arbitrary pre-existing scripts, runtime variable
values and general interpreter semantics remain outside static analysis. OS
ownership and isolation remain necessary; this review does not establish a
complete shell sandbox.

Verification: `python3 -m unittest discover -s harness/tests` passes all 78
methods, including the imported rows, protocol cases, adversarial probes and
configuration controls. The forbidden-identifier and IPv4 scans over the guard,
example policy, guard tests and this document return no matches; `git diff
--check` passes. The full suite uses local mocks and temporary fixtures and took
about 16 seconds; it performs no deployment or cloud operation.
