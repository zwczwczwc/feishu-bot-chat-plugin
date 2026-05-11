"""
Mention Processor — @botName ↔ <at> tag conversion.

Port of the JS plugin's @-mention regex logic (index.js lines 158-431):

- Outgoing: replaces `@BotName` (flexible matching, dashes optional)
  with Feishu `<at user_id='open_id'>BotName</at>` tags.
  Also adds text fallback "(BotName)" after each <at> tag for
  streaming card visibility.

- Inbound: extracts <at> tags from incoming messages to identify
  which bots were mentioned.
"""

import re
from typing import Any, Dict, List, Optional


def _escape_regex(text: str) -> str:
    """Escape special regex characters in a string."""
    return re.escape(text)


def outgoing_convert(text: str, bot_registry: Dict[str, Dict[str, Any]],
                     current_agent_id: Optional[str] = None) -> str:
    """Replace @BotName mentions with Feishu <at> tags.

    Ported from index.js message_sending hook (lines 406-425).

    For each bot in the registry (optionally skipping the sender's own bot),
    builds a regex pattern matching '@BotName' where dashes in the bot name
    are treated as optional for flexible matching.

    After all bot replacements, adds a text fallback "(BotName)" after each
    <at> tag if one is not already present — needed for streaming card
    visibility in Feishu.

    Args:
        text: The outgoing message text.
        bot_registry: Dict mapping agentId → {botOpenId, botName, ...}.
        current_agent_id: If provided, skips the bot matching this agent
                          (avoids self-replacement).

    Returns:
        The converted text with <at> tags.
    """
    content = text

    for agent_id, bot in bot_registry.items():
        # Skip self if current_agent_id is provided
        if current_agent_id is not None and agent_id == current_agent_id:
            continue

        bot_name = bot.get("botName", "")
        bot_open_id = bot.get("botOpenId", "")

        if not bot_name or not bot_open_id:
            continue

        # Build flexible pattern: escape regex chars, then make dashes optional
        escaped_name = _escape_regex(bot_name)
        flex_pattern = escaped_name.replace(r"\-", r"\-?")
        pattern = re.compile("@" + flex_pattern, re.IGNORECASE)

        replacement = f'<at user_id="{bot_open_id}">{bot_name}</at>'

        new_content, count = pattern.subn(replacement, content)
        if count > 0:
            content = new_content

    # Add text fallback after <at> tags if not already present
    # Pattern: <at user_id="...">name</at> NOT followed by " (name)"
    content = re.sub(
        r'<at user_id="([^"]+)">([^<]+)</at>(?!\s*\([^)]+\))',
        r'<at user_id="\1">\2</at> (\2)',
        content,
    )

    return content


def inbound_convert(text: str) -> List[Dict[str, str]]:
    """Extract <at> tags from incoming message text.

    Parses all Feishu <at> tags from the text and returns a list of
    dicts with 'user_id' and 'name' keys. This is used for inbound bot
    identification — checking which bots were mentioned in a message.

    Args:
        text: The incoming message content.

    Returns:
        A list of dicts: [{'user_id': 'ou_xxx', 'name': 'BotName'}, ...].
        Returns an empty list if no <at> tags are found.
    """
    if not text:
        return []

    pattern = re.compile(r'<at\s+user_id="([^"]+)"\s*>([^<]+)</at>')
    matches = pattern.findall(text)

    return [{"user_id": uid, "name": name} for uid, name in matches]


def inbound_extract_bot_open_ids(text: str) -> List[str]:
    """Extract just the open_id values from <at> tags.

    Convenience wrapper around inbound_convert for quick bot ID checks.

    Args:
        text: The incoming message content.

    Returns:
        List of bot open_id strings.
    """
    return [m["user_id"] for m in inbound_convert(text)]


def is_bot_mentioned(text: str, bot_open_id: str) -> bool:
    """Check if a specific bot was mentioned via <at> tag.

    Args:
        text: The incoming message content.
        bot_open_id: The Feishu open_id of the bot to check for.

    Returns:
        True if the bot's open_id appears in any <at> tag.
    """
    return any(m["user_id"] == bot_open_id for m in inbound_convert(text))