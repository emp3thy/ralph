**All PBI types — always read first, in this order:**

1. Standing prompt (already loaded as your system prompt).
2. `$RALPH_PBI_DIR/HISTORY.md` — prior iteration attempts.

Then continue with the per-type read order:

### Feature PBI — read order

Entry file: `PBI.md`. Required siblings: `PBI.md`, `PLAN.md`,
`HISTORY.md`.

Read in this order:

1. Standing prompt (already loaded as your system prompt).
2. `$RALPH_PBI_DIR/HISTORY.md`
3. `$RALPH_PBI_DIR/PBI.md`
4. `$RALPH_PBI_DIR/PLAN.md`
