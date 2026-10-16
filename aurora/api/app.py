"""FastAPI application for the AURORA research platform.

Provides a REST API and WebSocket endpoint for real-time monitoring
of computational materials science research campaigns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from pathlib import Path

# Auto-load .env from project root so API keys are always available
from aurora.env import load_dotenv

load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from aurora.api.state import get_state_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)
        logger.info(
            "WebSocket client connected (%d active)", len(self._connections)
        )

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info(
            "WebSocket client disconnected (%d active)", len(self._connections)
        )

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients.

        Disconnected clients are silently removed.
        """
        stale: list[WebSocket] = []
        payload = json.dumps(message, default=str)
        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)

    @property
    def active_connections(self) -> int:
        return len(self._connections)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

ws_manager = ConnectionManager()


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    app = FastAPI(
        title="AURORA API",
        description=(
            "Backend API for the AURORA multi-agent research platform "
            "for computational materials science."
        ),
        version="0.1.0",
    )

    # CORS — allow all origins for development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Import and include routers ----------------------------------------
    from aurora.api.routes.lhtb import router as lhtb_router, campaign_router, store, _campaigns, CampaignState
    from aurora.api.routes.skills import router as skills_router

    app.include_router(lhtb_router)
    app.include_router(campaign_router)
    app.include_router(skills_router)

    # -- Startup event -----------------------------------------------------
    @app.on_event("startup")
    async def startup() -> None:
        state = get_state_manager()
        state.set_ws_manager(ws_manager)
        recovered = store.recover_interrupted()
        if recovered:
            logger.info("Recovered %d interrupted experiments", recovered)
        logger.info("AURORA API started — %d campaigns in DB", len(store.list_campaigns()))

    # -- Health check ------------------------------------------------------
    @app.get("/api/health")
    async def health_check() -> dict[str, Any]:
        state = get_state_manager()
        campaigns = state.scan_workspace()
        return {
            "status": "ok",
            "version": "0.1.0",
            "campaigns_loaded": len(state._campaigns),
            "campaigns_on_disk": len(campaigns),
            "websocket_clients": ws_manager.active_connections,
        }

    # -- WebSocket endpoint ------------------------------------------------
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await ws_manager.connect(websocket)
        try:
            while True:
                # Keep the connection alive; handle incoming messages
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    await _handle_ws_message(msg, websocket)
                except json.JSONDecodeError:
                    await websocket.send_text(
                        json.dumps({"error": "Invalid JSON"})
                    )
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception:
            ws_manager.disconnect(websocket)
            logger.exception("WebSocket error")

    # -- Serve frontend static files -----------------------------------------
    frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="static")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> FileResponse:
            file_path = frontend_dist / full_path
            if file_path.is_file():
                return FileResponse(file_path)
            if full_path.startswith("api/") or full_path == "ws":
                from fastapi import HTTPException
                raise HTTPException(404, "Not found")
            return FileResponse(
                frontend_dist / "index.html",
                headers={"Cache-Control": "no-cache"},
            )

        logger.info("Serving frontend from %s", frontend_dist)

    return app


async def _handle_ws_message(
    msg: dict[str, Any], websocket: WebSocket
) -> None:
    """Process an incoming WebSocket message from a client.

    Supported message types:
    - ``ping``: responds with ``pong``
    - ``subscribe``: acknowledges subscription to a campaign
    """
    msg_type = msg.get("type", "")

    if msg_type == "ping":
        await websocket.send_text(json.dumps({"type": "pong"}))
    elif msg_type == "subscribe":
        campaign_id = msg.get("campaign_id", "")
        await websocket.send_text(json.dumps({
            "type": "subscribed",
            "campaign_id": campaign_id,
        }))
    elif msg_type == "chat_message":
        campaign_id = msg.get("campaign_id", "")
        text = msg.get("text", "")
        if not campaign_id or not text:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "chat_message requires campaign_id and text",
            }))
            return
        state = get_state_manager()
        handler = state.get_chat_handler(campaign_id)
        if handler is None:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"No chat handler for campaign '{campaign_id}'",
            }))
            return
        result = await handler.send_user_message(text)
        await websocket.send_text(json.dumps({
            "type": "chat_message_ack",
            "campaign_id": campaign_id,
            **result,
        }))
    else:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"Unknown message type: {msg_type}",
        }))


# Convenience: create the app instance at module level for uvicorn
app = create_app()
