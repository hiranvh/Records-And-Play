import asyncio
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from core.constants import stop_recording_event, stop_execution_event

from .run_state import RunState


@dataclass
class RouteDependencies:
    root_dir: str
    run_state: RunState
    web_core: Any
    agent: Any
    get_workflows_dir: Callable[[], str]
    get_log_file: Callable[[], str]
    get_capture_log: Callable[[], Callable[[str, str], None]]
    get_clear_run_log: Callable[[], Callable[[], None]]
    get_run_autonomous_agent: Callable[[], Callable[..., dict]]
    start_background_task: Callable[[Callable[[], None]], None]


def register_routes(app: FastAPI, deps: RouteDependencies) -> None:
    reports_dir = os.path.join(deps.root_dir, "reports")
    run_history: List[Dict[str, Any]] = []
    run_history_lock = threading.Lock()
    max_run_history = 120

    def _ensure_reports_dir() -> None:
        os.makedirs(reports_dir, exist_ok=True)

    def _snapshot_report_files() -> Dict[str, Set[str]]:
        _ensure_reports_dir()
        snapshot: Dict[str, Set[str]] = {"json": set(), "excel": set()}
        for filename in os.listdir(reports_dir):
            full_path = os.path.join(reports_dir, filename)
            if not os.path.isfile(full_path):
                continue
            lower_name = filename.lower()
            if "_qa_discrepancy_" not in lower_name:
                continue
            if lower_name.endswith(".json"):
                snapshot["json"].add(os.path.abspath(full_path))
            elif lower_name.endswith(".xlsx"):
                snapshot["excel"].add(os.path.abspath(full_path))
        return snapshot

    def _latest_path(paths: Set[str]) -> Optional[str]:
        if not paths:
            return None
        return max(paths, key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0.0)

    def _load_report_payload(path: Optional[str]) -> Dict[str, Any]:
        if not path:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _resolve_replay_status(
        ok: bool,
        message: str,
        qa_outcome: str,
        warnings: int,
        blockers: int,
    ) -> str:
        outcome = str(qa_outcome or "").strip().lower()
        msg = str(message or "").strip().lower()
        if blockers > 0 or "failed" in outcome:
            return "failed"
        if warnings > 0 or "warning" in outcome:
            return "warning"
        if ok:
            return "passed"
        if "warning" in msg:
            return "warning"
        return "failed"

    def _report_download_info(path: Optional[str]) -> Dict[str, str]:
        if not path:
            return {"name": "", "download": ""}
        filename = os.path.basename(path)
        if not filename:
            return {"name": "", "download": ""}
        return {
            "name": filename,
            "download": f"/api/reports/file?name={filename}",
        }

    def _serialize_run_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        report_json_info = _report_download_info(entry.get("report_json"))
        report_excel_info = _report_download_info(entry.get("report_excel"))
        return {
            "run_id": entry.get("run_id", ""),
            "workflow": entry.get("workflow", ""),
            "url": entry.get("url", ""),
            "replay_mode": entry.get("replay_mode", "standard"),
            "status": entry.get("status", "running"),
            "message": entry.get("message", ""),
            "started_at": entry.get("started_at", ""),
            "completed_at": entry.get("completed_at", ""),
            "duration_seconds": float(entry.get("duration_seconds", 0.0) or 0.0),
            "warnings": int(entry.get("warnings", 0) or 0),
            "blockers": int(entry.get("blockers", 0) or 0),
            "healed_selectors": int(entry.get("healed_selectors", 0) or 0),
            "unstable_pages": int(entry.get("unstable_pages", 0) or 0),
            "total_discrepancies": int(entry.get("total_discrepancies", 0) or 0),
            "reports": {
                "json": report_json_info,
                "excel": report_excel_info,
            },
        }

    def _add_run_entry(entry: Dict[str, Any]) -> None:
        with run_history_lock:
            run_history.insert(0, entry)
            if len(run_history) > max_run_history:
                del run_history[max_run_history:]

    def _update_run_entry(run_id: str, updates: Dict[str, Any]) -> None:
        with run_history_lock:
            for item in run_history:
                if str(item.get("run_id")) == run_id:
                    item.update(updates)
                    break

    def _history_snapshot(limit: int = 20) -> List[Dict[str, Any]]:
        capped_limit = max(1, min(limit, max_run_history))
        with run_history_lock:
            copy_items = [dict(item) for item in run_history[:capped_limit]]
        return copy_items

    def _resolve_report_file(name: str) -> Optional[str]:
        if not name:
            return None
        normalized = os.path.basename(name)
        if normalized != name:
            return None

        _ensure_reports_dir()
        reports_root = os.path.abspath(reports_dir)
        candidate = os.path.abspath(os.path.join(reports_dir, normalized))
        try:
            if os.path.commonpath([reports_root, candidate]) != reports_root:
                return None
        except ValueError:
            return None

        if not os.path.exists(candidate) or not os.path.isfile(candidate):
            return None
        return candidate

    def _list_report_catalog(limit: int = 25) -> List[Dict[str, Any]]:
        snapshot = _snapshot_report_files()
        records: List[Dict[str, Any]] = []
        for json_path in snapshot["json"]:
            base_path, _ = os.path.splitext(json_path)
            excel_path = f"{base_path}.xlsx"
            payload = _load_report_payload(json_path)
            generated_at = str(payload.get("generated_at") or "")
            qa_outcome = str(payload.get("qa_outcome") or "")
            workflow_name = str(payload.get("workflow_name") or "")
            records.append(
                {
                    "workflow": workflow_name,
                    "generated_at": generated_at,
                    "qa_outcome": qa_outcome,
                    "json": _report_download_info(json_path),
                    "excel": _report_download_info(excel_path if os.path.exists(excel_path) else None),
                    "modified_at": os.path.getmtime(json_path),
                }
            )

        records.sort(key=lambda item: float(item.get("modified_at", 0.0)), reverse=True)
        capped_limit = max(1, min(limit, 100))
        for item in records:
            item.pop("modified_at", None)
        return records[:capped_limit]

    @app.get("/", response_class=HTMLResponse)
    @app.get("/record-and-play", response_class=HTMLResponse)
    async def home():
        with open(os.path.join(deps.root_dir, "templates", "index.html"), "r", encoding="utf-8") as file_obj:
            content = file_obj.read()
        return HTMLResponse(content=content)

    @app.get("/dashboard", response_class=HTMLResponse)
    @app.get("/record-and-play/dashboard", response_class=HTMLResponse)
    async def dashboard_page():
        path = os.path.join(deps.root_dir, "templates", "dashboard.html")
        with open(path, "r", encoding="utf-8") as file_obj:
            return HTMLResponse(content=file_obj.read())

    @app.get("/logs", response_class=HTMLResponse)
    async def view_logs():
        log_file = deps.get_log_file()
        if not os.path.exists(log_file):
            return HTMLResponse("No logs.")
        with open(log_file, "r", encoding="utf-8") as file_obj:
            content = file_obj.read()
        return HTMLResponse(
            f"<html><body style='background:#0f172a; color:#f8fafc; font-family:monospace; padding:20px;'><pre>{content}</pre></body></html>"
        )

    @app.get("/api/workflows")
    async def get_workflows():
        workflows_dir = deps.get_workflows_dir()
        return [filename for filename in os.listdir(workflows_dir) if filename.endswith(".json")]

    @app.post("/api/workflow/delete")
    async def delete_workflow(name: str = Form(...)):
        workflows_dir = deps.get_workflows_dir()
        filename = name if name.endswith(".json") else f"{name}.json"
        file_path = os.path.join(workflows_dir, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            return {"status": "success", "message": f"Deleted {filename}"}
        return {"status": "error", "message": "Not found"}

    @app.post("/api/workflow/compact")
    async def compact_workflow(name: str = Form(...)):
        result = deps.web_core.compact_workflow(name)
        return JSONResponse(content=result)

    @app.post("/api/workflow/rename")
    async def rename_workflow(
        old_name: str = Form(...),
        new_name: str = Form(...),
        overwrite: bool = Form(default=False),
    ):
        try:
            result = deps.web_core.rename_workflow(old_name, new_name, overwrite=overwrite)
            return JSONResponse(content=result)
        except FileNotFoundError as exc:
            return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=404)
        except FileExistsError as exc:
            return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=409)
        except ValueError as exc:
            return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=500)

    @app.post("/api/workflow/save")
    async def save_last_recorded(
        name: str = Form(...),
        force_save: bool = Form(default=False),
    ):
        latest_steps = deps.run_state.get_latest_recorded_steps()
        if not latest_steps:
            return {"status": "error", "message": "No recording found in memory."}

        filename = name if name.endswith(".json") else f"{name}.json"
        result = deps.web_core.save_workflow(
            deps.run_state.get_last_recorded_url(),
            latest_steps,
            filename,
            force_save=force_save,
        )

        if result.get("status") == "warning":
            audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
            return {
                "status": "warning",
                "message": result.get("message") or "Recorder quality audit warning",
                "missing_count": int(audit.get("missing_count", 0) or 0),
                "required_missing_count": int(audit.get("required_missing_count", 0) or 0),
                "missing_fields": audit.get("missing_fields") or [],
                "truncated": bool(audit.get("truncated", False)),
            }

        if not result.get("success"):
            return {
                "status": "error",
                "message": result.get("message") or "Failed to save workflow",
            }

        deps.run_state.set_latest_recorded_steps([])
        return {
            "status": "success",
            "message": result.get("message") or f"Workflow saved as '{filename}'",
        }

    @app.post("/api/chat")
    async def chat_commander(
        command: str = Form(...),
        url: str = Form(default=""),
        workflow: str = Form(default=""),
    ):
        capture_log = deps.get_capture_log()
        capture_log(f"Commander: {command}", "USER")
        intent = deps.agent.parse_commander_intent(command)
        task, count = intent.get("task", "enrollment"), intent.get("count", 1)
        intent_overrides = intent.get("overrides", {}) if isinstance(intent.get("overrides"), dict) else {}

        preferred_workflow = workflow.strip() or intent.get("workflow_name") or f"{task}.json"
        resolved_workflow = deps.agent.resolve_workflow_name(preferred_workflow, fallback_task=task)

        capture_log(f"Parsed: task={task} x{count} | workflow={resolved_workflow}", "AI")
        if intent_overrides:
            capture_log(f"Commander values: {json.dumps(intent_overrides)}", "AI")
        else:
            capture_log(
                "No explicit values provided. Agent will only fill fields backed by supplied runtime data.",
                "AI",
            )

        def run_loop():
            deps.get_clear_run_log()()
            data = deps.agent.load_spreadsheet_data("data.xlsx")
            for index in range(count):
                if deps.web_core.stop_execution_event.is_set():
                    break

                capture = deps.get_capture_log()
                capture(f"Iteration {index+1}/{count}...", "SYSTEM")

                sheet_row = data[index] if len(data) > index and isinstance(data[index], dict) else {}
                merged_overrides = {**sheet_row, **intent_overrides, "iteration": index + 1}
                execution_data = deps.agent.build_intelligent_execution_data(merged_overrides, task=task)

                run_url = url.strip() or None
                ok, message, _, _ = deps.web_core.run_execution_mode(
                    run_url,
                    execution_data,
                    workflow_name=resolved_workflow,
                    update_callback=capture,
                )
                capture(f"Done iteration {index+1}: {message}", "SUCCESS" if ok else "ERROR")
                time.sleep(1)

        deps.start_background_task(run_loop)
        return {
            "status": "started",
            "message": "Running loop...",
            "task": task,
            "count": count,
            "workflow": resolved_workflow,
        }

    @app.post("/api/replay")
    async def replay_workflow(
        url: str = Form(...),
        workflow: str = Form("workflow.json"),
        replay_mode: str = Form(default=""),
        fail_on_missing_required_fields: bool = Form(default=False),
        fail_on_new_required_fields: bool = Form(default=False),
        fail_on_not_filled_fields: bool = Form(default=False),
    ):
        mode_value = str(replay_mode or "").strip().lower()
        if mode_value not in {"lenient", "standard", "strict"}:
            mode_value = "standard"

        run_started_ts = time.time()
        run_id = f"replay-{int(run_started_ts * 1000)}"
        _add_run_entry(
            {
                "run_id": run_id,
                "workflow": workflow,
                "url": url,
                "replay_mode": mode_value,
                "status": "running",
                "message": "Replay running",
                "started_at": datetime.fromtimestamp(run_started_ts).strftime("%Y-%m-%d %H:%M:%S"),
                "completed_at": "",
                "duration_seconds": 0.0,
                "warnings": 0,
                "blockers": 0,
                "healed_selectors": 0,
                "unstable_pages": 0,
                "total_discrepancies": 0,
                "report_json": "",
                "report_excel": "",
            }
        )

        def run():
            deps.get_clear_run_log()()
            deps.web_core.stop_execution_event.clear()

            capture = deps.get_capture_log()

            report_snapshot_before = _snapshot_report_files()

            replay_overrides: Dict[str, Any] = {}
            if str(replay_mode or "").strip():
                replay_overrides["replay_mode"] = mode_value
            if fail_on_missing_required_fields:
                replay_overrides["fail_on_missing_required_fields"] = True
            if fail_on_new_required_fields:
                replay_overrides["fail_on_new_required_fields"] = True
            if fail_on_not_filled_fields:
                replay_overrides["fail_on_not_filled_fields"] = True

            capture(f"Replaying '{workflow}'", "WARNING")

            try:
                ok, message, _, _ = deps.web_core.run_execution_mode(
                    url,
                    replay_overrides,
                    False,
                    workflow,
                    update_callback=capture,
                )

                report_snapshot_after = _snapshot_report_files()
                new_json_files = report_snapshot_after["json"] - report_snapshot_before["json"]
                new_excel_files = report_snapshot_after["excel"] - report_snapshot_before["excel"]

                report_json = _latest_path(new_json_files) or _latest_path(report_snapshot_after["json"])
                report_excel = _latest_path(new_excel_files)

                if not report_excel and report_json:
                    inferred_excel = f"{os.path.splitext(report_json)[0]}.xlsx"
                    if os.path.exists(inferred_excel):
                        report_excel = inferred_excel

                payload = _load_report_payload(report_json)
                qa_summary = payload.get("qa_summary") if isinstance(payload.get("qa_summary"), dict) else {}
                run_metrics = payload.get("run_metrics") if isinstance(payload.get("run_metrics"), dict) else {}
                instability = (
                    payload.get("instability_indicators")
                    if isinstance(payload.get("instability_indicators"), dict)
                    else {}
                )

                warnings = int(qa_summary.get("warnings", run_metrics.get("warnings", 0)) or 0)
                blockers = int(qa_summary.get("blockers", run_metrics.get("blockers", 0)) or 0)
                healed_selectors = int(
                    run_metrics.get("healed_matches", qa_summary.get("healed_matches", 0)) or 0
                )
                unstable_pages_list = instability.get("pages_with_most_discrepancies")
                unstable_pages = len(unstable_pages_list) if isinstance(unstable_pages_list, list) else 0
                total_discrepancies = int(qa_summary.get("total_discrepancies", 0) or 0)
                qa_outcome = str(payload.get("qa_outcome") or "")
                final_message = qa_outcome or message
                status = _resolve_replay_status(ok, final_message, qa_outcome, warnings, blockers)

                completed_ts = time.time()
                _update_run_entry(
                    run_id,
                    {
                        "status": status,
                        "message": final_message,
                        "completed_at": datetime.fromtimestamp(completed_ts).strftime("%Y-%m-%d %H:%M:%S"),
                        "duration_seconds": round(max(0.0, completed_ts - run_started_ts), 2),
                        "warnings": warnings,
                        "blockers": blockers,
                        "healed_selectors": healed_selectors,
                        "unstable_pages": unstable_pages,
                        "total_discrepancies": total_discrepancies,
                        "report_json": report_json or "",
                        "report_excel": report_excel or "",
                    },
                )
                capture(f"Replay finished: {final_message}", "SUCCESS" if ok else "ERROR")
            except Exception as exc:
                completed_ts = time.time()
                error_message = str(exc) or "Replay failed"
                _update_run_entry(
                    run_id,
                    {
                        "status": "failed",
                        "message": error_message,
                        "completed_at": datetime.fromtimestamp(completed_ts).strftime("%Y-%m-%d %H:%M:%S"),
                        "duration_seconds": round(max(0.0, completed_ts - run_started_ts), 2),
                    },
                )
                capture(f"Replay finished: {error_message}", "ERROR")

        deps.start_background_task(run)
        return {"status": "started"}

    @app.post("/api/record")
    async def record_workflow(url: str = Form(...)):
        deps.run_state.set_last_recorded_url(url)

        def run():
            deps.get_clear_run_log()()
            capture = deps.get_capture_log()
            capture(f"Recording @ {url}...", "WARNING")
            steps = deps.web_core.start_teaching_mode(url, update_callback=capture)
            deps.run_state.set_latest_recorded_steps(steps)
            capture(f"Recording captured {len(steps)} steps. Sending save prompt...", "SUCCESS")
            deps.run_state.append_log_event("RECORDING_FINISHED", "COMMAND")

        deps.start_background_task(run)
        return {"status": "started"}

    @app.post("/api/stop")
    async def stop_execution():
        deps.web_core.stop_execution_event.set()
        stop_recording_event.set()
        deps.get_capture_log()("Stop signal sent.", "ERROR")
        return {"status": "success", "message": "Stopped"}

    @app.post("/api/agent/run")
    async def agent_run(url: str = Form(...), override_data: str = Form(default="{}")):
        deps.run_state.reset_agent_run_result()

        try:
            overrides = json.loads(override_data) if override_data.strip() else {}
        except Exception:
            overrides = {}

        group_name = str(overrides.get("group_name") or overrides.get("group") or "").strip() or None

        capture_log = deps.get_capture_log()
        capture_log(f"🤖 Autonomous Agent starting on: {url}", "SYSTEM")
        if group_name:
            capture_log(f"🤖 Autonomous Agent group target: {group_name}", "AI")
        elif "benefit-test.com/fbmc" in (url or "").lower():
            capture_log(
                "⚠️ FBMC run started without group_name. Use Agent Commander with 'under group name ...' or set override_data {\"group_name\":\"Cairn Industries\"}.",
                "WARNING",
            )

        def _run():
            deps.get_clear_run_log()()
            stop_execution_event.clear()
            capture = deps.get_capture_log()
            result = deps.get_run_autonomous_agent()(
                start_url=url,
                override_data=overrides,
                group_name=group_name,
                headless=False,
                update_callback=capture,
            )
            deps.run_state.set_agent_run_result(result)
            status_level = "SUCCESS" if result.get("status") == "success" else "ERROR"
            capture(
                f"🤖 Agent finished: status={result.get('status')}, "
                f"pages={result.get('pages_processed', 0)}, "
                f"filled={result.get('fields_filled', 0)}",
                status_level,
            )

        deps.start_background_task(_run)
        return {"status": "started", "message": "Autonomous agent running..."}

    @app.post("/api/agent/command")
    async def agent_command(command: str = Form(...), url: str = Form(...)):
        deps.run_state.reset_agent_run_result()

        cmd = (command or "").strip()
        run_url = (url or "").strip()
        if not cmd:
            return JSONResponse(content={"status": "error", "message": "command is required"}, status_code=400)
        if not run_url:
            return JSONResponse(content={"status": "error", "message": "url is required for agent mode"}, status_code=400)

        deps.get_clear_run_log()()
        capture_log = deps.get_capture_log()
        capture_log(f"🤖 Agent Commander: {cmd}", "USER")
        capture_log("Agent commander running (workflow-guided)...", "AI")

        intent = deps.agent.parse_commander_intent(cmd)
        task = intent.get("task", "enrollment")
        try:
            count = max(1, int(intent.get("count", 1)))
        except Exception:
            count = 1
        intent_overrides = intent.get("overrides", {}) if isinstance(intent.get("overrides"), dict) else {}

        preferred_workflow = intent.get("workflow_name") or f"{task}.json"
        resolved_workflow = deps.agent.resolve_workflow_name(preferred_workflow, fallback_task=task)

        if intent_overrides:
            capture_log(f"Agent Commander values: {json.dumps(intent_overrides)}", "AI")
        else:
            capture_log("Agent Commander: no explicit values, auto-generating missing enrollment data.", "AI")
        capture_log(f"Agent Commander: workflow={resolved_workflow}, task={task} x{count}", "AI")

        def _run():
            deps.web_core.stop_execution_event.clear()
            runs = []
            total_steps = 0
            all_errors = []
            final_status = "success"

            for index in range(count):
                if deps.web_core.stop_execution_event.is_set():
                    break

                merged = {**intent_overrides, "iteration": index + 1}
                execution_data = deps.agent.build_intelligent_execution_data(merged, task=task)
                group_name = str(execution_data.get("group_name") or execution_data.get("group") or "").strip() or None

                capture = deps.get_capture_log()
                capture(f"🤖 Agent iteration {index + 1}/{count} — group: {group_name or '(none)'}", "SYSTEM")

                success, message, _, steps = deps.web_core.run_execution_mode(
                    url=run_url,
                    override_data=execution_data,
                    workflow_name=resolved_workflow,
                    update_callback=capture,
                    group_name=group_name,
                )

                run_result = {
                    "status": "success" if success else "failed",
                    "message": message,
                    "steps": len(steps) if isinstance(steps, list) else 0,
                    "group_name": group_name,
                }
                runs.append(run_result)
                total_steps += run_result["steps"]

                if not success:
                    all_errors.append(message or f"iteration {index+1} failed")
                    final_status = "failed"

            deps.run_state.set_agent_run_result(
                {
                    "status": final_status,
                    "task": task,
                    "workflow": resolved_workflow,
                    "count": count,
                    "total_steps": total_steps,
                    "errors": all_errors,
                    "runs": runs,
                }
            )

            status_level = "SUCCESS" if final_status == "success" else "ERROR"
            deps.get_capture_log()(
                f"🤖 Agent Commander finished: status={final_status}, workflow={resolved_workflow}, steps={total_steps}",
                status_level,
            )

        deps.start_background_task(_run)
        return {
            "status": "started",
            "message": "Agent commander running (workflow-guided)...",
            "task": task,
            "workflow": resolved_workflow,
            "count": count,
        }

    @app.get("/api/agent/status")
    async def agent_status():
        return JSONResponse(content=deps.run_state.get_agent_run_result())

    @app.get("/api/runs/history")
    async def runs_history(limit: int = 20):
        items = [_serialize_run_entry(item) for item in _history_snapshot(limit=limit)]
        return JSONResponse(content={"items": items})

    @app.get("/api/dashboard/summary")
    async def dashboard_summary(limit: int = 20):
        entries = [_serialize_run_entry(item) for item in _history_snapshot(limit=limit)]
        counts = {"passed": 0, "warning": 0, "failed": 0, "running": 0}
        healed_total = 0
        unstable_total = 0

        for item in entries:
            status = str(item.get("status") or "").lower()
            if status in counts:
                counts[status] += 1
            healed_total += int(item.get("healed_selectors", 0) or 0)
            unstable_total += int(item.get("unstable_pages", 0) or 0)

        return JSONResponse(
            content={
                "counts": counts,
                "healed_selectors": healed_total,
                "unstable_pages": unstable_total,
                "last_runs": entries[:8],
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    @app.get("/api/reports")
    async def reports_catalog(limit: int = 25):
        return JSONResponse(content={"items": _list_report_catalog(limit=limit)})

    @app.get("/api/reports/file")
    async def report_file(name: str):
        resolved = _resolve_report_file(name)
        if not resolved:
            raise HTTPException(status_code=404, detail="Report file not found")

        lower = resolved.lower()
        media_type = "application/octet-stream"
        if lower.endswith(".json"):
            media_type = "application/json"
        elif lower.endswith(".xlsx"):
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        return FileResponse(resolved, filename=os.path.basename(resolved), media_type=media_type)

    @app.get("/api/logs")
    async def get_logs():
        return JSONResponse(content=deps.run_state.consume_logs())

    @app.get("/api/logs/events")
    async def get_log_events(request: Request):
        async def generator():
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break

                events = deps.run_state.consume_logs()
                if events:
                    yield f"data: {json.dumps({'events': events})}\n\n"

                await asyncio.sleep(0.35)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.get("/record-and-play/logs", response_class=HTMLResponse)
    async def logs_viewer():
        path = os.path.join(deps.root_dir, "templates", "logs.html")
        with open(path, "r", encoding="utf-8") as file_obj:
            return HTMLResponse(content=file_obj.read())

    @app.get("/api/logs/stream")
    async def logs_stream(request: Request):
        async def generator():
            log_file = deps.get_log_file()
            if os.path.exists(log_file):
                try:
                    with open(log_file, "r", encoding="utf-8") as file_obj:
                        content = file_obj.read()
                    if content:
                        # SSE events must end with a blank line to be dispatched by EventSource.
                        yield f"data: {json.dumps({'type': 'bulk', 'lines': content})}\n\n"
                except Exception:
                    pass

            last_size = os.path.getsize(log_file) if os.path.exists(log_file) else 0
            while True:
                if await request.is_disconnected():
                    break
                await asyncio.sleep(0.4)
                try:
                    if not os.path.exists(log_file):
                        last_size = 0
                        continue
                    size = os.path.getsize(log_file)
                    if size < last_size:
                        last_size = 0
                        yield f"data: {json.dumps({'type': 'reset'})}\n\n"
                    if size > last_size:
                        with open(log_file, "r", encoding="utf-8") as file_obj:
                            file_obj.seek(last_size)
                            new_content = file_obj.read()
                        last_size = size
                        if new_content.strip():
                            yield f"data: {json.dumps({'type': 'append', 'lines': new_content})}\n\n"
                except Exception:
                    pass

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/logs/clear")
    async def clear_logs_endpoint():
        deps.get_clear_run_log()()
        return {"status": "success", "message": "Logs cleared"}

    @app.get("/api/logs/download")
    async def download_logs():
        log_file = deps.get_log_file()
        if not os.path.exists(log_file):
            raise HTTPException(status_code=404, detail="Log file not found")
        return FileResponse(log_file, filename="recordandplay.logs", media_type="text/plain")
