# Project structures

Choose a structure by execution model. Document deviations in an ADR. Every maintained project includes a README, owner-approved north star, agent instructions, contribution steps, architecture, decisions, runbook, ignore rules and CI.

## Service baseline

```text
src/
  domain/                    entities and business rules
  application/
    ports/                   capability interfaces
    use-cases/               one action per module
  adapters/
    http/                    input validation and response mapping
    persistence/             store implementations
    models/                  AI provider adapters and prompts
  infrastructure/            configuration and dependency wiring
  main.*                     entry point
 tests/                      unit, integration, acceptance
 docs/                       architecture, ADRs, operations
 ci/                         repository verification commands
```

For Python use a real package under `src/<package>/`, snake_case modules, pyproject configuration and import-linter. For TypeScript use kebab-case modules, strict type checking, a committed lockfile and enforced import boundaries.

## Worker or website

A Worker keeps the same domain/application split; bindings are injected by its entry point. Store migrations separately and keep credential values out of Wrangler configuration. A static website can use `src/content`, `components`, `layouts`, `pages` and `public`; put data loading in a dedicated adapter rather than every visual component. Add accessibility and broken-link checks appropriate to the site.

## Infrastructure

Use reusable `modules/`, environment composition under `environments/`, host configuration under `hosts/`, and policy checks under `policies/`. Plans must reject ingress, paid shapes or excess resource totals unless specifically approved. Treat state and plan artifacts as sensitive: remote state needs encrypted access control and locking supported by the selected backend. Do not assume an S3-compatible backend implements every locking feature. Apply only the reviewed plan from the approved deployment path.

## Monorepo and tooling

Keep deployable applications under `apps/` and intentionally shared contracts/domain code under `packages/`. An app does not import another app's internals. Check boundaries per package and keep ownership explicit.

Agent tools may use `bin/` for thin commands, `lib/` for rules/use cases/adapters, `hooks/` for enforced entry checks, `systemd/` for units, `config/` for example schemas and `tests/` for behavior checks. This describes a recommended layout, not a claim about any unreleased harness module.

Name ports for capabilities (`DocumentStore`), adapters for implementations (`SqlDocumentStore`) and use cases for actions (`publish-document`). Avoid a growing shared utils folder that defeats the dependency rule.
