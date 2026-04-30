# Playwright QA Replay Platform

A production-ready QA automation platform built with Python, FastAPI, Playwright, and a dark-theme web UI for recording workflows, replaying them with drift awareness, and generating actionable QA evidence.

## What This Platform Solves

Modern web apps change frequently. Manual regression checks miss subtle UI drift and consume QA bandwidth. This platform turns every replay into a verification run by comparing expected workflow structure against current UI behavior.

## Core Capabilities

- Record and Replay web workflows through a browser-driven UI.
- UI drift detection during replay.
- Missing field detection (expected fields not found).
- New field detection (unexpected required fields appearing).
- Self-healing selectors (fallback locator recovery).
- Replay strictness control with Lenient, Standard, and Strict modes.
- Exportable QA artifacts in Excel and JSON.
- Trend analytics payloads for run-over-run tracking.

## Technology Stack

- Backend: FastAPI, Uvicorn
- Browser automation: Playwright (Chromium)
- Data and reporting: openpyxl, pandas, Faker
- Frontend: HTML, CSS, JavaScript (dark theme UI)

## High-Level Architecture

- `app/`: FastAPI app setup, routes, run-state orchestration
- `recorder/`: Teaching mode and workflow capture
- `playback/`: Replay engine, discrepancy detection, report generation
- `agent/`: Command interpretation and autonomous orchestration
- `templates/`: Web UI (`index.html`, `logs.html`, `dashboard.html`)
- `workflows/`: Recorded workflow JSON files
- `reports/`: QA discrepancy Excel + JSON outputs

A text architecture diagram is available at `docs/productization/architecture_diagram.txt`.

## Quick Start

### Prerequisites

- Python 3.10+
- Playwright-compatible OS/browser dependencies
- Windows PowerShell or terminal

### Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

### Run

```powershell
python main.py
```

Open the web app at:

- http://127.0.0.1:8001

## Typical Workflow

1. Record a flow in Record mode and save it to `workflows/`.
2. Replay the workflow against target URL in Lenient/Standard/Strict mode.
3. Review replay analytics:
   - Missing fields
   - New fields
   - Healed selectors
   - Warnings
4. Download generated Excel and JSON reports from the UI.
5. Use trend-ready metrics for release confidence tracking.

## Reports and Evidence

Each replay can produce:

- JSON QA discrepancy payload for tooling integration and trend analysis.
- Excel report for stakeholder-friendly review and audit traceability.

Reports are saved under:

- `reports/`

## Productization Assets Included

See `docs/productization/` for:

- Resume bullet points
- Architecture diagram (text)
- Feature list
- Screenshots checklist
- 2-minute demo script
- GitHub project description
- Business value summary

## Recommended Next Steps

- Add CI-triggered replay smoke runs for critical workflows.
- Persist trend metrics to a datastore for release dashboards.
- Introduce role-based access and environment profiles for larger teams.

## License

Internal/proprietary unless otherwise specified.
