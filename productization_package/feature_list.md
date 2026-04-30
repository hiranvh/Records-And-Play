# Feature List

## 1) Record and Replay

- Teach workflows once using Record mode.
- Save reusable workflow JSON files.
- Replay consistently across environments and releases.

## 2) UI Drift Detection

- Compares recorded expectations with current page structure during replay.
- Flags mismatch signals that indicate front-end contract drift.

## 3) Missing and New Field Detection

- Missing fields: expected recorded fields that are not present.
- New fields: newly introduced required fields not present in baseline workflow.
- Supports faster triage for release-impacting form changes.

## 4) Self-Healing Selectors

- Uses fallback locator strategies when primary selectors fail.
- Captures healed selector counts as an explicit quality signal.

## 5) Replay Modes

- Lenient: prioritize continuity, fewer hard failures.
- Standard: balanced validation for regular regression runs.
- Strict: enforce strong checks for release gates.

## 6) Reporting

- Excel reports for reviewer-friendly QA summaries.
- JSON reports for machine ingestion, trend analysis, and integration.

## 7) Trend Analytics

- Exposes trend-ready run metrics (warnings, blockers, healed selectors, etc.).
- Supports run-over-run quality visibility and release confidence tracking.

## 8) Operational UX

- Dark-theme web console for record, replay, logs, and dashboard operations.
- Run history table and report download workflows.
- Live logs and QA summary indicators.

## 9) API-Driven Control Plane

- FastAPI endpoints orchestrate record/replay lifecycle.
- Designed for future integration with CI/CD and external QA systems.
