"""Hermes Agent adapter for the Feishu A2A Collaboration Plugin.

Maps the A2A Core Engine (core/) to Hermes Agent's plugin hook system.
"""

import json
import os
from typing import Any, Dict, Optional

from core.bot_registry import BotRegistry
from core.cache import Cache
from core.collaboration_rules import build_collaboration_context
from core.feishu_api import get_bot_info_sync, get_tenant_token_sync
from core.mention_processor import outgoing_convert


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

    # ---- Hook: pre_gateway_dispatch ----

    def handle_inbound(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Filter incoming Feishu messages. Maps from inbound_claim."""
        self.initialize()

        # Only process Feishu group messages
        if event.get("platform") != "feishu":
            return None
        if not event.get("is_group"):
            return None

        sender_id = event.get("sender_id", "")
        chat_id = event.get("chat_id", "")
        content = event.get("content", "")
        was_mentioned = event.get("was_mentioned", False)

        # Check if sender is a known bot
        if not self.bot_registry.is_known_bot(sender_id):
            return None  # Human message — pass through

        # Bot message with wasMentioned=true → native A2A delivery confirmed
        if was_mentioned:
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
                return {"content": sender_info + content}

            return None

        # Bot message without mention — swallow it
        return {"handled": True}

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