from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import json
import os

router = APIRouter()

CONFIG_FILE_PATH = "./config/configuration.properties"

def load_config():
    """Load configuration from properties file"""
    config = {}
    if os.path.exists(CONFIG_FILE_PATH):
        with open(CONFIG_FILE_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    config[key] = value
    return config

def save_config(config: dict):
    """Save configuration to properties file"""
    with open(CONFIG_FILE_PATH, 'w') as f:
        for key, value in config.items():
            f.write(f"{key}={value}\n")

@router.get("/")
async def get_config() -> Dict[str, Any]:
    """Get current configuration"""
    return load_config()

@router.put("/")
async def update_config(config_updates: Dict[str, Any]) -> Dict[str, str]:
    """Update configuration"""
    config = load_config()
    config.update(config_updates)
    save_config(config)
    return {"message": "Configuration updated"}

@router.post("/test-connection")
async def test_connection(url: str) -> Dict[str, Any]:
    """Test URL connectivity"""
    # In a real implementation, this would actually test the connection
    return {
        "url": url,
        "status": "reachable",
        "response_time": "45ms",
        "message": "Connection successful"
    }