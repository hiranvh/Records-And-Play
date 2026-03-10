import os
import subprocess
import sys

def build_exe():
    print("Starting Nuitka build process to compile the application...")
    
    # We must install nuitka first if it's not present
    try:
        import nuitka
    except ImportError:
        print("Nuitka not found. Installing Nuitka...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "nuitka"])

    # Nuitka command arguments
    nuitka_cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--enable-plugin=tk-inter",
        "--enable-plugin=sqlalchemy",
        "--enable-plugin=pylint-warnings", # Just in case
        "--follow-imports",
        "--output-dir=build",
        "main.py"
    ]
    
    # We use --standalone instead of --onefile initially because --onefile takes longer 
    # and unzips to a temp directory. A standalone folder is faster to test.
    # The user can add --onefile later if they truly want a single file.

    print("Running command: " + " ".join(nuitka_cmd))
    
    try:
        subprocess.check_call(nuitka_cmd)
        print("\nBuild Completed Successfully!")
        print("You can find the standalone application in the 'build/main.dist' directory.")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with error: {e}")

if __name__ == "__main__":
    build_exe()
