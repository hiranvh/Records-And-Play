"""Shared configuration loading helpers for runtime credentials."""

import os
from typing import Dict


def load_credentials_for_url(target_url: str) -> Dict[str, str]:
    """Load URL-scoped credentials from config.properties."""
    config_path = os.path.join(os.getcwd(), "config.properties")
    if not os.path.exists(config_path):
        return {}

    creds: Dict[str, str] = {}
    current_section = None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section_url = line[1:-1]
                    current_section = section_url if section_url in target_url else None
                elif current_section and "=" in line:
                    key, value = [x.strip() for x in line.split("=", 1)]
                    creds[key.lower()] = value
    except Exception:
        return {}

    if "pass" in creds and "password" not in creds:
        creds["password"] = creds["pass"]
    if "password" in creds and "pass" not in creds:
        creds["pass"] = creds["password"]
    return creds
