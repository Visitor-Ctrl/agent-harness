# 06 — Install and operate the harness

Complete [profile isolation](05-profiles-and-herdr.md) and the [Vault boundary](04-secrets.md)
first. Installation is an administrator task from a reviewed, trusted checkout.
Linux and Python 3.11+ are required. Install the AI CLIs and herdr separately;
the harness never installs subscriptions, signs in, provisions clouds or enables
services automatically. The [command reference](../harness/README.md) describes
all components and configuration.

## Validate and install a release

From the repository root:

```sh
python3 -m unittest discover -s harness/tests
harness/install.sh --dry-run
sudo harness/install.sh
```

Both installer modes run the full offline Python suite **before any writes** and
refuse on failure. `harness/VERSION` names the immutable release directory under
`/opt/agent-harness/`. The installer copies libraries, commands, config examples,
templates, docs, tests, optional extras and the installer. Root owns the release;
commands/directories are 0755 and data is 0644. A single atomic symlink replacement
switches `current`. System units go to `/etc/systemd/system` and user units to
`/etc/systemd/user`; installation reloads the system manager, without enabling or
restarting services. Add `/opt/agent-harness/current/bin` to the operator/profile
PATH. `harness version` reports the selected release.

`--prefix /absolute/path` is available for an alternate administrator-owned
installation; copied unit command paths are adjusted to that prefix. Guard and
broker policy paths still need explicit review. Never install from an untrusted
checkout as root. Releases refuse overwrites. Inspect any partially copied
release or failed provisioning manually before retrying; installation is not a
whole-host transaction and does not revert users, files or unit changes on error.

## Create the first profile

Review the plan, then provision a **new** Linux user:

```sh
harness profile add research --root /srv/projects --dry-run
sudo harness profile add research --root /srv/projects \
  --memory-high 8G --cpu-weight 100 --tasks-max 4096
harness profile list
```

The default project parent is `HARNESS_ROOT`, otherwise `/srv/projects`.
Existing users or profile destinations are refused rather than adopted. The
command creates a home and project directory mode 0700, membership in
`agent-heavy`, a `user-<uid>.slice.d/50-agent-harness.conf` drop-in, private config
examples in `~/.config/agent-harness/`, and draft project instructions/checkpoints.
The administrator-owned inventory is `/etc/agent-harness/profiles/`. No sudo rule,
cloud role, sign-in or running coordinator is created. Complete placeholders in
the templates, adopt machine rules and obtain approval for NORTH-STAR.md.
The copied draft is not an approval record. Align the template worker cap with
`dispatch.toml`; the default is three.

Provision the machine-wide lock group/directory and reviewed
`/etc/agent-harness/heavy-lock.toml` as described in the
[heavy-lock reference](../harness/docs/dispatch.md). Use a root-owned tmpfiles
rule to recreate `/run/lock/agent-harness` with mode 2770 and group `agent-heavy`
after reboot; log in again after group changes. All profiles must share its
slot count and lock path. Slice defaults are starting values: choose limits that
fit your host, and verify effective cgroups before workloads start.

## Configure accounts and guard hooks

As the profile user, review each TOML in `~/.config/agent-harness/` and remove
unused examples/backends. `engines.toml` selects executable argv and account
environment variables; `pool.toml` selects account roots and priorities. Create
those private roots and sign in interactively **one account at a time**, using
the environment selector declared for that engine. For the supplied examples:

```sh
CLAUDE_CONFIG_DIR="$HOME/.local/share/agent-harness/accounts/claude/acct-a" claude
CODEX_HOME="$HOME/.local/share/agent-harness/accounts/codex/acct-a" codex login
GEMINI_CLI_HOME="$HOME/.local/share/agent-harness/accounts/gemini/acct-a" gemini
ai-pool probe --force
ai-pool status
harness doctor
```

Check these vendor flags against the installed CLI release; the examples are
configuration templates and offline tests use fake engines. Repeat sign-in for
additional accounts, including the reserved coordinator account. Never copy
credentials between Linux profiles. `doctor` checks local dependencies and TOML
syntax; it does not prove authentication, active hooks, secret access or isolation.
See [engine configuration](07-ai-engines.md) for model selection and pool policy.

An administrator reviews `guard.toml`, installs the guard with `guard-install`
into a new root-owned version directory, then distributes the generated client
settings through the owner-managed account workflow. The full
[guard installation reference](../harness/docs/guard.md) covers hook events,
settings merging and acceptance tests. Copying example settings alone does not
activate hooks. Point coordinator health at the **installed** launcher and policy.
Other engines need their own supported enforcement integration; the supplied
hooks use the Claude Code event protocol.

Install the root-owned broker policy with actual numeric profile UIDs and allowed
Vault secret IDs at `/etc/agent-harness/secrets.toml` mode 0600. Complete the
[broker reference](../harness/docs/secrets-and-watchers.md) and chapter 04's
metadata-isolation tests before starting `secret-broker.service`. Configure notify
with only intended transports and test delivery without secret content.

## Start a coordinator and dispatch one task

As the profile user, start herdr using [chapter 05](05-profiles-and-herdr.md).
Set `herdr.toml`'s project cwd and session names. Run:

```sh
herdr-ensure-workspace --cwd /srv/projects/research
herdr-ensure-coordinator --cwd /srv/projects/research
```

Inspect the coordinator tab through herdr and record its actual pane ID in
`coordinator.toml`, along with `engine`, a concrete `model`, project cwd and
`remote_name` matching `herdr.toml`'s coordinator session. In that pane run
`coordinator --once` for the first bounded turn; inspect state and the handover,
then use `coordinator` for the continuous loop. The helper only ensures a tab:
it does not discover the pane ID or supervise a crashed launcher. Leave
`coordinator_command` unset until the configured pane exists. Rotation and
failover preserve that pane and remote-control session name.

From the profile, dispatch a harmless task in the approved project:

```sh
dispatch start first-check --engine codex --model sample-model \
  --cwd /srv/projects/research \
  --task 'Inspect project instructions, write the requested handover, and end with ## Summary.'
dispatch status first-check
dispatch summary first-check
harness-watch --ack first-check
```

Replace `sample-model` with an actual supported model ID. Launch success means
submission; poll status until terminal. Inspect the attempt handover and final
Summary, then assign an independent reviewer. A continuation uses
`dispatch continue` with an explicit engine/model and the previous handover;
it launches a fresh worker. Use `--heavy` for resource-heavy jobs. See
[messaging and artifacts](09-agent-messaging.md).

After configuration, reload the profile's user manager and explicitly enable
only the wanted timers:

```sh
systemctl --user daemon-reload
systemctl --user enable --now ai-pool-probe.timer harness-watch.timer
systemctl --user enable --now coordinator-watch.timer coordinator-health.timer
```

Persistent user services require the administrator's reviewed linger setup.
The watcher runs every two minutes and health runs daily; neither starts the
launcher. Verify notifications, stopped-job handling, pool reservation and
checkpoint rotation locally before unattended use.

## Upgrade and rollback

Review a new release, choose a new VERSION and run the dry-run/tests again.
Stop coordinators and wait for workers to finish before installing; running
Python processes keep old imported code while new commands use `current`.
Restart only after inspecting configs and unit changes. Per-profile config,
credentials and state are never overwritten by a release upgrade.

```sh
harness/install.sh --dry-run
sudo harness/install.sh
sudo /opt/agent-harness/current/install.sh --rollback 0.1.0
```

Use an actually retained release version for rollback. Rollback verifies the
retained tree's root ownership/modes and runs its tests before switching
`current`, reinstalls that release's supplied unit files and reloads the system
manager. Reload user managers too. It does not reverse config/state migrations,
remove units introduced by newer versions, or restart running processes. Review
compatibility and disable obsolete units explicitly. Keep previous releases until
acceptance succeeds; do not delete a version used by a running process.

Acceptance: observe a passing offline suite, root-owned release and policies,
private profile paths, effective slice limits, denied cross-profile access,
working hooks, broker UID rejection, a completed dispatch with handover, and
verified alert delivery. Live cloud isolation remains an operator acceptance gate.
