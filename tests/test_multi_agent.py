"""Offline tests for the Manager and specialist tool boundaries."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from loop_agent.manager import Manager, legacy_agent
from loop_agent.subagents import base as base_module


EXPECTED_TOOLS = {
    "search": {"web_search", "web_fetch"},
    "code": {"file_read", "file_write", "file_append", "file_create_zip"},
    "test": {"sandbox_run_python", "sandbox_run_shell", "code_loop"},
    "image": {"generate_image"},
}


class MultiAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.manager = Manager(backend=legacy_agent)

    def test_specialists_receive_only_their_tools(self) -> None:
        agents = {
            "search": self.manager.search_agent,
            "code": self.manager.code_agent,
            "test": self.manager.test_agent,
            "image": self.manager.image_agent,
        }
        for name, agent in agents.items():
            exposed = {
                schema["function"]["name"]
                for schema in agent._get_tool_schemas()
            }
            self.assertEqual(exposed, EXPECTED_TOOLS[name])

    async def test_forced_image_route_skips_classifier(self) -> None:
        with patch.object(
            base_module.connector,
            "complete",
            new=AsyncMock(side_effect=AssertionError("router should not be called")),
        ):
            route = await self.manager.classify("draw a small robot", force_image=True)
        self.assertEqual(route, "image")

    async def test_router_uses_fallback_when_provider_is_unavailable(self) -> None:
        with patch(
            "loop_agent.manager.connector.complete",
            new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
        ):
            route = await self.manager.classify("Create a Python script that prints hello")
        self.assertEqual(route, "code")

    async def test_volatile_history_keeps_context_without_postgres(self) -> None:
        observed_histories: list[list[dict]] = []

        async def fake_general(prompt, history, current_content):
            observed_histories.append(list(history))
            return f"answer to: {prompt}"

        with (
            patch.object(self.manager.backend, "pool", None),
            patch.object(self.manager, "classify", new=AsyncMock(return_value="general")),
            patch.object(self.manager, "_general_response", new=fake_general),
        ):
            await self.manager.ask_with_metadata(101, 202, "My project is called Atlas")
            await self.manager.ask_with_metadata(101, 202, "What is my project called?")

        self.assertEqual(len(observed_histories), 2)
        self.assertEqual(observed_histories[1][0]["content"], "My project is called Atlas")
        self.assertEqual(observed_histories[1][1]["content"], "answer to: My project is called Atlas")

    async def test_subagent_executes_only_allowlisted_tool(self) -> None:
        calls = {"count": 0}
        observed_tools: list[str] = []

        async def fake_complete(messages, tools=None, model=None):
            observed_tools[:] = [
                item["function"]["name"] for item in (tools or [])
            ]
            calls["count"] += 1
            if calls["count"] == 1:
                return {
                    "choices": [{
                        "message": {
                            "tool_calls": [{
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "web_search",
                                    "arguments": '{"query":"OpenCoder"}',
                                },
                            }]
                        },
                        "finish_reason": "tool_calls",
                    }]
                }
            return {
                "choices": [{
                    "message": {"content": "Research completed."},
                    "finish_reason": "stop",
                }]
            }

        with (
            patch.object(base_module.connector, "complete", new=fake_complete),
            patch.object(
                legacy_agent,
                "run_tool",
                new=AsyncMock(return_value={"results": []}),
            ) as run_tool,
        ):
            result = await self.manager.search_agent.run("Research OpenCoder")

        self.assertEqual(result.text, "Research completed.")
        self.assertEqual(set(observed_tools), EXPECTED_TOOLS["search"])
        run_tool.assert_awaited_once_with("web_search", {"query": "OpenCoder"})


if __name__ == "__main__":
    unittest.main()
