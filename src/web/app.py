#!/usr/bin/python3

import logging
import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, Response

from src import __version__
from src.web.agents_api import router as agents_router
from src.web.config_api import router as config_router
from src.web.deps import require_token
from src.web.lifecycle_api import router as lifecycle_router
from src.web.memory_api import router as memory_router
from src.web.secrets_api import router as secrets_router

logger: logging.Logger = logging.getLogger(__name__)

# Directory containing the built React SPA (copied into the image at build time)
STATIC_DIR: str = os.environ.get(
    "MEMTRIX_WEB_STATIC",
    os.path.join(os.path.dirname(__file__), "static"),
)


def create_app() -> FastAPI:
    """
    This function builds the FastAPI application: it wires the API routers behind
    the optional shared-secret dependency and serves the built SPA for all other
    routes.
    """
    app: FastAPI = FastAPI(title="Memtrix Control Panel", version=__version__)

    # CORS is only relevant for local dev where the Vite dev server runs on a
    # different origin; production serves the SPA from this same origin.
    dev_origins: str = os.environ.get("MEMTRIX_WEB_DEV_ORIGINS", "")
    if dev_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in dev_origins.split(",") if o.strip()],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # All API routers require the shared-secret header when one is configured
    guard = Depends(require_token)
    app.include_router(config_router, dependencies=[guard])
    app.include_router(agents_router, dependencies=[guard])
    app.include_router(secrets_router, dependencies=[guard])
    app.include_router(memory_router, dependencies=[guard])
    app.include_router(lifecycle_router, dependencies=[guard])

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """This endpoint is an unauthenticated liveness probe for the web service."""
        return {"status": "ok", "version": __version__}

    _mount_spa(app=app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """
    This function serves the built SPA: static assets from the assets directory and
    index.html as a fallback for client-side routes.
    """
    if not os.path.isdir(STATIC_DIR):
        logger.warning("SPA static directory not found at %s; serving API only", STATIC_DIR)
        return

    assets_dir: str = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    index_path: str = os.path.join(STATIC_DIR, "index.html")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> Response:
        """This endpoint serves a concrete static file when present, else index.html."""
        candidate: str = os.path.join(STATIC_DIR, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        if os.path.isfile(index_path):
            return FileResponse(index_path)
        return Response(content="Not found", status_code=404)


app: FastAPI = create_app()
