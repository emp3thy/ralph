## When to write STUCK.md

Writing `STUCK.md` is the right call when you cannot proceed honestly.
It is a per-PBI halt; the executor moves the PBI to `blocked/` and the
loop continues with other work. A clean STUCK is **much** better than a
plausible-looking wrong fix.

Write `STUCK.md` when ANY of these trigger:

- **Repeating yourself.** `HISTORY.md` shows two or more prior attempts
  and the approach you are about to take is materially similar to one
  of them. Same hypothesis, same fix → stop.
- **Attempts >= 3 with no clear new angle.** Default to STUCK unless you
  can articulate, in writing, why this attempt is materially different
  from the previous ones.
- **Contradictory evidence.** The test passes locally and fails in CI
  at the same commit (or vice versa). Something is wrong about the
  environment assumption; stop and report rather than hacking around it.
- **PBI is ambiguous or contradicts the codebase.** The acceptance
  criteria conflict with conventions already in the repo, or the PBI
  references files/APIs that do not exist.
- **Fix would require crossing a safety line.** The only way you see to
  make this work involves one of:
  - **Deleting tests** that are not genuinely obsolete.
  - **Widening IAM / auth / permissions** beyond what the PBI
    authorises.
  - **Touching files outside the declared scope** without clear
    justification.
  - **Bypassing a security control** (input validation, secret
    handling, signature checks).
  If you find yourself reaching for any of these, STOP and write
  `STUCK.md` describing the situation. A human triages.
- **`docs/INVESTIGATE.md` is missing** and you cannot meaningfully
  debug a bug PBI without it.

### STUCK.md structure

Write `STUCK.md` in the PBI directory with this exact section structure:

```markdown
# STUCK — <PBI-id>

## What I tried
- Iteration <N>: <one-line description>
- Iteration <N+1>: <one-line description>
- ...

## What is blocking me
<one or two paragraphs — be specific. "I cannot tell if X" is good;
"this is hard" is not.>

## What would unblock me
- <specific input, file, decision, or clarification you would need>
- <if multiple, list each>

## My confidence with help
<integer 0-100>% — if a human gave me the unblockers above, how
confident am I that I can close this PBI on the next attempt?
```

Then exit. Do not commit code changes alongside `STUCK.md` — the file
itself is the only change. The executor moves the PBI to `blocked/` on
the next sweep.
