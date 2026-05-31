## How to ship a PR

For PR-feedback iterations there is no new PR. Push commits to the
existing branch named in `PR-LINK.md`; the PR auto-updates and CI
re-runs. See step 4 of `## How to work a PR-feedback PBI` for the
push protocol.

**Never write to `HISTORY.md` AFTER pushing the PR.** The append
happens BEFORE the push so the commit captures the full history.
