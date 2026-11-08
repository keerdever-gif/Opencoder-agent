"""Manager / router for the OpenCoder multi-agent architecture."""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from config import config
from loop_agent.agent import agent as legacy_agent
from loop_agent.code_loop import LoopState
from loop_agent.connector import connector
from loop_agent.subagents import (
    AgentResult,
    CodeAgent,
    ImageAgent,
    SearchAgent,
    TestAgent,
)

_VALID_ROUTES = {"search", "code", "test", "image", "general"}

ROUTER_SYSTEM_PROMPT = """You are a fast intent router for a multi-agent software assistant.
Classify the user's latest request into exactly one route:
- search: find or verify external information, research, news, current facts, or URLs.
- code: create, inspect, edit, debug, refactor, or package software/files/projects.
- test: run tests, execute code, diagnose runtime output, or verify existing code.
- image: explicitly generate, draw, or create an image.
- general: conversation, explanations, planning, or requests that need no tools.

Return ONLY one JSON object: {"route":"search|code|test|image|general"}.
Use prior conversation only to resolve short follow-ups such as "run it" or "fix that".
Do not classify analysing an uploaded image as image generation unless the user asks to create one.
Do not classify discussion about image concepts as image generation.
"""


GENERAL_SYSTEM_PROMPT = """You are OpenCoder-Agent's general assistant.
Answer clearly and honestly. You are not the search, coding, testing, or image agent.
Do not claim to have browsed websites, edited files, executed code, or generated images.
When the user asks for one of those actions, that request should be handled by a specialist agent.
Use the conversation context where relevant. Ask only for information essential to answer.
"""


class Manager:
    """Classify each request and delegate it to exactly one specialist."""

    def __init__(self, backend: Any | None = None) -> None:
        self.backend = backend or legacy_agent
        self.search_agent = SearchAgent(self.backend)
        self.code_agent = CodeAgent(self.backend)
        self.test_agent = TestAgent(self.backend)
        self.image_agent = ImageAgent(self.backend)
        # Keep short-lived conversation context when PostgreSQL is not configured.
        self._volatile_history: OrderedDict[tuple[int, int], list[dict[str, Any]]] = OrderedDict()
        self._max_volatile_sessions = 2_000

    async def init(self) -> None:
        """Initialize shared persistence/resources through the legacy backend."""
        await self.backend.init()

    async def close(self) -> None:
        """Close shared persistence/resources."""
        await self.backend.close()

    @staticmethod
    def _route_model() -> str | None:
        configured = os.getenv("AI_ROUTER_MODEL", "").strip()
        return configured or config.ai_model

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
            return "\n".join(parts)
        return ""

    @staticmethod
    def _fallback_route(prompt: str) -> str:
        text = prompt.casefold()
        image_terms = (
            "generate an image", "create an image", "draw me", "illustrate",
            "make an image", "genera una imagen", "crea una imagen", "dibuja",
            "créer une image", "crée une image",
        )
        test_terms = (
            "run tests", "run test", "test this", "test the code", "pytest",
            "execute this code", "run this code", "debug this error", "stack trace",
            "ejecuta los tests", "ejecuta las pruebas", "prueba este código",
            "corrige este error de ejecución", "verifica el código",
        )
        search_terms = (
            "search the web", "look up", "find sources", "latest", "current",
            "news", "today", "research", "web search", "busca en internet",
            "busca en la web", "últimas noticias", "actualidad", "fuentes",
        )
        code_terms = (
            "write code", "create a file", "build an app", "implement", "refactor",
            "fix this code", "modify the project", "edit the file", "python script",
            "website", "web app", "html page", "write a program", "write a script",
            "escribe código", "crea un archivo", "crea una aplicación", "programa",
            "modifica el proyecto", "corrige el código", "página web", "sitio web",
        )
        if any(term in text for term in image_terms):
            return "image"
        if any(term in text for term in test_terms):
            return "test"
        if any(term in text for term in search_terms):
            return "search"
        if any(term in text for term in code_terms):
            return "code"
        return "general"

    async def classify(
        self,
        prompt: str,
        history: list[dict[str, Any]] | None = None,
        *,
        force_image: bool = False,
    ) -> str:
        """Use a short LLM call to choose one route, with a deterministic fallback."""
        if force_image:
            return "image"

        context = []
        for item in (history or [])[-4:]:
            if item.get("role") not in {"user", "assistant"}:
                continue
            text = self._extract_text(item.get("content"))
            if text:
                context.append(f"{item['role']}: {text[-700:]}")

        messages = [
            {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Recent conversation:\n"
                    + ("\n".join(context) if context else "(none)")
                    + "\n\nLatest user request:\n"
                    + prompt[:5000]
                ),
            },
        ]
        try:
            response = await connector.complete(
                messages,
                tools=None,
                model=self._route_model(),
            )
            choices = response.get("choices") or []
            content = self._extract_text((choices[0].get("message") or {}).get("content")) if choices else ""
            match = re.search(r"\{.*?\}", content, flags=re.DOTALL)
            if match:
                payload = json.loads(match.group(0))
                route = str(payload.get("route", "")).strip().casefold()
                if route in _VALID_ROUTES:
                    return route
            normalized = content.strip().casefold()
            for route in _VALID_ROUTES:
                if normalized == route or normalized == f'"{route}"':
                    return route
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[MANAGER] router model unavailable; using fallback: {exc}")

        return self._fallback_route(prompt)

    @staticmethod
    def _history_without_latest(
        history: list[dict[str, Any]],
        prompt: str,
    ) -> list[dict[str, Any]]:
        """Avoid sending the latest user request twice."""
        if history:
            last = history[-1]
            if last.get("role") == "user" and Manager._extract_text(last.get("content")) == prompt:
                return history[:-1]
        return history

    @staticmethod
    def _trim_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        trimmed: list[dict[str, Any]] = []
        for item in history[-8:]:
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = item.get("content")
            if isinstance(content, str):
                content = content[-6000:]
            elif not isinstance(content, list):
                continue
            trimmed.append({"role": role, "content": content})
        return trimmed

    async def _prepare_user_content(self, prompt: str) -> Any:
        """Reuse the existing attachment handling without invoking the old agent loop."""
        attachment_context = await self.backend._read_attached_text_files(prompt)
        media_paths = self.backend._extract_media_paths(prompt)
        if media_paths:
            content = self.backend._build_multimodal_content(prompt, media_paths)
            if attachment_context:
                content.append({"type": "text", "text": attachment_context})
            return content
        if attachment_context:
            return prompt + "\n\n" + attachment_context
        return prompt

    async def _general_response(
        self,
        prompt: str,
        history: list[dict[str, Any]],
        current_content: Any,
    ) -> str:
        messages = [
            {"role": "system", "content": GENERAL_SYSTEM_PROMPT},
            *self._trim_history(history),
            {"role": "user", "content": current_content},
        ]
        models = self.backend._select_model(prompt)
        errors: list[str] = []
        for model in models:
            try:
                response = await connector.complete(messages, tools=None, model=model)
                choices = response.get("choices") or []
                if not choices:
                    raise RuntimeError("The provider returned no choices.")
                message = choices[0].get("message") or {}
                answer = self._extract_text(message.get("content")).strip()
                if answer:
                    return answer
                raise RuntimeError("The provider returned an empty response.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
        raise RuntimeError(
            "The general assistant could not complete the request: "
            + (" | ".join(errors[-3:]) or "no model is configured")
        )

    async def _run_code_workflow(
        self,
        prompt: str | list[dict[str, Any]],
        history: list[dict[str, Any]],
        loop_state: LoopState | None = None,
        original_request: str | None = None,
    ) -> AgentResult:
        original_context = original_request or (
            prompt if isinstance(prompt, str) else self._extract_text(prompt)
        )
        state = loop_state or LoopState(
            workspace=Path(config.workspace).expanduser().resolve(),
            max_attempts=config.max_fix_attempts,
        )
        result = await self.code_agent.run(
            prompt,
            history=history,
            loop_state=state,
        )

        if not config.verify_code or not state.pending():
            return result

        verification_note = ""
        # The Test Agent owns verification; failed results are fed back to the Code Agent.
        for _ in range(config.max_fix_attempts + 1):
            gate = await self.test_agent.verify_workspace(state)
            if gate.action in {"skip", "pass"}:
                verification_note = gate.message
                break
            if gate.action == "giveup":
                verification_note = gate.message
                break
            if gate.action != "retry":
                verification_note = (
                    "\n\nThe verification agent returned an unknown status; "
                    "review the changed files manually."
                )
                break

            repair_prompt = (
                "Original user request: "
                + original_context[:4000]
                + "\n\nThe Test Agent found a verification failure in the files you just changed. "
                "Read the affected file(s), identify the root cause, and fix the issue using "
                "your available file tools. Do not claim success until the Manager verifies it.\n\n"
                + gate.message
            )
            result = await self.code_agent.run(
                repair_prompt,
                history=None,
                loop_state=state,
            )
            if not state.pending():
                verification_note = (
                    "\n\nThe Code Agent did not register any further verifiable file changes. "
                    "Please review the reported failure manually."
                )
                break

        final_text = result.text
        if verification_note:
            final_text = f"{final_text.rstrip()}\n\n{verification_note.strip()}"
        return AgentResult(
            text=final_text,
            image_url=result.image_url,
            files=result.files,
            tool_events=result.tool_events,
        )

    @staticmethod
    def _user_requested_repair(prompt: str) -> bool:
        return bool(re.search(
            r"(?i)\b(?:fix|repair|correct|resolve|patch|arregla|corrige|repara|soluciona)\b",
            prompt,
        ))

    @staticmethod
    def _is_environment_failure(tool_events: list[dict[str, Any]]) -> bool:
        failed_errors = " ".join(
            str(event.get("error", ""))
            for event in tool_events
            if not event.get("ok")
        ).casefold()
        return bool(re.search(
            r"api[_ -]?key|token|credential|not configured|not set|missing environment|"
            r"connection refused|sandbox unavailable|authentication failed",
            failed_errors,
        ))

    async def _run_test_workflow(
        self,
        prompt: str | list[dict[str, Any]],
        prompt_text: str,
        history: list[dict[str, Any]],
    ) -> AgentResult:
        state = LoopState(
            workspace=Path(config.workspace).expanduser().resolve(),
            max_attempts=config.max_fix_attempts,
        )
        test_result = await self.test_agent.run(
            prompt,
            history=history,
            loop_state=state,
        )
        has_failed_check = any(
            not event.get("ok", True)
            for event in test_result.tool_events
        )

        if (
            self._user_requested_repair(prompt_text)
            and has_failed_check
            and not self._is_environment_failure(test_result.tool_events)
        ):
            repair_request = (
                "The user asked to test and repair this project. The Test Agent found "
                "the following failure. Inspect the relevant workspace files, fix the "
                "root cause, and preserve existing functionality. The Manager will run "
                "verification after your edits.\n\n"
                + test_result.text
            )
            repair_result = await self._run_code_workflow(
                repair_request,
                history,
                loop_state=state,
            )
            combined = (
                test_result.text.rstrip()
                + "\n\n**Repair attempt:**\n"
                + repair_result.text.strip()
            )
            return AgentResult(
                text=combined,
                image_url=repair_result.image_url,
                files=repair_result.files,
                tool_events=test_result.tool_events + repair_result.tool_events,
            )

        return test_result

    async def _dispatch(
        self,
        route: str,
        prompt: str,
        history: list[dict[str, Any]],
        current_content: Any,
    ) -> AgentResult:
        agent_input = current_content if current_content is not None else prompt
        if route == "search":
            return await self.search_agent.run(agent_input, history=history)
        if route == "code":
            return await self._run_code_workflow(
                agent_input,
                history,
                original_request=prompt,
            )
        if route == "test":
            return await self._run_test_workflow(
                agent_input,
                prompt,
                history,
            )
        if route == "image":
            return await self.image_agent.run(agent_input, history=history)
        if route == "general":
            answer = await self._general_response(prompt, history, current_content)
            return AgentResult(text=answer)
        raise RuntimeError(f"Unsupported Manager route: {route}")

    def _remember_volatile_history(
        self,
        key: tuple[int, int],
        history: list[dict[str, Any]],
    ) -> None:
        """Store a bounded transcript for sessions when PostgreSQL is disabled."""
        try:
            message_limit = max(2, int(config.memory_messages))
        except (TypeError, ValueError):
            message_limit = 12
        self._volatile_history[key] = [dict(item) for item in history[-message_limit:]]
        self._volatile_history.move_to_end(key)
        while len(self._volatile_history) > self._max_volatile_sessions:
            self._volatile_history.popitem(last=False)

    async def ask_with_metadata(
        self,
        user_id: int,
        channel_id: int,
        prompt: str,
        force_image: bool = False,
    ) -> dict[str, Any]:
        """Compatibility-friendly request entry point returning route metadata."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be a non-empty string.")

        await self.backend.cleanup_memory()
        conversation_key = (int(user_id), int(channel_id))
        if getattr(self.backend, "pool", None) is not None:
            await self.backend.save(user_id, channel_id, "user", prompt)
            history = await self.backend.history(user_id, channel_id)
        else:
            history = [
                dict(item)
                for item in self._volatile_history.get(conversation_key, [])
            ]
            history.append({"role": "user", "content": prompt})
            self._remember_volatile_history(conversation_key, history)

        if (
            not history
            or history[-1].get("role") != "user"
            or history[-1].get("content") != prompt
        ):
            history.append({"role": "user", "content": prompt})

        history_for_context = self._history_without_latest(history, prompt)
        current_content = await self._prepare_user_content(prompt)
        route = await self.classify(
            prompt,
            history_for_context,
            force_image=force_image,
        )

        result = await self._dispatch(
            route,
            prompt,
            self._trim_history(history_for_context),
            current_content,
        )
        answer = result.text.strip() or "The agent completed without a text response."
        if getattr(self.backend, "pool", None) is not None:
            await self.backend.save(user_id, channel_id, "assistant", answer)
        else:
            history.append({"role": "assistant", "content": answer})
            self._remember_volatile_history(conversation_key, history)

        return {
            "answer": answer,
            "image_url": result.image_url,
            "files": result.files or None,
            "route": route,
        }

    async def ask(
        self,
        user_id: int,
        channel_id: int,
        prompt: str,
        force_image: bool = False,
    ) -> tuple[str, str | None, list[str] | None]:
        """Preserve the tuple contract used by the existing Discord bot and CLI."""
        result = await self.ask_with_metadata(
            user_id=user_id,
            channel_id=channel_id,
            prompt=prompt,
            force_image=force_image,
        )
        return result["answer"], result["image_url"], result["files"]


manager = Manager()
