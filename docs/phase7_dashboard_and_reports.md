# Phase 7: Dashboard, Reports, and Replay UX

This phase adds QA-focused web UX and additive APIs without changing existing route contracts.

## New pages

- `GET /record-and-play/dashboard` (also `GET /dashboard`): QA dashboard with replay metrics, run history, and report downloads.

## Existing pages enhanced

- `templates/index.html`
  - Replay mode selector (`lenient`, `standard`, `strict`)
  - Optional fail policy checkboxes
  - Embedded recent run history table
  - Embedded report download list
  - Quick links to dashboard and live logs

- `templates/logs.html`
  - Dashboard navigation shortcuts
  - QA summary strip (pass/warn/fail, unstable pages, healed selectors, latest replay)

## New additive APIs

- `GET /api/dashboard/summary`
  - Returns aggregate counters and latest runs.

- `GET /api/runs/history`
  - Returns in-memory replay run history captured at `/api/replay` execution time.

- `GET /api/reports`
  - Lists discrepancy report bundles found under `reports/`.

- `GET /api/reports/file?name=<filename>`
  - Downloads a report artifact from `reports/` with path traversal protection.

## Backward compatibility notes

- `POST /api/replay` still returns `{"status": "started"}`.
- Existing request fields (`url`, `workflow`) are unchanged.
- New replay fields are optional and additive:
  - `replay_mode`
  - `fail_on_missing_required_fields`
  - `fail_on_new_required_fields`
  - `fail_on_not_filled_fields`
- When optional fields are omitted, replay executes with the same defaults as before.
