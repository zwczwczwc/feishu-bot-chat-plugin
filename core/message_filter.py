"""
Message Filter — inbound bot message filtering + native A2A delivery detection.

Translated from feishu-bot-chat-plugin/index.js `inbound_claim` hook (lines 436-475).

Logic:
  1. Skip messages not from Feishu group chats.
  2. Human messages → pass through unconditionally.
  3. Bot messages with @mention → confirm native A2A delivery, inject sender info,
     then pass through (possibly rewritten).
  4. Bot messages without @mention → swallow (don't forward to agent).

Key differences from JS original:
  - No `wasMentioned` on the event → inferred from `mentioned_ids` (parsed
    from `<at>` tags in the message text).
  - No `isBot` flag on the event → checked via `bot_open_id_set`.
  - Returns a dict compatible with any adapter layer (Hermes pre_gateway_dispatch,
    OpenClaw inbound_claim, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── FilterResult ──────────────────────────────────────────────────────────────


@dataclass
class FilterResult:
    """Result of filtering an inbound message.

    Compatible with the adapter protocol defined in the A2A plugin spec.
    Each adapter translates this to its own hook return format.
    """

    action: str  # "pass" | "skip" | "rewrite"
    text: Optional[str] = None  # New text when action == "rewrite"
    reason: Optional[str] = None  # Log-friendly explanation

    def to_adapter_dict(self) -> Optional[dict]:
        """Convert to Hermes pre_gateway_dispatch compatible dict."""
        action_map = {
            "skip": "skip",
            "rewrite": "rewrite",
            "pass": "allow",
        }
        mapped = action_map.get(self.action)
        if mapped is None:
            return None
        if mapped == "skip":
            return {"action": "skip", "reason": self.reason or "filtered"}
        if mapped == "rewrite":
            return {"action": "rewrite", "text": self.text or ""}
        # "allow" — pass through
        return {"action": "allow"}


# ── MessageFilter ─────────────────────────────────────────────────────────────


class MessageFilter:
    """Filters inbound Feishu messages based on sender type and mention status.

    Encapsulates the filtering logic formerly in the JS `inbound_claim` hook.
    Designed to be stateless for any given call — shared mutable state
    (bot registry, native A2A chats set) is injected at construction time.

    Parameters
    ----------
    bot_open_id_set : set[str]
        Set of known bot open_id values. Senders in this set are bots.
    bot_open_id_to_agent_map : dict[str, dict]
        Map from bot open_id to {botOpenId, botName, agentId, ...}.
    native_a2a_chats : set[str]
        Mutable set of chat IDs where native bot-to-bot delivery has been
        confirmed. Updated as a side effect when bots @ each other.
    logger : callable, optional
        Log sink. Called like ``logger.info(msg)`` or ``logger.debug(msg)``.
        Falls back to print when None.
    """

    def __init__(
        self,
        bot_open_id_set: set[str],
        bot_open_id_to_agent_map: dict[str, dict],
        native_a2a_chats: set[str],
        logger: Optional[callable] = None,
    ):
        self._bot_set = bot_open_id_set
        self._bot_map = bot_open_id_to_agent_map
        self._native_a2a_chats = native_a2a_chats
        self._log = logger or _noop_logger

    # ── Public API ────────────────────────────────────────────────────────────

    def handle_inbound(
        self,
        sender_id: str,
        mentioned_ids: list[str],
        text: str,
        chat_id: str,
        platform: str = "feishu",
        is_group: bool = True,
    ) -> FilterResult:
        """Process an inbound message using the same logic as the JS inbound_claim hook.

        Parameters
        ----------
        sender_id : str
            The open_id of the message sender.
        mentioned_ids : list[str]
            List of open_ids that were @-mentioned in this message.
            Parsed from ``<at user_id="ou_xxx">`` tags in the text.
        text : str
            The raw message text content.
        chat_id : str
            The conversation (group chat) ID.
        platform : str, default "feishu"
            Platform name. Non-feishu messages are passed through.
        is_group : bool, default True
            Whether the message is from a group chat.

        Returns
        -------
        FilterResult
            - ``action="pass"`` — human message, or bot @mention without
              registered info (allow through unchanged).
            - ``action="rewrite"`` — bot @mention with sender info injected
              (allow through with modified text).
            - ``action="skip"`` — bot message without @mention (swallow).
        """
        # ── Stage 1: platform + chat type gate ────────────────────────────────
        if platform.lower() != "feishu" or not is_group:
            return FilterResult(action="pass", reason="not-feishu-or-not-group")

        # ── Stage 2: determine sender type ────────────────────────────────────
        if sender_id not in self._bot_set:
            # Human sender — always pass through
            self._log(
                "debug",
                f"[message_filter] Human message from {sender_id}, passing through",
            )
            return FilterResult(action="pass", reason="human-sender")

        # ── Sender is a bot ────────────────────────────────────────────────────
        # Check if this bot was @mentioned
        was_mentioned = sender_id in mentioned_ids

        if was_mentioned:
            # ── Stage 3a: bot @mention → native A2A confirmed ─────────────────
            if chat_id and chat_id not in self._native_a2a_chats:
                self._native_a2a_chats.add(chat_id)
                self._log(
                    "info",
                    f"[message_filter] Native A2A delivery confirmed for chat={chat_id} "
                    f"(sender={sender_id})",
                )

            # ── Stage 3b: inject sender bot identity ──────────────────────────
            sender_bot = self._bot_map.get(sender_id)
            if sender_bot and text:
                sender_info = _build_sender_info(
                    bot_name=sender_bot.get("botName", "Unknown Bot"),
                    bot_open_id=sender_bot.get("botOpenId", sender_id),
                )
                self._log(
                    "debug",
                    f"[message_filter] Injecting sender info: "
                    f"{sender_bot.get('botName')} ({sender_bot.get('botOpenId')})",
                )
                return FilterResult(
                    action="rewrite",
                    text=sender_info + text,
                    reason="bot-at-mention-with-injection",
                )

            # Bot @mention but no lookup data — allow through unchanged
            self._log(
                "debug",
                f"[message_filter] Bot @mention from {sender_id}, allowing through",
            )
            return FilterResult(action="pass", reason="bot-at-mention-no-info")

        # ── Stage 4: bot message without @mention → swallow ──────────────────
        self._log(
            "info",
            f"[message_filter] Swallowing bot message (not mentioned) from {sender_id}",
        )
        return FilterResult(action="skip", reason="bot-not-mentioned")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_sender_info(bot_name: str, bot_open_id: str) -> str:
    """Build the sender identity prefix that gets injected into the message.

    Matches the JS original format::

        [来自机器人「{name}」— 如需 @ 回对方请使用：<at user_id="{id}">{name}</at>]\n\n
    """
    return (
        f"[来自机器人「{bot_name}」— 如需 @ 回对方请使用："
        f"<at user_id=\"{bot_open_id}\">{bot_name}</at>]\n\n"
    )


def _noop_logger(level: str, msg: str) -> None:
    """Fallback logger that prints to stdout."""
    print(f"[{level.upper()}] {msg}")