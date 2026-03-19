"""
Initialization script for the AI-Powered Record and Playback Automation Framework
"""

import os
import sys
from pathlib import Path

def create_directories():
    """Create necessary directories for the application"""
    directories = [
        'recordings',
        'reports',
        'logs',
        'config',
        'model'
    ]

    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
        print(f"Created directory: {directory}")

def check_dependencies():
    """Check if all required dependencies are installed"""
    try:
        import fastapi
        import uvicorn
        import playwright
        print("All core dependencies are installed")
        return True
    except ImportError as e:
        print(f"Missing dependency: {e}")
        return False

def initialize_config():
    """Initialize configuration files if they don't exist"""
    config_path = "./config/configuration.properties"
    if not os.path.exists(config_path):
        # Create default config
        default_config = """# Application URLs
base.url=https://example.com
login.url=/login
target.url=/dashboard

# Authentication Credentials
username=admin
password=password123

# Standard Test Data
standard.zipcode=2075
standard.phone=(555) 123-4567
standard.email.domain=test.com

# AI Model Settings
ai.model.path=./model/phi3_model.bin
ai.temperature=0.7
ai.max_tokens=500

# Playback Settings
playback.speed=normal
playback.retries=3
playback.timeout=30

# Data Generation Settings
data.dynamic.firstname=true
data.dynamic.lastname=true
data.dynamic.dob=true
data.dynamic.gender=true
data.mask.ssn=true

# Reporting Settings
report.format=html
report.directory=./reports
log.level=INFO
"""
        with open(config_path, "w") as f:
            f.write(default_config)
        print("Created default configuration file")
    else:
        print("Configuration file already exists")

def main():
    """Main initialization function"""
    print("Initializing AI-Powered Record and Playback Automation Framework...")

    # Create directories
    create_directories()

    # Check dependencies
    if not check_dependencies():
        print("Please install required dependencies using: pip install -r requirements.txt")
        sys.exit(1)

    # Initialize config
    initialize_config()

    print("Initialization complete!")
    print("You can now start the application with: python backend/main.py")

if __name__ == "__main__":
    main()