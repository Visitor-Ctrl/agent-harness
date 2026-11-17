# Architecture

## Context and trust boundaries

<Users, workloads, external systems, isolation assumptions and untrusted inputs.>

## Layer map

<Domain rules; application use cases and ports; adapters; composition root.>

## Ports and implementations

| Capability | Port | Implementation | Failure behavior |
|---|---|---|---|
| <capability> | <interface> | <adapter> | <timeout/error policy> |

## Data and secrets

<Storage, retention, migrations, credential source, authorized readers and negative isolation checks.>

## Operation

<Health, capacity, cost constraints, backup and restore references. See ops/runbook.md.>
