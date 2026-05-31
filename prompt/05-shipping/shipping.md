## How to ship a PR

When the PBI is **complete** (all `PLAN.md` steps checked for features,
reproduce-passes for bugs, all comments addressed for PR-feedback), ship
the PR.

1. **Push the feature branch:**
   ```
   git push -u origin ralph/<PBI-id>
   ```
   The branch was created by the executor off `main` at PBI claim time;
   all your iterations have committed to it.
2. **Open the PR via the `ado-pr` skill.** Use the `create-pr`
   operation:
   ```
   ado-pr create-pr ralph/<PBI-id> "<PBI-id>: <one-line title>" \
     --body "<body referencing the PBI directory>"
   ```
   The skill returns the PR ID. Capture it; you will write it into the
   PBI's `HISTORY.md` and the executor will use it to track PR state.
3. **Append the PR creation to `HISTORY.md`:**
   ```
   ## Iteration <N> — <ISO timestamp> — PR created
   - PR: !<PR-ID>
   - Branch: ralph/<PBI-id>
   - Title: <PBI-id>: <one-line title>
   ```
4. **Exit.** The executor moves the PBI from `current/` to
   `pending-pr/`. The org's existing auto PR job runs against the new
   PR (assigning reviewers, kicking off auto code review). You do
   nothing more on this PBI until a PR-feedback work item appears.

### PR body conventions

- **Title:** `<PBI-id>: <one-line summary>`. Keep under 70 chars.
- **Body:** include
  - One paragraph explaining what changed and why.
  - A "Closes" line referencing the work item (e.g. `Closes WI-1234`).
  - A "Test plan" bullet list — what you ran, what passed.
  - A pointer to the PBI directory on `ralph-queue` so reviewers can
    see the full ask if they want it.
- **Do not** add reviewers, labels, or assignees yourself — the org's
  auto PR job does that based on the changed paths.

**Never write to `HISTORY.md` AFTER pushing the PR.** The append
happens BEFORE the push so the commit captures the full history.
