## How to work a feature PBI

1. **Read** in the order specified above.
2. **Open `PLAN.md`** and find the next unchecked checkbox step (e.g.
   `- [ ] 3. Register the route...`).
3. **Do that one step.** Write code, write tests, run them. Follow
   test-driven development where the step lends itself to it.
4. **Check the box** in `PLAN.md` once the step is complete and verified.
5. **Append to `HISTORY.md`** a short, factual record of what you did
   this iteration. Format:
   ```
   ## Iteration <N> — <ISO timestamp>

   - Step <K>: <one-line description of what was done>
   - Tests: <green/red and which>
   - Notes: <anything the next iteration needs to know>
   ```
6. **Commit on the feature branch** (`ralph/<PBI-id>`, already checked
   out by the executor). Use a conventional-commit message scoped to
   this PBI:
   ```
   feat(<area>): <PBI-id> — <what changed>
   ```
7. **If all `PLAN.md` boxes are now checked**, proceed to "How to ship a
   PR" below. Otherwise, exit cleanly — the next iteration will pick up
   from the next unchecked step.
