**All PBI types — always read first, in this order:**

1. Standing prompt (already loaded as your system prompt).
2. `$RALPH_PBI_DIR/HISTORY.md` — prior iteration attempts.

Then continue with the per-type read order:

### Bug PBI — read order

Entry file: `BUG.md`. Required siblings: `BUG.md`, `REPRODUCE.md`,
`HISTORY.md`.

Read in this order:

1. Standing prompt (already loaded as your system prompt).
2. `$RALPH_PBI_DIR/HISTORY.md`
3. `$RALPH_PBI_DIR/BUG.md`
4. `$RALPH_PBI_DIR/REPRODUCE.md`
5. `docs/INVESTIGATE.md` (at the **repo root** of the code worktree —
   i.e. your current working directory — NOT inside the PBI directory.
   This is the per-service debugging guide written and maintained by
   the service's engineers.)

If `docs/INVESTIGATE.md` does not exist for this service, note that in
`HISTORY.md` and proceed without it — but write `STUCK.md` if the absence
is materially blocking your investigation.
