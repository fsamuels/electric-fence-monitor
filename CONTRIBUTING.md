# Contributing

## Branch naming

Prefix branches by the kind of work they contain:

| Prefix | Use for |
|---|---|
| `feature/` | New capability or implementation work |
| `bug/` | Bug fixes |
| `docs/` | Documentation-only changes |
| `chore/` | Tooling, dependency bumps, cleanup with no behavior change |

Example: `feature/dashboard-d0-scaffolding`.

Keep implementation work off docs-only branches (e.g. `docs/dashboard-plan`) — cut a `feature/...` branch from `main` instead, even if it implements something a docs branch is proposing.
