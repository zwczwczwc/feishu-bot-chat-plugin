"""Integration tests for the Feishu A2A Collaboration Plugin.

Tests core engine modules using mock data (no real Feishu API calls).
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.bot_registry import BotInfo, BotRegistry
from core.cache import Cache
from core.mention_processor import (
    outgoing_convert,
    inbound_convert,
    inbound_extract_bot_open_ids,
    is_bot_mentioned,
)
from core.message_filter import FilterResult, MessageFilter
from core.collaboration_rules import (
    build_collaboration_context,
    extract_chat_id_from_session,
    format_bot_list,
    split_bots_by_group,
)


# ==============================================================================
# Cache Tests
# ==============================================================================

class TestCache:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = Cache(cache_dir=self.tmpdir)

    def test_set_and_get(self):
        self.cache.set("test_key", {"hello": "world"}, ttl=3600)
        result = self.cache.get("test_key", ttl=3600)
        assert result == {"hello": "world"}

    def test_expired(self):
        self.cache.set("expired_key", "data", ttl=0)
        result = self.cache.get("expired_key", ttl=0)
        assert result is None

    def test_missing_key(self):
        result = self.cache.get("nonexistent")
        assert result is None

    def test_invalidate(self):
        self.cache.set("temp", "data", ttl=3600)
        self.cache.invalidate("temp")
        assert self.cache.get("temp") is None

    def test_clear(self):
        self.cache.set("a", 1, ttl=3600)
        self.cache.set("b", 2, ttl=3600)
        self.cache.clear()
        assert self.cache.get("a") is None
        assert self.cache.get("b") is None


# ==============================================================================
# BotRegistry Tests
# ==============================================================================

class TestBotRegistry:
    def setup_method(self):
        self.registry = BotRegistry()
        self.bots = {
            "bot-a": BotInfo(
                account_id="acc-1",
                bot_open_id="ou_bot_a",
                bot_name="测试BotA",
            ),
            "bot-b": BotInfo(
                account_id="acc-2",
                bot_open_id="ou_bot_b",
                bot_name="测试BotB",
            ),
        }
        self.registry.build_lookups(self.bots)

    def test_build_lookups(self):
        assert self.registry.is_known_bot("ou_bot_a")
        assert self.registry.is_known_bot("ou_bot_b")
        assert not self.registry.is_known_bot("ou_unknown")

    def test_get_agent_by_bot_open_id(self):
        bot = self.registry.get_agent_by_bot_open_id("ou_bot_a")
        assert bot is not None
        assert bot.bot_name == "测试BotA"

    def test_get_other_bots(self):
        others = self.registry.get_other_bots("bot-a")
        assert len(others) == 1
        assert others[0].bot_name == "测试BotB"

    def test_native_a2a_tracking(self):
        assert not self.registry.has_native_a2a("chat_123")
        self.registry.mark_native_a2a("chat_123")
        assert self.registry.has_native_a2a("chat_123")


# ==============================================================================
# MentionProcessor Tests
# ==============================================================================

class TestMentionProcessor:
    def setup_method(self):
        self.bot_dict = {
            "bot-a": {
                "botOpenId": "ou_bot_a",
                "botName": "BotA",
                "accountId": "acc-1",
            },
            "bot-b": {
                "botOpenId": "ou_bot_b",
                "botName": "BotB-前端",
                "accountId": "acc-2",
            },
        }

    def test_basic_conversion(self):
        text = "请 @BotA 帮忙检查代码"
        result = outgoing_convert(text, self.bot_dict)
        assert '<at user_id="ou_bot_a">BotA</at>' in result
        assert "@BotA" not in result

    def test_bot_with_hyphen(self):
        text = "请 @BotB-前端 帮忙"
        result = outgoing_convert(text, self.bot_dict)
        assert '<at user_id="ou_bot_b">BotB-前端</at>' in result

    def test_no_mention(self):
        text = "你好，今天天气不错"
        result = outgoing_convert(text, self.bot_dict)
        assert result == text

    def test_inbound_extract(self):
        text = '你好 <at user_id="ou_bot_a">BotA</at> 请帮忙'
        mentions = inbound_convert(text)
        assert len(mentions) == 1
        assert mentions[0]["user_id"] == "ou_bot_a"
        assert mentions[0]["name"] == "BotA"


# ==============================================================================
# MessageFilter Tests
# ==============================================================================

class TestMessageFilter:
    def setup_method(self):
        self.native_a2a_chats = set()
        self.bot_set = {"ou_bot_a"}
        self.bot_map = {
            "ou_bot_a": {
                "agentId": "bot-a",
                "botOpenId": "ou_bot_a",
                "botName": "BotA",
            },
        }
        self.filter_inst = MessageFilter(
            bot_open_id_set=self.bot_set,
            bot_open_id_to_agent_map=self.bot_map,
            native_a2a_chats=self.native_a2a_chats,
        )

    def test_skip_if_bot_not_mentioned(self):
        """Bot message without @mention should be swallowed."""
        result = self.filter_inst.handle_inbound(
            sender_id="ou_bot_a",
            mentioned_ids=[],
            text="Hello from bot",
            chat_id="oc_test",
        )
        assert result.action == "skip"

    def test_pass_human_message(self):
        """Human message should pass through."""
        result = self.filter_inst.handle_inbound(
            sender_id="ou_human",
            mentioned_ids=[],
            text="Hello",
            chat_id="oc_test",
        )
        assert result.action == "pass"

    def test_detect_native_a2a(self):
        """Bot message with @mention confirms native delivery."""
        result = self.filter_inst.handle_inbound(
            sender_id="ou_bot_a",
            mentioned_ids=["ou_bot_a"],
            text="Hello from BotA",
            chat_id="oc_test",
        )
        assert result.action == "rewrite"
        assert "BotA" in result.text
        assert "oc_test" in self.native_a2a_chats


# ==============================================================================
# CollaborationRules Tests
# ==============================================================================

class TestCollaborationRules:
    def setup_method(self):
        self.registry = {}
        self.bot_a = {
            "bot-a": {
                "botOpenId": "ou_bot_a",
                "botName": "BotA",
                "accountId": "acc-1",
            },
            "bot-b": {
                "botOpenId": "ou_bot_b",
                "botName": "BotB",
                "accountId": "acc-2",
            },
        }
        self.registry.update(self.bot_a)

    def test_build_context_with_other_bots(self):
        context = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="bot-a",
            session_key="test:feishu:group:oc_test",
            bot_registry=self.registry,
            native_a2a_chats=set(),
        )
        assert context is not None
        assert "BotB" in context
        assert '<at user_id="ou_bot_b">' in context
        # Should not include self
        assert "BotA" not in context or context.count("BotA") == 0

    def test_build_context_no_other_bots(self):
        single = {"only-bot": {"botOpenId": "ou_only", "botName": "OnlyBot"}}
        context = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="only-bot",
            session_key="test:feishu:group:oc_test",
            bot_registry=single,
            native_a2a_chats=set(),
        )
        assert context is None or "暂" in context

    def test_native_a2a_note_in_context(self):
        context = build_collaboration_context(
            channel_id="feishu",
            current_agent_id="bot-a",
            session_key="test:feishu:group:oc_confirmed",
            bot_registry=self.registry,
            native_a2a_chats={"oc_confirmed"},
        )
        assert context is not None