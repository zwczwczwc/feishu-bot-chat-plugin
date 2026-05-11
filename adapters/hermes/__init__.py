"""Hermes Plugin entry point for Feishu A2A Collaboration.

Registers hooks and tools via the Hermes plugin system.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure the plugin root is on sys.path so core.* absolute imports work
_plugin_root = os.path.dirname(__file__)
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

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

    # Register tools — the feishu_discover_bots tool goes into the "feishu" toolset
    ctx.register_tool(
        name="feishu_discover_bots",
        toolset="feishu",
        description="Discover available Feishu bots for A2A collaboration in group chats",
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
    """Register A2A collaboration skills for the agent to use.

    Skills are stored alongside the repo at ../../skills/ relative to the
    plugin dir.  Falls back to trying ~/.hermes/skills/ and the repo clone.
    """
    # Path 1: relative to plugin dir → ../../skills/
    plugin_dir = Path(__file__).resolve().parent
    skills_dir = plugin_dir / ".." / ".." / "skills"
    if skills_dir.is_dir():
        for skill_name in os.listdir(str(skills_dir)):
            skill_path = skills_dir / skill_name / "SKILL.md"
            if skill_path.is_file():
                ctx.register_skill(name=skill_name, path=skill_path)
                continue

    # Path 2: look in the clone
    clone_skills = Path("/tmp/feishu-a2a-plugin/skills")
    if clone_skills.is_dir():
        for skill_name in os.listdir(str(clone_skills)):
            skill_path = clone_skills / skill_name / "SKILL.md"
            if skill_path.is_file():
                ctx.register_skill(name=skill_name, path=skill_path)
                continue

    # Path 3: try ~/.hermes/skills/
    home_skills = Path.home() / ".hermes" / "skills"
    if home_skills.is_dir():
        for skill_name in os.listdir(str(home_skills)):
            skill_path = home_skills / skill_name / "SKILL.md"
            if skill_path.is_file():
                ctx.register_skill(name=skill_name, path=skill_path)