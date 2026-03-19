from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import json

router = APIRouter()

# In-memory storage for recordings (in production, use a database)
recordings_db = {}

@router.post("/start")
async def start_recording(session_name: str):
    """Start a new recording session"""
    recording_id = len(recordings_db) + 1
    recordings_db[recording_id] = {
        "id": recording_id,
        "name": session_name,
        "status": "recording",
        "steps": [],
        "created_at": "2026-03-19T00:00:00Z"
    }
    return {"message": "Recording started", "recording_id": recording_id}

@router.post("/stop/{recording_id}")
async def stop_recording(recording_id: int):
    """Stop and save current recording"""
    if recording_id not in recordings_db:
        raise HTTPException(status_code=404, detail="Recording not found")

    recordings_db[recording_id]["status"] = "completed"
    return {"message": "Recording stopped and saved", "recording_id": recording_id}

@router.post("/step/{recording_id}")
async def add_step(recording_id: int, step: Dict[str, Any]):
    """Add a step to the recording"""
    if recording_id not in recordings_db:
        raise HTTPException(status_code=404, detail="Recording not found")

    recordings_db[recording_id]["steps"].append(step)
    return {"message": "Step added", "recording_id": recording_id}