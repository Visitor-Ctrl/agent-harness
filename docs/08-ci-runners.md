# 08 — ARM64 CI runners

Self-hosted jobs execute arbitrary repository code. Use this shared VM only for trusted contributors in private repositories. Fork PRs and all public-repository PRs go to GitHub-hosted runners. A workflow `if` expression alone is not a security boundary: PR authors may modify workflows. Do not register this host with repositories whose writers you do not trust. See [GitHub's self-hosted warning](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners) and [secure use guidance](https://docs.github.com/en/actions/reference/security/secure-use).

## 1. Create a dedicated user

```sh
sudo adduser --disabled-password --gecos '' ghrunner
sudo chmod 700 /home/ghrunner
sudo usermod -aG heavy ghrunner
sudo loginctl enable-linger ghrunner
```

Give it no sudo, Docker socket, profile group membership, SSH private keys, Vault bundle permissions or deployment credentials. Apply the metadata restriction from chapter 04. Set `/etc/systemd/system/user-<runner-uid>.slice.d/resources.conf`:

```ini
[Slice]
CPUWeight=20
IOWeight=20
MemoryHigh=1536M
MemoryMax=2G
MemorySwapMax=1G
TasksMax=512
ManagedOOMMemoryPressure=kill
```

Run `sudo systemctl daemon-reload`. Start with **one runner** on this host; agent memory takes priority. Run heavy builds through the harness's `heavy-lock` after chapter 06 supplies it. Verify its CLI with `heavy-lock --help` and document the actual invocation in your project's scripts. No project may silently bypass the shared lock. It coordinates only processes using the same host lock.

## 2. Download and register

In GitHub **Repository → Settings → Actions → Runners → New self-hosted runner**, choose **Linux, ARM64**. As ghrunner in `/home/ghrunner/runner-1`, execute the download, checksum verification and extraction commands shown there. Use the current supported release; do not copy an old download URL from a blog. Missing OS dependencies must be installed by the administrator using the runner's documented dependency installer, never by granting ghrunner sudo.

Run `./config.sh --url "$REPOSITORY_URL" --name arm-pr-1 --labels pr` where `REPOSITORY_URL` is your intended repository URL. Enter the short-lived registration token at the interactive prompt; do not paste it into history or a checked-in script. Keep default labels: **self-hosted, Linux, ARM64**, plus **pr**. Do not use `--no-default-labels`. Registration-time labels avoid queued jobs failing to match labels added later through the API; if changing labels, recheck every selector and cancel/requeue affected jobs. Confirm the runner's labels in GitHub before queuing work.

Each extra runner requires its own directory, name, unit, memory allowance and clean work area. Multiple runners on the same VM share the same kernel and resources.

## 3. Run as a user systemd service

As ghrunner, create `~/.config/systemd/user/actions-runner-1.service`:

```ini
[Unit]
Description=Trusted ARM64 PR runner
After=network-online.target
[Service]
WorkingDirectory=/home/ghrunner/runner-1
ExecStart=/home/ghrunner/runner-1/run.sh
Restart=always
RestartSec=10
UMask=0077
NoNewPrivileges=yes
[Install]
WantedBy=default.target
```

From an administrator shell use `sudo -iu ghrunner`, then set `XDG_RUNTIME_DIR=/run/user/$(id -u)` if the user bus is not discovered automatically. Run `systemctl --user daemon-reload` and `systemctl --user enable --now actions-runner-1`. Check `systemctl --user status actions-runner-1` and GitHub's Idle state. Avoid `svc.sh install`, which creates a system service rather than this user unit. Do not run runner software as root. Confirm `systemd-cgls` places it under the runner UID's limited slice.

## 4. Route and shard trusted checks

Before enabling self-hosted routing, restrict the runner group to approved repositories/workflows where your GitHub plan supports that. Protect workflow changes with required review. For public repositories use `ubuntu-latest` instead. A trusted-private-repo job can include this routing guard:

```yaml
on: pull_request
permissions:
  contents: read
jobs:
  test:
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: [self-hosted, Linux, ARM64, pr]
    strategy:
      fail-fast: false
      matrix:
        shard: [1]
    env:
      SHARD_COUNT: '1'
    steps:
      # Add checkout pinned to a reviewed full commit SHA, with persist-credentials: false.
      # Install locked dependencies and use the project's actual test command.
      - run: ./ci/test-shard "${{ matrix.shard }}" "$SHARD_COUNT"
```

This is a routing fragment, not a complete workflow. Implement `ci/test-shard` using the test framework's shard option. Set **shard count = eligible runner count**, not CPU count: one runner → `[1]` / `1`; two runners → `[1, 2]` / `2`. Verify every test runs exactly once. Forks must have a separate hosted job or workflow so checks do not remain pending forever. Never check out untrusted PR code under `pull_request_target`. Never interpolate PR titles or branch contents into shell code.

PR jobs receive no application/deploy secrets or broker access. The unavoidable `GITHUB_TOKEN` gets only required read permissions; do not persist checkout credentials. Disable caches/artifact reuse across trust boundaries. A persistent runner may retain malicious state even after workspace deletion: rebuild it after suspected compromise. Do not rely on directory cleanup to make an untrusted runner safe.

## 5. Deploy on hosted runners with approval

Create **Settings → Environments → production**, require a reviewer, prevent self-approval if available, and allow only the protected `main` branch. Put deployment credentials only in this environment, provisioned from Vault. The deployment workflow uses `runs-on: ubuntu-latest`, `environment: production`, and runs only after successful protected-branch checks. Pin actions by commit, deploy the tested artifact and include a rollback command. Never download arbitrary PR artifacts into a privileged deploy.

[Required environment reviewers](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments) are available only for public repositories on GitHub Free/Pro/Team. If a private repository's plan lacks this gate, choose a plan that supports it or keep deployment manual with a documented human approval path; do not claim an ungated job meets this requirement.

Exit check: an approved internal PR selects the ARM64 runner; a fork selects only hosted capacity; no PR can read Vault or profile homes; shard coverage matches the full suite; two heavy tasks serialize; a deploy waits for the environment approval and then runs on a hosted machine.
