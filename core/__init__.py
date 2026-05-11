"""Feishu A2A Collaboration Plugin — Core Engine.

A universal Python implementation of the Feishu bot-to-bot A2A collaboration
protocol, ported from the original JS plugin (Leochens/feishu-bot-chat-plugin).

This core engine is agent-agnostic — it does not depend on any specific Agent
framework. Each Agent integrates via a thin adapter layer.
"""

# ── Re-export public API ──────────────────────────────────────────────────────
# Adapters import from the package root:  from core import BotRegistry, ...
# Internal modules continue to import each other directly.

from .bot_registry import BotRegistry, BotInfo as _BotInfo
from .cache import Cache
from .collaboration_rules import (
    build_collaboration_context,
    extract_chat_id_from_session,
    format_bot_list,
    split_bots_by_group,
)
from .feishu_api import FeishuAPIClient
from .mention_processor import (
    inbound_convert,
    inbound_extract_bot_open_ids,
    is_bot_mentioned,
    outgoing_convert,
)
from .message_filter import FilterResult, MessageFilter

# Re-export BotInfo for adapter use
BotInfo = _BotInfo

__all__ = [
    "BotInfo",
    "BotRegistry",
    "Cache",
    "FeishuAPIClient",
    "FilterResult",
    "MessageFilter",
    "build_collaboration_context",
    "extract_chat_id_from_session",
    "format_bot_list",
    "inbound_convert",
    "inbound_extract_bot_open_ids",
    "is_bot_mentioned",
    "outgoing_convert",
    "split_bots_by_group",
]

__version__ = "0.1.0"