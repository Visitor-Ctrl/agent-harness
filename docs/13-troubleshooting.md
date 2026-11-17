# 13 — Troubleshooting

Use a trusted admin session. Start with read-only checks; do not dump environments, auth directories or whole logs. Capture bounded, redacted evidence in the private runbook. If Access is unavailable, use the tested OCI Run Command/recovery path from chapter 02 rather than opening ingress.

## A1 out of host capacity

**Distinguish:** compare **Limits, Quotas and Usage** with the create-instance error. A quota/limit error and an out-of-host-capacity error have different remedies. Check home region, eligible shape and the selected AD. Quotas may intentionally allow only one AD.

**Recover:** retry later with backoff or use another eligible AD only after adjusting the reviewed quota policy without increasing aggregate allowance. A smaller A1 request can fit capacity, but must stay within the total monthly allowance. Do not choose a paid substitute, another region or an upgrade automatically. OCI suggests another AD or waiting: [official guidance](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm). Home region cannot be switched after signup.

**Verify:** the created VM has the intended architecture, resources, boot size and zero-ingress network. Inspect billing after creation. If still unavailable, leave compute unprovisioned and record the capacity blocker.

## Tunnel down or intermittent SSH

**Check:** Cloudflare's tunnel page should show both connector IDs. Via Run Command inspect `systemctl is-active cloudflared@a cloudflared@b`, `journalctl -u cloudflared@a -n 40 --no-pager` (review privately for sensitive data), DNS delegation, system clock and outbound connectivity. Check [Tunnel firewall requirements](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-with-firewall/), including the selected protocol's outbound port. In this baseline both connectors must use the same local SSH origin.

**Recover:** restart one failed connector, preserving the other; repair token-file ownership/mode, expired/rotated credentials or package issues. Verify `sshd -t`, `systemctl status ssh` and the tunnel route's `localhost:22` target. If multiple hosts were attached to one localhost route, remove the incorrect connector or supply a reviewed common origin. Do not publish direct VM DNS or permit port 22.

**Verify:** new SSH sessions succeed while replica a is stopped, then while b is stopped; restore both. A VM reboot affects both, so use OCI recovery if neither can run.

## Access lockout

**Check:** use the independent administrator and Cloudflare dashboard to inspect policy decisions, identity allow-list, application hostname, enabled login method, provider callback URL, session expiry and MFA enrollment. `cloudflared access login https://ssh.example.com` can refresh a human login. A successful Access login followed by SSH `Permission denied` is an OS key/user problem.

**Recover:** restore the narrow policy using the recovery administrator. Correct identity/provider configuration and use offline recovery codes if needed. Never add an Everyone/Bypass rule as a shortcut. For SSH host-key changes compare the fingerprint through OCI control-plane access before modifying `known_hosts`; do not blindly disable host-key checking.

**Verify:** allowed identity succeeds with MFA, unrelated identity fails, and the original SSH public key is still required. Retest the secondary admin before closing sessions.

## Runner not picking up jobs

**Check:** GitHub **Settings → Actions → Runners**: Online/Idle, correct repository/group, labels **self-hosted, Linux, ARM64, pr**. Every `runs-on` label must match. Under ghrunner inspect `systemctl --user status actions-runner-1` with the correct `XDG_RUNTIME_DIR`. Check whether all runners are busy, a concurrency group is blocking, or an environment approval is waiting. An x64 image cannot run an ARM64-only action/container.

**Recover:** fix registration-time labels (or re-register with a new short-lived token), retaining defaults. If labels were added through the API after queuing, cancel and requeue the affected job after confirming matches. Correct the selector rather than weakening trust routing. Reconnect outbound access and restart the user unit if needed; update an unsupported runner release. Do not run fork jobs here to clear a queue.

**Verify:** a harmless trusted job reports ARM64 and completes under the limited runner slice; a fork routes only to hosted runners. Ensure shard count matches eligible runner count, and no required check stays pending because a guarded job was skipped.

## OOM or stalled builds

**Check:** `free -h`, `swapon --show`, `zramctl`, `oomctl`, `systemctl show user-<uid>.slice -p MemoryCurrent -p MemoryHigh -p MemoryMax`, and bounded `journalctl -k`/`journalctl -u systemd-oomd` output. Distinguish cgroup MemoryMax kills, early oomd pressure kills, disk-full errors and swap thrashing. A healthy-looking host can still kill a process that exceeds its own user slice.

**Recover:** stop/requeue the affected job after preserving work, reduce agent concurrency and test workers, shard suites, restore the shared heavy-lock path, or move heavy work to hosted/approved external capacity. Check swap activation and free disk. Do not raise every MemoryMax or disable oomd. Avoid rebooting a host while backups/builds are active; use the control plane only if unresponsive.

**Verify:** rerun a representative light task, inspect peak memory and pressure, and confirm other profiles remain responsive. A larger-memory workload may simply not fit this free host; record that constraint instead of hiding repeated kills.
