"""Shared implementation for narrowly-scoped LLM sub-agents."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from config import config
from loop_agent.connector import connector

ToolHandler = Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]]


@dataclass
class AgentResult:
    """Result returned by a sub-agent."""

    text: str
    image_url: str | None = None
    files: list[str] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)


class BaseAgent:
    """A tool-calling agent that can only see and execute its allow-listed tools."""

    name = "base"
    system_prompt = "You are a focused assistant."
    tool_names: tuple[str, ...] = ()

    def __init__(
        self,
        backend: Any,
        *,
        name: str | None = None,
        system_prompt: str | None = None,
        tool_names: tuple[str, ...] | None = None,
        extra_tool_schemas: list[dict[str, Any]] | None = None,
        local_handlers: dict[str, ToolHandler] | None = None,
    ) -> None:
        if backend is None:
            raise ValueError("A shared Agent backend is required.")
        self.backend = backend
        self.name = name or self.name
        self.system_prompt = system_prompt or self.system_prompt
        self.tool_names = tuple(tool_names or self.tool_names)
        self.extra_tool_schemas = copy.deepcopy(extra_tool_schemas or [])
        self.local_handlers = dict(local_handlers or {})
        self.last_result: AgentResult | None = None

        unknown_local = set(self.local_handlers) - {
            item["function"]["name"] for item in self.extra_tool_schemas
        }
        if unknown_local:
            raise ValueError(
                f"Local handlers have no matching schemas: {sorted(unknown_local)}"
            )

    @staticmethod
    def _max_rounds() -> int:
        try:
            value = int(os.getenv("SUBAGENT_MAX_TOOL_ROUNDS", "16"))
        except ValueError:
            value = 16
        return max(1, min(value, 50))

    def _get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return only the schemas explicitly assigned to this sub-agent."""
        schemas_by_name: dict[str, dict[str, Any]] = {}
        for schema in self.backend._all_tool_schemas():
            function = schema.get("function") or {}
            name = function.get("name")
            if name in self.tool_names:
                schemas_by_name[name] = schema

        missing = set(self.tool_names) - set(schemas_by_name)
        if missing:
            raise RuntimeError(
                f"{self.name} references tools not registered by the backend: "
                f"{', '.join(sorted(missing))}"
            )

        schemas = [copy.deepcopy(schemas_by_name[name]) for name in self.tool_names]
        schemas.extend(copy.deepcopy(self.extra_tool_schemas))
        return schemas

    @staticmethod
    def _compact_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Keep a small, valid conversational context without old tool transcripts."""
        if not history:
            return []
        result: list[dict[str, Any]] = []
        for item in history[-8:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = item.get("content")
            if not isinstance(content, (str, list)):
                continue
            if isinstance(content, str):
                content = content[-6000:]
            result.append({"role": role, "content": content})
        return result

    def _extract_tool_calls(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """Reuse the project's OpenAI/Gemini-compatible tool-call normalizer."""
        return self.backend._extract_tool_calls(message)

    @staticmethod
    def _parse_arguments(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    async def _execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        loop_state: Any | None,
    ) -> dict[str, Any]:
        if name in self.local_handlers:
            return await self.local_handlers[name](arguments, loop_state)
        if name not in self.tool_names:
            return {"error": f"Tool '{name}' is not enabled for {self.name}."}
        return await self.backend.run_tool(name, arguments)

    async def run(
        self,
        prompt: str | list[dict[str, Any]],
        *,
        history: list[dict[str, Any]] | None = None,
        loop_state: Any | None = None,
    ) -> AgentResult:
        """Run the sub-agent with bounded tool rounds and provider fallback."""
        if isinstance(prompt, str):
            prompt_text = prompt.strip()
            user_content: Any = prompt
        elif isinstance(prompt, list) and prompt:
            prompt_text = "\n".join(
                str(part.get("text", ""))
                for part in prompt
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip() or "User supplied a multimodal attachment."
            user_content = prompt
        else:
            raise ValueError("Sub-agent prompt must be non-empty text or multimodal content.")
        if not prompt_text:
            raise ValueError("Sub-agent prompt must not be empty.")

        tools = self._get_tool_schemas()
        base_messages = [
            {"role": "system", "content": self.system_prompt},
            *self._compact_history(history),
            {"role": "user", "content": user_content},
        ]
        model_override = os.getenv(f"AI_{self.name.upper()}_MODEL", "").strip()
        models = [model_override] if model_override else self.backend._select_model(prompt_text)
        if not models:
            models = [config.ai_model]

        errors: list[str] = []
        for model in models:
            try:
                result = await self._run_for_model(
                    base_messages=base_messages,
                    tools=tools,
                    model=model,
                    loop_state=loop_state,
                )
                self.last_result = result
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                print(f"[SUBAGENT:{self.name}] model {model!r} failed: {exc}")

        detail = " | ".join(errors[-3:]) or "no model was available"
        raise RuntimeError(f"{self.name} could not complete the task: {detail}")

    async def _run_for_model(
        self,
        *,
        base_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        loop_state: Any | None,
    ) -> AgentResult:
        messages = copy.deepcopy(base_messages)
        image_url: str | None = None
        files: list[str] = []
        events: list[dict[str, Any]] = []
        call_signatures: list[str] = []
        malformed_retries = 0
        max_rounds = self._max_rounds()

        # Permit up to `max_rounds` tool-call rounds plus one final model response.
        for round_number in range(1, max_rounds + 2):
            response = await connector.complete(messages, tools, model=model)
            choices = response.get("choices") or []
            if not choices:
                raise RuntimeError("The model returned no completion choices.")

            choice = choices[0] or {}
            message = choice.get("message") or {}
            tool_calls = self._extract_tool_calls(message)
            finish_reason = choice.get("finish_reason", "unknown")

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content"),
            }
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            messages.append(assistant_message)

            if not tool_calls and finish_reason == "malformed_function_call":
                malformed_retries += 1
                if malformed_retries <= 2:
                    messages.append({
                        "role": "user",
                        "content": (
                            "The previous tool call was malformed. Return valid JSON "
                            "tool arguments and continue using only your available tools."
                        ),
                    })
                    continue

            if not tool_calls:
                raw_content = message.get("content")
                if isinstance(raw_content, str):
                    answer = raw_content.strip()
                elif isinstance(raw_content, list):
                    answer = "\n".join(
                        str(part.get("text", ""))
                        for part in raw_content
                        if isinstance(part, dict) and part.get("type") == "text"
                    ).strip()
                else:
                    answer = ""
                if not answer:
                    if finish_reason in {
                        "safety", "recitation", "blocked", "prohibited_content"
                    }:
                        answer = "The provider blocked this response with its safety filters."
                    elif finish_reason == "max_tokens":
                        answer = "The response was truncated by the model token limit."
                    else:
                        answer = "The sub-agent did not receive a final text response."
                return AgentResult(
                    text=answer,
                    image_url=image_url,
                    files=files,
                    tool_events=events,
                )

            if round_number > max_rounds:
                raise RuntimeError(
                    f"{self.name} reached its tool-call limit ({max_rounds})."
                )

            for call in tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = self._parse_arguments(function.get("arguments", "{}"))
                call_id = str(call.get("id") or f"call_{uuid.uuid4().hex}")

                if not name:
                    tool_result: dict[str, Any] = {"error": "Tool call has no name."}
                else:
                    signature = json.dumps(
                        [name, arguments], sort_keys=True, ensure_ascii=False, default=str
                    )
                    call_signatures.append(signature)
                    if call_signatures.count(signature) > 4:
                        raise RuntimeError(f"Repeated tool call detected: {name}")
                    try:
                        tool_result = await self._execute_tool(name, arguments, loop_state)
                        if not isinstance(tool_result, dict):
                            tool_result = {"result": tool_result}
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        tool_result = {
                            "error": f"{type(exc).__name__}: {exc}"
                        }

                if loop_state is not None and name in {
                    "file_write", "file_append", "file_replace_many"
                }:
                    try:
                        loop_state.track(name, arguments, tool_result)
                    except Exception as exc:
                        print(f"[SUBAGENT:{self.name}] could not track file change: {exc}")

                if tool_result.get("image_url"):
                    image_url = str(tool_result["image_url"])
                if tool_result.get("_send_file"):
                    file_path = str(tool_result["_send_file"])
                    if file_path not in files:
                        files.append(file_path)

                clean_result = connector.clean_tool_result(tool_result)
                return_code = tool_result.get("returncode", tool_result.get("exit_code"))
                failed = (
                    bool(tool_result.get("error"))
                    or tool_result.get("ok") is False
                    or (isinstance(return_code, int) and return_code != 0)
                    or tool_result.get("action") in {"retry", "giveup"}
                )
                diagnostic = (
                    tool_result.get("error")
                    or tool_result.get("stderr")
                    or (tool_result.get("message") if failed else "")
                    or ""
                )
                events.append({
                    "tool": name,
                    "ok": not failed,
                    "error": str(diagnostic)[:1200],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": clean_result,
                })
                print(
                    f"[SUBAGENT:{self.name}] round {round_number}: "
                    f"{name} ({'error' if failed else 'ok'})"
                )

        raise RuntimeError(f"{self.name} stopped without a final answer.")
