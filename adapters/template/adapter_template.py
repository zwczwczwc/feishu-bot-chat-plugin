"""
Feishu A2A Adapter — Template Stub
====================================

A minimal adapter implementing the 3-function contract defined in
``docs/adapter-protocol.md``.

Usage
-----
Copy this file to ``adapters/<platform>/adapter.py``, then wire the 3 hooks
into your Agent platform's lifecycle.

Platforms (choose your mapping from docs/adapter-protocol.md):

  OpenClaw:  inbound_claim  | message_sending  | before_prompt_build
  Hermes:    pre_gateway_dispatch | transform_llm_output | on_session_start
  Discord:   gateway middleware    | message builder hook | channel topic inject
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core import (
    BotRegistry,
    MessageFilter,
    build_collaboration_context,
    outgoing_convert,
)


# ── Shared state ──────────────────────────────────────────────────────────────
# Constructed once at plugin/adapter init, shared across all hook calls.
# Replace with your platform's equivalent when adapting.

_registry: BotRegistry = BotRegistry()
_filter: Optional[MessageFilter] = None


def _ensure_filter() -> MessageFilter:
    """Lazily build the MessageFilter from the current registry state."""
    global _filter
    if _filter is None:
        _filter = MessageFilter(
            bot_open_id_set=_registry.bot_open_ids,
            bot_open_id_to_agent_map={
                bid: {
                    "botOpenId": info.bot_open_id,
                    "botName": info.bot_name,
                    "agentId": aid,
                }
                for aid, info in _registry.bots.items()
            },
            native_a2a_chats=_registry.native_a2a_chats,
        )
    return _filter


# ── Contract function 1: handle_inbound ──────────────────────────────────────


def handle_inbound(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process an incoming Feishu message.

    Args:
        event: Platform-specific inbound event dict. Expected keys (adapt
               to your platform's event format):

               - sender_id: str         — open_id of the message sender
               - mentioned_ids: list[str] — open_ids from <at> tags
               - text: str              — raw message content
               - chat_id: str           — group chat ID (oc_*)
               - platform: str          — "feishu" (default)
               - is_group: bool         — True for group chats (default)

    Returns:
        ``{"action": "skip", "reason": "..."}`` — swallow the message
        ``{"action": "rewrite", "text": "..."}`` — rewrite and forward
        ``{"action": "allow"}``                 — pass through unchanged
        ``None``                                — fall through / unhandled
    """
    # ── Step 1: parse platform event ──────────────────────────────────────
    # TODO: adapt to your platform's event shape
    sender_id = event.get("sender_id", "")
    mentioned_ids = event.get("mentioned_ids", [])
    text = event.get("text", "")
    chat_id = event.get("chat_id", "")
    platform = event.get("platform", "feishu")
    is_group = event.get("is_group", True)

    # ── Step 2: delegate to core engine ───────────────────────────────────
    f = _ensure_filter()
    result = f.handle_inbound(
        sender_id=sender_id,
        mentioned_ids=mentioned_ids,
        text=text,
        chat_id=chat_id,
        platform=platform,
        is_group=is_group,
    )

    # ── Step 3: translate to platform response ────────────────────────────
    return result.to_adapter_dict()


# ── Contract function 2: process_outgoing ────────────────────────────────────


def process_outgoing(
    text: str,
    current_agent_id: Optional[str] = None,
) -> Optional[str]:
    """Convert ``@BotName`` mentions to Feishu ``<at>`` tags in outgoing text.

    Args:
        text: The outgoing message text to transform.
        current_agent_id: The agent's own ID (skips self-replacement).

    Returns:
        Modified text, or ``None`` if no changes were needed.
    """
    converted = outgoing_convert(
        text=text,
        bot_registry={
            aid: {
                "botOpenId": info.bot_open_id,
                "botName": info.bot_name,
            }
            for aid, info in _registry.bots.items()
        },
        current_agent_id=current_agent_id,
    )
    return converted if converted != text else None


# ── Contract function 3: build_collaboration_context ─────────────────────────


def build_collaboration_context_for(
    *,
    channel_id: str,
    session_key: str,
    current_agent_id: str,
    group_bot_open_ids: Optional[set[str]] = None,
) -> Optional[str]:
    """Build the A2A collaboration instruction for system prompt injection.

    Args:
        channel_id: Platform channel identifier (e.g. ``"feishu"``).
        session_key: Session key from which the chat ID is extracted.
        current_agent_id: Agent ID of the bot whose prompt is being built.
        group_bot_open_ids: Optional set of bot open_ids in this group chat.
            When ``None``, all known bots are listed as available.

    Returns:
        A Chinese collaboration rules string to append to the system prompt,
        or ``None`` if no injection is needed.
    """
    return build_collaboration_context(
        channel_id=channel_id,
        current_agent_id=current_agent_id,
        session_key=session_key,
        bot_registry={
            aid: {
                "botOpenId": info.bot_open_id,
                "botName": info.bot_name,
            }
            for aid, info in _registry.bots.items()
        },
        native_a2a_chats=_registry.native_a2a_chats,
        group_bot_open_ids=group_bot_open_ids,
    )