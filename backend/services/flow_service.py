import json
import os
from typing import List, Dict, Any

class FlowService:
    """Service to handle CRUD operations for recorded flows"""

    def __init__(self, storage_path: str = "./recordings/"):
        self.storage_path = storage_path
        os.makedirs(storage_path, exist_ok=True)

    def list_flows(self) -> List[Dict[str, Any]]:
        """List all recorded flows"""
        flows = []
        for filename in os.listdir(self.storage_path):
            if filename.endswith(".json"):
                with open(os.path.join(self.storage_path, filename), 'r') as f:
                    flow = json.load(f)
                    flows.append(flow)
        return flows

    def get_flow(self, flow_id: str) -> Dict[str, Any]:
        """Retrieve specific flow by ID"""
        flow_file = os.path.join(self.storage_path, f"{flow_id}.json")
        if os.path.exists(flow_file):
            with open(flow_file, 'r') as f:
                return json.load(f)
        else:
            raise FileNotFoundError(f"Flow with id {flow_id} not found")

    def update_flow(self, flow_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update flow metadata"""
        flow = self.get_flow(flow_id)
        flow.update(updates)

        flow_file = os.path.join(self.storage_path, f"{flow_id}.json")
        with open(flow_file, 'w') as f:
            json.dump(flow, f, indent=2)

        return flow

    def delete_flow(self, flow_id: str) -> bool:
        """Delete flow permanently"""
        flow_file = os.path.join(self.storage_path, f"{flow_id}.json")
        if os.path.exists(flow_file):
            os.remove(flow_file)
            return True
        return False

    def duplicate_flow(self, flow_id: str) -> Dict[str, Any]:
        """Create copy of existing flow"""
        original_flow = self.get_flow(flow_id)

        # Create new flow ID
        import uuid
        new_flow_id = str(uuid.uuid4())

        # Copy the original flow and update its ID
        new_flow = original_flow.copy()
        new_flow["id"] = new_flow_id
        new_flow["name"] = f"{original_flow['name']} (Copy)"

        # Save the new flow
        flow_file = os.path.join(self.storage_path, f"{new_flow_id}.json")
        with open(flow_file, 'w') as f:
            json.dump(new_flow, f, indent=2)

        return new_flow

    def save_flow(self, flow: Dict[str, Any]) -> str:
        """Save a new flow"""
        flow_id = flow.get("id") or str(hash(str(flow)))
        flow["id"] = flow_id

        flow_file = os.path.join(self.storage_path, f"{flow_id}.json")
        with open(flow_file, 'w') as f:
            json.dump(flow, f, indent=2)

        return flow_id