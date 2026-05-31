## How to work a bug PBI

1. **Read** in the order specified above.
2. **Reproduce.** Run the commands in `REPRODUCE.md`. Confirm you observe
   the failure signature documented in `BUG.md`. If you cannot reproduce,
   write `STUCK.md` ("cannot reproduce: <what I saw instead>") and stop.
3. **Form a hypothesis.** Use `docs/INVESTIGATE.md` to navigate the
   codebase (key classes, log locations, external dependencies, common
   gotchas). State your hypothesis explicitly in `HISTORY.md` BEFORE
   writing the fix — this guards against post-hoc rationalisation.
4. **Apply the fix.** Smallest change that addresses the hypothesis.
   Avoid scope creep — if you see other latent bugs, note them in
   `HISTORY.md` but do not fix them in this PBI.
5. **Verify.** Re-run `REPRODUCE.md`'s commands. The failure must no
   longer occur. Also run the relevant existing test suite to make sure
   you did not regress anything.
6. **Append to `HISTORY.md`** with the same structure as for features,
   plus a `Root cause:` line and a `Fix:` line. Be specific:
   ```
   Root cause: <one sentence — what was actually broken>
   Fix: <one sentence — what changed to fix it>
   ```
7. **Commit on the feature branch** (`ralph/<PBI-id>`):
   ```
   fix(<area>): <PBI-id> — <what was fixed>
   ```
8. **If the fix is complete**, proceed to "How to ship a PR".

If your hypothesis was wrong (reproduce still shows the failure after
the fix), do NOT keep stacking patches. Append the failed hypothesis to
`HISTORY.md`, revert the failed change, and exit. The next iteration
forms a new hypothesis. Hitting three failed hypotheses on the same bug
is a STUCK.md trigger (see below).

### Memory triggers specific to bug PBIs

In addition to the universal triggers documented in the memory section,
bug iterations also record:

- **After `REPRODUCE.md` confirms the failure signature.** If the
  reproduction signal is non-obvious (specific input, environment
  variable, race condition, ordering), call
  `mcp__better-memory__memory_observe` with `outcome="neutral"`
  recording how you triggered the failure.
- **After you identify the root cause in `HISTORY.md`.** Call
  `memory_observe` with `outcome="failure"` recording the cause and the
  diagnostic path that surfaced it. Include component / file paths in
  `component`.
