"""FastAPI application behind the AURORA web console.

Serves the REST + WebSocket API under ``/api`` and, when a frontend build is
available, the single-page console at ``/``.  Start it with ``aurora serve``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aurora.env import load_dotenv

# API keys from .env must be in the environment before any route builds a config
load_dotenv()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from aurora import __version__  # noqa: E402

logger = logging.getLogger(__name__)

# The wheel ships the console under aurora/web; a source checkout may build it in frontend/dist
_PACKAGED_UI = Path(__file__).resolve().parent.parent / "web"
_SOURCE_UI = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def _frontend_dir() -> Path | None:
    for d in (_PACKAGED_UI, _SOURCE_UI):
        if (d / "index.html").is_file():
            return d
    return None


def create_app(*, cors_origins: list[str] | None = None) -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(
        title="AURORA API",
        description="Backend API for the AURORA web console.",
        version=__version__,
    )

    # The console is served from the same origin; cross-origin access is opt-in
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    from aurora.api.routes.campaigns import campaign_router, settings_router, store
    from aurora.api.routes.insights import router as insights_router
    from aurora.api.routes.skills import router as skills_router

    app.include_router(campaign_router)
    app.include_router(settings_router)
    app.include_router(skills_router)
    app.include_router(insights_router)

    @app.on_event("startup")
    async def startup() -> None:
        recovered = store.recover_interrupted()
        if recovered:
            logger.info("Marked %d interrupted experiments as stopped", recovered)
        import shutil
        if shutil.which("docker"):
            from aurora.core.docker_backend import remove_orphaned_containers
            orphans = await remove_orphaned_containers(store.data_dir)
            if orphans:
                logger.info("Removed %d containers left behind by interrupted experiments", orphans)
        logger.info("AURORA API started; %d projects in %s",
                    len(store.list_campaigns()), store.data_dir)

    @app.get("/api/health")
    async def health_check() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    frontend = _frontend_dir()
    if frontend is not None:
        if (frontend / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=str(frontend / "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(404, "Not found")
            file_path = (frontend / full_path).resolve()
            if file_path.is_file() and file_path.is_relative_to(frontend.resolve()):
                return FileResponse(file_path)
            return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-cache"})

        logger.info("Serving the web console from %s", frontend)
    else:
        logger.warning("No web console build found; only the API is served "
                       "(build it with `npm run build` in frontend/)")

    return app


# Module-level instance for `uvicorn aurora.api.app:app`
app = create_app()
