"""Hermes Agent adapter for the Feishu A2A Collaboration Plugin.

Maps the A2A Core Engine (core/) to Hermes Agent's plugin hook system.

IMPORTANT: The pre_gateway_dispatch hook receives `event` as a
``MessageEvent`` dataclass (from gateway/platforms/base.py), NOT a dict.
Use attribute access (``event.source.platform``), not ``.get()``.
"""

import json
import os
from typing import Any, Dict, Optional

from core.bot_registry import BotRegistry
from core.cache import Cache
from core.collaboration_rules import build_collaboration_context
from core.feishu_api import get_bot_info_sync, get_tenant_token_sync
from core.mention_processor import outgoing_convert

# Hermes gateway types — imported here so the adapter works correctly
# with the pre_gateway_dispatch hook.
try:
    from gateway.config import Platform as HermesPlatform
except ImportError:
    HermesPlatform = None


class FeishuA2AAdapter:
    """Adapter that bridges Core Engine logic with Hermes Agent hooks."""

    def __init__(self):
        self.cache = Cache()
        self.bot_registry = BotRegistry(cache=self.cache)
        self._initialized = False

    def initialize(self) -> None:
        """Initialize the adapter: discover bots, build lookups."""
        if self._initialized:
            return
        self.bot_registry.discover_from_hermes_config()
        self._initialized = True

    def _bot_registry_as_dict(self) -> Dict[str, Dict[str, Any]]:
        """Convert BotRegistry.bots (dict of BotInfo) to the dict-of-dicts
        format expected by core functions (botName, botOpenId, accountId)."""
        return {
            agent_id: {
                "botName": bot.bot_name,
                "botOpenId": bot.bot_open_id,
                "accountId": bot.account_id,
                "description": getattr(bot, "description", ""),
            }
            for agent_id, bot in self.bot_registry.bots.items()
        }

    def _is_feishu_group(self, event: Any) -> bool:
        """Check if the event is a Feishu group chat message.

        Works with both MessageEvent dataclass and dict-like events.
        """
        # Determine platform — MessageEvent uses `source.platform` (Platform enum)
        if HermesPlatform is not None and hasattr(event, "source"):
            source = event.source
            if not source or source.platform != HermesPlatform.FEISHU:
                return False
            if source.chat_type != "group":
                return False
            return True

        # Fallback: dict-style (OpenClaw compatibility)
        if isinstance(event, dict):
            if event.get("platform") != "feishu":
                return False
            if not event.get("is_group"):
                return False
            return True

        # Last resort: check attributes directly
        platform_val = getattr(event, "platform", None) or ""
        if platform_val != "feishu":
            return False
        if not getattr(event, "is_group", False):
            return False
        return True

    def _get_event_sender_id(self, event: Any) -> str:
        """Extract sender user_id from the event."""
        if hasattr(event, "source") and event.source:
            return event.source.user_id or ""
        if isinstance(event, dict):
            return event.get("sender_id", "")
        return getattr(event, "sender_id", "") or getattr(event, "user_id", "")

    def _get_event_chat_id(self, event: Any) -> str:
        """Extract chat_id from the event."""
        if hasattr(event, "source") and event.source:
            return event.source.chat_id or ""
        if isinstance(event, dict):
            return event.get("chat_id", "")
        return getattr(event, "chat_id", "")

    def _get_event_text(self, event: Any) -> str:
        """Extract message text from the event."""
        if hasattr(event, "text"):
            return event.text or ""
        if isinstance(event, dict):
            return event.get("content", "") or event.get("text", "")
        return getattr(event, "content", "") or getattr(event, "text", "")

    def _was_our_bot_mentioned(self, event: Any) -> bool:
        """Detect if our bot was @-mentioned in this message.

        Uses several detection strategies since MessageEvent has no
        ``wasMentioned`` field:
        1. Check if the sender's open_id appears in ``<at>`` tags within text
        2. Check raw_message mentions for our bot's open_id
        3. Detect the feishu platform's mention hint prefix
        """
        text = self._get_event_text(event)
        our_bot_ids = self.bot_registry.bot_open_ids

        # Strategy 1: <at> tag check
        for bot_id in our_bot_ids:
            if bot_id and f'<at user_id="{bot_id}">' in text:
                return True

        # Strategy 2: raw_message mentions (lark-oapi Message object)
        raw = getattr(event, "raw_message", None) or getattr(event, "raw", None)
        if raw is not None and our_bot_ids:
            try:
                mentions = getattr(raw, "mentions", None) or (
                    raw.get("mentions") if isinstance(raw, dict) else []
                )
                if mentions:
                    for mention in mentions:
                        mid = (
                            getattr(mention, "open_id", None)
                            or getattr(mention, "user_id", None)
                            or (mention.get("open_id") if isinstance(mention, dict) else None)
                            or (mention.get("user_id") if isinstance(mention, dict) else None)
                            or ""
                        )
                        if str(mid) in our_bot_ids:
                            return True
            except Exception:
                pass

        # Strategy 3: [Mentioned: ...] hint prefix from feishu adapter
        if text.startswith("[Mentioned:") and our_bot_ids:
            import re
            mentioned_ids = re.findall(r'open_id=([^\s,\)]+)', text)
            for mid in mentioned_ids:
                if mid in our_bot_ids:
                    return True

        return False

    # ---- Hook: pre_gateway_dispatch ----

    def handle_inbound(self, event: Any) -> Optional[Dict[str, Any]]:
        """Filter incoming Feishu messages.

        ``event`` is a ``MessageEvent`` dataclass (NOT a dict) when called
        via the Hermes pre_gateway_dispatch hook.

        Returns:
            ``None`` or ``{"action": "allow"}`` → pass through
            ``{"action": "skip", "reason": "..."}`` → drop message
            ``{"action": "rewrite", "text": "..."}`` → modify text
        """
        self.initialize()

        # Only process Feishu group messages
        if not self._is_feishu_group(event):
            return None

        sender_id = self._get_event_sender_id(event)
        chat_id = self._get_event_chat_id(event)
        content = self._get_event_text(event)

        # Check if sender is a known bot
        if not self.bot_registry.is_known_bot(sender_id):
            return None  # Human message — pass through

        # Bot message — detect if our bot was @-mentioned
        was_mentioned = self._was_our_bot_mentioned(event)

        if was_mentioned:
            # Native A2A delivery confirmed
            self.bot_registry.mark_native_a2a(chat_id)

            # Inject sender bot info so the receiving agent knows how to @ back
            sender_bot = self.bot_registry.get_agent_by_bot_open_id(sender_id)
            if sender_bot and content:
                sender_at_tag = (
                    f'<at user_id="{sender_bot.bot_open_id}">'
                    f"{sender_bot.bot_name}</at>"
                )
                sender_info = (
                    f'[来自机器人「{sender_bot.bot_name}」'
                    f"— 如需 @ 回对方请使用：{sender_at_tag}]\n\n"
                )
                return {"action": "rewrite", "text": sender_info + content}

            return None  # Bot @mention but no lookup data — allow through

        # Bot message without mention — swallow it
        return {"action": "skip", "reason": "bot-not-mentioned"}

    # ---- Hook: transform_llm_output ----

    def process_outgoing(self, text: str) -> Optional[str]:
        """Convert @BotName to <at> tags. Maps from message_sending."""
        self.initialize()
        reg = self._bot_registry_as_dict()
        return outgoing_convert(text, reg)

    # ---- Hook: on_session_start ----

    def build_context(self, chat_id: str) -> Optional[str]:
        """Build A2A collaboration context. Maps from before_prompt_build."""
        self.initialize()

        reg_dict = self._bot_registry_as_dict()
        current_agent_id = os.environ.get("HERMES_AGENT_ID", "")

        # Build session_key from chat_id for the core function
        session_key = f":feishu:group:{chat_id}"

        return build_collaboration_context(
            channel_id="feishu",
            current_agent_id=current_agent_id,
            session_key=session_key,
            bot_registry=reg_dict,
            native_a2a_chats=self.bot_registry.native_a2a_chats,
        )

    # ---- Tool: feishu_discover_bots ----

    def discover_bots_tool(self) -> str:
        """Tool function that returns available Feishu bots as formatted text."""
        self.initialize()
        bots = self.bot_registry.bots
        if not bots:
            return json.dumps({
                "success": True,
                "bots": [],
                "message": "No Feishu bots discovered. Check FEISHU_APP_ID and FEISHU_APP_SECRET.",
            }, ensure_ascii=False)

        result = [
            {"agent_id": agent_id, "bot_name": bot.bot_name, "bot_open_id": bot.bot_open_id}
            for agent_id, bot in bots.items()
        ]
        return json.dumps({"success": True, "bots": result}, ensure_ascii=False)