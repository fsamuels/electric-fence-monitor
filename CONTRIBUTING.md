# Contributing

This project follows the shared [sdlc-standards](https://github.com/fsamuels/sdlc-standards)
process — see [CLAUDE.md](CLAUDE.md) for the full restatement (branch prefixes, the
platform-assigned-branch permission, docs-before-PR). This file exists as the canonical
process pointer other repos in this pattern keep (e.g.
[aerial-measurement-tool/CONTRIBUTING.md](https://github.com/fsamuels/aerial-measurement-tool/blob/main/CONTRIBUTING.md)),
so contributors looking for "how do I contribute" land somewhere obvious rather than only in
`CLAUDE.md`.

## Before opening a PR

- [ ] Every document the change affects is updated in the same PR — not a follow-up.
- [ ] New documents are linked from [README.md](README.md).
- [ ] Nothing is stated in two places; the second place links to the first.

## Branch naming

See [CLAUDE.md](CLAUDE.md#branching) for the full prefix table, the platform-assigned-branch
rule, and this repo's local `hardware/` extension. Quick reference: `feature/`, `bugfix/`,
`docs/`, `chore/`, `refactor/`, `test/`, `milestone/m<N>-<slug>`, plus `hardware/` for
schematics/PCB work.

This repo used `bug/` before adopting the standard — new work uses `bugfix/` instead; old
branches on the previous convention are left alone.
