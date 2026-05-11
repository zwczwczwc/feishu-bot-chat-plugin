# Feishu A2A Adapter Protocol

> Interface contract between the A2A Core Engine (`core/`) and Agent platforms.
> Any Agent can integrate Feishu A2A by implementing this protocol.

## Overview

The core engine is agent-agnostic. Each Agent platform (Hermes, OpenClaw, etc.)
wraps it in a thin adapter that maps platform hooks to engine methods.

```
                    ┌─────────────────────┐
                    │   Agent Platform    │
                    │  (Hermes, OpenClaw) │
                    └──────┬───┬───┬──────┘
                           │   │   │
                    ┌──────┘   │   └──────┐
                    ▼          ▼          ▼
               ┌─────────────────────────────┐
               │       Adapter Layer         │
               │  (thin bridge, < 200 LoC)   │
               └──────┬──────────┬───────────┘
                      │          │
                      ▼          ▼
               ┌─────────────────────────────┐
               │       Core Engine           │
               │   (core/ — agent-agnostic)  │
               └─────────────────────────────┘
```

## Core Engine Reference

All core engine classes and functions live under the `core/` package. Import by
name from the package root:

```python
from core import (
    BotRegistry, BotInfo,
    MessageFilter, FilterResult,
    outgoing_convert, inbound_convert, inbound_extract_bot_open_ids, is_bot_mentioned,
    build_collaboration_context, extract_chat_id_from_session, split_bots_by_group,
    format_bot_list,
    Cache,
    FeishuAPIClient,
)
```

> `__init__.py` re-exports the full public API. No deep imports needed.

---

### BotRegistry — Discovery & Lookup

**Source:** `core/bot_registry.py`

Manages bot discovery from Hermes/OpenClaw config, reverse lookup maps, group
member caching, and native A2A delivery detection.

```python
class BotRegistry:

    # --- Registry management ---
    def build_lookups(registry: Dict[str, BotInfo]) -> None
    def get_agent_by_bot_open_id(bot_open_id: str) -> Optional[BotInfo]
    def is_known_bot(bot_open_id: str) -> bool
    def get_other_bots(agent_id: str) -> List[BotInfo]

    # --- Properties ---
    @property
    def bots(self) -> Dict[str, BotInfo]           # agent_id → BotInfo
    @property
    def bot_open_ids(self) -> Set[str]             # all known bot open_id values

    # --- Discovery ---
    def discover_from_hermes_config() -> Dict[str, BotInfo]
    #   Auto-discovers bots from ~/.hermes/config.yaml or FEISHU_APP_ID env.
    #   Calls Feishu bot/v3/info for metadata, caches result.

    # --- Group membership ---
    async def get_group_bot_open_ids(chat_id: str, domain: str = "feishu")
        -> Optional[Set[str]]
    #   Paginated member enumeration (max 10 pages, 100/page), 10-min cache.
    #   Returns None on error or suspiciously-few-results.

    # --- Native A2A detection ---
    def mark_native_a2a(chat_id: str) -> None
    def has_native_a2a(chat_id: str) -> bool
```

**`BotInfo` dataclass:**
```python
@dataclass
class BotInfo:
    account_id: str
    bot_open_id: str
    bot_name: str
    description: str = ""
```

**State that adapters manage externally:** The `BotRegistry` holds mutable state
(`native_a2a_chats`, `_group_member_cache`). Adapters should construct it once
at plugin/extension startup and reuse the same instance for the lifetime of the
gateway process.

---

### MessageFilter — Inbound Message Routing

**Source:** `core/message_filter.py`

Determines what to do with each inbound Feishu message: pass through (human),
rewrite with sender injection (bot @mention), or skip (bot not mentioned).

```python
class MessageFilter:

    def __init__(
        self,
        bot_open_id_set: set[str],
        bot_open_id_to_agent_map: dict[str, dict],
        native_a2a_chats: set[str],
        logger: Optional[callable] = None,
    )

    def handle_inbound(
        self,
        sender_id: str,           # open_id of who sent the message
        mentioned_ids: list[str], # open_ids from <at> tags in the message
        text: str,                # raw message text
        chat_id: str,             # group chat ID (oc_*)
        platform: str = "feishu", # only "feishu" triggers filtering
        is_group: bool = True,    # only group chats trigger filtering
    ) -> FilterResult
```

**`FilterResult` dataclass:**
```python
@dataclass
class FilterResult:
    action: str            # "pass" | "skip" | "rewrite"
    text: Optional[str]    # new text when action == "rewrite"
    reason: Optional[str]  # log-friendly explanation

    def to_adapter_dict(self) -> Optional[dict]:
        # Converts to adapter-compatible dict:
        #   {"action": "allow", ...}
        #   {"action": "skip", "reason": "..."}
        #   {"action": "rewrite", "text": "..."}
        #   None if action is unrecognized
```

**Adapter usage:** Call `filter.handle_inbound(...)` with the parsed event
fields, then call `.to_adapter_dict()` on the result to get the platform-agnostic
directive.

---

### MentionProcessor — @ → `<at>` Conversion

**Source:** `core/mention_processor.py`

Module-level functions — no class wrapper.

```python
def outgoing_convert(
    text: str,
    bot_registry: Dict[str, Dict[str, Any]],
    current_agent_id: Optional[str] = None,
) -> str
#   Replaces @BotName mentions with Feishu <at> tags.
#   Dashes in bot names are treated as optional for flexible matching.
#   Adds "(BotName)" text fallback after each <at> tag for streaming cards.

def inbound_convert(text: str) -> List[Dict[str, str]]
#   Parses <at> tags from incoming text.
#   Returns [{"user_id": "ou_xxx", "name": "BotName"}, ...].

def inbound_extract_bot_open_ids(text: str) -> List[str]
#   Convenience: extracts just the open_id list from <at> tags.

def is_bot_mentioned(text: str, bot_open_id: str) -> bool
#   Checks if a specific bot was @-mentioned.
```

**Adapter usage for outgoing:** Call `mention_processor.outgoing_convert(text, registry_dict, current_agent_id)` before sending. If result differs from original, replace the message text.

**Adapter usage for inbound:** Call `mention_processor.inbound_extract_bot_open_ids(text)` to discover which bots were @-mentioned, then pass the result as `mentioned_ids` to `MessageFilter.handle_inbound()`.

---

### CollaborationRules — Context Injection

**Source:** `core/collaboration_rules.py`

Module-level functions — no class wrapper.

```python
def build_collaboration_context(
    *,
    channel_id: str,              # "feishu" — returns None for other channels
    current_agent_id: str,        # agentId of the bot whose prompt is being built
    session_key: str,             # agent session key (contains chat ID)
    bot_registry: Dict[str, Dict[str, Any]],  # agent_id → {botOpenId, botName, ...}
    native_a2a_chats: Set[str],   # set of confirmed-native-A2A chat IDs
    group_bot_open_ids: Optional[Set[str]] = None,  # group-filtered bot open_ids
) -> Optional[str]
#   Returns a Chinese A2A collaboration instruction string, or None if no
#   injection is needed (non-Feishu channel, no other bots).

def extract_chat_id_from_session(session_key: str) -> Optional[str]
#   Extracts oc_* chat ID from a Feishu session key.

def split_bots_by_group(
    bot_registry: Dict[str, Dict[str, Any]],
    current_agent_id: str,
    group_bot_open_ids: Optional[Set[str]] = None,
) -> tuple[List, List]
#   Splits bots into (in_group, not_in_group) lists.

def format_bot_list(bots: List) -> str
#   Formats bot list as markdown bullet points with <at> tags.
```

**Adapter usage:** Call `build_collaboration_context(...)` in the agent's prompt
build hook. If the return value is non-None, inject it as `appendSystemContext`.

---

### Cache — File-Based JSON Cache

**Source:** `core/cache.py`

```python
class Cache:
    def __init__(self, cache_dir: str = "~/.hermes/cache", ttl_hours: int = 24)
    def get(key: str) -> Optional[Any]
    def set(key: str, value: Any) -> None
    def clear() -> None
```

---

### FeishuAPI — HTTP Client

**Source:** `core/feishu_api.py`

Two usage patterns:

**Standalone functions** (one-off calls, auto-closes session):
```python
async def get_tenant_token(app_id, app_secret, domain="feishu", session=None) -> str
async def get_bot_info(token, domain="feishu", session=None) -> BotInfo
async def get_group_bot_open_ids(chat_id, feishu_accounts, ...) -> Optional[Set[str]]
```

**`FeishuAPIClient`** (reusable session, async context manager):
```python
async with FeishuAPIClient(feishu_accounts, domain="feishu") as client:
    token = await client.get_tenant_token(app_id, app_secret)
    info = await client.get_bot_info(token)
    bots = await client.get_group_bot_open_ids(chat_id, bot_open_id_set)
```

---

## Adapter Contract

Each adapter MUST implement **3 functions** that bridge platform hooks to the
core engine. These are the only entry points the platform calls — everything
else (bot discovery, caching, API calls) is managed inside the adapter's
lifecycle.

### 1. `handle_inbound(event) -> Optional[dict]`

Called when a message arrives from the platform's gateway.

| Return               | Meaning                          |
|----------------------|----------------------------------|
| `{"action": "skip", "reason": "..."}` | Swallow the message (bot self-message) |
| `{"action": "rewrite", "text": "..."}`| Rewrite and forward (inject sender info) |
| `{"action": "allow"}`                 | Let the message pass through unchanged |
| `None`               | Unrecognized action — fall through |

**Expected implementation:**
```python
def handle_inbound(event: dict, filter: MessageFilter) -> Optional[dict]:
    # 1. Parse event fields (platform-specific)
    #    sender_id = event.get("sender", {}).get("user_id")
    #    mentioned_ids = inbound_extract_bot_open_ids(event.get("text", ""))
    #    text = event.get("text", "")
    #    chat_id = event.get("chat_id", "")
    # 2. Delegate to core engine
    #    result = filter.handle_inbound(sender_id, mentioned_ids, text, chat_id)
    # 3. Translate to platform response
    #    return result.to_adapter_dict()
    pass
```

### 2. `process_outgoing(text: str, registry: BotRegistry, agent_id: str) -> Optional[str]`

Called before a message is sent to Feishu. Converts `@BotName` → `<at>` tags.

| Return               | Meaning                          |
|----------------------|----------------------------------|
| Modified string      | Replaces the outgoing text       |
| `None`               | No modification needed           |

**Expected implementation:**
```python
def process_outgoing(
    text: str,
    bot_registry: BotRegistry,
    current_agent_id: str,
) -> Optional[str]:
    converted = outgoing_convert(text, bot_registry.bots, current_agent_id)
    return converted if converted != text else None
```

### 3. `build_collaboration_context(session, registry) -> Optional[str]`

Called when building the agent's system prompt. Injects A2A collaboration rules.

| Return               | Meaning                          |
|----------------------|----------------------------------|
| String               | Content to append to system prompt |
| `None`               | No injection needed              |

**Expected implementation:**
```python
def build_collaboration_context(
    channel_id: str,
    session_key: str,
    current_agent_id: str,
    bot_registry: BotRegistry,
) -> Optional[str]:
    return build_collaboration_context(
        channel_id=channel_id,
        current_agent_id=current_agent_id,
        session_key=session_key,
        bot_registry=bot_registry.bots,
        native_a2a_chats=bot_registry.native_a2a_chats,
    )
```

---

## Agent Hook Mapping

| Agent     | Inbound Filter         | Outbound Transform       | Context Injection     | Lifecycle Init         |
|-----------|------------------------|--------------------------|-----------------------|------------------------|
| OpenClaw  | `inbound_claim`        | `message_sending`        | `before_prompt_build` | `register()`           |
| **Hermes**| `pre_gateway_dispatch` | `transform_llm_output`   | `on_session_start`    | Plugin `__init__`      |
| Discord   | Gateway middleware     | Message builder hook     | Channel topic inject  | Client setup           |

## Adapter Template

A complete, copy-paste-ready stub is provided at:

> `adapters/template/adapter_template.py`

Copy it, fill in the platform-specific event parsing, and register the 3 hooks.