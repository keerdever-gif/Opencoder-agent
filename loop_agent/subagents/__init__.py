"""Specialized sub-agents for OpenCoder-Agent."""
from loop_agent.subagents.base import AgentResult, BaseAgent
from loop_agent.subagents.code_agent import CodeAgent
from loop_agent.subagents.image_agent import ImageAgent
from loop_agent.subagents.search_agent import SearchAgent
from loop_agent.subagents.test_agent import TestAgent

__all__ = [
    "AgentResult",
    "BaseAgent",
    "CodeAgent",
    "ImageAgent",
    "SearchAgent",
    "TestAgent",
]
