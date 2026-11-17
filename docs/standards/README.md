# Project standards

Adopt these standards when creating or maintaining a project on the platform. They are a starting policy, not evidence that controls are installed.

1. Obtain an owner-approved north star and pick a [project structure](project-structures.md).
2. Copy [the starter](templates/new-project/) into the new repository, including dotfiles. Replace its placeholders, implement its CI command and review its license choices.
3. Document the architecture and decisions, then enforce [layer boundaries](clean-architecture.md) in the project's tooling.
4. Apply the numbered [rules](project-rules.md) and work through the [checklists](checklists.md).

For an existing repository, inventory missing controls first. Introduce import checks in report-only mode, fix violations in scoped changes, then make the gate required. Record exceptions and migration dates in an ADR rather than claiming an unimplemented policy is active.
