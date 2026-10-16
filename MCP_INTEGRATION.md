# MCP 集成说明

> **本文给谁看**：评委（技术评审）—— 用了哪些 MCP Tool、调用流程、业务价值。

> 本文说明walk-kcal **实际使用了哪些 MCP Server / Tool、调用流程、业务价值**。

---

## 一、使用的 MCP Server 与 Tool

| MCP Server | 地址 | Tool | 用途 | 必需 |
|---|---|---|---|---|
| **麦当劳 MCP** | `https://mcp.mcd.cn` | `list-nutrition-foods` | 官方营养数据（158 条，含热量/蛋白/脂肪/碳水/钠/钙） | ★ **必需** |
| 麦当劳 MCP | `https://mcp.mcd.cn` | `query-nearby-stores` | 门店检索与距离 | 推荐 |
| 麦当劳 MCP | `https://mcp.mcd.cn` | `query-meals` / `query-my-coupons` | 套餐与券（扩展用） | 可选 |
| **高德 MCP** | `https://mcp.amap.com` | 周边检索 / 步行路径 | 本地餐厅 POI + 真实路网距离 | 推荐 |

配置见仓库根 `mcp-config.example.json`（**只含 `${MCD_MCP_TOKEN}` 占位符**）。

---

## 二、★ 三种入口，各自怎么调

同一份内核（`walkkcal/` + `packs/`），三个壳的差别只在**最外层那一跳由谁发起**。
—— **「MCP 调 MCP」只在第 ③ 个入口成立。**

| 入口 | 用户怎么用 | 最外层谁调起引擎 | 到麦当劳的那一跳 |
|---|---|---|---|
| **① Skill** | 对 Agent 说「附近吃什么」 | Agent 跑 `python walkkcal/shoot.py "地址"` | 程序调 MCP |
| **② Bot** | 在聊天软件里发消息 / 分享位置 | `entry/bot.py` 收到消息 → `registry.collect()` | 程序调 MCP |
| **③ MCP** | 任何 MCP 客户端 `tools/call` | `entry/mcp_server.py`（本机 MCP Server） | ★ **MCP 调 MCP** |

> 「到麦当劳的那一跳」要额外满足两个条件才会真的走实时：
> `MCD_MCP_TOKEN` + `WALKKCAL_MCD_LIVE=1`。否则读**内置快照**（效果仍完整）。

三者的**数据源完全相同** —— 都要经过 `packs/registry.collect()`，
再走 `pack_mcd` / `pack_nearby_dining`。**换了入口，算法和数据一行都不变。**

### 只有第 ③ 条是「MCP 调 MCP」

```
任何 MCP 客户端 ──tools/call──▶ walkkcal-mcp（本机 MCP Server）
                                        │
                                        │ tools/call（★ 第二跳，仍是 MCP）
                                        ▼
                                  麦当劳 MCP（mcp.mcd.cn）
```

**为什么值得单独说**：客户端只问一次，拿回的是**已经融合好的结论** ——
官方营养值 + 周边餐厅估算 + 额度判定，不用自己去逐个编排多个 MCP。
这把「用一次 MCP」变成了「**把一个 MCP 能力编排进更大的算法层**」。

> 一句话区分：**从 Skill / Bot 进来是「程序调 MCP」；从 MCP 进来才是「MCP 调 MCP」。**

---

## 三、调用流程（以 MCP 入口为例）

```
① initialize            → 协商协议版本 2025-06-18
② tools/list            → 暴露 4 个工具
③ tools/call list-nutrition-foods
       ↓ 返回自定义文本格式（★ 非 JSON）：
         name,kcal,protein,fat,carb,sodium,calcium      ← 9 列
       ↓ 逐行解析 + 去重（160 → 158）
④ 门店：麦当劳 MCP query-nearby-stores
       ★ 它只吃文字（city + keyword），不收经纬度 →
         坐标先经地图逆地理编码翻译成「城市 + 最近路名」再喂进去
       ★ 通用接口本身仍是经纬度：翻译发生在麦当劳的适配层
         （packs/pack_mcd.py），其它渠道一行都不用改
⑤ 与本地数据融合：
       麦当劳门店  → MCP 官方热量（精确，100% 覆盖）
       本地餐厅    → 自建语料库估算（覆盖 37%，算不出则弃权）
⑥ 叠额度：quota = 450 + (walk//80+1)×4
⑦ 判档：每道菜 吃下 / 超了；每家店 低于/适配/高于缺口
```

---

## 四、业务价值

### 1. 麦当劳 MCP 是「权威数据源」，不是「产品边界」

| 场景 | 数据来源 | 覆盖 |
|---|---|---|
| 麦当劳门店的菜 | MCP `list-nutrition-foods` | **100% 精确** |
| 周边本地餐厅的菜 | 自建语料库 155 万条 | 37%（算不出则弃权） |

有了这个官方测定的基准，才能回答「麦当劳和隔壁那家比，差多少」。

### 2. 用官方数据做校准基准

中餐估算误差 25–28%。麦当劳 MCP 的精确值提供了**交叉验证的锚点** ——
「隔壁那家面馆的热量，是我们用 155 万条菜谱估算的；麦当劳的，
是官方测定的。两者放一起看，才知道差距有多大。」

### 3. 从 MCP 进来时，展示可编排性

麦当劳 MCP 是**能力**，不是**终点**。当调用方本身就是 MCP 客户端时，
我们的 MCP Server 再把麦当劳 MCP 包进一个更大的算法层（额度引擎 + 多源融合），
客户端**一次调用**就能拿到「融合后的结论」，而不用自己编排多个 MCP。

> 从 Skill / Bot 进来时走的是**同一条内核**，只是最外层那一跳不是 MCP —— 见第一节的对照表。

---

## 五、离线回退

评委/访客**不申请 Token 也能看到完整效果**：
- `packs/mcd_nutrition_snapshot.py` 保存了 MCP 实测快照（2026-10-09）
- `packs/pack_mcd.py` 在无 Token 时自动回退到快照
- 界面上标注「数据快照日期」，不假装是实时值
- ★ 每行门店带 `src`（`mcp` / `snapshot`），回退原因写进 `registry.notes()` ——
  **「本该实时、实际是快照」这件事看得见**，不会静默冒充

★ 想跑**实时门店检索**（`query-nearby-stores`）需要**两个 Key**：

| Key | 干什么 | 缺了会怎样 |
|---|---|---|
| `MCD_MCP_TOKEN` | 调得动麦当劳 MCP | 读本地快照（默认路径） |
| `AMAP_KEY` | 把坐标翻译成 `city`/`keyword` | 连 MCP 都不调，回退快照并说明原因 |
