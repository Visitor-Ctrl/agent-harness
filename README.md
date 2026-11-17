# Agent harness platform

A reproducible infrastructure guide and generic agent-tooling project for a small OCI workspace behind Cloudflare Access. Bring your own domain and AI subscriptions. Engines and accounts are configured per profile; no private deployment configuration belongs here.

Start with [the overview](docs/00-overview.md), [prerequisites](docs/01-prerequisites.md) and [dated budget ledger](docs/11-free-tier-budget.md). Choose a home region and billing mode, then follow the numbered infrastructure chapters. Do not enable agents until the harness's secret broker and isolation checks are configured and verified. This repository does not provision your cloud account automatically.

| Chapter | Contents |
|---|---|
| [00 — Overview](docs/00-overview.md) | Architecture, outcomes and limitations |
| [01 — Prerequisites](docs/01-prerequisites.md) | Accounts, home region, billing and recovery |
| [02 — Oracle server](docs/02-oracle-server.md) | Eligible VMs, zero-ingress network and hardening |
| [03 — Cloudflare](docs/03-cloudflare.md) | DNS, Tunnel, Access, SSH and public hosting |
| [04 — Secrets](docs/04-secrets.md) | Vault, instance principal and broker requirements |
| [05 — Profiles and herdr](docs/05-profiles-and-herdr.md) | Linux users, resource limits and remote sessions |
| [06 — Harness installation](docs/06-harness-install.md) | Release installation, guards and secret broker |
| [07 — AI engines](docs/07-ai-engines.md) | Configurable providers and account setup |
| [08 — CI runners](docs/08-ci-runners.md) | Trusted ARM64 checks and approved hosted deploys |
| [09 — Agent-to-agent messaging](docs/09-agent-messaging.md) | Dispatch and cross-machine messaging |
| [10 — Monitoring and backups](docs/10-monitoring-and-backups.md) | Heartbeats, alerts, restic and recovery drills |
| [11 — Free-tier budget](docs/11-free-tier-budget.md) | Confirmed allowances and items to re-check |
| [12 — Security](docs/12-security.md) | Threat model and agent boundaries |
| [13 — Troubleshooting](docs/13-troubleshooting.md) | Capacity, access, runners and OOM |

Use [project standards](docs/standards/README.md) and the [new-project scaffold](docs/standards/templates/new-project/) for workloads built here. Copy dotfiles too; the scaffold's CI deliberately fails until project checks are implemented. Each real project requires an owner-approved NORTH-STAR; the template is proposed, not an approval.

The [harness reference](harness/README.md) covers the versioned installer, pool, dispatch, coordinators, guards, broker, watchers and optional mailbox example. The offline suite validates local behavior; active hooks and cloud services require operator configuration and acceptance checks. There is no platform deployment or test command that can establish cloud isolation without creating and testing your own environment. Each chapter ends with live acceptance checks for the operator. Local documentation checks cover links, syntax and prohibited identifiers only.

Code is [MIT licensed](LICENSE). Documentation under `docs/` and this README are [CC BY 4.0](docs/LICENSE), with attribution to the Agent Harness contributors. Original code snippets/scaffold code in the docs may also be used under MIT. Referenced third-party products, code and licenses retain their own terms. This is an infrastructure recipe, not a guarantee of availability, zero costs or independent tenant isolation.
