# MCP 真实调用证据

> **本文给谁看**：评委（真实性核验）—— 2026-10-09 直连麦当劳 MCP 的实测输出快照。

> 本文件记录 2026-10-09 直连 `https://mcp.mcd.cn` 的**实测输出快照**。
> 可复现：见文末「复现方式」。

---

## 一、`list-nutrition-foods`

**请求**
```
POST https://mcp.mcd.cn
Authorization: Bearer ${MCD_MCP_TOKEN}
{
  "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": {"name": "list-nutrition-foods", "arguments": {}}
}
```

**实测结果**：返回 **160 条**（去重后 **158 条**）。

**★ 响应格式是自定义文本，不是 JSON**（重要注意点）：
```
name,kcal,protein,fat,carb,sodium,calcium
麦香鱼,325,15,16,44,640,60
麦辣鸡腿堡,517,26,29,44,1050,120
...
```
**9 列，不是 10 列**（容易误判）。

**解析纪律**：必须用 `txt.find('{"success"')` 定位 payload，
**不能用 `rfind('{')`** —— 嵌套的 `reservationTimeOptions` 里也有 `{`，
rfind 会抓到预约时段导致解析到空对象。

---

## 二、`query-my-account`

**实测结果**（已验证 Token 有效）：
```
可用积分: 245.9
累计积分: 4351.4
已过期:   2615.5（60.1%）
```

---

## 三、`query-my-coupons`

**实测结果**：14 张券，**100% 套餐型**。

---

## 四、`query-meals`

**实测结果**：需传 `orderType`（1 = 到店 / 2 = 外送）。
23:21 后部分门店报错 `600057`。

---

## 五、★ 匹配易错点（真实案例）

**现象**：用 `name in k or k in name` 双向包含匹配时，
「**双层深海鳕鱼堡四件套**」被匹配到「**麦香鱼**」。

**后果**：两笔订单都显示 **472 kcal** ——
格式对、值域对、看着完全合理，**实际差 160 kcal**。

**修法**：必须按 `productCode` 精确匹配，不能只靠名字。

> 这是本项目「最贵的经验教训」之一：
> **最危险的情况是「数字看着正常」**。

---

## 六、高德 POI（辅助源，非 MCP）

**实测**：
- 上海市区（31.2657, 121.4101）：180–358m 内 5 家
- 昆山千灯古镇（31.2653, 121.0019）：1200m 内 **185 家**，仅 80 家有菜名线索

**两种语义**：
- 连锁品牌 tag ≈ **菜单项**（真实菜单）
- 本地小店 tag ≈ **特色菜**

---

## 七、复现方式

```bash
export MCD_MCP_TOKEN=xxxxx
python - <<'EOF'
import json, urllib.request
TOKEN = __import__('os').environ['MCD_MCP_TOKEN']
body = {'jsonrpc':'2.0','id':1,'method':'tools/call',
        'params':{'name':'list-nutrition-foods','arguments':{}}}
req = urllib.request.Request('https://mcp.mcd.cn',
    data=json.dumps(body).encode(),
    headers={'Authorization':'Bearer '+TOKEN,
             'Content-Type':'application/json',
             'Accept':'application/json, text/event-stream',
             'MCP-Protocol-Version':'2025-06-18'})
print(urllib.request.urlopen(req, timeout=30).read().decode()[:500])
EOF
```

或在仓库内：
```bash
python packs/pack_mcd.py --refresh     # 拉取并缓存最新营养表
```

---

## 八、★ 2026-10-16 复测：实时路径跑通 + 快照自我校准

### 8.1 先修了一个「会让本节证据失效」的 bug

复测前用**假 token** 压链路，发现 `tool_what_can_i_eat` 里写死
`_pipeline(None, None, …)` 且 schema 没声明经纬度 —— **坐标被丢掉**，
于是麦当劳实时路径永远报「没有坐标 —— 翻译不出 city/keyword」。
**就算 token 有效也照样回退快照。**

| | `notes()` 的值 |
|---|---|
| 修前 | `没有坐标 —— 翻译不出 city/keyword` |
| 修后（假 token） | `MCP query-nearby-stores 失败：HTTP Error 401: Unauthorized` |
| **修后（真 token）** | **空 —— 无回退** |

修法：schema 补 `longitude`/`latitude`、实现读出来传给 `_pipeline`；
回归测试 `test_mcp_tool_forwards_coords`（schema + 实现，两头各一断言）。

### 8.2 真 token 下的实时结果

- `tools/list` → 服务端自报 `mcd-mcp` v1.0.0，注册 **35 个工具**
- `list-nutrition-foods` → **158 条**（与 10-09 一致）
- **端到端文字链**：`notes = {}`、`honesty = 没有回退：每条渠道都走了它该走的路`

★ 最硬的一条证据：同一坐标下，门店**换了**——

| | 门店 | 距离 | 来源 |
|---|---|---|---|
| 走快照 | 麦当劳(四川北路店) | 1391 m | `examples/*/stores.json` |
| **走实时** | **麦当劳上海闸北虬江路永兴汇餐厅** | **792 m** | MCP `query-nearby-stores` |

「永兴汇餐厅」这家**本地快照里根本没有** —— 只有真调 MCP 才会出现。

### 8.3 ★ 顺手校准出快照里 7 条错值

拿实时表逐条比对本地快照，**同名同物但有 7 条对不上**，已按官方值改正：

| 名称 | 原快照 | **官方实时** | 差 |
|---|---|---|---|
| 板烧鸡腿堡 | 517 | **391** | **−126（−24%）** |
| 麦辣鸡腿堡 | 517 | **485** | −32 |
| 薯条（中） | 337 | **289** | −48 |
| 圆筒冰淇淋 | 147 | **93** | −54 |
| 吉士汉堡包 | 277 | **294** | +17 |
| 巨无霸 | 520 | **513** | −7 |
| 冰美式 | 15 | **13** | −2 |

每条都用 **kJ ÷ 4.184 = kcal** 交叉验算过（官方表第 3 列 kJ、第 4 列 kcal）。

**另有 7 条故意不改** —— 官方无同名项，或规格/口味不同，机械替换会出错。
其中一条正是 10-09 那个易错点的翻版：快照「**麦香鸡翅（3块）**」按子串匹配会命中
官方「**麦香鸡**」（369 卡）—— 那是**汉堡不是鸡翅**。

> 复核方式：用麦当劳 MCP 的 `list-nutrition-foods` 重新拉一次官方表，
> 与本快照逐条 diff（上方三区的判据都写在 `packs/mcd_nutrition_snapshot.py` 的注释里）。

★ 诚实说明：第 8.3 的「原快照」值来自 2026-10-09 那次调用（见本文第一节）。
本次复测只能确认**现在**官方表给的是新值，无法判断是官方中途调表、
还是当初抄录有误 —— 一律以复测到的官方实时值为准。

