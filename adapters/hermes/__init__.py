"""Hermes Plugin entry point for Feishu A2A Collaboration.

Registers hooks and tools via the Hermes plugin system.
"""

import json
from typing import Any, Dict, Optional

from .adapter import FeishuA2AAdapter

# Singleton adapter instance
_adapter: Optional[FeishuA2AAdapter] = None


def get_adapter() -> FeishuA2AAdapter:
    global _adapter
    if _adapter is None:
        _adapter = FeishuA2AAdapter()
    return _adapter


def register(ctx) -> None:
    """Register plugin hooks and tools with Hermes Agent."""
    adapter = get_adapter()

    # Register tools
    ctx.register_tool(
        name="feishu_discover_bots",
        description="Discover available Feishu bots for A2A collaboration",
        schema={
            "name": "feishu_discover_bots",
            "description": "Get the list of available Feishu bots that can be @ mentioned",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=lambda args, **kw: adapter.discover_bots_tool(),
    )

    # Register hook: pre_gateway_dispatch — filter bot messages
    ctx.register_hook(
        "pre_gateway_dispatch",
        lambda event, gateway, session_store, **kw: adapter.handle_inbound(event),
    )

    # Register hook: transform_llm_output — @BotName → <at> conversion
    ctx.register_hook(
        "transform_llm_output",
        lambda response_text, session_id, model, platform, **kw: (
            adapter.process_outgoing(response_text) if platform == "feishu" else None
        ),
    )

    # Register hook: on_session_start — inject A2A context
    ctx.register_hook(
        "on_session_start",
        lambda session_id, model, platform, **kw: (
            _inject_collaboration_context(session_id, platform)
            if platform == "feishu" else None
        ),
    )

    # Register the A2A collaboration skills
    _register_skills(ctx)


def _inject_collaboration_context(session_id: str, platform: str) -> None:
    """Inject A2A collaboration context into the session."""
    # Note: Hermes on_session_start hook receives session context
    # The adapter builds and returns collaboration rules text
    adapter = get_adapter()
    context = adapter.build_context(chat_id=session_id)
    if context:
        # Return the context as a system prompt append
        return {"append_system_context": context}


def _register_skills(ctx) -> None:
    """Register A2A collaboration skills for the agent to use."""
    skills_dir = os.path.join(os.path.dirname(__file__), "..", "..", "skills")
    import os
    if os.path.isdir(skills_dir):
        for skill_name in os.listdir(skills_dir):
            skill_path = os.path.join(skills_dir, skill_name, "SKILL.md")
            if os.path.isfile(skill_path):
                ctx.register_skill(name=skill_name, path=skill_path)