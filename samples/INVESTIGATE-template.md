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
