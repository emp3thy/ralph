**All PBI types — always read first, in this order:**

1. Standing prompt (already loaded as your system prompt).
2. `$RALPH_PBI_DIR/HISTORY.md` — prior iteration attempts.

Then continue with the per-type read order:

### PR-feedback PBI — read order

Entry file: `FEEDBACK.md`. Required siblings: `FEEDBACK.md`, `PR-LINK.md`,
`ORIGINAL.md`, `HISTORY.md`.

Read in this order:

1. Standing prompt (already loaded as your system prompt).
2. `$RALPH_PBI_DIR/HISTORY.md`
3. `$RALPH_PBI_DIR/ORIGINAL.md`
4. `$RALPH_PBI_DIR/FEEDBACK.md`

`FEEDBACK.md` itself contains explicit per-round instructions (decide
correct / not / unclear; apply or reply; set thread status). Those
instructions take precedence over the generic guidance in this file when
they conflict — they are the per-round contract.
