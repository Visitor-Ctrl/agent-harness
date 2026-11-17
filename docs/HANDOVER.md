# Infrastructure guide handover

Infrastructure authoring is complete for chapters 00–05, 08 and 10–13. Chapters 06, 07 and 09 are reserved for the harness installation, engine and messaging work. Do not infer that a security component exists merely because the infrastructure guide describes its required contract.

## Decisions

- Two local cloudflared replicas target the same SSH service; this is process redundancy, not cross-host failover. The baseline preserves zero OCI ingress.
- Vault is the credential source of truth. Instance identity is shared by the host, so broker and metadata-isolation acceptance gates precede agent execution.
- Only trusted private contributions can use shared CI; public/fork PRs use hosted capacity. Private-repo environment approvals need a compatible GitHub plan.
- Budget alerts are soft limits. Object Storage planning distinguishes free-only from paid/trial allowances. Unknown current limits are explicitly marked re-check.
- The project starter uses hosted CI and fails until actual project checks replace its placeholder command.

## Verification evidence

Checked 2026-11-17:

- 34 relative Markdown links resolve; 49 public source links returned HTTP 200 after correcting two obsolete paths.
- Parsed YAML and concrete shell snippets; JavaScript Worker passed Node syntax validation; starter shell passed ShellCheck.
- Local Worker exercise passed unauthorized/method/unknown-host rejection, heartbeat recording, stale and recovery transitions, deduplication and notification-failure retry behavior.
- Gitleaks scanned all 15 authoring commits scoped to the guide paths: no leaks. Prohibited-identifier and IPv4-pattern scans: no matches and no false positives.
- `git diff --check` passed. No cloud account was provisioned or accessed; no live SSH, firewall, IAM, runner or restore claim is made.

## Integration and next action

The owner must approve a project NORTH-STAR. Integrate the reserved chapters only when their actual release behavior and security services are available. The broker and shared lock are explicit dependencies, not fabricated commands. Execute each chapter's live acceptance checks in an operator-controlled environment before enabling agents.

No Git remote was configured during authoring, so commits remain local and no PR was created. An owner must provide the intended remote before publication. Keep work on a topic branch and independently review the guide and any concurrent code before pushing or merging.
