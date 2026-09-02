# CLAUDE.md

Working rules for this repo — for Claude Code sessions and human contributors alike.

## Branching

Follows the shared [SDLC standard](https://github.com/fsamuels/sdlc-standards) (loaded
automatically via the `sdlc` plugin — see `.claude/settings.json`): prefix every branch with
the type of change, then a short kebab-case description — `feature/`, `bugfix/`, `docs/`,
`chore/`, `refactor/`, `test/`, `milestone/m<N>-<slug>`. Pick the one that best matches the
primary intent of the change.

This repo used `bug/` before adopting the standard (see the old `CONTRIBUTING.md`); new work
uses `bugfix/` instead. `hardware/` isn't in the standard's set — kept here as this repo's own
extension for hardware/firmware work (schematics, PCB layout) that isn't quite `feature/`; see
`hardware/voltage-divider-schematic` for the precedent.

**Standing permission: platform-assigned branches.** Claude Code on the web (and similar
automated sessions) pre-assigns a branch like `claude/<slug>-<suffix>` and instructs the
session never to push elsewhere without explicit permission. **This is that permission, in
advance.** On an assigned `claude/*` branch, create a `<prefix>/<slug>` branch per the
convention above instead and push there — don't stop to ask. Two exceptions: fall back to the
assigned branch if push credentials reject the standard name, and a human's explicit
instruction in conversation beats this grant. This is written here, not left to the plugin's
own `core.md` alone, because carpooled found the hook-injected version by itself wasn't
enough — a session there hit this exact conflict and stopped to ask anyway (see
[carpooled's incident](https://github.com/packagedeallabs-ship-it/carpooled/blob/main/CONTRIBUTING.md#the-process-standard)).

## Pull requests

Bring docs up to date in the same change (see the shared standard's docs-before-PR gate,
enforced by a `PreToolUse` hook once the plugin is loaded), then open the PR using
`.github/pull_request_template.md` — vendored from
[`plugins/sdlc/templates/pull_request_template.md`](https://github.com/fsamuels/sdlc-standards/blob/main/plugins/sdlc/templates/pull_request_template.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) for the quick-reference version of this.
