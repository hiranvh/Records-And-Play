# 2-Minute Demo Script

Total runtime: 2:00
Audience: engineering managers, QA leads, product stakeholders
Goal: show business-ready value in one flow

## Pre-Demo Setup (Do Before Recording)

- Start app: `python main.py`
- Open `http://127.0.0.1:8001`
- Ensure one workflow exists in `workflows/`
- Ensure at least one prior replay exists in `reports/`
- Prepare a clean browser window at 1920x1080

## Script with Timeline

### 0:00 - 0:15 | Problem and Promise

Narration:
"Regression testing slows releases when UI changes break automation. This platform records workflows once, replays them with drift detection, and generates downloadable QA evidence in Excel and JSON."

Action:
- Show main page and workflow selector.

### 0:15 - 0:35 | Replay Controls and Modes

Narration:
"We can run in Lenient, Standard, or Strict mode depending on pipeline stage. Strict can act as a release gate."

Action:
- Navigate to Replay tab.
- Show mode dropdown and policy toggles.

### 0:35 - 0:55 | Start Replay

Narration:
"I will replay a recorded workflow against the target URL."

Action:
- Select workflow.
- Click Replay Selected Workflow.
- Keep replay log area visible.

### 0:55 - 1:20 | Analytics and Drift Signals

Narration:
"After execution, analytics cards summarize missing fields, new fields, healed selectors, warnings, and run mode. This gives immediate drift visibility."

Action:
- Highlight analytics cards.
- Point to run history table row updates.

### 1:20 - 1:40 | Report Evidence

Narration:
"Every run can produce evidence artifacts: Excel for reviewers and JSON for tooling and trend pipelines."

Action:
- Click Download Excel.
- Click Download JSON.
- Briefly show files (or browser download bar).

### 1:40 - 1:55 | Dashboard and Trend Readiness

Narration:
"The dashboard consolidates pass/warn/fail counts, healed selectors, unstable pages, and recent runs for release decision support."

Action:
- Open dashboard page.
- Scroll through summary and reports section.

### 1:55 - 2:00 | Close

Narration:
"This turns replay into QA intelligence: faster validation, earlier drift detection, and auditable release evidence."

Action:
- End on dashboard overview.

## Optional Alternate Closing (Technical Audience)

"The JSON payload includes trend-ready metrics, so teams can wire this into CI/CD and monitor quality over time."
