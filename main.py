from web_app import app
import uvicorn

if __name__ == "__main__":
    print("Starting AI-Driven Automation Agent - Web Commander...")
    print("Access the web interface at: http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8001)
