"""Tests for core/collaboration_rules.py."""

from __future__ import annotations

from core.collaboration_rules import (
    build_collaboration_context,
    extract_chat_id_from_session,
    format_bot_list,
    split_bots_by_group,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_REGISTRY = {
    "backend": {
        "botOpenId": "ou_aaa111",
        "botName": "backend-bot",
        "description": "Handles data & APIs",
    },
    "frontend": {
        "botOpenId": "ou_bbb222",
        "botName": "frontend-bot",
        "description": "UI & frontend",
    },
    "qa": {
        "botOpenId": "ou_ccc333",
        "botName": "qa-bot",
        "description": "",
    },
}


# ---------------------------------------------------------------------------
# extract_chat_id_from_session
# ---------------------------------------------------------------------------


class TestExtractChatIdFromSession:
    def test_extracts_from_full_session_key(self):
        assert (
            extract_chat_id_from_session("agent:feishu:group:oc_abc123:extra")
            == "oc_abc123"
        )

    def test_returns_none_for_non_group_session(self):
        assert extract_chat_id_from_session("agent:feishu:p2p:ou_xxx") is None

    def test_returns_none_for_empty_string(self):
        assert extract_chat_id_from_session("") is None

    def test_returns_none_for_none(self):
        assert extract_chat_id_from_session(None) is None

    def test_matches_complex_chat_id(self):
        assert (
            extract_chat_id_from_session(
                "myagent:feishu:group:oc_12345_6789:thread"
            )
            == "oc_12345_6789"
        )

    def test_oc_id_with_underscores(self):
        assert (
            extract_chat_id_from_session(
                "agent:feishu:group:oc_a_b_c"
            )
            == "oc_a_b_c"
        )


# ---------------------------------------------------------------------------
# split_bots_by_group
# ---------------------------------------------------------------------------


class TestSplitBotsByGroup:
    def test_returns_all_other_bots_when_no_group_info(self):
        in_group, not_in_group = split_bots_by_group(
            SAMPLE_REGISTRY, "backend", group_bot_open_ids=None,
        )
        agent_ids = {aid for aid, _ in in_group}
        assert agent_ids == {"frontend", "qa"}
        assert not_in_group == []

    def test_splits_bots_correctly(self):
        in_group, not_in_group = split_bots_by_group(
            SAMPLE_REGISTRY,
            "backend",
            group_bot_open_ids={"ou_bbb222"},  # only frontend in group
        )
        assert [aid for aid, _ in in_group] == ["frontend"]
        assert [aid for aid, _ in not_in_group] == ["qa"]

    def test_excludes_current_agent(self):
        in_group, not_in_group = split_bots_by_group(
            SAMPLE_REGISTRY,
            "backend",
            group_bot_open_ids={"ou_aaa111", "ou_bbb222", "ou_ccc333"},
        )
        # backend (ou_aaa111) should be excluded
        agent_ids = {aid for aid, _ in in_group}
        assert agent_ids == {"frontend", "qa"}

    def test_returns_empty_when_only_current_agent_in_registry(self):
        in_group, not_in_group = split_bots_by_group(
            {"current": {"botOpenId": "ou_xxx", "botName": "me"}},
            "current",
        )
        assert in_group == []
        assert not_in_group == []

    def test_empty_group_set_means_all_are_in_group(self):
        in_group, not_in_group = split_bots_by_group(
            SAMPLE_REGISTRY, "backend", group_bot_open_ids=set(),
        )
        assert len(in_group) == 2
        assert not_in_group == []


# ---------------------------------------------------------------------------
# format_bot_list
# ---------------------------------------------------------------------------


class TestFormatBotList:
    def test_formats_bots_with_description(self):
        bots = [("backend", SAMPLE_REGISTRY["backend"])]
        result = format_bot_list(bots)
        assert (
            '<at user_id="ou_aaa111">backend-bot</at>' in result
        )
        assert "Handles data & APIs" in result
        assert result.startswith("- ")

    def test_formats_bot_without_description(self):
        bots = [("qa", SAMPLE_REGISTRY["qa"])]
        result = format_bot_list(bots)
        assert '<at user_id="ou_ccc333">qa-bot</at>' in result
        assert " — " not in result

    def test_multiple_bots_newline_separated(self):
        bots = [
            ("backend", SAMPLE_REGISTRY["backend"]),
            ("frontend", SAMPLE_REGISTRY["frontend"]),
        ]
        result = format_bot_list(bots)
        lines = result.split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("- ")
        assert lines[1].startswith("- ")

    def test_returns_empty_string_for_empty_list(self):
        assert format_bot_list([]) == ""


# ---------------------------------------------------------------------------
# build_collaboration_context
# ---------------------------------------------------------------------------


class TestBuildCollaborationContext:
    SESSION_KEY = "agent:feishu:group:oc_group01:thread"

    def test_returns_none_for_non_feishu_channel(self):
        result = build_collaboration_context(
            channel_id="telegram",
            current_agent_id="backend",
            session_key=self.SESSION_KEY,
            bot_registry=SAMPLE_REGISTRY,
            native_a2a_chats=set(),
        )
        assert result is None

    def test_returns_none_when_no_other_bots(self):
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="solo",
            session_key=self.SESSION_KEY,
            bot_registry={"solo": {"botOpenId": "ou_xxx", "botName": "solo"}},
            native_a2a_chats=set(),
        )
        assert result is None

    def test_includes_bot_list_in_context(self):
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="backend",
            session_key=self.SESSION_KEY,
            bot_registry=SAMPLE_REGISTRY,
            native_a2a_chats=set(),
        )
        assert result is not None
        assert "本群中可用的机器人" in result
        assert '<at user_id="ou_bbb222">frontend-bot</at>' in result
        assert '<at user_id="ou_ccc333">qa-bot</at>' in result

    def test_filters_to_group_members(self):
        """Only bots in group_bot_open_ids are listed as available."""
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="backend",
            session_key=self.SESSION_KEY,
            bot_registry=SAMPLE_REGISTRY,
            native_a2a_chats=set(),
            group_bot_open_ids={"ou_bbb222"},  # only frontend
        )
        assert result is not None
        assert '<at user_id="ou_bbb222">frontend-bot</at>' in result
        assert '<at user_id="ou_ccc333">qa-bot</at>' not in result

    def test_shows_missing_bots_note(self):
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="backend",
            session_key=self.SESSION_KEY,
            bot_registry=SAMPLE_REGISTRY,
            native_a2a_chats=set(),
            group_bot_open_ids={"ou_bbb222"},
        )
        assert result is not None
        assert "以下机器人未在本群中" in result
        assert "qa-bot" in result

    def test_no_missing_bots_note_when_all_are_in_group(self):
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="backend",
            session_key=self.SESSION_KEY,
            bot_registry=SAMPLE_REGISTRY,
            native_a2a_chats=set(),
            group_bot_open_ids={"ou_aaa111", "ou_bbb222", "ou_ccc333"},
        )
        assert result is not None
        assert "以下机器人未在本群中" not in result

    def test_permission_note_when_no_native_a2a(self):
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="backend",
            session_key=self.SESSION_KEY,
            bot_registry=SAMPLE_REGISTRY,
            native_a2a_chats=set(),  # empty → no confirmation
        )
        assert result is not None
        assert "尚未检测到飞书原生" in result

    def test_no_permission_note_when_native_a2a_confirmed(self):
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="backend",
            session_key="agent:feishu:group:oc_group01:thread",
            bot_registry=SAMPLE_REGISTRY,
            native_a2a_chats={"oc_group01"},
        )
        assert result is not None
        assert "尚未检测到飞书原生" not in result

    def test_permission_note_only_requires_chat_id_and_confirmation(self):
        """Without a chatId (no session key match), always show permission note."""
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="backend",
            session_key="agent:feishu:p2p:ou_xxx",  # no group → no chatId
            bot_registry=SAMPLE_REGISTRY,
            native_a2a_chats={"oc_group01"},
        )
        assert result is not None
        assert "尚未检测到飞书原生" in result

    def test_includes_collaboration_rules_template(self):
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="backend",
            session_key=self.SESSION_KEY,
            bot_registry=SAMPLE_REGISTRY,
            native_a2a_chats=set(),
        )
        assert result is not None
        assert "[A2A — 群内协作规则]" in result
        assert "@ 的两种类型" in result
        assert "任务型 @" in result
        assert "通知型 @" in result
        assert "@ 格式要求" in result

    def test_handles_empty_registry(self):
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="solo",
            session_key=self.SESSION_KEY,
            bot_registry={},
            native_a2a_chats=set(),
        )
        assert result is None  # no other bots → nothing to inject

    def test_includes_description_in_bot_list(self):
        result = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="qa",
            session_key=self.SESSION_KEY,
            bot_registry=SAMPLE_REGISTRY,
            native_a2a_chats=set(),
        )
        assert result is not None
        assert "Handles data & APIs" in result
        assert "UI & frontend" in result