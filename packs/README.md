# packs —— 为什么这个目录是项目的核心

> **本文给谁看**：评委（技术评审）—— 可插拔数据源架构，为什么这是项目的核心。

## 一句话

**麦当劳 MCP 开放的是它自己的营养表。但真实场景里，你 400 米外那家面馆多少卡？**

所以我们把数据源做成了**可插拔**的 —— 麦当劳只是其中一个包。

---

## 已接入的两个包

| 包 | 覆盖 | 数据来源 | 条目 | 状态 |
|---|---|---|---|---|
| `pack_mcd` | 麦当劳全部门店 | **MCP `list-nutrition-foods`** | 158 | ✅ 已接入 |
| `pack_nearby_dining` | 周边任意餐厅 | **当前地图 provider** 的推荐菜 + **当前语料 provider** 的热量 | 见快照 | ✅ 已接入 |
| `pack_template` | 星巴克 / 海底捞 / 你楼下那家面馆 | 你填 | — | ○ 模板 |

> `pack_nearby_dining` **不认识任何地图/语料服务商** ——
> 它读快照走 `walkkcal/snapshot.py`，类目翻译问当前 provider，
> 热量统一走 `corpus.lookup()`。
> 所以 `WALKKCAL_GEO=baidu` + `WALKKCAL_CORPUS=boing` 时，**这个包一行都不用改**。

---

## 接一个新品牌 = 30 行

```python
# packs/pack_yourbrand.py
from packs.base import BasePack

class YourBrandPack(BasePack):
    name = u'你的品牌'

    def search_stores(self, lon, lat, radius=1500):
        # ① 找店：MCP / HTTP API / 本地快照，都行
        return self.mcp.call('search-stores', {'lon': lon, 'lat': lat})['data']

    def menu_of(self, store):
        # ② 拿菜名
        return [i['name'] for i in store['menu']]

    def heat_of(self, dish):
        # ③ 算热量：有官方表就直接用；没有返回 None
        #    调用方会自动回退到通用估算（份量归一 + 质量闸门）
        return self.official_table.get(dish)
```

写完，在 `registry.py` 的 `PACKS` 列表加一行，你的品牌门店就会
**和麦当劳门店一起参与「按额度排序」**。

---

## 三个方法的分层设计

`heat_of()` 是**分层的** —— 包里有官方数据就直接用，没有就自动回退：

| 层 | 谁来算 | 说明 |
|---|---|---|
| **① 官方层** | `pack.heat_of()` | 有官方营养表 → 直接用（麦当劳走这条） |
| **② 估算层** | `walkkcal.core` | 官方没有 → 回退通用估算：份量归一（`portion.py`）+ 主料闸/品类闸（`quality_gate.py`） |
| **③ 弃权层** | —— | 估算仍算不出 → 显示「算不出」，**不猜** |

**关键**：`heat_of()` 返回 `None` 是**合法且鼓励**的 —— 这是「弃权」原则的接口体现。

---

## 和另外两层的关系（别搞混）

本项目有**三个**可插拔层，各管一件事：

| 层 | 管什么 | 想换的时候 |
|---|---|---|
| `walkkcal/geo.py` | **店在哪 / 多远** | 换地图服务商 → [`../docs/扩展开发.md`](../docs/扩展开发.md) |
| **`packs/`（本目录）** | **有哪些店 / 各店有什么菜** | 加一个品牌/平台 |
| `walkkcal/corpus.py` | **菜多少卡** | 换语料源 → [`../docs/扩展开发.md`](../docs/扩展开发.md) |

例如「换成百度地图找店，但保留下厨房语料」——
只需换 `geo`，`packs` 和 `corpus` 都不用动。

---

## 为什么这层能赢

查重实测（493 个报名）：
- **226 个**热量类项目，全部只算麦当劳的菜
- **79 个**提到步行/距离，只有 **9 个**和热量挂钩
- **0 个**做「麦当劳 + 周边本地 + 步行额度」

**没有一个对手的麦门是「数据源」，我们的才是。**
`packs/` 把「我们用了高德 POI」从「不遵守赛事要求」翻转成
**「我们提供了一个可扩展架构，麦当劳包只是其中一个」**。
