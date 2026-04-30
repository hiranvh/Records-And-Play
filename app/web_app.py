import os
import threading

import uvicorn
from fastapi import FastAPI

from agent import AgentEngine, run_autonomous_agent
from core.constants import stop_execution_event

from . import web_core
from .log_service import LogService
from .routes import RouteDependencies, register_routes
from .run_state import RunState

# Root directory of the project (one level above this file)
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="AI-Driven Automation Agent - Web Commander")


@app.on_event("shutdown")
def shutdown_event():
    print("Received shutdown signal. Stopping background tasks immediately...")
    stop_execution_event.set()


LOG_FILE = os.path.join(_ROOT_DIR, "logs", "recordandplay.logs")
logs_queue = []
latest_recorded_steps = []
last_recorded_url = ""
_agent_run_result: dict = {}

WORKFLOWS_DIR = os.path.join(os.getcwd(), "workflows")
os.makedirs(WORKFLOWS_DIR, exist_ok=True)

_scanner_log_file = os.path.join(_ROOT_DIR, "logs", "scanner_picked_fields.txt")
_log_service = LogService(
    log_file=LOG_FILE,
    scanner_log_file=_scanner_log_file,
    get_logs_queue=lambda: logs_queue,
)


def capture_log(message: str, log_type: str = "SYSTEM"):
    _log_service.capture_log(message, log_type)


def _clear_run_log():
    _log_service.clear_run_log()


agent = AgentEngine(update_callback=lambda msg: capture_log(msg, "SYSTEM"))


def _set_latest_recorded_steps(value):
    global latest_recorded_steps
    latest_recorded_steps = value


def _set_last_recorded_url(value: str):
    global last_recorded_url
    last_recorded_url = value


def _set_agent_run_result(value: dict):
    global _agent_run_result
    _agent_run_result = value


_run_state = RunState(
    get_logs_queue=lambda: logs_queue,
    get_latest_recorded_steps=lambda: latest_recorded_steps,
    set_latest_recorded_steps=_set_latest_recorded_steps,
    get_last_recorded_url=lambda: last_recorded_url,
    set_last_recorded_url=_set_last_recorded_url,
    get_agent_run_result=lambda: _agent_run_result,
    set_agent_run_result=_set_agent_run_result,
)


def _get_workflows_dir() -> str:
    return WORKFLOWS_DIR


def _get_log_file() -> str:
    return LOG_FILE


def _get_capture_log():
    return capture_log


def _get_clear_run_log():
    return _clear_run_log


def _get_run_autonomous_agent():
    return run_autonomous_agent


def _start_background_task(target):
    threading.Thread(target=target, daemon=True).start()


register_routes(
    app,
    RouteDependencies(
        root_dir=_ROOT_DIR,
        run_state=_run_state,
        web_core=web_core,
        agent=agent,
        get_workflows_dir=_get_workflows_dir,
        get_log_file=_get_log_file,
        get_capture_log=_get_capture_log,
        get_clear_run_log=_get_clear_run_log,
        get_run_autonomous_agent=_get_run_autonomous_agent,
        start_background_task=_start_background_task,
    ),
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)