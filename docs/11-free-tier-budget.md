# 11 — Free-tier allowance ledger

**Checked 2026-11-17** against the official pages linked below. Re-check before provisioning and quarterly. Tenancy limits, eligibility labels, billing mode and current pricing take precedence over this plan. “Re-check” means the exact allowance was not confirmed on an official page during this review; do not treat it as a promise. AI subscriptions and domain registration are outside this budget.

## OCI

Source for the following rows: [OCI Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), checked 2026-11-17.

| Resource | Confirmed allowance | Planned consumption / control |
|---|---|---|
| A1.Flex | 1,500 OCPU-hours + 9,000 GB-hours/month | 2 OCPU / 12 GB; confirm tenancy Limits page |
| E2.1.Micro | Two instances | Two micros; no paid shape substitution |
| Boot + block volumes | 200 GB combined | 100 + 50 + 50 GB; no room for extra restore volumes |
| Volume backups | Five combined | One per VM, rotate carefully |
| Object Storage, free-only after trial | 20 GB combined across tiers; 50,000 requests/month | Target under 15 GB Standard, measure requests |
| Object Storage, paid/free-trial | 10 GB each in Standard, Infrequent Access and Archive; 50,000 requests/month | Keep restic Standard under 8 GB; other tiers are not interchangeable working space |
| Vault | Software keys free; 150 secrets | Ordinary vault; no virtual private vault |
| Outbound transfer | 10 TB/month | Measure usage; no assumption of unlimited transfer |

Use only home-region eligible resources. Idle reclamation still applies; see [server setup](02-oracle-server.md). A PAYG upgrade does not turn quotas or budgets into a guarantee of a zero invoice. Higher volume performance, retained orphan volumes, paid images, extra backups and non-home-region copies need separate review.

## Cloudflare

Each confirmed value below was checked 2026-11-17 on its linked official page. Limits are generally account-wide; separate profiles do not create separate allowances.

| Resource | Free allowance / status | Planned use |
|---|---|---|
| [Workers](https://developers.cloudflare.com/workers/platform/pricing/) | 100,000 requests/day; 10 ms CPU/invocation | Light APIs and monitoring; observe CPU errors |
| [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) | Five Cron Triggers/account | One monitor trigger |
| [KV](https://developers.cloudflare.com/kv/platform/limits/) | 100,000 reads/day; 1,000 writes/day; 1 GB | 672 heartbeat writes/day plus transitions; keep messaging out of KV |
| [D1](https://developers.cloudflare.com/workers/platform/pricing/#d1) | 5 million rows read/day; 100,000 written/day; 5 GB total | Optional app data; scans consume row allowance |
| [Durable Objects](https://developers.cloudflare.com/durable-objects/platform/pricing/) | Re-check exact current Free request/duration/storage allowance and SQLite eligibility | See [messaging](09-agent-messaging.md); do not provision from an old estimate |
| [Pages](https://developers.cloudflare.com/pages/platform/limits/) | 500 builds/month, one concurrent Free build | Small sites; Functions have Workers limits |
| [R2 Standard](https://developers.cloudflare.com/r2/pricing/) | 10 GB-month, 1 million Class A and 10 million Class B operations/month; internet egress free | Optional independent copy; billing exposure above allowance; not required for baseline |
| [Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/) | Re-check current commercial terms/limits | Two local connectors; no paid load balancer |
| [Access](https://www.cloudflare.com/plans/zero-trust-services/) | Re-check seat ceiling shown when selecting Free (often described as 50 users) | Only intended identities; do not assume unlimited seats |
| [WAF / rate limiting](https://developers.cloudflare.com/waf/rate-limiting-rules/) | Free rules are available; re-check exact count and allowed fields in your zone | Protect a narrow path; avoid paid features |
| [Turnstile](https://developers.cloudflare.com/turnstile/plans/) | Re-check Free widget/hostname ceilings | One public form widget; verify server-side |

## GitHub, alerts and changes

GitHub-hosted minutes/storage depend on plan and repository visibility: [billing reference](https://docs.github.com/en/billing/concepts/product-billing/github-actions); **re-check** the exact allowance before using hosted heavy jobs. A self-hosted runner still consumes your VM and any applicable GitHub service charges. Required environment reviewers on Free/Pro/Team apply only to public repositories: [environment reference](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments). Private-repo approval requirements may need a different plan.

ntfy hosting and email provider allowances are **re-check**; no paid provider is implicitly authorized. OCI Monitoring/Logging/Notifications allowances are **re-check** if you add them; avoid log retention growth without a cap.

Before every resource change: inspect current usage, include temporary migration/restore capacity, check the pricing page, and record approval for any paid exposure. In OCI set root-compartment actual and forecast budget alerts; in Cloudflare inspect subscription and usage settings per product. [OCI budgets](https://docs.oracle.com/en-us/iaas/Content/Billing/Concepts/budgetsoverview.htm) alert periodically and do not stop spending. Review invoices and resource inventory during the trial and after it ends. Do not deliberately exhaust a free quota to test billing behavior.
