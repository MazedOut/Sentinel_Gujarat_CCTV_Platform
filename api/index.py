"""
Vercel Serverless Function Entrypoint for Drishti CCTV Intelligence Platform.
Imports the FastAPI ASGI app from backend.app.main.
"""
import os
import sys
from pathlib import Path

# Add project root directory to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Mark serverless environment
os.environ.setdefault("VERCEL", "1")

from backend.app.main import app
