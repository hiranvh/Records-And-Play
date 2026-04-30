from app.web_app import app
import uvicorn

if __name__ == "__main__":
    print("🚀 Starting AI-Driven Automation Agent - Web Commander...")
    print("Access the web interface at: http://localhost:8001")
    # Changed port to 8001 to match user request
    uvicorn.run(app, host="127.0.0.1", port=8001)