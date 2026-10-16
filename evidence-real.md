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

**★ 响应格式是自定义文本，不是 JSON**（重要踩坑）：
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

## 五、★ 匹配陷阱（真实案例）

**现象**：用 `name in k or k in name` 双向包含匹配时，
「**双层深海鳕鱼堡四件套**」被匹配到「**麦香鱼**」。

**后果**：两笔订单都显示 **472 kcal** ——
格式对、值域对、看着完全合理，**实际差 160 kcal**。

**修法**：必须按 `productCode` 精确匹配，不能只靠名字。

> 这是本项目「最贵的经验教训」之一：
> **「数字看着正常」是最大陷阱**。

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
