"""
server.py -- FastAPI application entry point.

Mounts all API routes under /api/ and serves the Vue SPA from frontend/dist/.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from gui.routes import profiles, config, jobs, contacts, health, reports

app = FastAPI(title="Inbox Sentinel", docs_url="/api/docs", openapi_url="/api/openapi.json")

# CORS for Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(profiles.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(contacts.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(reports.router, prefix="/api")

# Serve Vue SPA static files (production)
_DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")

if os.path.isdir(_DIST_DIR):
    # Serve static assets (JS, CSS, images)
    _ASSETS_DIR = os.path.join(_DIST_DIR, "assets")
    if os.path.isdir(_ASSETS_DIR):
        app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve index.html for all non-API routes (SPA routing)."""
        # Try to serve an exact file match first
        file_path = os.path.join(_DIST_DIR, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        # Fall back to index.html for SPA routing
        return FileResponse(os.path.join(_DIST_DIR, "index.html"))
