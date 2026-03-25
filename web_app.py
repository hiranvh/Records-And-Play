from fastapi import FastAPI, Request, Form, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import os
import threading
from agent_engine import AgentEngine
import web_core

app = FastAPI(title="AI-Driven Automation Agent - Web Commander")

logs_queue = []

def capture_log(message: str, log_type: str = "SYSTEM"):
    logs_queue.append({"msg": message, "type": log_type})
    print(f"[{log_type}] {message}")

# Global agent engine
agent = AgentEngine(update_callback=lambda msg: capture_log(msg, "SYSTEM"))

WORKFLOWS_DIR = os.path.join(os.path.dirname(__file__), "workflows")


@app.get("/", response_class=HTMLResponse)
async def home():
    with open(os.path.join(os.path.dirname(__file__), "templates", "index.html"), "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content)

@app.get("/record-and-play", response_class=HTMLResponse)
async def record_and_play():
    with open(os.path.join(os.path.dirname(__file__), "templates", "index.html"), "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content)

@app.get("/api/workflows")
async def get_workflows():
    if not os.path.exists(WORKFLOWS_DIR):
        return []
    return [f for f in os.listdir(WORKFLOWS_DIR) if f.endswith('.json')]

@app.post("/api/workflow/delete")
async def delete_workflow(name: str = Form(...)):
    path = os.path.join(WORKFLOWS_DIR, name)
    if os.path.exists(path):
        os.remove(path)
        return {"status": "success", "message": f"Deleted workflow: {name}"}
    return {"status": "error", "message": "Workflow not found"}

@app.post("/api/workflow/compact")
async def compact_workflow(name: str = Form(...)):
    try:
        if not name.endswith('.json'):
            name += '.json'
        result = web_core.compact_workflow(name)
        return {"status": "success", "message": f"Compacted {name}: {result['before_count']} -> {result['after_count']} steps"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/execute")
async def execute_command(
    url: str = Form(...),
    workflow: str = Form("workflow.json"),
    command: str = Form(...)
):
    def run_agent():
        agent.execute_workflow(command, target_url=url, workflow_name=workflow)
    
    threading.Thread(target=run_agent, daemon=True).start()
    return {"status": "started", "message": "Execution started in background"}

@app.post("/api/replay")
async def replay_workflow(url: str = Form(...), workflow: str = Form("workflow.json")):
    def run_replay():
        capture_log(f"Replay started for {workflow}", "WARNING")
        success, msg, screenshot, _ = web_core.run_execution_mode(
            url, override_data={}, headless=False, workflow_name=workflow
        )
        if success:
            capture_log(f"Replay finished. Execution logged to Excel (.xlsx) & Screenshot saved to {screenshot}", "SUCCESS")
        else:
            capture_log(msg, "ERROR")
    
    threading.Thread(target=run_replay, daemon=True).start()
    return {"status": "started", "message": "Replay started"}

@app.post("/api/record")
async def record_workflow(url: str = Form(...), workflow: str = Form("workflow.json")):
    def run_record():
        capture_log("Starting recording mode...", "WARNING")
        web_core.start_teaching_mode(url, workflow_name=workflow)
        capture_log("Recording completed.", "SUCCESS")
    
    threading.Thread(target=run_record, daemon=True).start()
    return {"status": "started", "message": "Recording started (browser opened)"}

@app.post("/api/stop")
async def stop_execution():
    web_core.stop_execution_event.set()
    capture_log("Stop signal sent.", "ERROR")
    return {"status": "success", "message": "Stop signal sent"}

@app.get("/api/logs")
async def get_logs():
    global logs_queue
    out = list(logs_queue)
    logs_queue.clear()
    return JSONResponse(content=out)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)