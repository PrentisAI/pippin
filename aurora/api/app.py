"""FastAPI application behind the Pippin web console.

Serves the REST + WebSocket API under ``/api`` and, when a frontend build is
available, the single-page console at ``/``.  Start it with ``aurora serve``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from aurora.env import load_dotenv

# API keys from .env must be in the environment before any route builds a config
load_dotenv()

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
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
        title="Pippin API",
        description="Backend API for the Pippin web console.",
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

    from aurora.api.routes.accounts import access_router, auth_router, me_router, team_router
    from aurora.api.routes.assistant import router as assistant_router
    from aurora.api.routes.today import router as today_router
    from aurora.api.routes.brainstorm import router as brainstorm_router
    from aurora.api.routes.campaigns import campaign_router, settings_router, store
    from aurora.api.routes.feed import router as feed_router
    from aurora.api.routes.files import router as files_router
    from aurora.api.routes.insights import router as insights_router
    from aurora.api.routes.links import public_router as report_pages
    from aurora.api.routes.links import router as links_router
    from aurora.api.routes.literature import library_router, project_router
    from aurora.api.routes.memory import router as memory_router
    from aurora.api.routes.notebook import router as notebook_router
    from aurora.api.routes.onboarding import router as onboarding_router
    from aurora.api.routes.reader import router as reader_router
    from aurora.api.routes.review import router as review_router
    from aurora.api.routes.run_results import router as results_router
    from aurora.api.routes.setup import router as setup_router
    from aurora.api.routes.skills import router as skills_router
    from aurora.api.routes.zotero import router as zotero_router
    from aurora.auth.guard import guard

    # every route checks who is asking and whether they may (aurora.auth.guard.RULES);
    # only signing in, invitations, read-only report links, decision links (checked by their
    # signed token) and the chat apps' callbacks (checked by their token or signature) are open
    app.include_router(auth_router)
    app.include_router(report_pages)            # /r/<token>: a report shared read-only, checked by its token
    from aurora.api.routes.actions import router as decision_links
    app.include_router(decision_links)          # /a/<token>: answering a decision from a notification
    from aurora.api.routes.im import public_router as chat_apps
    from aurora.api.routes.im import router as im_router
    app.include_router(chat_apps)               # /im/feishu, /im/wecom: messages to Pippin from a chat app
    for router in (campaign_router, settings_router, skills_router, insights_router, files_router,
                   me_router, team_router, access_router, project_router, library_router,
                   zotero_router, brainstorm_router, feed_router, links_router, results_router, setup_router,
                   onboarding_router, reader_router, review_router, memory_router, assistant_router, today_router,
                   im_router, notebook_router):
        app.include_router(router, dependencies=[Depends(guard)])

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
        # the examples that come with Pippin (once per console; an admin can take them out)
        try:
            from aurora.examples import install_all
            await asyncio.to_thread(install_all, home=store.data_dir, store=store)
        except Exception:
            logger.warning("Could not install the examples", exc_info=True)
        logger.info("Pippin API started; %d projects in %s",
                    len(store.list_campaigns()), store.data_dir)
        # everyone's Zotero collections, every few minutes
        from aurora.literature.zotero import sync_loop
        app.state.zotero_sync = asyncio.create_task(sync_loop())
        # the brainstorm agents' own work (by each person's "How active")
        from aurora.brainstorm.background import loop as brainstorm_loop
        app.state.brainstorm_work = asyncio.create_task(brainstorm_loop())
        # each person's feed: their sources every few hours, and their daily digest
        from aurora.feed.service import loop as feed_loop
        app.state.feed_work = asyncio.create_task(feed_loop())
        # what each person follows and the server reads (Reddit, feeds), into their notebook
        from aurora.memory.follow import loop as follow_loop
        app.state.follow_work = asyncio.create_task(follow_loop())
        # notifications held during quiet hours, sent when those end

        async def held_loop() -> None:
            from aurora.api.routes.campaigns import send_held_notifications
            while True:
                await asyncio.sleep(60)
                try:
                    await send_held_notifications()
                except Exception:
                    logger.warning("sending held notifications failed", exc_info=True)
        app.state.held_notifications = asyncio.create_task(held_loop())

    @app.on_event("shutdown")
    async def shutdown() -> None:
        for name in ("zotero_sync", "brainstorm_work", "feed_work", "follow_work", "held_notifications"):
            task = getattr(app.state, name, None)
            if task is not None:
                task.cancel()

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
