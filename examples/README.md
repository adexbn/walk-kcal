# examples/ · 预采集的演示点位

这里放着**几个真实抓取过的点位快照** —— 用来演示、也用来做回归测试。

> ⚠️ **它们不是你所在的位置。** 真实使用（Skill / MCP / Bot 入口）会
> **实时**抓你附近的店；只有「零配置先看看效果」时才会读这里的文件。

| 目录 | 点位 | 池 | 备注 |
|---|---|---|---|
| `default/` | 上海·静安虬江路 | 233 家 | `render.py` 的默认场景 |
| `k11/` | 上海·淮海路黄陂南路（K11） | 218 家 | 商场场景 |
| `baidu_k11/` | 上海·淮海路 K11 | — | 同一地点，改用百度地图源采集（用来对照两个数据源） |
| `jintie/` | 上海·普陀近铁城市广场 | 187 家 | |
| `mchq/` | 上海·徐汇西岸麦当劳中国总部 | 185 家 | |
| `linghelu/` | 上海·浦东新区凌河路 | 149 家 | 住宅区场景 |
| `guangzhou/` | **广州·天河区员村** | 191 家 | ★ **异地场景** —— 粤菜专名多（`沙姜啫啫糕尤`、`黑椒啫啫牛仔骨`），正好验证「掐头去尾」剥离逻辑 |

每个点位目录里有两个文件：

| 文件 | 内容 |
|---|---|
| `stores.json` | 全量池 —— 含没有推荐菜的店（画淡点用） |
| `detail.json` | 明细 —— 有推荐菜的店（出卡片用） |

## 怎么用

```bash
python walkkcal/render.py                              # 默认读 examples/default
WALKKCAL_DATA=examples/k11 python walkkcal/render.py   # 换点位
python walkkcal/render.py                              # 不联网，1~2 秒
```

## 采集你自己的点位

```bash
python walkkcal/fetch_pois_at.py 31.23 121.38 800 myspot --scene
# → examples/myspot/{stores.json,detail.json}
WALKKCAL_DATA=examples/myspot python walkkcal/render.py
```

★ 这些快照是**某一时刻**的。店会开关、菜单会变 ——
所以「回答我附近有什么」永远走**实时**，快照只用于演示与测试复现。
