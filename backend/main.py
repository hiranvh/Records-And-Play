from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
import socket

try:
    from backend.routers import recording, flow_management, ai_service, config_service
except ModuleNotFoundError:
    from routers import recording, flow_management, ai_service, config_service

app = FastAPI(
    title="AI-Powered Record and Playback Automation Framework",
    description="An advanced automation framework for recording, managing, and replaying user interactions with AI-enhanced capabilities",
    version="1.0.0"
)

# Include API routers
app.include_router(recording.router, prefix="/record", tags=["Recording"])
app.include_router(flow_management.router, prefix="/flows", tags=["Flow Management"])
app.include_router(ai_service.router, prefix="/ai", tags=["AI Service"])
app.include_router(config_service.router, prefix="/config", tags=["Configuration"])

# Serve frontend static files
frontend_build_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "build")
if os.path.exists(os.path.join(frontend_build_path, "static")):
    app.mount("/static", StaticFiles(directory=os.path.join(frontend_build_path, "static")), name="static")

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "version": "1.0.0"}

# Serve the React application
@app.api_route("/{path_name:path}", methods=["GET"])
async def catch_all(path_name: str):
    # Do not intercept API module routes
    api_prefixes = ["record", "flows", "ai", "config", "health"]
    if any(path_name == prefix or path_name.startswith(prefix + "/") for prefix in api_prefixes):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "API route not found or requires trailing slash"})
        
    file_path = os.path.join(frontend_build_path, path_name)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    
    index_file = os.path.join(frontend_build_path, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    
    return {"message": "Frontend not built yet. Please run 'npm run build' in the frontend directory."}


def _find_available_port(host: str, preferred_port: int, max_attempts: int = 10) -> int:
    for port in range(preferred_port, preferred_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_socket:
            test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if test_socket.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError(f"No available port found in range {preferred_port}-{preferred_port + max_attempts - 1}")

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("AUTOMATION_FRAMEWORK_HOST", "0.0.0.0")
    preferred_port = int(os.getenv("AUTOMATION_FRAMEWORK_PORT", "8000"))
    port = _find_available_port("127.0.0.1", preferred_port)

    if port != preferred_port:
        print(f"Port {preferred_port} is busy, starting on port {port} instead.")

    uvicorn.run(app, host=host, port=port)