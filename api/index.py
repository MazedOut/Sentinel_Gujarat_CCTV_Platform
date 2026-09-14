"""
Vercel Serverless Function Entrypoint for Drishti CCTV Intelligence Platform.
Imports the FastAPI ASGI app from backend.app.main and fixes Vercel rewritten paths.
"""
import os
import sys
from pathlib import Path
from fastapi.responses import JSONResponse

# Add project root directory to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Mark serverless environment
os.environ.setdefault("VERCEL", "1")

from backend.app.main import app as base_app

class VercelPathFixer:
    """
    ASGI middleware that restores the original request path on Vercel.
    When Vercel uses internal rewrites, it sets x-matched-path or x-forwarded-uri header,
    while scope['path'] gets rewritten to '/api/index.py'.
    """
    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            matched = headers.get(b"x-matched-path") or headers.get(b"x-vercel-matched-path") or headers.get(b"x-forwarded-uri")
            if matched:
                path_str = matched.decode("utf-8", errors="replace")
                if path_str and not path_str.startswith("/api/index"):
                    scope["path"] = path_str
                    scope["raw_path"] = matched

            # If path is still /api/index.py, map to root
            if scope.get("path") in ("/api/index.py", "/api/index"):
                scope["path"] = "/"

        await self.asgi_app(scope, receive, send)

app = VercelPathFixer(base_app)
handler = app
