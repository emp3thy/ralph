## What you never do

These are hard rules. Violating one is a STUCK.md trigger or a halt.

- **Never touch `main` directly.** All code work happens on
  `ralph/<PBI-id>`. The executor handles the branch checkout; you stay
  on it.
- **Never touch the `ralph-queue` branch from code work.** Queue state
  is the executor's job. You only edit files inside the PBI directory
  (HISTORY.md, STUCK.md) on the feature branch — the executor moves
  those over to `ralph-queue` at PBI boundaries.
- **Never delete or move files outside the PBI directory's declared
  scope** without the PBI explicitly asking for it.
- **Never widen permissions, IAM, or security controls** without
  explicit PBI authorisation.
- **Never call `AskUserQuestion` or any other interactive tool.** The
  session is non-interactive (`-p` mode). If you find yourself wanting
  to ask a question, write `STUCK.md` with the question instead.
