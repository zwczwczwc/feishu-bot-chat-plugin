"""
Feishu Open API client — aiohttp-based HTTP client.

Provides core API operations translated from the JS plugin (index.js):

  - get_tenant_token()      — obtains a tenant_access_token via auth/v3
  - get_bot_info()          — fetches bot metadata (open_id, name)
  - get_group_bot_open_ids()— paginated group membership for bot filtering

All functions accept an optional aiohttp.ClientSession; if omitted they
create a short-lived one automatically.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------

FEISHU_BASE = "https://open.feishu.cn"
LARK_BASE = "https://open.larksuite.com"

FEISHU_DOMAIN_MAP = {"feishu": FEISHU_BASE, "lark": LARK_BASE}


def _base_url(domain: str) -> str:
    """Map Feishu/Lark domain names to their API base URLs."""
    return FEISHU_DOMAIN_MAP.get(domain.lower(), FEISHU_BASE)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class BotInfo:
    """Bot metadata returned by get_bot_info()."""
    bot_open_id: str
    bot_name: str


@dataclass
class GroupMemberCacheEntry:
    """Cached group membership data."""
    bot_open_ids: Set[str]
    fetched_at: float  # time.time()


# ---------------------------------------------------------------------------
# API functions
# ---------------------------------------------------------------------------


async def get_tenant_token(
    app_id: str,
    app_secret: str,
    domain: str = "feishu",
    session: Optional[aiohttp.ClientSession] = None,
) -> str:
    """
    Obtain a tenant_access_token from Feishu Open API.

    Corresponds to JS ``getTenantToken()`` (index.js lines 55-66).
    POST to ``/open-apis/auth/v3/tenant_access_token/internal`` with
    ``app_id`` and ``app_secret``.

    Returns the raw ``tenant_access_token`` string.
    Raises ``ValueError`` on API error (code != 0).
    """
    base = _base_url(domain)
    url = f"{base}/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": app_id, "app_secret": app_secret}

    async def _do(s: aiohttp.ClientSession) -> str:
        async with s.post(url, json=payload) as resp:
            data = await resp.json()
        if data.get("code") != 0:
            raise ValueError(
                f"tenant_token failed: {data.get('msg', 'unknown error')}"
            )
        return data["tenant_access_token"]

    if session:
        return await _do(session)
    async with aiohttp.ClientSession() as s:
        return await _do(s)


async def get_bot_info(
    token: str,
    domain: str = "feishu",
    session: Optional[aiohttp.ClientSession] = None,
) -> BotInfo:
    """
    Fetch bot metadata (open_id, name) from Feishu Open API.

    Corresponds to JS ``getBotInfo()`` (index.js lines 68-78).
    GET to ``/open-apis/bot/v3/info`` with Bearer token.

    Returns a ``BotInfo`` dataclass.
    Raises ``ValueError`` on API error.
    """
    base = _base_url(domain)
    url = f"{base}/open-apis/bot/v3/info"
    headers = {"Authorization": f"Bearer {token}"}

    async def _do(s: aiohttp.ClientSession) -> BotInfo:
        async with s.get(url, headers=headers) as resp:
            data = await resp.json()
        if data.get("code") != 0:
            raise ValueError(
                f"bot/v3/info failed: {data.get('msg', 'unknown error')}"
            )
        bot = data.get("bot") or {}
        return BotInfo(
            bot_open_id=bot.get("open_id", ""),
            bot_name=bot.get("app_name") or bot.get("bot_name", ""),
        )

    if session:
        return await _do(session)
    async with aiohttp.ClientSession() as s:
        return await _do(s)


async def get_group_bot_open_ids(
    chat_id: str,
    feishu_accounts: Dict[str, dict],
    feishu_domain: str = "feishu",
    bot_open_id_set: Optional[Set[str]] = None,
    group_member_cache: Optional[Dict[str, GroupMemberCacheEntry]] = None,
    cache_ttl: float = 600.0,
    log: Optional[logging.Logger] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[Set[str]]:
    """
    Get the set of bot open_ids that are members of a given group chat.

    Corresponds to JS ``getGroupBotOpenIds()`` (index.js lines 196-248).

    * Iterates through ``feishu_accounts`` to find a valid token.
    * Paginates through the chat member list (up to 10 pages, 100/page).
    * Filters for ``member_type == 'bot'``.
    * Caches results per ``chat_id`` with ``cache_ttl`` seconds (default 10 min).
    * If the result suspiciously undershoots the known bot count
      (``bot_open_id_set`` has >1 entries but only ≤1 bots were found),
      returns ``None`` and warns about a likely permission issue.

    Parameters
    ----------
    chat_id:
        Feishu chat (conversation) ID to query.
    feishu_accounts:
        ``{account_id: {app_id, app_secret}}`` dict — first valid account
        is used to obtain a token.
    feishu_domain:
        ``"feishu"`` or ``"lark"``.
    bot_open_id_set:
        Known bot open_ids (for suspicious-few detection).
    group_member_cache:
        Mutable dict (in-memory cache). If omitted, caching is skipped.
    cache_ttl:
        Cache TTL in seconds. Defaults to 600 (10 minutes, matching JS).
    log:
        Optional logger for warnings/debug.
    session:
        Optional shared aiohttp session.

    Returns a ``Set[str]`` of bot open_ids, or ``None`` if:
    * no valid token could be obtained, or
    * the API returned an error, or
    * the result is suspiciously few bots (likely permission issue).
    """
    log = log or logger

    # --- Cache check ---
    if group_member_cache is not None:
        entry = group_member_cache.get(chat_id)
        if entry and (asyncio.get_event_loop().time() - entry.fetched_at) < cache_ttl:
            return entry.bot_open_ids

    # --- Get a token from any valid account ---
    token: Optional[str] = None
    for acct in feishu_accounts.values():
        if acct.get("appId") and acct.get("appSecret"):
            try:
                token = await get_tenant_token(
                    acct["appId"], acct["appSecret"], feishu_domain, session=session
                )
                break
            except Exception:
                continue
    if not token:
        return None

    base = _base_url(feishu_domain)
    member_open_ids: Set[str] = set()
    page_token: str = ""
    headers = {"Authorization": f"Bearer {token}"}

    async def _do(s: aiohttp.ClientSession) -> Optional[Set[str]]:
        nonlocal page_token, member_open_ids, headers

        for _ in range(10):  # max 10 pages
            params: Dict[str, str] = {
                "member_id_type": "open_id",
                "page_size": "100",
            }
            if page_token:
                params["page_token"] = page_token

            url = f"{base}/open-apis/im/v1/chats/{chat_id}/members"
            async with s.get(url, headers=headers, params=params) as resp:
                data = await resp.json()

            if data.get("code") != 0:
                log.debug(
                    "[get_group_bot_open_ids] API error for "
                    f"chat={chat_id}: {data.get('msg')}"
                )
                return None

            items = (data.get("data") or {}).get("items") or []
            for m in items:
                if m.get("member_type") == "bot" and m.get("member_id"):
                    member_open_ids.add(m["member_id"])

            has_more = (data.get("data") or {}).get("has_more")
            if not has_more:
                break
            page_token = (data.get("data") or {}).get("page_token") or ""

        # --- Suspiciously few bots check ---
        known_bot_count = len(bot_open_id_set) if bot_open_id_set else 0
        found_count = len(member_open_ids)

        if known_bot_count > 1 and found_count <= 1:
            log.warning(
                f"[feishu-bot-chat] Group member API returned suspiciously "
                f"few bots ({found_count}/{known_bot_count}) for "
                f"chat={chat_id} — token may lack "
                f"im:chat or im:chat.member:readonly permission"
            )
            return None

        # --- Update cache ---
        if group_member_cache is not None:
            group_member_cache[chat_id] = GroupMemberCacheEntry(
                bot_open_ids=member_open_ids,
                fetched_at=asyncio.get_event_loop().time(),
            )

        log.debug(
            f"[get_group_bot_open_ids] chat={chat_id} has "
            f"{len(member_open_ids)} bots: {', '.join(sorted(member_open_ids))}"
        )
        return member_open_ids

    if session:
        return await _do(session)
    async with aiohttp.ClientSession() as s:
        return await _do(s)


# ---------------------------------------------------------------------------
# Convenience: shared session context manager
# ---------------------------------------------------------------------------


class FeishuAPIClient:
    """Wraps the three API functions with a reusable aiohttp session."""

    def __init__(
        self,
        feishu_accounts: Dict[str, dict],
        domain: str = "feishu",
        log: Optional[logging.Logger] = None,
    ):
        self.feishu_accounts = feishu_accounts
        self.domain = domain
        self.log = log or logger
        self._session: Optional[aiohttp.ClientSession] = None
        self.group_member_cache: Dict[str, GroupMemberCacheEntry] = {}

    async def __aenter__(self) -> "FeishuAPIClient":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def get_tenant_token(self, app_id: str, app_secret: str) -> str:
        return await get_tenant_token(
            app_id, app_secret, self.domain, session=self._session
        )

    async def get_bot_info(self, token: str) -> BotInfo:
        return await get_bot_info(token, self.domain, session=self._session)

    async def get_group_bot_open_ids(
        self,
        chat_id: str,
        bot_open_id_set: Optional[Set[str]] = None,
    ) -> Optional[Set[str]]:
        return await get_group_bot_open_ids(
            chat_id=chat_id,
            feishu_accounts=self.feishu_accounts,
            feishu_domain=self.domain,
            bot_open_id_set=bot_open_id_set,
            group_member_cache=self.group_member_cache,
            log=self.log,
            session=self._session,
        )