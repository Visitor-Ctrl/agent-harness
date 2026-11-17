# Adoption and review checks

## New project

- [ ] Owner approved the NORTH-STAR and acceptance criteria.
- [ ] Starter placeholders replaced, including CI commands and runbook steps.
- [ ] Layout, architecture and initial ADRs explain trust boundaries and dependencies.
- [ ] Fast checks reject a deliberate temporary import violation.
- [ ] Required PR checks, independent review and protected branch rules are active.
- [ ] Staged/history secret scanning tested with a synthetic fixture.
- [ ] Hosted PR/deploy routing, approval availability and budgets checked against the actual plan.
- [ ] Health, rollback, backup and restore paths tested where applicable.

## Pull request

- [ ] Scope matches the issue; unrelated work is tracked separately.
- [ ] Dependencies point inward and inputs are validated at the edge.
- [ ] Meaningful behavior/regression checks pass on this commit.
- [ ] No private identifiers, credentials or secret-bearing logs in files or history.
- [ ] Docs, decisions and runbook describe the resulting behavior.
- [ ] Added dependencies are justified and pinned; scans reviewed.
- [ ] Deployment artifact and rollback are identified.
- [ ] Reviewer is independent; required approvals and checks are current.

## Architecture and security

- [ ] Alternatives and residual risks are explicit.
- [ ] Every external capability sits behind a narrow port where useful.
- [ ] Slow/down/malformed dependency behavior is defined.
- [ ] User, process, cloud and CI boundaries have negative verification checks.
- [ ] Instance-principal access cannot bypass the intended broker.
- [ ] Steady-state and temporary recovery resources fit the budget.
- [ ] An ADR records the approved choice and its operational cost.
