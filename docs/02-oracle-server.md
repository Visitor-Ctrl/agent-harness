# 02 — Oracle server and host controls

Use an OCI administrator for this chapter. Commands are examples to execute on your own new Ubuntu host after review; they are not run by this documentation project. Do not place credentials in cloud-init, which is retained as instance metadata and in logs.

## 1. Compartments, quotas and a budget

1. **Identity & Security → Compartments → Create compartment**: create `platform`. Under it create `compute`, `backups`, `profile-a` and `profile-b`. Keep compute together so the quota applies to all VMs. Profiles' secrets go in separate compartments in chapter 04.
2. Open **Limits, Quotas and Usage**, select the home region and Compute, and record the limits for A1 cores, A1 memory and micro instances. The [Always Free page](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), checked 2026-11-17, lists 1,500 OCPU hours and 9,000 GB hours monthly: plan for 2 OCPU and 12 GB. Check your tenancy's Limits page before proceeding.
3. **Governance & Administration → Quota Policies → Create quota policy**. Use the following starting policy, substituting your compartment path. [Quota names and families](https://docs.oracle.com/en-us/iaas/Content/Quotas/Concepts/resourcequotas_topic-Compute_Quotas.htm) differ by service. Validate in the Console before relying on it.

```text
zero compute-core quotas in compartment platform:compute
zero compute-memory quotas in compartment platform:compute
zero compute quotas in compartment platform:compute
set compute-core quota standard-a1-core-count to 2 in compartment platform:compute
set compute-memory quota standard-a1-memory-count to 12 in compartment platform:compute
set compute-core quota standard-e2-micro-core-count to 2 in compartment platform:compute
set compute quota vm-standard-e2-1-micro-count to 2 in compartment platform:compute
```

These quotas are availability-domain scoped. In a multi-AD region add `where request.ad.name = '<chosen-ad-name>'` to the four `set` statements; leave the zero rules broad. Verify the other ADs remain zero. The micro core ceiling above is deliberately loose; the instance count limits it to two. A1 quotas cover more than just this VM shape: prevent agents from creating infrastructure at all through IAM. Quotas do not establish free eligibility. Restrict human operators to the approved region/compartment and review every provisioning plan for shape, volume total and paid attachments.

4. Confirm the [budget alert](01-prerequisites.md) exists for the root compartment. Review usage after provisioning and daily during the trial. No budget is a hard cap.

## 2. Network with zero ingress

1. **Networking → Virtual Cloud Networks → Create VCN**, choose manual configuration. Create one regional public subnet, an Internet Gateway and a route for all IPv4 destinations to that gateway. Use a private CIDR of your choosing in the Console, not a real network copied into this guide. Assign ephemeral public addresses for outbound connectivity; never publish an A record pointing to a VM.
2. Create a security list with **no ingress rules**. Remove the wizard's default SSH and ICMP entries. Attach only this list to the subnet. Use stateful outbound rules so replies to initiated connections work. Start with outbound allowed; later narrow it only after testing DNS, NTP, package updates, OCI APIs and Tunnel transport.
3. Inspect **every attached security list and network security group**. Rules are additive: an empty NSG does not cancel ingress allowed by another list. Confirm none allows ingress. Do not create a NAT gateway, load balancer or paid network appliance for this baseline.
4. Recovery uses the OCI control plane, not an open SSH port. Enable the **Compute Instance Run Command** plugin under the instance's Oracle Cloud Agent tab and follow [Run Command prerequisites and permissions](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/runningcommands.htm). It works without SSH ingress. Test a harmless `id` command before hardening. Use it only for non-secret bootstrap commands; secrets must come from Vault. Keep Console Connection/boot recovery instructions offline.

## 3. Create the three VMs

**Compute → Instances → Create instance**, compartment `platform:compute`:

| Name | Shape and image | Boot volume | Purpose |
|---|---|---|---|
| `build` | A1.Flex, 2 OCPU / 12 GB, Ubuntu 24.04 ARM64 | 100 GB | agents and light CI |
| `edge` | E2.1.Micro, Ubuntu 24.04 x86-64 | 50 GB | watchdog/light services |
| `services` | E2.1.Micro, Ubuntu 24.04 x86-64 | 50 GB | light services |

Use eligible platform images, no Marketplace paid images, no reserved capacity. Keep boot plus block volumes within the allowance, including old volumes left by terminated instances. Do not select higher paid performance tiers. Upload only your SSH **public** key. In Advanced options → Management → Initialization script, upload this `cloud-init.yaml` (retain privately only if you later customize it with private values):

```yaml
#cloud-config
package_update: true
package_upgrade: true
packages:
  - ca-certificates
  - curl
  - git
  - nftables
  - unattended-upgrades
  - systemd-oomd
write_files:
  - path: /etc/ssh/sshd_config.d/00-platform.conf
    owner: root:root
    permissions: '0644'
    content: |
      PermitRootLogin no
      PasswordAuthentication no
      KbdInteractiveAuthentication no
      X11Forwarding no
      AllowAgentForwarding no
runcmd:
  - [systemctl, enable, --now, unattended-upgrades]
  - [systemctl, enable, --now, systemd-oomd]
  - [sshd, -t]
  - [systemctl, reload, ssh]
```

Check with Run Command: `sudo cloud-init status --wait`, `uname -m`, `free -h`, `df -h /`, `sudo sshd -T`. Expect `aarch64` on build and `x86_64` on micros, completed cloud-init and password/root login disabled. Investigate `cloud-init status --long` before proceeding. Do not retrieve full cloud-init logs if they could contain secrets.

## 4. Memory pressure and updates

On build only, create an 8 GB disk swap file if no swap file already exists:

```sh
sudo install -m 600 /dev/null /swapfile
sudo fallocate -l 8G /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
printf '/swapfile none swap sw 0 0\n' | sudo tee -a /etc/fstab >/dev/null
sudo apt-get install -y zram-tools
```

Run once: recreating active swap is unsafe. For micros use 1 GB disk swap instead, after checking free disk. Set `/etc/default/zramswap` to `ALGO=lz4`, `PERCENT=25`, `PRIORITY=100`, one setting per line. Then `sudo systemctl restart zramswap`; inspect `swapon --show` and `zramctl`. zram consumes RAM and compressed swap is not extra guaranteed capacity. Configure profile pressure handling in chapter 05. Use `oomctl` and `journalctl -u systemd-oomd` to distinguish early pressure kills from kernel OOM.

Run `sudo dpkg-reconfigure -plow unattended-upgrades`, choose Yes; verify `apt-daily-upgrade.timer` is enabled and `sudo unattended-upgrade --dry-run` succeeds. Set an operator maintenance window for reboots; inspect `/var/run/reboot-required`. Never reboot during a build or backup.

## 5. Host firewall

Install this as `/etc/nftables.conf` only on a fresh host without Docker or another firewall manager. The loopback exception permits cloudflared to reach SSH locally. Keep OCI Run Command available while applying.

```nft
table inet platform {
  chain input {
    type filter hook input priority 0; policy drop;
    iifname "lo" accept
    ct state established,related accept
    ct state invalid drop
    udp sport 67 udp dport 68 accept
    udp sport 547 udp dport 546 accept
    meta l4proto ipv6-icmp icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-advert } accept
  }
  chain forward { type filter hook forward priority 0; policy drop; }
  chain output { type filter hook output priority 0; policy accept; }
}
```

Check `sudo nft -c -f /etc/nftables.conf`, then load it once with `sudo nft -f /etc/nftables.conf` and enable `nftables`. For later changes replace the existing table transactionally rather than appending duplicate chains. Disable unused IPv6 in OCI if you do not intend to operate it. Confirm `sudo nft list ruleset`, DNS resolution, outbound HTTPS, Run Command, and later Access SSH. There is no remote SSH exception in this ruleset.

## 6. Reclamation and acceptance

OCI may reclaim idle free instances. The official rule looks at a seven-day period with CPU 95th percentile below 20%, network below 20%, and A1 memory below 20%. Do not generate artificial load to evade reclamation. Back up, document recreation and accept possible downtime. PAYG is a billing choice, not a capacity or retention guarantee.

Exit check: inspect all three instances' shape/image/volume totals; verify zero OCI ingress and the host firewall; confirm updates and pressure services; test control-plane recovery. After chapter 03, reboot build and verify Tunnel and authenticated SSH recover. Stop here if A1 is unavailable; see [troubleshooting](13-troubleshooting.md).
