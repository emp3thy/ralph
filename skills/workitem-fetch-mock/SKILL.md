---
name: workitem-fetch-mock
description: Test fixture only. Not a real fetcher. The mock fetcher script is generated into a tmp_path by the orchestrator tests; this SKILL.md exists so the package layout is consistent and discoverable.
---

# workitem-fetch-mock

This is a placeholder for the test-fixture skill. The orchestrator tests in `tests/skills/test_ralph_add.py` create a temporary fetcher script in `tmp_path` and point `RALPH_WORKITEM_FETCH_SCRIPT` at it. There is no real `fetch.py` checked in here.
