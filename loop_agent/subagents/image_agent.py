"""Image generation sub-agent."""
from __future__ import annotations

from typing import Any

from loop_agent.subagents.base import BaseAgent

IMAGE_SYSTEM_PROMPT = """You are OpenCoder's image-generation specialist.

Your only tool is generate_image.
- Use generate_image only when the user explicitly requests a generated image.
- Convert the user's idea into a clear, detailed visual prompt.
- Preserve requested subject, style, composition, aspect ratio, and mood.
- Do not claim an image was generated unless the tool succeeds.
- When the tool returns an image URL, include it in the final response.
- Do not use tools unrelated to image generation.
"""


class ImageAgent(BaseAgent):
    name = "image"
    system_prompt = IMAGE_SYSTEM_PROMPT
    tool_names = ("generate_image",)

    def __init__(self, backend: Any) -> None:
        super().__init__(backend)
