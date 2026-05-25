# Workspace + Samples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap the Ralph Python project (uv, ruff, mypy strict, pytest), create one sample PBI of each type (feature, bug, pr-feedback) plus an INVESTIGATE template, and land a Python validator that gates sample correctness via the canonical PBI frontmatter schema defined in the orchestrator.

**Architecture:** A tiny standalone validator module (`scripts/validate_samples.py`) parses the YAML frontmatter at the top of the per-type entry file (`PBI.md` / `BUG.md` / `FEEDBACK.md`) and asserts type-appropriate sibling files exist. A pytest test (`tests/test_workspace_samples.py`) drives the validator over every directory under `samples/`. The samples themselves are static markdown trees that the rest of the implementation (Plans 3, 4, 7) will use as fixtures.

**Tech Stack:** Python 3.12+, uv, pytest, ruff, mypy strict, YAML+markdown

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Project metadata, dependencies (`pyyaml`), and `[tool.ruff]` / `[tool.mypy]` / `[tool.pytest.ini_options]` configuration. |
| `.gitignore` | Ignores `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `dist/`, `build/`, `*.egg-info/`. |
| `README.md` | One-paragraph repo intro plus the bootstrap commands (`uv sync`, `uv run pytest`). |
| `ralph_executor/__init__.py` | Empty package marker so mypy has a target to type-check from day one. |
| `tests/__init__.py` | Empty package marker for the tests tree. |
| `tests/test_workspace_samples.py` | Pytest tests that invoke the validator against every sample directory and assert the bootstrap toolchain is wired (`pyyaml` importable, sample tree exists). |
| `scripts/__init__.py` | Empty package marker so the validator is importable from tests. |
| `scripts/validate_samples.py` | Parses YAML frontmatter from each sample's entry file, validates the required-field set per PBI type, and verifies required sibling files exist. Exposes `validate_sample(path: Path) -> list[str]` returning a list of error strings (empty list = OK), plus a `main()` CLI. |
| `samples/INVESTIGATE-template.md` | Per-service debugging guide template (the eight sections specified in the spec). |
| `samples/feature-WI-1234/PBI.md` | Sample feature PBI: frontmatter + body for "add /healthz endpoint". |
| `samples/feature-WI-1234/PLAN.md` | Five-step checkbox plan for the healthz endpoint. |
| `samples/feature-WI-1234/HISTORY.md` | Empty placeholder file (executor appends after each attempt). |
| `samples/bug-deploy-rosa-irsa-2026-05-23/BUG.md` | Sample bug PBI: frontmatter + body describing IRSA `AccessDenied` failure on ROSA. |
| `samples/bug-deploy-rosa-irsa-2026-05-23/REPRODUCE.md` | kubectl / aws commands that surface the failure locally. |
| `samples/bug-deploy-rosa-irsa-2026-05-23/HISTORY.md` | Empty placeholder. |
| `samples/pr-feedback-WI-1234-r2/FEEDBACK.md` | Round-2 PR feedback PBI: frontmatter + two sample reviewer comments using the spec's boilerplate. |
| `samples/pr-feedback-WI-1234-r2/PR-LINK.md` | Placeholder PR URL, branch, and commit SHA at time of feedback. |
| `samples/pr-feedback-WI-1234-r2/ORIGINAL.md` | Points at `samples/feature-WI-1234/PBI.md` as the parent work item. |
| `samples/pr-feedback-WI-1234-r2/HISTORY.md` | Empty placeholder. |

---

## Task 1 — Bootstrap the Python project

**Files**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `ralph_executor/__init__.py`
- Create: `tests/__init__.py`
- Create: `scripts/__init__.py`

**Steps**

- [ ] 1. Verify the Python toolchain is present. Run:
  ```
  uv --version
  python --version
  ```
  Expected: `uv` reports a version (any ≥ 0.4); `python` reports 3.12 or higher. If `uv` is missing, install per https://docs.astral.sh/uv/getting-started/installation/ before continuing. If Python is below 3.12, run `uv python install 3.12`.

- [ ] 2. Confirm the repo root is empty of Python config. Run:
  ```
  ls pyproject.toml .gitignore README.md 2>/dev/null || true
  ```
  Expected: none of those files exist yet (or, if `.gitignore` / `README.md` exist, capture their content before overwriting — the bootstrap REPLACES them).

- [ ] 3. Write `pyproject.toml` with the exact content below:
  ```toml
  [project]
  name = "ralph"
  version = "0.1.0"
  description = "Ralph v1 — per-repo autonomous coding loop executor and supervisor skills"
  requires-python = ">=3.12"
  dependencies = [
      "pyyaml>=6.0",
  ]

  [dependency-groups]
  dev = [
      "pytest>=8.0",
      "ruff>=0.5",
      "mypy>=1.10",
      "types-pyyaml>=6.0",
  ]

  [tool.ruff]
  line-length = 100
  target-version = "py312"

  [tool.ruff.lint]
  select = ["E", "F", "I", "B", "UP", "SIM"]

  [tool.mypy]
  python_version = "3.12"
  strict = true
  files = ["ralph_executor", "scripts", "tests"]

  [tool.pytest.ini_options]
  testpaths = ["tests"]
  addopts = "-ra"
  ```

- [ ] 4. Write `.gitignore` with the exact content below:
  ```
  # Python
  __pycache__/
  *.py[cod]
  *.egg-info/
  build/
  dist/

  # Tooling caches
  .venv/
  .pytest_cache/
  .ruff_cache/
  .mypy_cache/

  # Ralph runtime + tooling
  .ralph/
  .worktrees/
  .superpowers/

  # Editor / OS
  .vscode/
  .idea/
  .DS_Store
  Thumbs.db
  ```

- [ ] 5. Write `README.md` with the exact content below:
  ```markdown
  # Ralph

  Ralph v1 — a per-repo autonomous coding loop. See
  `docs/superpowers/specs/2026-05-23-ralph-v1-per-repo-loop-design.md` for the design
  and `docs/superpowers/plans/2026-05-24-00-orchestrator.md` for the implementation plan.

  ## Bootstrap

  ```bash
  uv sync
  uv run pytest
  uv run ruff check .
  uv run mypy ralph_executor scripts tests
  ```
  ```

- [ ] 6. Create the package markers. Each file is exactly the single line `"""Empty package marker."""\n`:
  - `ralph_executor/__init__.py`
  - `tests/__init__.py`
  - `scripts/__init__.py`

- [ ] 7. Sync the environment and confirm the toolchain is wired. Run:
  ```
  uv sync
  ```
  Expected: `uv` creates `.venv/`, installs `pyyaml`, `pytest`, `ruff`, `mypy`, `types-pyyaml`, and reports `Resolved N packages` with no errors.

- [ ] 8. Run the linters against the (currently empty) source tree to prove the configs parse:
  ```
  uv run ruff check .
  uv run mypy ralph_executor scripts tests
  ```
  Expected: ruff reports `All checks passed!`; mypy reports `Success: no issues found in 3 source files`.

- [ ] 9. Stage and commit. Run:
  ```
  git add pyproject.toml .gitignore README.md uv.lock ralph_executor/__init__.py tests/__init__.py scripts/__init__.py
  git commit -m "chore(bootstrap): scaffold Python project with uv, ruff, mypy strict, pytest"
  ```
  Note: `uv.lock` is generated by `uv sync` in step 7 and is checked in for reproducible builds (uv's recommended practice for applications).
  Expected: commit succeeds.

---

## Task 2 — Write the failing validator test

**Files**
- Test: `tests/test_workspace_samples.py`

**Steps**

- [ ] 1. Write `tests/test_workspace_samples.py` with the exact content below. The test imports the validator (which does not exist yet) so the failure mode is `ImportError`/`ModuleNotFoundError`:
  ```python
  """Workspace + sample-PBI tests.

  Drives ``scripts.validate_samples`` against every directory under ``samples/``
  to verify that the canonical PBI frontmatter and sibling-file layout hold.
  """
  from __future__ import annotations

  from pathlib import Path

  import pytest

  from scripts.validate_samples import validate_sample

  REPO_ROOT = Path(__file__).resolve().parent.parent
  SAMPLES_DIR = REPO_ROOT / "samples"

  REQUIRED_SAMPLE_DIRS = (
      "feature-WI-1234",
      "bug-deploy-rosa-irsa-2026-05-23",
      "pr-feedback-WI-1234-r2",
  )


  def test_samples_dir_exists() -> None:
      assert SAMPLES_DIR.is_dir(), f"samples/ directory missing at {SAMPLES_DIR}"


  def test_investigate_template_present() -> None:
      template = SAMPLES_DIR / "INVESTIGATE-template.md"
      assert template.is_file(), "samples/INVESTIGATE-template.md is required"
      body = template.read_text(encoding="utf-8")
      for heading in (
          "Service overview",
          "Key classes",
          "How to run locally",
          "How to read",
          "Configuration",
          "External dependencies",
          "gotchas",
          "Tests",
      ):
          assert heading.lower() in body.lower(), (
              f"INVESTIGATE-template.md missing section referencing '{heading}'"
          )


  @pytest.mark.parametrize("sample_name", REQUIRED_SAMPLE_DIRS)
  def test_required_sample_dirs_exist(sample_name: str) -> None:
      assert (SAMPLES_DIR / sample_name).is_dir(), (
          f"required sample directory missing: samples/{sample_name}"
      )


  @pytest.mark.parametrize("sample_name", REQUIRED_SAMPLE_DIRS)
  def test_sample_validates(sample_name: str) -> None:
      errors = validate_sample(SAMPLES_DIR / sample_name)
      assert errors == [], (
          f"validator errors for samples/{sample_name}:\n  - "
          + "\n  - ".join(errors)
      )
  ```

- [ ] 2. Run the test to confirm it fails for the expected reason. Run:
  ```
  uv run pytest tests/test_workspace_samples.py -v
  ```
  Expected: collection fails with `ModuleNotFoundError: No module named 'scripts.validate_samples'` (the validator does not exist yet). This is the red step.

- [ ] 3. Do NOT commit yet — the failing test is consumed in Task 3, then committed together with the validator implementation.

---

## Task 3 — Implement the validator

**Files**
- Create: `scripts/validate_samples.py`

**Steps**

- [ ] 1. Write `scripts/validate_samples.py` with the exact content below:
  ```python
  """Validate sample PBI directories against the canonical frontmatter schema.

  The schema is defined in
  ``docs/superpowers/plans/2026-05-24-00-orchestrator.md`` (Cross-plan
  integration points) and mirrored here for runtime enforcement.
  """
  from __future__ import annotations

  import argparse
  import sys
  from collections.abc import Mapping
  from datetime import datetime
  from pathlib import Path
  from typing import Any

  import yaml

  REQUIRED_FRONTMATTER_FIELDS: tuple[str, ...] = (
      "id",
      "type",
      "status",
      "severity",
      "attempts",
      "created_at",
      "updated_at",
  )

  ALLOWED_TYPES: frozenset[str] = frozenset({"feature", "bug", "pr-feedback"})
  ALLOWED_STATUSES: frozenset[str] = frozenset(
      {"inbox", "current", "pending-pr", "done", "blocked", "archive"}
  )
  ALLOWED_SEVERITIES: frozenset[str] = frozenset(
      {"critical", "high", "normal", "low"}
  )

  ENTRY_FILE_BY_TYPE: Mapping[str, str] = {
      "feature": "PBI.md",
      "bug": "BUG.md",
      "pr-feedback": "FEEDBACK.md",
  }

  REQUIRED_SIBLINGS_BY_TYPE: Mapping[str, tuple[str, ...]] = {
      "feature": ("PBI.md", "PLAN.md", "HISTORY.md"),
      "bug": ("BUG.md", "REPRODUCE.md", "HISTORY.md"),
      "pr-feedback": ("FEEDBACK.md", "PR-LINK.md", "ORIGINAL.md", "HISTORY.md"),
  }


  def _split_frontmatter(text: str) -> tuple[str, str] | None:
      """Return ``(frontmatter_yaml, body)`` if the file starts with a YAML block.

      Returns ``None`` if no leading ``---`` fence is present.
      """
      if not text.startswith("---"):
          return None
      # The first line must be exactly '---' (allowing trailing newline only).
      lines = text.splitlines()
      if not lines or lines[0].strip() != "---":
          return None
      for idx in range(1, len(lines)):
          if lines[idx].strip() == "---":
              frontmatter = "\n".join(lines[1:idx])
              body = "\n".join(lines[idx + 1 :])
              return frontmatter, body
      return None


  def _infer_type_from_dirname(dir_name: str) -> str | None:
      if dir_name.startswith("feature-"):
          return "feature"
      if dir_name.startswith("bug-"):
          return "bug"
      if dir_name.startswith("pr-feedback-"):
          return "pr-feedback"
      return None


  def _validate_frontmatter(
      frontmatter: Mapping[str, Any], expected_type: str | None
  ) -> list[str]:
      errors: list[str] = []

      missing = [f for f in REQUIRED_FRONTMATTER_FIELDS if f not in frontmatter]
      if missing:
          errors.append(f"missing frontmatter fields: {sorted(missing)}")

      pbi_type = frontmatter.get("type")
      if pbi_type is not None and pbi_type not in ALLOWED_TYPES:
          errors.append(
              f"type={pbi_type!r} not in allowed set {sorted(ALLOWED_TYPES)}"
          )
      if expected_type is not None and pbi_type != expected_type:
          errors.append(
              f"type={pbi_type!r} does not match directory-name-inferred "
              f"type {expected_type!r}"
          )

      status = frontmatter.get("status")
      if status is not None and status not in ALLOWED_STATUSES:
          errors.append(
              f"status={status!r} not in allowed set {sorted(ALLOWED_STATUSES)}"
          )

      severity = frontmatter.get("severity")
      if severity is not None and severity not in ALLOWED_SEVERITIES:
          errors.append(
              f"severity={severity!r} not in allowed set "
              f"{sorted(ALLOWED_SEVERITIES)}"
          )

      attempts = frontmatter.get("attempts")
      if attempts is not None and not isinstance(attempts, int):
          errors.append(f"attempts must be int, got {type(attempts).__name__}")
      elif isinstance(attempts, int) and attempts < 0:
          errors.append(f"attempts must be >= 0, got {attempts}")

      for field in ("created_at", "updated_at"):
          value = frontmatter.get(field)
          if value is None:
              continue
          if isinstance(value, datetime):
              continue
          if isinstance(value, str):
              try:
                  datetime.fromisoformat(value)
              except ValueError:
                  errors.append(
                      f"{field}={value!r} is not a valid ISO-8601 datetime"
                  )
          else:
              errors.append(
                  f"{field} must be a datetime or ISO-8601 string, "
                  f"got {type(value).__name__}"
              )

      pbi_id = frontmatter.get("id")
      if pbi_id is not None and not isinstance(pbi_id, str):
          errors.append(f"id must be a string, got {type(pbi_id).__name__}")
      elif isinstance(pbi_id, str) and not pbi_id.strip():
          errors.append("id must be a non-empty string")

      return errors


  def validate_sample(sample_dir: Path) -> list[str]:
      """Validate a single sample PBI directory.

      Returns a list of error strings. An empty list means the sample is valid.
      """
      errors: list[str] = []

      if not sample_dir.is_dir():
          return [f"not a directory: {sample_dir}"]

      expected_type = _infer_type_from_dirname(sample_dir.name)
      if expected_type is None:
          errors.append(
              f"directory name {sample_dir.name!r} must start with "
              f"'feature-', 'bug-', or 'pr-feedback-'"
          )
          return errors

      entry_file_name = ENTRY_FILE_BY_TYPE[expected_type]
      entry_file = sample_dir / entry_file_name
      if not entry_file.is_file():
          errors.append(f"missing entry file: {entry_file_name}")
          return errors

      try:
          text = entry_file.read_text(encoding="utf-8")
      except OSError as exc:
          return [f"failed to read {entry_file_name}: {exc}"]

      split = _split_frontmatter(text)
      if split is None:
          errors.append(
              f"{entry_file_name} does not start with a YAML frontmatter block "
              f"(expected leading '---' fence)"
          )
          return errors

      frontmatter_yaml, _body = split
      try:
          parsed: Any = yaml.safe_load(frontmatter_yaml)
      except yaml.YAMLError as exc:
          return [f"{entry_file_name} frontmatter is not valid YAML: {exc}"]

      if not isinstance(parsed, Mapping):
          return [
              f"{entry_file_name} frontmatter must be a YAML mapping, "
              f"got {type(parsed).__name__}"
          ]

      errors.extend(_validate_frontmatter(parsed, expected_type))

      for sibling in REQUIRED_SIBLINGS_BY_TYPE[expected_type]:
          if not (sample_dir / sibling).is_file():
              errors.append(f"missing required sibling file: {sibling}")

      return errors


  def main(argv: list[str] | None = None) -> int:
      parser = argparse.ArgumentParser(
          description="Validate sample PBI directories."
      )
      parser.add_argument(
          "samples_dir",
          type=Path,
          help="Path to the samples/ directory.",
      )
      args = parser.parse_args(argv)

      samples_dir: Path = args.samples_dir
      if not samples_dir.is_dir():
          print(f"error: not a directory: {samples_dir}", file=sys.stderr)
          return 2

      any_errors = False
      for child in sorted(samples_dir.iterdir()):
          if not child.is_dir():
              continue
          errors = validate_sample(child)
          if errors:
              any_errors = True
              print(f"FAIL {child.name}", file=sys.stderr)
              for err in errors:
                  print(f"  - {err}", file=sys.stderr)
          else:
              print(f"OK   {child.name}")

      return 1 if any_errors else 0


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] 2. Run the validator test again — it should now fail because the sample directories don't exist yet. Run:
  ```
  uv run pytest tests/test_workspace_samples.py -v
  ```
  Expected: `test_samples_dir_exists` fails (no `samples/` directory). `test_required_sample_dirs_exist` and `test_sample_validates` fail with the same root cause. `test_investigate_template_present` also fails. This confirms the validator imports cleanly but has nothing to validate yet.

- [ ] 3. Run mypy and ruff against the new module to verify the strict configs hold:
  ```
  uv run ruff check scripts/validate_samples.py tests/test_workspace_samples.py
  uv run mypy scripts/validate_samples.py tests/test_workspace_samples.py
  ```
  Expected: both report success (`All checks passed!` and `Success: no issues found in 2 source files`).

- [ ] 4. Stage and commit. Run:
  ```
  git add scripts/validate_samples.py tests/test_workspace_samples.py
  git commit -m "test(samples): add sample-PBI validator and failing workspace test"
  ```
  Expected: commit succeeds. The test is intentionally red; it goes green in Task 7 after every sample is created.

---

## Task 4 — Create the feature sample PBI

**Files**
- Create: `samples/feature-WI-1234/PBI.md`
- Create: `samples/feature-WI-1234/PLAN.md`
- Create: `samples/feature-WI-1234/HISTORY.md`

**Steps**

- [ ] 1. Write `samples/feature-WI-1234/PBI.md` with the exact content below:
  ```markdown
  ---
  id: WI-1234
  type: feature
  status: inbox
  severity: normal
  attempts: 0
  created_at: 2026-05-24T09:15:00+00:00
  updated_at: 2026-05-24T09:15:00+00:00
  ---

  # Add `/healthz` endpoint to service-auth

  ## Context

  Platform requires every service to expose a liveness probe at `GET /healthz`
  that returns `200 OK` with a small JSON body when the process can serve
  traffic. service-auth currently has no liveness probe, which blocks the
  ROSA migration backlog (the deployment manifest references a probe path that
  does not exist; the pod is marked unhealthy on first scrape).

  ## Acceptance criteria

  - `GET /healthz` returns status `200` with body `{"status":"ok"}` when the
    process is running.
  - The endpoint requires no authentication.
  - The endpoint completes in under 50ms p99 against the existing test
    harness.
  - A new test in `tests/test_health.py` asserts the contract.

  ## Constraints

  - Do not introduce a new web framework. Use the existing FastAPI app
    factory in `service_auth/app.py`.
  - Do not log on the hot path — health checks fire every 10s and would
    drown the log stream.
  ```

- [ ] 2. Write `samples/feature-WI-1234/PLAN.md` with the exact content below:
  ```markdown
  # Implementation plan — WI-1234 — /healthz endpoint

  - [ ] 1. Add `tests/test_health.py` with a failing test that asserts
    `GET /healthz` returns 200 and `{"status":"ok"}`.
  - [ ] 2. Register a `/healthz` route on the FastAPI app in
    `service_auth/app.py` that returns the JSON payload above.
  - [ ] 3. Verify the new test passes and the existing test suite still
    passes (`pytest -q`).
  - [ ] 4. Confirm no new log lines are emitted by the route under the
    existing log configuration.
  - [ ] 5. Update `README.md` with a one-line note that `/healthz` is the
    liveness path.
  ```

- [ ] 3. Create the empty placeholder. Run:
  ```
  touch samples/feature-WI-1234/HISTORY.md
  ```
  (Or, on Windows PowerShell: `New-Item -ItemType File samples/feature-WI-1234/HISTORY.md`.)
  Expected: a zero-byte file exists at that path.

- [ ] 4. Validate just this sample to confirm it parses. Run:
  ```
  uv run python scripts/validate_samples.py samples
  ```
  Expected: `OK   feature-WI-1234` printed. (Other samples are absent and the script simply skips non-existent dirs; it only iterates existing children.)

- [ ] 5. Stage and commit. Run:
  ```
  git add samples/feature-WI-1234/
  git commit -m "feat(samples): add feature-WI-1234 sample PBI (/healthz endpoint)"
  ```
  Expected: commit succeeds.

---

## Task 5 — Create the bug sample PBI

**Files**
- Create: `samples/bug-deploy-rosa-irsa-2026-05-23/BUG.md`
- Create: `samples/bug-deploy-rosa-irsa-2026-05-23/REPRODUCE.md`
- Create: `samples/bug-deploy-rosa-irsa-2026-05-23/HISTORY.md`

**Steps**

- [ ] 1. Write `samples/bug-deploy-rosa-irsa-2026-05-23/BUG.md` with the exact content below:
  ```markdown
  ---
  id: BUG-deploy-rosa-irsa-2026-05-23
  type: bug
  status: inbox
  severity: critical
  attempts: 0
  created_at: 2026-05-23T22:41:00+00:00
  updated_at: 2026-05-23T22:41:00+00:00
  ---

  # service-auth pod on ROSA: AccessDenied calling STS via IRSA

  ## Failure signature

  ```
  botocore.exceptions.ClientError: An error occurred (AccessDenied) when
  calling the AssumeRoleWithWebIdentity operation: Not authorized to perform
  sts:AssumeRoleWithWebIdentity
  ```

  ## Where it fires

  `service_auth/aws/credentials.py:get_session` on pod startup, before the
  HTTP server binds. The container restarts in a `CrashLoopBackOff`.

  ## Environment

  - Cluster: `rosa-prod-eu-west-1`
  - Namespace: `service-auth`
  - ServiceAccount: `service-auth-runtime`
  - Image: `service-auth:2026-05-23.1`

  ## What we know

  - The same image runs cleanly on the EKS staging cluster, where IRSA is
    also configured.
  - The ServiceAccount on ROSA has the annotation
    `eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/service-auth`.
  - The role's trust policy lists the EKS staging OIDC provider but not the
    ROSA OIDC provider.

  ## Hypothesis (unverified)

  Trust policy on the IAM role does not include the ROSA cluster's OIDC
  issuer, so STS rejects the web identity token.

  ## What success looks like

  - Pod reaches `Running` and serves traffic on `/healthz` (when that
    endpoint exists; see WI-1234).
  - No `AccessDenied` lines in the first 5 minutes of pod logs.
  ```

- [ ] 2. Write `samples/bug-deploy-rosa-irsa-2026-05-23/REPRODUCE.md` with the exact content below:
  ```markdown
  # Reproduce — BUG-deploy-rosa-irsa-2026-05-23

  Run these commands from a machine with `kubectl` pointed at
  `rosa-prod-eu-west-1` and `aws` configured for the target account.

  ## 1. Confirm the pod is crash-looping

  ```bash
  kubectl -n service-auth get pods -l app=service-auth
  kubectl -n service-auth logs -l app=service-auth --tail=50
  ```

  Expected output: at least one pod in `CrashLoopBackOff`; the tail of
  the log includes the `AccessDenied` signature shown in `BUG.md`.

  ## 2. Inspect the ServiceAccount annotation

  ```bash
  kubectl -n service-auth get sa service-auth-runtime -o yaml \
    | grep eks.amazonaws.com/role-arn
  ```

  Expected output: a single line containing the IAM role ARN.

  ## 3. Inspect the IAM role trust policy

  ```bash
  aws iam get-role --role-name service-auth \
    --query 'Role.AssumeRolePolicyDocument' --output json | jq .
  ```

  Expected output: a trust policy whose `Federated` principal lists the
  EKS staging OIDC provider but not the ROSA cluster's OIDC issuer URL.

  ## 4. Confirm the ROSA OIDC issuer

  ```bash
  rosa describe cluster -c rosa-prod-eu-west-1 \
    | grep -E "OIDC|Issuer"
  ```

  Expected output: the cluster's OIDC issuer URL. Compare against the
  trust policy from step 3; if it's missing, the hypothesis in
  `BUG.md` is confirmed.

  ## Stop condition

  The bug is reproduced when step 3 shows the ROSA OIDC issuer is absent
  from the trust policy AND step 1 still shows `AccessDenied`.
  ```

- [ ] 3. Create the empty placeholder. Run:
  ```
  touch samples/bug-deploy-rosa-irsa-2026-05-23/HISTORY.md
  ```

- [ ] 4. Validate the new sample. Run:
  ```
  uv run python scripts/validate_samples.py samples
  ```
  Expected: `OK   bug-deploy-rosa-irsa-2026-05-23` and `OK   feature-WI-1234`.

- [ ] 5. Stage and commit. Run:
  ```
  git add samples/bug-deploy-rosa-irsa-2026-05-23/
  git commit -m "feat(samples): add bug sample PBI (ROSA IRSA AccessDenied)"
  ```
  Expected: commit succeeds.

---

## Task 6 — Create the PR-feedback sample PBI

**Files**
- Create: `samples/pr-feedback-WI-1234-r2/FEEDBACK.md`
- Create: `samples/pr-feedback-WI-1234-r2/PR-LINK.md`
- Create: `samples/pr-feedback-WI-1234-r2/ORIGINAL.md`
- Create: `samples/pr-feedback-WI-1234-r2/HISTORY.md`

**Steps**

- [ ] 1. Write `samples/pr-feedback-WI-1234-r2/FEEDBACK.md` with the exact content below. The body uses the spec's boilerplate verbatim, with two concrete sample comments substituted in:
  ```markdown
  ---
  id: PR-feedback-WI-1234-r2
  type: pr-feedback
  status: inbox
  severity: high
  attempts: 0
  created_at: 2026-05-24T11:02:00+00:00
  updated_at: 2026-05-24T11:02:00+00:00
  ---

  # PR feedback — PBI WI-1234 — round 2

  **PR:** !4711 — "WI-1234: add /healthz endpoint"
  **Branch:** ralph/WI-1234
  **Commit at time of feedback:** 9f1c0a3d1e4b2c5a7f8b6e3d4c5a9b8e7d6c5b4a
  **Reviewers:** alice@example.com, bob@example.com
  **Feedback collected at:** 2026-05-24T11:00:00+00:00

  ## Active comments

  ### Comment 1 — file: service_auth/app.py, line 42
  - **Thread ID:** 8821
  - **Reviewer:** alice@example.com
  - **Posted:** 2026-05-24T10:58:14+00:00

  > The new route is registered before the existing `@app.middleware("http")`
  > logging middleware. Move the registration below it so the health probes
  > are excluded from the access log filter we already added in PR !4690.

  ---

  ### Comment 2 — file: tests/test_health.py, line 12
  - **Thread ID:** 8822
  - **Reviewer:** bob@example.com
  - **Posted:** 2026-05-24T10:59:47+00:00

  > The test asserts `response.json() == {"status": "ok"}` but the body in
  > the implementation is `{"status":"ok"}` with no space. The test passes
  > because `json()` round-trips, but please add an assertion on the raw
  > content-type header (`application/json`) so a future regression that
  > switches to `text/plain` would fail loudly.

  ---

  ## Instructions for Ralph

  For each active comment above:

  1. **Read the comment carefully** and examine the referenced code
     (file:line for line comments; the diff for general comments).

  2. **Decide: is the reviewer correct?**
     - If the concern is valid → implement the fix.
     - If the concern is incorrect or based on a misunderstanding →
       don't change the code. Prepare a clear, respectful reply explaining why.
     - If you can't tell from available context → write STUCK.md
       with what's unclear.

  3. **Always reply.** Use the `ado-pr` skill to post in the thread.
     Tell the reviewer:
     - What you changed (filename, line, brief description), OR
     - Why you didn't change anything (your reasoning).

  4. **Set thread status after replying:**
     - Applied a fix → set thread to **fixed**.
     - Challenged AND your reasoning is strong → leave **active**;
       the reviewer will close it after reading.
     - Never set **wontFix** or **byDesign** — those are human-only.

  5. **Commit and push** to the existing branch after all comments addressed.
     The PR will auto-update and CI re-runs.

  ## Constraints
  - Don't open a new PR. Push commits to the existing branch.
  - Don't address comments outside this FEEDBACK.md (no scope creep).
  - If a comment references work that should be a separate PBI
    ("can we also rename X?"), note it in your reply and don't do it here.
  - If you challenge and the reviewer later responds with a counter, that
    becomes a new FEEDBACK round — don't try to predict their response.
  ```

- [ ] 2. Write `samples/pr-feedback-WI-1234-r2/PR-LINK.md` with the exact content below:
  ```markdown
  # PR link — PBI WI-1234 — round 2

  - **PR URL:** https://dev.azure.com/example-org/example-project/_git/service-auth/pullrequest/4711
  - **PR ID:** 4711
  - **Branch:** ralph/WI-1234
  - **Target branch:** main
  - **Commit at time of feedback:** 9f1c0a3d1e4b2c5a7f8b6e3d4c5a9b8e7d6c5b4a
  - **Sample status:** placeholder values — the PR does not exist on a real ADO instance.
  ```

- [ ] 3. Write `samples/pr-feedback-WI-1234-r2/ORIGINAL.md` with the exact content below:
  ```markdown
  # Original PBI — WI-1234

  This feedback round operates against the feature PBI defined in
  `samples/feature-WI-1234/PBI.md`. The executor copies (or links) the
  original PBI here so Ralph can read the full context without leaving
  the PBI directory.

  - **Original PBI ID:** WI-1234
  - **Original PBI path:** `samples/feature-WI-1234/PBI.md`
  - **Type:** feature
  ```

- [ ] 4. Create the empty placeholder. Run:
  ```
  touch samples/pr-feedback-WI-1234-r2/HISTORY.md
  ```

- [ ] 5. Validate the new sample. Run:
  ```
  uv run python scripts/validate_samples.py samples
  ```
  Expected: `OK` for all three sample directories. No `FAIL` lines.

- [ ] 6. Stage and commit. Run:
  ```
  git add samples/pr-feedback-WI-1234-r2/
  git commit -m "feat(samples): add pr-feedback sample PBI (WI-1234 round 2)"
  ```
  Expected: commit succeeds.

---

## Task 7 — Create the INVESTIGATE template and turn the workspace test green

**Files**
- Create: `samples/INVESTIGATE-template.md`

**Steps**

- [ ] 1. Write `samples/INVESTIGATE-template.md` with the exact content below. The eight section headings match what `tests/test_workspace_samples.py::test_investigate_template_present` searches for (case-insensitive substring match):
  ```markdown
  # INVESTIGATE.md — `<service-name>`

  > Template. Copy this file to `docs/INVESTIGATE.md` in your service repo
  > and fill in each section. Ralph reads this guide every time it works
  > a bug PBI against this service; treat it as the manual a senior engineer
  > would hand a junior on day one of debugging.

  ## Service overview

  One or two paragraphs:
  - What this service does (the verb, not the noun).
  - Why it exists (what would break if it went away).
  - Where it sits in the wider system (upstream callers, downstream
    dependencies, ownership team).

  ## Key classes and modules

  A pointer map into the codebase. For each major class or module, give
  one sentence about its responsibility and the path to its file.

  | Symbol | Path | Responsibility |
  |---|---|---|
  | `<ClassName>` | `<path/to/file.py>` | `<one-line responsibility>` |

  ## How to run locally

  Minimum viable setup to reproduce real behaviour. Include:
  - Required environment variables and where to obtain test values.
  - The exact commands to start the service (e.g. `uv run service-auth serve`).
  - How to hit the service once it's running (curl, browser, test client).
  - Any non-obvious prerequisites (Docker, local databases, fake AWS).

  ## How to read the logs

  - Log format (JSON, key=value, plain text) and where to inspect them
    locally vs in each deployment environment.
  - The signal patterns that matter most (e.g. `request.id=`, error codes,
    correlation IDs).
  - Where to look first when something is wrong (which file, which level,
    which time window).

  ## Configuration

  - All environment variables the service reads, with type, default, and
    purpose.
  - Where secrets come from in each environment (local env vars vs ROSA
    pod secrets vs AWS Secrets Manager).
  - Where deployment manifests live (path in repo and what they configure).

  ## External dependencies

  What this service talks to, and how to spot when one of these is the
  actual cause of a failure:
  - Other internal services (URLs, auth model, expected latency).
  - Cloud APIs (AWS services and which IAM permissions are required).
  - Databases / queues / caches.
  - Third-party APIs.

  For each dependency, note the failure modes that look like a bug in
  THIS service but actually originate elsewhere.

  ## Common gotchas

  Non-obvious things that bite first-timers. Examples:
  - "The retry layer swallows 4xx responses by default; check raw HTTP
    before assuming the call succeeded."
  - "Local dev uses fake-S3 by default; if a test passes locally but
    fails in CI, suspect real-S3 differences."

  ## Tests

  - Where tests live (e.g. `tests/`).
  - How to run them (`uv run pytest`, plus any markers or selection
    patterns).
  - What's covered well vs known gaps.
  - How to run a single failing test for fast iteration.
  ```

- [ ] 2. Run the full workspace test suite. It must now be green end-to-end:
  ```
  uv run pytest tests/test_workspace_samples.py -v
  ```
  Expected output (exact ordering may vary):
  ```
  tests/test_workspace_samples.py::test_samples_dir_exists PASSED
  tests/test_workspace_samples.py::test_investigate_template_present PASSED
  tests/test_workspace_samples.py::test_required_sample_dirs_exist[feature-WI-1234] PASSED
  tests/test_workspace_samples.py::test_required_sample_dirs_exist[bug-deploy-rosa-irsa-2026-05-23] PASSED
  tests/test_workspace_samples.py::test_required_sample_dirs_exist[pr-feedback-WI-1234-r2] PASSED
  tests/test_workspace_samples.py::test_sample_validates[feature-WI-1234] PASSED
  tests/test_workspace_samples.py::test_sample_validates[bug-deploy-rosa-irsa-2026-05-23] PASSED
  tests/test_workspace_samples.py::test_sample_validates[pr-feedback-WI-1234-r2] PASSED
  ```
  All eight tests pass. No warnings about missing files or YAML errors.

- [ ] 3. Run the validator directly as a CLI check (belt-and-braces). Run:
  ```
  uv run python scripts/validate_samples.py samples
  ```
  Expected stdout:
  ```
  OK   bug-deploy-rosa-irsa-2026-05-23
  OK   feature-WI-1234
  OK   pr-feedback-WI-1234-r2
  ```
  Exit code 0.

- [ ] 4. Stage and commit. Run:
  ```
  git add samples/INVESTIGATE-template.md
  git commit -m "feat(samples): add INVESTIGATE.md template and turn validator green"
  ```
  Expected: commit succeeds.

---

## Verification

This is the orchestrator's Plan 1 verification gate. Both commands must pass before declaring Plan 1 complete.

- [ ] 1. Run the workspace test selection:
  ```
  uv run pytest tests/ -k workspace
  ```
  Expected: all eight tests collected and pass. Exit code 0.

- [ ] 2. Confirm the three sample directories exist with at least their entry files:
  ```
  ls samples/feature-WI-1234/PBI.md \
     samples/bug-deploy-rosa-irsa-2026-05-23/BUG.md \
     samples/pr-feedback-WI-1234-r2/FEEDBACK.md \
     samples/INVESTIGATE-template.md
  ```
  Expected: all four paths print without error.

- [ ] 3. Run the full toolchain pass to confirm Plan 1 leaves the repo green for downstream plans:
  ```
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy ralph_executor scripts tests
  uv run pytest
  ```
  Expected: all four commands exit 0. Ruff: `All checks passed!`. Format check: no files would be reformatted. Mypy: `Success: no issues found`. Pytest: all tests pass.

- [ ] 4. Confirm the commit history is clean and conventional:
  ```
  git log --oneline -10
  ```
  Expected: five Plan-1 commits visible, each with a conventional-commit prefix:
  - `chore(bootstrap): scaffold Python project with uv, ruff, mypy strict, pytest`
  - `test(samples): add sample-PBI validator and failing workspace test`
  - `feat(samples): add feature-WI-1234 sample PBI (/healthz endpoint)`
  - `feat(samples): add bug sample PBI (ROSA IRSA AccessDenied)`
  - `feat(samples): add pr-feedback sample PBI (WI-1234 round 2)`
  - `feat(samples): add INVESTIGATE.md template and turn validator green`

If any of these steps fail, fix in place — do NOT mark Plan 1 complete with a known-red gate.
