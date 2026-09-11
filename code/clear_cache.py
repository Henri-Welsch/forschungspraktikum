import os
import shutil
from pathlib import Path

# Base directory where this script is located
PROJECT_ROOT = Path(__file__).resolve().parent

# Target directories to clear
CACHE_DIRS = [
    "cache",
    os.path.join("code", "cache"),
    os.path.join("code", "css_geodata_service", "robustness_of_accessibility", "data", "processed")
]

print("Starting project cache cleanup...")

# 1. Clear designated cache directories
for cache_dir in CACHE_DIRS:
    full_path = PROJECT_ROOT / cache_dir
    if full_path.exists():
        print(f"Removing cache directory: {cache_dir}")
        try:
            shutil.rmtree(full_path)
            print(f"Successfully cleared {cache_dir}.")
        except Exception as e:
            print(f"Failed to remove {cache_dir}: {e}")
    else:
        print(f"Directory not found (already clean): {cache_dir}")

# 2. Clear Python __pycache__ folders inside 'code' (excluding .venv)[cite: 1]
print("Cleaning Python __pycache__ folders...")
code_dir = PROJECT_ROOT / "code"

if code_dir.exists():
    for pycache in code_dir.rglob("__pycache__"):
        if ".venv" not in pycache.parts:
            print(f"Removing {pycache}")
            try:
                shutil.rmtree(pycache)
            except Exception as e:
                print(f"Failed to remove {pycache}: {e}")

print("\nCleanup complete! Your source data (like hq_raw) remains safe.")