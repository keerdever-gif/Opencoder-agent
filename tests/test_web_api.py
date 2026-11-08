"""HTTP API tests that run without external providers or a database."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from loop_agent import web_api


class WebsiteAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.[REDACTED-SECRET]
        app = web.Application(middlewares=[web_api.cors_middleware])
        app[web_api._API_KEY] = self.api_key
        app[web_api._ALLOWED_ORIGINS] = set()
        app[web_api._SEMAPHORE] = asyncio.Semaphore(10)
        app.router.add_post("/api/chat", web_api.chat)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_startup_rejects_short_api_secret(self) -> None:
        app = web.Application()
        app[web_api._API_KEY] = "too-short"
        with self.assertRaisesRegex(RuntimeError, "WEB_API_KEY"):
            await web_api._on_startup(app)

    async def test_chat_requires_authentication(self) -> None:
        with patch.object(
            web_api.manager,
            "ask_with_metadata",
            new=AsyncMock(return_value={"answer": "unexpected"}),
        ) as ask:
            response = await self.client.post(
                "/api/chat",
                json={"message": "hello", "session_id": "visitor-session-123"},
            )

        self.assertEqual(response.status, 401)
        ask.assert_not_awaited()

    async def test_authenticated_chat_hides_server_paths(self) -> None:
        result = {
            "answer": "Created the file.",
            "image_url": None,
            "files": ["/srv/opencoder/workspace/report.py", "exports/data.csv"],
            "route": "code",
        }
        with patch.object(
            web_api.manager,
            "ask_with_metadata",
            new=AsyncMock(return_value=result),
        ) as ask:
            response = await self.client.post(
                "/api/chat",
                json={"message": "Create a report", "session_id": "visitor-session-123"},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            payload = await response.json()
            response_text = await response.text()

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["route"], "code")
        self.assertEqual(payload["files"], ["report.py", "data.csv"])
        self.assertNotIn("/srv/opencoder", response_text)
        ask.assert_awaited_once()

    async def test_rejects_invalid_session_id(self) -> None:
        response = await self.client.post(
            "/api/chat",
            json={"message": "hello", "session_id": "bad id"},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
