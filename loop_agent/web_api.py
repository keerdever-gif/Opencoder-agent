"""Standalone HTTP API for embedding OpenCoder-Agent in a website.

The WEB_API_KEY must remain on your website's server. Do not expose it in browser
JavaScript; have your website backend proxy requests to this API.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import uuid
from collections import OrderedDict
from typing import Any

from aiohttp import web

from config import config
from loop_agent.connector import connector
from loop_agent.manager import manager


_SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_MAX_MESSAGE_CHARS = 20_000
_MAX_ACTIVE_SESSIONS = 2_000
_SESSION_LOCKS: OrderedDict[str, asyncio.Lock] = OrderedDict()
_API_KEY = web.AppKey("api_key", str)
_ALLOWED_ORIGINS = web.AppKey("allowed_origins", set)
_SEMAPHORE = web.AppKey("semaphore", asyncio.Semaphore)


def _api_key() -> str:
    return (os.getenv("WEB_API_KEY") or "").strip()


def _allowed_origins() -> set[str]:
    raw = os.getenv("WEB_CORS_ORIGINS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


@web.middleware
async def cors_middleware(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    origin = request.headers.get("Origin")
    allowed = request.app[_ALLOWED_ORIGINS]

    same_origin = bool(
        origin and origin.rstrip("/") == f"{request.scheme}://{request.host}".rstrip("/")
    )
    if origin and origin not in allowed and not same_origin:
        raise web.HTTPForbidden(text="Origin is not allowed.")

    if request.method == "OPTIONS":
        response: web.StreamResponse = web.Response(status=204)
    else:
        response = await handler(request)

    if origin and origin in allowed and not same_origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return response


def _authenticated(request: web.Request) -> bool:
    expected = request.app[_API_KEY]
    authorization = request.headers.get("Authorization", "")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    supplied = supplied or request.headers.get("X-API-Key", "")
    if not supplied:
        return False
    try:
        return hmac.compare_digest(supplied, expected)
    except (TypeError, UnicodeEncodeError):
        return False


def _session_ids(session_id: str, secret: str) -> tuple[int, int]:
    digest = hmac.new(
        secret.encode("utf-8"),
        session_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    mask = (1 << 63) - 1
    user_id = int.from_bytes(digest[:8], "big") & mask
    channel_id = int.from_bytes(digest[8:16], "big") & mask
    return user_id, channel_id


def _session_lock(session_id: str) -> asyncio.Lock:
    lock = _SESSION_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _SESSION_LOCKS[session_id] = lock
    _SESSION_LOCKS.move_to_end(session_id)
    while len(_SESSION_LOCKS) > _MAX_ACTIVE_SESSIONS:
        oldest_key, oldest_lock = next(iter(_SESSION_LOCKS.items()))
        if oldest_lock.locked():
            _SESSION_LOCKS.move_to_end(oldest_key)
            if all(item.locked() for item in _SESSION_LOCKS.values()):
                break
        else:
            _SESSION_LOCKS.popitem(last=False)
    return lock


async def health(_: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "service": config.bot_name,
        "api": "OpenCoder-Agent Multi-Agent API",
    })


async def chat(request: web.Request) -> web.Response:
    if not _authenticated(request):
        raise web.HTTPUnauthorized(
            text="A valid Bearer token or X-API-Key header is required."
        )

    try:
        payload = await request.json(loads=json.loads)
    except (json.JSONDecodeError, web.HTTPBadRequest, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="Request body must be valid JSON.")

    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="JSON body must be an object.")

    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise web.HTTPBadRequest(text="'message' must be a non-empty string.")
    message = message.strip()
    if len(message) > _MAX_MESSAGE_CHARS:
        raise web.HTTPRequestEntityTooLarge(
            max_size=_MAX_MESSAGE_CHARS,
            actual_size=len(message),
        )

    session_id = payload.get("session_id")
    if session_id is None:
        session_id = str(uuid.uuid4())
    if not isinstance(session_id, str) or not _SESSION_RE.fullmatch(session_id):
        raise web.HTTPBadRequest(
            text="'session_id' must contain 8-128 letters, digits, '.', '_', ':', or '-'."
        )

    force_image = payload.get("force_image", False)
    if not isinstance(force_image, bool):
        raise web.HTTPBadRequest(text="'force_image' must be a boolean.")

    user_id, channel_id = _session_ids(session_id, request.app[_API_KEY])
    lock = _session_lock(session_id)

    async with request.app[_SEMAPHORE]:
        async with lock:
            try:
                result = await manager.ask_with_metadata(
                    user_id=user_id,
                    channel_id=channel_id,
                    prompt=message,
                    force_image=force_image,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[WEB API] {type(exc).__name__}: {exc}")
                raise web.HTTPInternalServerError(
                    text="The agent could not complete this request."
                ) from exc

    # Never send server-side absolute workspace paths to browser clients.
    public_files = [
        str(item).replace("\\", "/").rsplit("/", 1)[-1]
        for item in (result.get("files") or [])
        if isinstance(item, str) and item.strip()
    ]
    public_result = {
        "answer": result.get("answer"),
        "image_url": result.get("image_url"),
        "route": result.get("route"),
        # Return filenames for display, never absolute server-side paths.
        "files": public_files,
    }
    return web.json_response({
        "ok": True,
        "session_id": session_id,
        **public_result,
    })


async def _on_startup(app: web.Application) -> None:
    if len(app[_API_KEY]) < 32:
        raise RuntimeError(
            "WEB_API_KEY must contain at least 32 characters. Generate a long random secret and add it to .env."
        )
    if not config.ai_slots:
        raise RuntimeError("Configure AI_API_KEY or AI_API_KEY_1 before starting the web API.")
    await manager.init()


async def _on_cleanup(_: web.Application) -> None:
    try:
        await connector.close()
    finally:
        try:
            await manager.close()
        finally:
            from sandbox.sandbox import sandbox
            await sandbox.close()


def create_app() -> web.Application:
    """Create the aiohttp app; exposed separately to support integration tests."""
    app = web.Application(
        client_max_size=64 * 1024,
        middlewares=[cors_middleware],
    )
    app[_API_KEY] = _api_key()
    app[_ALLOWED_ORIGINS] = _allowed_origins()
    app[_SEMAPHORE] = asyncio.Semaphore(10)
    app.router.add_get("/health", health)
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/chat", chat)
    app.router.add_options("/api/chat", chat)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


async def run_web_api() -> None:
    """Run the standalone website API until the process is stopped."""
    app = create_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    host = os.getenv("WEB_HOST", "0.0.0.0").strip() or "0.0.0.0"
    try:
        port = int(os.getenv("WEB_PORT") or str(config.port))
    except ValueError as exc:
        await runner.cleanup()
        raise RuntimeError("WEB_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        await runner.cleanup()
        raise RuntimeError("WEB_PORT must be between 1 and 65535.")

    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
        print(f"[WEB API] Listening on http://{host}:{port}")
        print("[WEB API] POST /api/chat | GET /health")
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
