# Feishu Bot Chat Plugin — 飞书 Bot A2A 协作插件

[![Tests](https://github.com/zwczwczwc/feishu-bot-chat-plugin/actions/workflows/test.yml/badge.svg)](https://github.com/zwczwczwc/feishu-bot-chat-plugin/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

让飞书群聊中的多个 AI Bot 通过 **@ 互相通信、分工协作**的通用引擎。支持 **Hermes Agent、OpenClaw** 等多种 Agent 框架。

---

## 目录

- [快速了解](#快速了解)
- [工作原理](#工作原理)
- [架构总览](#架构总览)
- [前置条件](#前置条件)
- [接入指南](#接入指南)
  - [OpenClaw（JS 原版）](#openclawjs-原版)
  - [Hermes Agent](#hermes-agent)
  - [适配其他 Agent](#适配其他-agent)
- [Python 开发指南](#python-开发指南)
- [内置协作 Skills](#内置协作-skills)
- [限制](#限制)
- [License](#license)

---

## 快速了解

这是一个**跨 Agent 框架**的飞书 Bot 协作引擎。它让群聊里的多个 AI Bot 能够像人类同事一样：

```
@BotA "帮我写个 API" 
  → BotA @BotB "这个功能的前端部分交给你" 
    → BotB 完成后 @BotA "前端已就绪"
      → BotA 汇总回复用户 "已完成，细节如下..."
```

**核心能力：**

| 能力 | 说明 |
|------|------|
| 🤖 **自动发现** | 自动识别群内所有 Bot 及其 `open_id`，零配置 |
| 📝 **格式转换** | `@BotName` 自动转为飞书 `<at>` 标签 |
| 🔍 **群成员过滤** | 只展示当前群内实际存在的 Bot |
| 🚫 **消息过滤** | 自动吞掉非 @ 本 Bot 的其他 Bot 消息 |
| 📌 **协作规则注入** | 向每个 Bot 注入可用 Bot 列表和协作规范 |
| 📐 **多 Agent 支持** | 通用 Python 引擎 + 适配器模式，即插即用 |

---

## 工作原理

```
用户 @ BotA → BotA 回复中用 <at> 标签 @ BotB
                         ↓
              飞书原生投递：BotB 收到消息
                         ↓
              BotB 处理任务，回复时 @ 回 BotA
                         ↓
              飞书原生投递：BotA 收到结果
                         ↓
              BotA 汇总回复用户（不再 @ BotB）
```

飞书原生支持 Bot @ Bot 的消息投递，本插件负责让 Bot **知道该 @ 谁**、**如何 @**、**何时该 @ 何时不该 @**。

---

## 架构总览

本项目同时包含 **JS 原版（OpenClaw）** 和 **Python 通用引擎（跨 Agent）**：

```
feishu-bot-chat-plugin/
│
├── index.js                    # JS 原版 — OpenClaw 插件
├── openclaw.plugin.json        # OpenClaw 插件元数据
├── package.json
│
├── skills/                     # 6 个协作 Skill（JS/Python 共用）
│
├── core/                       # 🆕 通用 Python 核心引擎（Agent 无关）
│   ├── feishu_api.py          # Feishu Open API 客户端
│   ├── bot_registry.py        # Bot 自动发现 & 注册表
│   ├── mention_processor.py   # @botName ↔ <at> 标签转换
│   ├── message_filter.py      # 消息过滤 & A2A 检测
│   ├── collaboration_rules.py # 协作规则上下文注入
│   └── cache.py               # JSON 文件缓存
│
├── adapters/                   # 🆕 Agent 适配器
│   ├── hermes/                # Hermes Agent 适配器
│   │   ├── plugin.yaml        # 插件元数据
│   │   ├── __init__.py        # 入口（注册 Hook + Tool）
│   │   └── adapter.py         # 适配逻辑
│   └── template/              # 模板适配器（快速接入参考）
│       └── adapter_template.py
│
├── docs/
│   └── adapter-protocol.md    # 适配器接口规范
│
├── tests/
│   └── test_e2e.py            # 集成测试（19/19 ✅）
│
├── pyproject.toml              # Python 包配置
└── requirements.txt
```

### 三层架构

```
Agent 框架  ╔══════════════════════╗
(Hermes/    ║    适配器 Layer      ║  < 200 行薄桥接
 OpenClaw) ╚══════╦═══════════════╝
                   │
              ╔════╧══════════════╗
              ║  Core Engine      ║  通用，零 Agent 依赖
              ║  核心逻辑          ║  纯 Python，可测试
              ╚═══════════════════╝
```

---

## 前置条件

每个参与协作的 Bot 应用必须在飞书开发者后台开通以下权限：

**`im:message.group_at_msg.include_bot:readonly`**
> 接收群聊中机器人 @机器人的消息

路径：**开发者后台 → 应用 → 权限管理 → 搜索 → 开通**

> ⚠️ 不开通此权限，Bot @ Bot 的消息不会被飞书投递到 webhook。

---

## 接入指南

### OpenClaw（JS 原版）

```bash
# 从 ClawHub 安装
openclaw plugins install feishu-bot-chat

# 或从本地源码安装
openclaw plugins install /path/to/feishu-bot-chat-plugin

# 启用
openclaw plugins enable feishu-bot-chat

# 重启 Gateway
openclaw gateway --force
```

零配置，插件启动时自动从 OpenClaw 配置中发现所有飞书 Bot。

**可选配置** — 手动指定 Bot 列表（覆盖自动发现）：

```json
// ~/.openclaw/openclaw.json
{
  "plugins": {
    "entries": {
      "feishu-bot-chat": {
        "enabled": true,
        "config": {
          "botRegistry": {
            "bot-agent-id": {
              "accountId": "feishu-account-id",
              "botOpenId": "ou_xxxxxxxxxxxx",
              "botName": "显示名称"
            }
          }
        }
      }
    }
  }
}
```

---

### Hermes Agent

#### 1️⃣ 安装插件

```bash
# 复制适配器到 Hermes 插件目录
cp -r /path/to/feishu-bot-chat-plugin/adapters/hermes ~/.hermes/plugins/feishu-a2a/
```

#### 2️⃣ 启用插件

编辑 `~/.hermes/config.yaml`：

```yaml
plugins:
  enabled:
    - feishu-a2a
```

#### 3️⃣ 配置飞书环境变量

确保 `~/.hermes/.env` 中包含：

```bash
FEISHU_APP_ID=cli_axxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_DOMAIN=feishu          # 或 lark（国际版）
FEISHU_CONNECTION_MODE=websocket  # 或 webhook
```

#### 4️⃣ 重启 Gateway

```bash
hermes gateway restart
```

#### 5️⃣ 验证

在飞书群中 @ 你的 Bot，确认可用。日志路径：

```bash
tail -f ~/.hermes/logs/gateway.log | grep "feishu-a2a\|A2A"
```

---

### 适配其他 Agent

要接入一个新的 Agent 框架，只需实现 3 个函数：

```python
# 参考 adapters/template/adapter_template.py
# 详见 docs/adapter-protocol.md

def handle_inbound(event, bot_registry, message_filter):
    """返回 {"handled": True}（吞掉）或 {"content": "..."}（重写）或 None（放行）"""
    ...

def process_outgoing(text, bot_registry):
    """将 @BotName 转为 <at> 标签，返回修改后的文本"""
    ...

def build_collaboration_context(chat_id, bot_registry, native_a2a_chats):
    """返回要注入 system prompt 的协作上下文文本"""
    ...
```

完成后提交 PR 将此 Agent 的适配器加入 `adapters/` 目录！

---

## Python 开发指南

### 环境搭建

```bash
cd feishu-bot-chat-plugin
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### 运行测试

```bash
pytest tests/ -v
# 19 passed in 0.13s
```

### 代码结构

| 模块 | 职责 | 对应 JS 源码 |
|------|------|-------------|
| `core/feishu_api.py` | Feishu Open API 调用（Token/Bot/Member） | `getTenantToken`, `getBotInfo` |
| `core/bot_registry.py` | Bot 发现、注册表、反向查找 | `discoverBots`, `buildLookups` |
| `core/mention_processor.py` | @ 标签双向转换 | `message_sending` hook |
| `core/message_filter.py` | Bot 消息过滤 & A2A 检测 | `inbound_claim` hook |
| `core/collaboration_rules.py` | 协作上下文生成 | `before_prompt_build` hook |
| `core/cache.py` | JSON 缓存 | `readCache`, `writeCache` |
| `adapters/hermes/` | Hermes 插件桥接 | Hermes Plugin API |
| `docs/adapter-protocol.md` | 适配器接口规约 | — |

### 提交 PR

```bash
git checkout -b feat/xxx
git add .
git commit -m "feat: add xxx support"
```

---

## 内置协作 Skills

插件提供 6 个飞书 A2A 协作 Skill，群聊中自动生效：

| Skill | 说明 |
|-------|------|
| `a2a-collaboration-guide` | 📖 协作规则速查手册（始终激活） |
| `a2a-task-decompose` | 🧩 任务分解与分配指南 |
| `a2a-result-merge` | 🔗 多 Bot 结果汇总策略 |
| `a2a-interrupt` | ⛔ 协作中断与取消处理 |
| `a2a-status-check` | 📊 状态查询与进度汇报 |
| `a2a-mode-switch` | 🔀 协作模式切换（独立/指定/全力） |

---

## 限制

- 仅支持飞书群聊场景（非单聊）
- 每个 Bot 需开通 `im:message.group_at_msg.include_bot:readonly` 权限
- 飞书卡片中的 `<at>` 标签**不会**触发 webhook 投递，请使用**纯文本消息**
- 自动发现需要 Bot 在 Agent 配置中有对应的飞书账号配置

---

## License

MIT