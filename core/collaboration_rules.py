"""
Collaboration Rules — A2A context injection for bot-to-bot coordination.

Port of the JS plugin's ``before_prompt_build`` hook (index.js lines 291-394):

- Builds a collaboration instruction string listing available bots (filtered
  to actual group members where possible) and the A2A @-mention protocol.
- Injects a warning if native bot-to-bot delivery hasn't been confirmed yet.
- Returns the instruction as ``appendSystemContext`` for the agent's system
  prompt.

This module is framework-agnostic — it computes the collaboration context
string from raw data.  The actual hook registration and state management
(``botRegistry``, ``nativeA2AChats``, ``groupMemberCache``) lives in the
adapter layer.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

__all__ = [
    "build_collaboration_context",
    "extract_chat_id_from_session",
    "split_bots_by_group",
    "format_bot_list",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Regex to extract group chat ID from a Feishu session key.
# Session keys look like: ``:feishu:group:oc_abc123:...``
_SESSION_GROUP_RE = re.compile(r":feishu:group:(oc_[^:]+)")


def extract_chat_id_from_session(session_key: str) -> Optional[str]:
    """Extract the Feishu group chat ID from a session key.

    Ported from ``index.js`` line 303::

        const groupMatch = sessionKey.match(/:feishu:group:(oc_[^:]+)/);
        const chatId = groupMatch ? groupMatch[1] : null;

    Args:
        session_key: The agent's session key string (may contain
            ``:feishu:group:oc_xxx:...``).

    Returns:
        The ``oc_*`` chat ID, or ``None`` if the session is not a Feishu
        group chat.
    """
    if not session_key:
        return None
    m = _SESSION_GROUP_RE.search(session_key)
    return m.group(1) if m else None


def split_bots_by_group(
    bot_registry: Dict[str, Dict[str, Any]],
    current_agent_id: str,
    group_bot_open_ids: Optional[Set[str]] = None,
) -> tuple[List[tuple[str, Dict[str, Any]]], List[tuple[str, Dict[str, Any]]]]:
    """Split bots into in-group and not-in-group.

    Ported from ``index.js`` lines 316-326::

        const allOtherBots = Object.entries(botRegistry)
            .filter(([agentId]) => agentId !== currentAgentId);
        ...
        inGroupBots = allOtherBots.filter(...);
        notInGroupBots = allOtherBots.filter(...);

    Args:
        bot_registry: Dict mapping agentId → ``{botOpenId, botName, ...}``.
        current_agent_id: The agentId of the current bot (excluded from
            both lists).
        group_bot_open_ids: Optional set of bot open IDs that are members
            of the current group chat.  If ``None`` or empty, all other bots
            are considered in-group (no filtering).

    Returns:
        A 2-tuple ``(in_group, not_in_group)``, each a list of
        ``(agent_id, bot_info)`` tuples.
    """
    all_other_bots = [
        (agent_id, bot)
        for agent_id, bot in bot_registry.items()
        if agent_id != current_agent_id
    ]

    if not group_bot_open_ids:
        # No group membership info available — treat all as in-group
        return all_other_bots, []

    in_group: List[tuple[str, Dict[str, Any]]] = []
    not_in_group: List[tuple[str, Dict[str, Any]]] = []

    for agent_id, bot in all_other_bots:
        bot_open_id = bot.get("botOpenId", "")
        if bot_open_id in group_bot_open_ids:
            in_group.append((agent_id, bot))
        else:
            not_in_group.append((agent_id, bot))

    return in_group, not_in_group


def format_bot_list(
    bots: List[tuple[str, Dict[str, Any]]],
) -> str:
    """Format a list of bots as markdown bullet points with Feishu ``<at>`` tags.

    Ported from ``index.js`` lines 330-336::

        const botList = inGroupBots
            .map(([, bot]) => {
                const desc = bot.description ? ` — ${bot.description}` : '';
                const atTag = `<at user_id=\"${bot.botOpenId}\">${bot.botName}</at>`;
                return `- ${atTag}${desc}`;
            })
            .join('\\n');

    Args:
        bots: List of ``(agent_id, bot_info)`` tuples.

    Returns:
        A newline-separated markdown bullet list, e.g.::

            - <at user_id="ou_abc123">BotA</at> — Frontend developer
            - <at user_id="ou_def456">BotB</at>
    """
    lines: List[str] = []
    for _agent_id, bot in bots:
        bot_name = bot.get("botName", "?")
        bot_open_id = bot.get("botOpenId", "")
        desc = bot.get("description", "")

        at_tag = f'<at user_id="{bot_open_id}">{bot_name}</at>'
        desc_suffix = f" — {desc}" if desc else ""
        lines.append(f"- {at_tag}{desc_suffix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

# The A2A collaboration instruction template (port of the JS template literal
# at index.js lines 352-389).
#
_COLLABORATION_TEMPLATE = """\
[A2A — 群内协作规则]

默认行为：
- 正常情况下不要主动 @ 其他机器人
- 每次回复最多 @ 1 个机器人

重要：区分"提到"和"请求"
- 如果你只是在回复中提到某个机器人，直接用它的名字，不要用 <at> 标签
- 只有当你确实需要对方执行任务、回答问题时，才使用 <at> 标签

触发协作：
- 当用户提到"群内协作"、"分配任务"、"协作完成"等关键字时，可以根据任务需要主动 @ 合适的机器人
- 当用户明确要求你联系某个机器人时，也可以 @

@ 的两种类型：

1. 任务型 @（需要对方完成任务并回传结果）：
   - 直接在回复中用 <at> 标签 @ 对方，说明任务内容
   - 对方完成后应该 @ 回你汇报结果
   - 你收到结果后，整理结果回复用户，不要再 @ 回对方

2. 通知型 @（只是告知信息，不需要对方回复）：
   - 在消息中加上 🔕仅通知 标记
   - 示例：「🔕仅通知 <at ...>xxx</at> 排期已确认，按原计划推进即可」
   - 对方收到后不需要 @ 回你

回复规则：
- 当其他机器人 @ 你并请求你执行任务时，处理完后在回复末尾 @ 回发起者汇报结果
- 如果对方只是通知你信息（消息中包含🔕仅通知），不需要 @ 回对方
- 如果对方是把结果回传给你，不要 @ 回对方，直接整理结果回复用户
- 如果你是被用户直接 @ 的，不需要 @ 任何机器人（除非用户要求或触发了协作关键字）

⚠️ @ 格式要求（非常重要）：
- 必须使用 <at user_id="ou_xxxx">名字</at> 格式
- 禁止使用 @名字 这种明文写法，明文写法不会触发飞书的 @ 投递
- 示例：<at user_id="ou_abc123">mac-前端</at> 请帮忙实现这个页面

{bots_section}{missing_bots_note}{permission_note}"""


def build_collaboration_context(
    *,
    channel_id: str,
    current_agent_id: str,
    session_key: str,
    bot_registry: Dict[str, Dict[str, Any]],
    native_a2a_chats: Set[str],
    group_bot_open_ids: Optional[Set[str]] = None,
) -> Optional[str]:
    """Build the A2A collaboration instruction string for system prompt injection.

    This is the Python port of the ``before_prompt_build`` hook
    (index.js lines 294-393).  It produces an ``appendSystemContext``
    value describing available bots and collaboration rules.

    Args:
        channel_id: The channel type (e.g. ``\"feishu\"``).  If this is
            not ``\"feishu\"`` the function returns ``None`` immediately.
        current_agent_id: The agent ID of the bot whose prompt is being
            built.
        session_key: The session key (used to extract chat ID for group
            membership filtering).
        bot_registry: Dict mapping agentId → ``{botOpenId, botName, ...}``.
            Each entry may optionally include a ``description`` field.
        native_a2a_chats: Set of chat IDs where native bot-to-bot delivery
            has been confirmed.
        group_bot_open_ids: Optional set of bot open IDs that are members
            of the current group chat.  When provided, only bots in this
            set are listed as available; others are noted as missing.
            When ``None`` (or lookup failed), all known bots are listed
            without filtering.

    Returns:
        A string suitable for use as ``appendSystemContext`` in a Hermes
        or OpenClaw prompt build, or ``None`` if no injection is needed
        (non-Feishu channel or zero other bots in registry).
    """
    # Guard: only inject for Feishu channel
    if channel_id != "feishu":
        return None

    # Extract chatId from sessionKey
    chat_id = extract_chat_id_from_session(session_key)

    # Split bots into in-group / not-in-group
    in_group, not_in_group = split_bots_by_group(
        bot_registry, current_agent_id, group_bot_open_ids,
    )

    # If there are no other bots at all, nothing to inject
    if not in_group and not not_in_group:
        return None

    # Format bot list (only in-group bots shown as available)
    bots_section = (
        f"本群中可用的机器人（仅供参考，不要主动 @ 他们）：\n{format_bot_list(in_group)}"
        if in_group
        else "本群中暂无其他可协作的机器人。"
    )

    # Note about bots not in this group
    missing_bots_note = ""
    if not_in_group:
        missing_names = "、".join(
            bot.get("botName", "?") for _, bot in not_in_group
        )
        missing_bots_note = (
            f"\n\n💡 以下机器人未在本群中，如需协作请让管理员将它们拉入群聊：{missing_names}"
        )

    # Permission note — warn if native A2A delivery not yet confirmed
    permission_note = ""
    has_native_a2a = chat_id and chat_id in native_a2a_chats
    if not has_native_a2a:
        permission_note = (
            "\n\n⚠️ 注意：当前群聊尚未检测到飞书原生 bot@bot 投递能力。"
            "如果你 @ 其他机器人后对方没有响应，请提醒用户在飞书开发者后台"
            "为每个机器人应用开通「接收群聊中机器人@机器人的消息」权限"
            "（im:message.group_at_msg.include_bot:readonly）。"
            "开通后，机器人之间就可以直接通过 @ 来通信了。"
        )

    return _COLLABORATION_TEMPLATE.format(
        bots_section=bots_section,
        missing_bots_note=missing_bots_note,
        permission_note=permission_note,
    )