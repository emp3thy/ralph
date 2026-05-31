## How to work a PR-feedback PBI

1. **Read** in the order specified above.
2. **Check out the existing PR branch.** The branch name and PR ID are
   in `PR-LINK.md`. Do NOT open a new PR; do NOT rebase; just push
   additional commits to the existing branch.
3. **For each active comment in `FEEDBACK.md`:**
   a. Read the comment and examine the referenced code (file:line for
      line comments; the diff for general comments).
   b. Decide: is the reviewer correct?
      - **Correct** → implement the fix. Commit it with a message
        referencing the thread (e.g. `fix(app): address thread 8821 —
        move route below logging middleware`).
      - **Incorrect** → do not change the code. Prepare a clear,
        respectful reply explaining why. (See "Reviewer feedback" below
        for tone.)
      - **Unclear** → write `STUCK.md` with what is ambiguous.
   c. **Always reply** in the thread via the `ado-pr` skill:
      - If you applied a fix: tell them what you changed (file, line,
        brief description).
      - If you challenged: explain your reasoning.
   d. **Set thread status:**
      - Applied a fix → set to `fixed`.
      - Challenged → leave `active`. The reviewer closes it after
        reading. Never set `wontFix` or `byDesign` — those are
        human-only.
4. **Push to the existing branch.** The PR auto-updates and CI re-runs.
5. **Append to `HISTORY.md`** with one bullet per comment addressed
   ("Comment N → fixed: …" or "Comment N → challenged: …").

Do not address comments that are not in this `FEEDBACK.md`. If a
reviewer mentions something out-of-scope ("can we also rename X?"), note
it in the reply ("filing as a separate PBI") and move on.

## Elevated-attention categories

When you receive PR comments, most are routine: a name suggestion, a
missing docstring, a clearer log line. Treat those normally — fix or
challenge, reply, set status.

Four categories require elevated attention. If a comment falls in one of
these, do NOT mechanically apply or mechanically dismiss it — pause,
reason explicitly, and bias toward the reviewer's concern unless you
have a strong counter-argument.

| Category | Reviewer concern looks like | Your default response |
|---|---|---|
| **test deletion** | "You removed a test." | Restore the test unless it is genuinely obsolete (the feature it tested was removed by the PBI itself). If obsolete, justify in the reply with specifics — name the removed feature and the redundant assertion. |
| **scope creep** | "You changed file X which is outside the PBI's declared scope." | Revert that change unless it is a necessary precondition for the in-scope change. If necessary, justify in the reply. If not necessary, file a follow-up PBI rather than smuggling it in. |
| **permission widening** | "You widened IAM / changed auth / loosened a security control." | Revert unless the PBI explicitly authorises it. Security-sensitive changes are never incidental. If you genuinely need the change to complete the PBI, write `STUCK.md` and ask a human. |
| **large diff** | "This is too large a change to review safely." | Take it seriously. Either split the PR (push a revert + smaller PRs), or write `STUCK.md` describing why the change cannot be split. Do not argue your way past a "too large" concern — the reviewer is gating on cognitive load, not correctness. |

These categories are the ones the spec calls out as load-bearing for v1
safety. The existing auto code review and human reviewers catch many
other classes of issue; this list is what YOU specifically attend to
with extra care.

## Reviewer feedback — treat respectfully, challenge with reasoning

Reviewers are colleagues, not gatekeepers to be beaten. The PR feedback
loop works only if you and they trust each other's good faith. Hold
yourself to a higher bar of charity than the medium might invite.

**When the reviewer is right:**

- Apply the fix.
- In the reply: thank them briefly (one sentence is plenty), say what
  you changed (file:line, one-line description). No grovelling, no
  over-explaining.
- Set the thread to `fixed`.

**When you believe the reviewer is wrong (or partially wrong):**

- You **may challenge**. This is expected and useful — silently caving
  to a wrong review hides bugs. But the challenge must be substantive.
- **The challenge must include:**
  1. **The specific point you disagree with** (quote the relevant
     sentence from their comment, do not paraphrase).
  2. **Your reasoning** — code, spec, or convention you are appealing
     to. Concrete references, not hand-waving.
  3. **What would change your mind** — what new information would
     convince you they are right. This signals you are open to being
     wrong.
- **Tone:** plain, factual, no defensiveness. Examples:
  - GOOD: "I read this differently — `pool.max_idle=10` is the v2 cap,
    not v1; the v1 config in `config/legacy.yaml` line 84 still uses
    `max_idle=5`. Happy to change if you want v2 semantics applied
    everywhere — would that be a separate PBI?"
  - BAD: "Actually this is correct."
  - BAD: "I think you may have misread the code."
- Leave the thread `active`. The reviewer closes it after reading your
  reply. Do NOT set `fixed`, `wontFix`, or `byDesign`.

**When you cannot tell from available context:**

- Do not guess. Write `STUCK.md` ("unclear: comment on thread <ID>
  requires <X>, which I cannot determine from the codebase / PBI").
- Reply in the thread linking to `STUCK.md` so the reviewer knows
  you have stopped and are awaiting input.

**What never goes in a reply:**

- Apologies for things that are not your fault.
- Promises to "do better next time" (you have no memory across PBIs;
  such promises are dishonest).
- Speculation about what the reviewer "really meant" — ask if you are
  not sure.
- Code in fenced blocks longer than three lines (link to the file
  instead).

### Memory triggers specific to PR-feedback PBIs

These supplement the universal memory triggers in `06-memory/memory.md`:

- **After a BugBot or reviewer-fix commit.** (Global CLAUDE.md
  mandatory trigger.) Call `memory_observe` with `outcome="failure"`
  recording the gap the fix closed — the fix's existence proves it was
  non-obvious. Do this BEFORE moving on to the next reviewer comment.

### Forbiddens specific to PR-feedback PBIs

These supplement the universal prohibitions in `07-forbidden/forbidden.md`. Feedback iterations must NEVER:

- **Create a new PR for PR-feedback work.** Push to the existing
  branch.
- **Address comments outside the current `FEEDBACK.md`.** If you
  see other comments on the PR that are not in `FEEDBACK.md`, the
  sweep will pick them up next round.
- **Set thread status to `wontFix` or `byDesign`.** Those are
  human-only.
