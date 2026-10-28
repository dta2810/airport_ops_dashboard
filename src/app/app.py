"""Airport operations dashboard — Databricks App entry point."""

import os
import sys
from pathlib import Path

# Add src/app to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import uvicorn

from server.genie import router as genie_router
from server.routes import router

app = FastAPI(title="Airport Operations Dashboard")


class CacheControlMiddleware(BaseHTTPMiddleware):
    """Set Cache-Control: private, no-store on API responses without explicit cache headers."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        # Only modify /api/* responses
        if request.url.path.startswith("/api/"):
            # Set no-store if cache-control not already set
            if "cache-control" not in response.headers:
                response.headers["Cache-Control"] = "private, no-store"
        return response


app.add_middleware(CacheControlMiddleware)
app.include_router(router, prefix="/api")
app.include_router(genie_router, prefix="/api")

# Serve React frontend if built
frontend_dist = Path(__file__).parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    dist_root = frontend_dist.resolve()

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # resolve + containment check: an absolute or ../ path must never leave dist/
        file_path = (dist_root / full_path.lstrip("/")).resolve()
        if file_path.is_relative_to(dist_root) and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(dist_root / "index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
