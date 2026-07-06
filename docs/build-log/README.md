# Build Log

Records of what went wrong and what was fixed while building the backend, kept
separate by source so each is clean and complete.

## Module 0 — Platform Foundation (flat files, kept for history)
- [`code-review-findings.md`](code-review-findings.md) — issues flagged by the
  **automated code-review gate**: severity, cause, fix, how detected, plus deferred
  items and verified-correct positives.
- [`build-corrections.md`](build-corrections.md) — problems caught **during
  development** (environment, git/PR, integration, self-verification) that were
  not review-agent findings.

## Per-module folders (from Module 1 onward)
Each feature module gets its own folder with its build/QA logs inside:
- [`onboarding/`](onboarding/) — Module 1 (Onboarding): build approach, code-review
  findings, and test-gate record.

Later modules add their own `docs/build-log/<module>/` folder the same way.
