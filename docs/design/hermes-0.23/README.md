# Reference port to Hermes v0.23.1 (not a release)

These files record the mechanical port of Hermachy's Hermes patches to
[v0.23.1](https://github.com/NousResearch/hermes-agent/tree/v0.23.1)
(`3b421578588243f920886e6fe79a6faa87ea026e`). They are **not used** by any
build. See [the 0.6 design](../SHARED_GATEWAY_0.6.md#1-why-052-cannot-just-move-to-hermes-023)
for why the 0.5.x architecture cannot run on Hermes 0.23 as-is.

| File | What it is |
|---|---|
| `safe-executor-v2-v0.23.1.patch` | `compat/hermes/safe-executor-v2.patch` rebased onto v0.23.1, plus refusal of the new `image.render` and `vault.preview.fill` RPCs in Hermachy mode |
| `manifest-v0.23.1.json` | Base revision, patch checksum and resulting Git tree for `compat/hermes/prepare.py` |
| `activity-emojis-v0.23.1.patch` | `tui/activity-emojis.patch` rebased onto v0.23.1 |
| `omarchy-theme-v0.23.1.patch` | `tui/omarchy-theme.patch` rebased onto v0.23.1 |

Verification performed on Linux (aarch64):

- `prepare.py` with this manifest fetches the public commit, applies the patch
  and verifies the tree `0d09b1fbb77f8d665ea7a85b86c6f2ac008f74c1`.
- Hermes contract suite (the list in `compat/hermes/README.md`): 934 passed,
  0 failed, 12 skipped. Hermachy prompt-integration tests: 3 passed.
- Renderer: `tsc --noEmit` passes; patch tests 22/22; full `ui-tui` vitest
  suite 1985/1985. Build with Hermes's pinned pnpm via `source ./activate`,
  then `pnpm install --frozen-lockfile --filter 'hermes-tui...'`, then
  `pnpm --dir packages/hermes-ink run build` (needed by the tests) and
  `pnpm run build` in `ui-tui/`.

Why the port alone does not work:

- The stdio gateway entry refuses to start while the profile's gateway holds
  `gateway.lock`.
- The v0.23 TUI only dials a WebSocket.
