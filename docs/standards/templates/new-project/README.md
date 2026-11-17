# <project-name>

<Explain the user problem and the observable outcome.>

Read [NORTH-STAR.md](NORTH-STAR.md), [architecture](docs/architecture.md), [decisions](docs/adr/) and [runbook](docs/ops/runbook.md).

## Local setup

<Supported runtime versions, dependency install command and safe local start command. No live credentials.>

## Verify

Implement `ci/check` for this project's format, lint, types, architecture and meaningful tests, then run `./ci/check`. The starter fails deliberately until configured. Commit dependency locks and pin reviewed actions by full SHA.

## Deploy and recover

<Approved CI path, environment approval, tested artifact and rollback reference.>

This is a scaffold. Replace every angle-bracket placeholder, decide code/docs licenses and review platform rules before enabling deployments. The workflow uses hosted capacity so public/fork PRs never reach a shared VM.
