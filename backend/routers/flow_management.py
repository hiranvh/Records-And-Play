from fastapi import APIRouter, HTTPException, Path
from typing import List, Dict, Any
import json

router = APIRouter()

# In-memory storage for flows (in production, use a database)
flows_db = {
    1: {
        "id": 1,
        "name": "Login Flow",
        "description": "Standard login process",
        "created_at": "2026-03-18T10:00:00Z",
        "modified_at": "2026-03-18T10:00:00Z",
        "status": "active",
        "steps": [
            {"type": "input", "selector": "#username", "value": "admin"},
            {"type": "input", "selector": "#password", "value": "password123"},
            {"type": "click", "selector": "#login-button"}
        ]
    }
}

@router.get("/")
async def list_flows() -> List[Dict[str, Any]]:
    """List all recorded flows"""
    return list(flows_db.values())

@router.get("/{flow_id}")
async def get_flow(flow_id: int) -> Dict[str, Any]:
    """Get specific flow details"""
    if flow_id not in flows_db:
        raise HTTPException(status_code=404, detail="Flow not found")
    return flows_db[flow_id]

@router.put("/{flow_id}")
async def update_flow(flow_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update flow metadata"""
    if flow_id not in flows_db:
        raise HTTPException(status_code=404, detail="Flow not found")

    flows_db[flow_id].update(updates)
    flows_db[flow_id]["modified_at"] = "2026-03-19T00:00:00Z"
    return {"message": "Flow updated", "flow": flows_db[flow_id]}

@router.delete("/{flow_id}")
async def delete_flow(flow_id: int) -> Dict[str, str]:
    """Delete specific flow"""
    if flow_id not in flows_db:
        raise HTTPException(status_code=404, detail="Flow not found")

    del flows_db[flow_id]
    return {"message": "Flow deleted"}

@router.post("/{flow_id}/duplicate")
async def duplicate_flow(flow_id: int) -> Dict[str, Any]:
    """Duplicate existing flow"""
    if flow_id not in flows_db:
        raise HTTPException(status_code=404, detail="Flow not found")

    original_flow = flows_db[flow_id]
    new_flow_id = max(flows_db.keys()) + 1 if flows_db else 1

    new_flow = original_flow.copy()
    new_flow["id"] = new_flow_id
    new_flow["name"] = f"{original_flow['name']} (Copy)"
    new_flow["created_at"] = "2026-03-19T00:00:00Z"
    new_flow["modified_at"] = "2026-03-19T00:00:00Z"

    flows_db[new_flow_id] = new_flow

    return {"message": "Flow duplicated", "flow": new_flow}