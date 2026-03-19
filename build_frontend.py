"""
Build script for the frontend React application
"""

import os
import subprocess
import sys

def build_frontend():
    """Build the React frontend application"""
    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")

    if not os.path.exists(frontend_dir):
        print("Frontend directory not found")
        return False

    try:
        # Change to frontend directory
        original_cwd = os.getcwd()
        os.chdir(frontend_dir)

        # Check if node_modules exists, if not install dependencies
        if not os.path.exists("node_modules"):
            print("Installing frontend dependencies...")
            subprocess.run(["npm", "install"], check=True)

        # Build the React app
        print("Building frontend application...")
        subprocess.run(["npm", "run", "build"], check=True)

        print("Frontend build completed successfully!")
        return True

    except subprocess.CalledProcessError as e:
        print(f"Error building frontend: {e}")
        return False
    except FileNotFoundError:
        print("Node.js or npm not found. Please install Node.js to build the frontend.")
        return False
    finally:
        # Change back to original directory
        os.chdir(original_cwd)

def setup_frontend():
    """Setup the frontend development environment"""
    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")

    if not os.path.exists(frontend_dir):
        print("Frontend directory not found")
        return False

    try:
        # Change to frontend directory
        original_cwd = os.getcwd()
        os.chdir(frontend_dir)

        # Install dependencies
        print("Setting up frontend development environment...")
        subprocess.run(["npm", "install"], check=True)

        print("Frontend setup completed successfully!")
        return True

    except subprocess.CalledProcessError as e:
        print(f"Error setting up frontend: {e}")
        return False
    except FileNotFoundError:
        print("Node.js or npm not found. Please install Node.js to setup the frontend.")
        return False
    finally:
        # Change back to original directory
        os.chdir(original_cwd)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_frontend()
    else:
        build_frontend()