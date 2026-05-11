"""Bot registry — discovers and tracks Feishu bots for A2A collaboration.

Ported from JS index.js (discoverBots, buildLookups, getGroupBotOpenIds).

Auto-discovers bots from Hermes/OpenClaw config, calls Feishu API for metadata,
and maintains in-memory registry with reverse lookup maps.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .cache import Cache
from .feishu_api import get_bot_info_sync, get_group_bot_open_ids, get_tenant_token_sync


@dataclass
class BotInfo:
    """Information about a discovered Feishu bot."""
    account_id: str
    bot_open_id: str
    bot_name: str
    description: str = ""


@dataclass
class GroupMemberCache:
    """Cached group member info with TTL."""
    bot_open_ids: Set[str]
    fetched_at: float


GROUP_MEMBER_CACHE_TTL = 10 * 60  # 10 minutes


class BotRegistry:
    """Registry of discovered Feishu bots with reverse lookup support."""

    def __init__(self, cache: Optional[Cache] = None):
        self.cache = cache or Cache()

        # Primary map: agent_id -> BotInfo
        self._bots: Dict[str, BotInfo] = {}

        # Reverse lookup maps
        self._bot_open_id_set: Set[str] = set()
        self._bot_open_id_to_agent: Dict[str, BotInfo] = {}
        self._agent_id_set: Set[str] = set()

        # Group member cache: chat_id -> GroupMemberCache
        self._group_member_cache: Dict[str, GroupMemberCache] = {}

        # Native A2A delivery detection
        self.native_a2a_chats: Set[str] = set()

    @property
    def bots(self) -> Dict[str, BotInfo]:
        return dict(self._bots)

    @property
    def bot_open_ids(self) -> Set[str]:
        return set(self._bot_open_id_set)

    # ---- Registry management ----

    def build_lookups(self, registry: Dict[str, BotInfo]) -> None:
        """Rebuild reverse lookup maps from a registry dict."""
        self._bots = dict(registry)
        self._bot_open_id_set.clear()
        self._bot_open_id_to_agent.clear()
        self._agent_id_set.clear()

        for agent_id, bot in registry.items():
            self._bot_open_id_set.add(bot.bot_open_id)
            self._bot_open_id_to_agent[bot.bot_open_id] = bot
            self._agent_id_set.add(agent_id)

    def get_agent_by_bot_open_id(self, bot_open_id: str) -> Optional[BotInfo]:
        """Get bot info by its open_id."""
        return self._bot_open_id_to_agent.get(bot_open_id)

    def is_known_bot(self, bot_open_id: str) -> bool:
        """Check if a user is a known Feishu bot."""
        return bot_open_id in self._bot_open_id_set

    def get_other_bots(self, agent_id: str) -> List[BotInfo]:
        """Get all bots except the given agent_id."""
        return [b for a, b in self._bots.items() if a != agent_id]

    # ---- Discovery ----

    def discover_from_hermes_config(self) -> Dict[str, BotInfo]:
        """Discover bots from Hermes Agent config."""
        config_path = os.path.expanduser("~/.hermes/config.yaml")
        if not os.path.exists(config_path):
            return {}

        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except ImportError:
            # Fallback: read as raw text and do basic parsing
            return self._discover_from_env()
        except Exception:
            return {}

        bots = {}

        # Try to find Feishu platform config
        gateway = config.get("gateway", {})
        platforms = gateway.get("platforms", {})
        feishu_config = platforms.get("feishu", {})

        accounts = feishu_config.get("accounts", {})
        domain = feishu_config.get("domain", "feishu")

        # If no accounts section, try env vars
        if not accounts:
            return self._discover_from_env()

        # Try to discover bots from configured accounts
        cached = self.cache.get("registry")
        if cached and isinstance(cached, dict):
            if all(aid in cached for aid in accounts):
                bots = {aid: BotInfo(**b) if isinstance(b, dict) else b
                        for aid, b in cached.items()}
                self.build_lookups(bots)
                return bots

        token_cache = {}
        for account_id, acct in accounts.items():
            app_id = acct.get("app_id", "")
            app_secret = acct.get("app_secret", "")
            if not app_id or not app_secret:
                continue
            try:
                # Check token cache
                token = token_cache.get(account_id)
                if not token:
                    token = get_tenant_token_sync(app_id, app_secret, domain)
                    token_cache[account_id] = token

                info = get_bot_info_sync(token, domain)
                bots[account_id] = BotInfo(
                    account_id=account_id,
                    bot_open_id=info["bot_open_id"],
                    bot_name=info["bot_name"],
                )
            except Exception:
                # Fall back to cached data
                if cached and account_id in cached:
                    bots[account_id] = cached[account_id]

        if bots:
            self.cache.set("registry", {
                aid: {"account_id": b.account_id, "bot_open_id": b.bot_open_id,
                      "bot_name": b.bot_name}
                for aid, b in bots.items()
            })

        self.build_lookups(bots)
        return bots

    # Known companion registry paths to scan for additional bots.
    COMPANION_REGISTRIES = [
        os.path.expanduser("~/.openclaw/fbc-registry/registry.json"),
    ]

    def _discover_from_env(self) -> Dict[str, BotInfo]:
        """Discover own bot from env vars, then merge companions."""
        app_id = os.environ.get("FEISHU_APP_ID", "")
        app_secret = os.environ.get("FEISHU_APP_SECRET", "")
        domain = os.environ.get("FEISHU_DOMAIN", "feishu")

        bots: Dict[str, BotInfo] = {}

        # 1. Discover self from env
        if app_id and app_secret:
            try:
                token = get_tenant_token_sync(app_id, app_secret, domain)
                info = get_bot_info_sync(token, domain)
                bots["default"] = BotInfo(
                    account_id="default",
                    bot_open_id=info["bot_open_id"],
                    bot_name=info["bot_name"],
                )
            except Exception:
                pass

        # 2. Scan companion registries for additional bots
        self._merge_companion_registries(bots)

        if bots:
            self.build_lookups(bots)
        return bots

    def _merge_companion_registries(self, bots: Dict[str, BotInfo]) -> None:
        """Merge bots from companion registry files (e.g. OpenClaw's fbc-registry).

        Companion bots are merged into ``bots`` in-place, keyed by their
        ``accountId``.  Duplicate ``bot_open_id`` values are skipped so each
        physical bot appears only once.
        """
        known_ids = {b.bot_open_id for b in bots.values()}

        for reg_path in self.COMPANION_REGISTRIES:
            if not os.path.isfile(reg_path):
                continue
            try:
                import json
                with open(reg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                companion_bots = data.get("bots") or data.get("registry", {})
                if isinstance(companion_bots, dict):
                    for agent_id, info in companion_bots.items():
                        if isinstance(info, dict):
                            bot_open_id = info.get("botOpenId") or info.get("bot_open_id", "")
                            if not bot_open_id or bot_open_id in known_ids:
                                continue
                            bot_name = info.get("botName") or info.get("bot_name", "")
                            account_id = info.get("accountId") or info.get("account_id", agent_id)
                            bots[agent_id] = BotInfo(
                                account_id=account_id,
                                bot_open_id=bot_open_id,
                                bot_name=bot_name,
                            )
                            known_ids.add(bot_open_id)
            except Exception:
                continue

    # ---- Group member management ----

    async def get_group_bot_open_ids(self, chat_id: str, domain: str = "feishu") -> Optional[Set[str]]:
        """Get bot open_ids in a group chat, with caching."""
        cached = self._group_member_cache.get(chat_id)
        if cached and (time.time() - cached.fetched_at < GROUP_MEMBER_CACHE_TTL):
            return cached.bot_open_ids

        try:
            result = await get_group_bot_open_ids(chat_id, domain)
            if result is None:
                return None

            # Sanity check: if we know many bots but only found 1, likely permission issue
            known_count = len(self._bot_open_id_set)
            found_count = len(result)
            if known_count > 1 and found_count <= 1:
                return None

            self._group_member_cache[chat_id] = GroupMemberCache(
                bot_open_ids=result,
                fetched_at=time.time(),
            )
            return result
        except Exception:
            return None

    # ---- Native A2A detection ----

    def mark_native_a2a(self, chat_id: str) -> None:
        """Mark a chat as having confirmed native bot-to-bot delivery."""
        self.native_a2a_chats.add(chat_id)

    def has_native_a2a(self, chat_id: str) -> bool:
        """Check if a chat has confirmed native bot-to-bot delivery."""
        return chat_id in self.native_a2a_chats