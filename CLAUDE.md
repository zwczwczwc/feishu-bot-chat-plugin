# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Feishu Bot Chat Plugin** - An OpenClaw plugin that enables bot-to-bot @ communication in Feishu (Lark) group chats. Relies on Feishu's native `im:message.group_at_msg.include_bot:readonly` permission for message delivery between bots.

## Architecture

### Core Mechanism
When a bot @-mentions another bot in a group chat, Feishu natively delivers the message to the target bot's webhook (requires the `im:message.group_at_msg.include_bot:readonly` permission). The plugin handles bot discovery, prompt injection, and message formatting.

### Hook System
The plugin implements three OpenClaw hooks:

1. **`before_prompt_build`** - Injects available bot list into system prompts so bots know who they can @mention. Also detects if native delivery hasn't been confirmed yet and warns the user.
2. **`message_sending`** - Converts `@botName` text to Feishu `<at>` tags with correct `user_id`.
3. **`inbound_claim`** - Filters bot messages (swallows non-mentioned bot messages) and detects native bot-to-bot delivery confirmation.

### Auto-Discovery System
- Automatically discovers all Feishu bots from OpenClaw config (`~/.openclaw/openclaw.json`)
- Calls Feishu API (`bot/v3/info`) to get bot metadata (name, open_id)
- Caches results in `~/.openclaw/fbc-registry/registry.json` (24h TTL)

### Native Delivery Detection
- In `inbound_claim`, when a bot message arrives with `wasMentioned=true`, the plugin records that chat as having native A2A delivery confirmed (`nativeA2AChats` Set)
- If a chat hasn't been confirmed yet, `before_prompt_build` injects a warning about enabling the permission
- This is a passive detection — no canary messages needed, just observes real traffic

### Required Feishu Permission
Each bot app must have `im:message.group_at_msg.include_bot:readonly` enabled in the Feishu Developer Console (开发者后台 → 权限管理). Without this, bot @bot messages won't be delivered via webhook.

## Development

### No Build System
This is plain JavaScript with no build step. Edit `index.js` directly.

### Testing
No automated tests. Test by:
1. Installing plugin: `openclaw plugins install .`
2. Enabling: `openclaw plugins enable feishu-bot-chat`
3. Restarting gateway: `openclaw gateway --force`
4. Testing in Feishu group chat with multiple bots

### Debugging
Log file written daily to `logs/`:
- `a2a-debug-YYYY-MM-DD.log` - Human-readable debug logs (discovery, delivery detection, errors)

Monitor in real-time: `tail -f logs/a2a-debug-$(date +%Y-%m-%d).log`
Check registry cache: `~/.openclaw/fbc-registry/registry.json` (24h TTL)

### Configuration
Plugin config in `~/.openclaw/openclaw.json` under `plugins.feishu-bot-chat`:
- `botRegistry` (object) - Manual bot registry (overrides auto-discovery)

## Files

### Python (New — Cross-Platform Core Engine)
- **core/__init__.py** - Package init
- **core/feishu_api.py** - Feishu Open API client (token, bot info, members)
- **core/bot_registry.py** - Bot discovery and in-memory registry
- **core/mention_processor.py** - @botName ↔ <at> tag conversion
- **core/message_filter.py** - Bot message filtering and A2A detection
- **core/collaboration_rules.py** - A2A context injection for system prompts
- **core/cache.py** - JSON file-based cache with TTL
- **adapters/hermes/** - Hermes Agent plugin adapter
- **adapters/template/** - Template adapter (reference for new Agent integrations)
- **docs/adapter-protocol.md** - Adapter contract documentation
- **tests/test_e2e.py** - Integration tests (pytest)
- **pyproject.toml** - Python package config
- **requirements.txt** - Python dependencies

### Original (JS — OpenClaw Plugin)
- **index.js** - Main plugin implementation (~485 lines)
- **openclaw.plugin.json** - Plugin metadata

## Internal State

The plugin maintains several in-memory lookup maps built during `register()`:
- `botRegistry` - agentId → {accountId, botOpenId, botName}
- `nativeA2AChats` - Set of chatIds where native bot-to-bot delivery has been confirmed
- `botOpenIdSet`, `botOpenIdToAgentMap`, `agentIdSet` - reverse lookup tables
- `groupMemberCache` - chatId → {botOpenIds, fetchedAt} (10-min TTL, filters bot list to actual group members)

These are rebuilt on each gateway restart from auto-discovery results.

## Skills

The plugin provides 6 skills to help bots collaborate effectively:

1. **a2a-collaboration-guide** (alwaysActive) - Comprehensive reference for A2A collaboration rules
2. **a2a-task-decompose** - Task decomposition and delegation strategies
3. **a2a-result-merge** - Multi-bot result aggregation and conflict resolution
4. **a2a-interrupt** - Handling interruption and cancellation signals
5. **a2a-status-check** - Progress tracking and status reporting
6. **a2a-mode-switch** - Switching between collaboration modes (normal/solo/specified/full)

Skills are automatically loaded by OpenClaw from the `skills/` directory.

## Hermes Agent Adapter

The plugin includes a **Hermes Agent** adapter at `adapters/hermes/` that maps
the A2A Core Engine to Hermes's plugin hook system (`pre_gateway_dispatch`,
`transform_llm_output`, `on_session_start`).

### Core Patch Required

Hermes Agent's Feishu gateway adapter (`gateway/platforms/feishu.py`) sends
outgoing messages as plain text by default.  When the A2A plugin's
`transform_llm_output` hook injects `<at>` tags into the response, Feishu
renders them as **literal plain text** rather than clickable @-mentions.

Run the included patch script to fix this::

    python3 scripts/patch-hermes-feishu-adapter.py

This modifies `feishu.py` to:

1. Detect ``<at user_id="...">name</at>`` tags in outgoing content
2. Switch from text format to **post format** with explicit ``at`` elements
   (``{"tag": "at", "user_id": "ou_xxx"}``)
3. Split text at ``<at>`` boundaries so prose before/after remains as
   ``{"tag": "md", "text": "..."}`` elements

After patching, restart the gateway::

    systemctl --user restart hermes-gateway

### User-Identity Sending (Bot-to-Bot Bypass)

Feishu **blocks bot-to-bot message delivery** — when Hermes sends a message
as a bot that @-mentions another bot (e.g. OpenClaw), the target bot never
receives it.

The adapter works around this by detecting @-mentions of known companion
bots and sending those messages **as the user's identity** via direct HTTP
API call with a user access token.

**Setup:**

1. Install Feishu CLI: ``npm install -g @larksuite/cli``
2. Set ``FEISHU_COMPANION_BOT_IDS=ou_xxx`` in ``~/.hermes/.env``
3. Run ``python3 feishu_user_auth.py`` and complete OAuth to get a user token
4. The gateway adapter automatically uses user identity when @-mentioning
   a companion bot (detected in ``_send_raw_message`` → check via
   ``_mentions_companion_bot()``)

**Key files:**

- ``gateway/platforms/feishu_user_auth.py`` — OAuth flow, token storage,
  token auto-refresh, and ``send_message_as_user_http()``
- ``gateway/platforms/feishu.py`` — modified ``_send_raw_message`` with
  user-identity intercept, plus ``_have_companion_bots()`` and
  ``_mentions_companion_bot()`` helpers

**Token lifecycle:** Stored in ``~/.hermes/feishu_user_token.json``.
Auto-refreshed when < 5 minutes remaining. Falls back to bot identity
gracefully if user token is unavailable.

**Architecture:**
- Real-time messaging (receive + normal replies) still goes through
  Hermes's built-in Feishu WebSocket adapter (bot identity)
- Only messages @-mentioning companion bots are redirected through the
  user-identity HTTP path

### Configuration

For A2A collaboration with multiple Feishu bots, add each bot's credentials
to `~/.hermes/config.yaml` under `gateway.platforms.feishu.accounts`::

```yaml
gateway:
  platforms:
    feishu:
      domain: feishu
      accounts:
        bot-a:
          app_id: cli_xxxxxxxxxxxxxxxxxx
          app_secret: your-secret-here
        bot-b:
          app_id: cli_yyyyyyyyyyyyyyyyyy
          app_secret: other-bot-secret-here
```

The plugin auto-discovers all configured bots via the Feishu API and
builds the collaboration registry.

## Dependencies

Runtime: Node.js native modules only (`fs`, `path`, `os`)
External: Feishu Open API (auth, bot info)
Platform: OpenClaw plugin system / Hermes Agent plugin system
