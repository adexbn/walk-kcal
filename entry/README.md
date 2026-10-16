# entry —— 一份内核，三个壳

> **本文给谁看**：开发者 / 评委 —— 三个入口怎么跑，为什么一份内核要三个壳。

`walkkcal/` 是内核（额度算法 + 数据源 + 渲染），本目录是三个**入口**。
三者共用同一份内核，改核心逻辑不用碰任何一个入口。

| 入口 | 文件 | 形态 | 依赖 |
|---|---|---|---|
| **Skill** | `../SKILL.md` | WorkBuddy Agent 运行时加载 | 无（声明式） |
| **Bot** | `bot.py` | Telegram 机器人 | 仅标准库 |
| **MCP** | `mcp_server.py` | 标准 MCP Server（stdio） | 仅标准库 |

---

## 为什么三个都要

| 入口 | 覆盖谁 | 体现什么 |
|---|---|---|
| **Skill** | WorkBuddy 用户（赛事主场） | 赛事要求「提交 Skill」 |
| **Bot** | 微信/TG 普通用户 | **通用性**：同一内核能落地到 IM |
| **MCP** | 任何 MCP 客户端（Claude/Cursor/自建 Agent） | **架构野心**：不只是个 Skill，是个能力层 |

★ 三个入口都**不需要安装第三方包**，clone 即可跑 —— 降低评委/用户试用门槛。

---

## Bot

```bash
export TELEGRAM_BOT_TOKEN=xxxxx      # 不设置则跑离线自检
python entry/bot.py
```

命令：`/start` `/demo` `/quota 300`

## MCP

stdio 方式接入（配置见仓库根 `mcp-config.example.json`），或本地自测：

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python entry/mcp_server.py
```

工具：`walk_allowance` / `nearby_food` / `what_can_i_eat` / `explain_concept`

★ `mcp_server.py` 内部会去调**麦当劳 MCP**（MCP 调 MCP，第二跳），
  把官方营养表与本地语料库估算融合成一个统一工具。
