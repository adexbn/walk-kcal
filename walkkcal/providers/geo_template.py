# -*- coding: utf-8 -*-
u"""geo_template —— 接一个新地图服务商，30 行

步骤
────
1. 复制本文件为 `geo_你的服务商.py`
2. 实现下面两个方法（把对方的数据结构翻译成我们的标准结构）
3. 在 `walkkcal/geo.py` 的 `get()` 里加一行
4. `export WALKKCAL_GEO=你的服务商` + 对应 Key

就完了 —— 主流程（render / select / registry）**一行都不用改**。

标准结构（必须归一化成这个）
────────────────────────────
    {
      'id':           唯一 id,
      'name':         店名,
      'lon': float, 'lat': float,     # GCJ-02（国测局坐标）
      'distance_m':   int,            # 到中心距离（米）
      'address':      str,
      'category_hint': u'牛肉面',      # 品类线索（keytag 之类）
      'industries':   [u'面馆'],       # ★★ 业态（归一化词表）—— 见下
      'dish_hints':   [u'红烧牛肉面'],  # 推荐菜线索
      'raw':          原始响应,
    }

★★ `industries` 是**你的职责** —— 别把服务商的类目名透出去
────────────────────────────────────────────────────────────
各家的类目体系完全不一样（高德三级 / 百度两级 / 腾讯又是另一套），
所以翻译必须在本文件里做完。上层只认**归一化词表**：

    快餐 · 小吃 · 面馆 · 西餐 · 日料 · 韩餐 · 东南亚
    火锅烧烤 · 咖啡 · 茶饮 · 面包甜点 · 中餐本帮

（定义在 `walkkcal/filter.INDUSTRIES`）

**报不出来就返回 `[]`** —— 上层会退到 `category_hint` 和店名关键词兜底。
**不要瞎猜**：猜错会污染筛选，比留空更糟。

⚠️ **实测代价**：K11 218 家里有 171 家次靠 `industries` 分类。
若 provider 不产出这个字段，只剩店名兜底 → 分类覆盖率 **82% → 28%**。
（用 `walkkcal/filter.coverage()` 自检。）
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

# ── 直接执行本文件时，把仓库根加入 sys.path（打包导入时无副作用）──
import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from walkkcal.geo import GeoProvider, new_limiter

BASE = 'https://api.你的域名.com'


class YourProvider(GeoProvider):
    name = u'你的地图'

    # ★★ 必须声明：你这家的 POI 详情里**有没有「推荐菜」**
    #   有（像高德那样返回特色菜/推荐菜）→ True → 上层出「每道菜多少卡」的卡片
    #   没有（像百度那样只有类目）      → False → 上层降级为「门店 + 业态 + 距离」
    #   ★ 声明错了会怎样：声明 True 但其实没有 → 门店会因为「没有可展示的菜」
    #     被全部剔除，最后出一张空图（实测中出过问题）；反之只会少显示一点信息。
    provides_dishes = False

    def __init__(self, key=None):
        self.key = key or os.environ.get('YOUR_GEO_KEY', '')
        # ★ 限速器：地图配额按**并发**算，超了往往不报错、只发告警短信。
        #   放进 _call() 里，任何调用方都绕不过。
        self.limiter = new_limiter()
        self.min_interval = self.limiter.min_interval

    def _call(self, path, **q):
        if not self.key:
            raise SystemExit(u'未设置 YOUR_GEO_KEY')
        url = BASE + path + '?' + urllib.parse.urlencode(dict(key=self.key, **q))
        self.limiter.wait()          # ★ 限速：任何调用方都绕不过
        return json.loads(urllib.request.urlopen(url, timeout=25)
                          .read().decode('utf-8', 'ignore'))

    def nearby_dining(self, lon, lat, radius=1500, limit=600):
        u"""① 找周边的餐饮店，翻页抓全，归一化后返回"""
        rows = []
        d = self._call('/你的周边搜索端点', location='%f,%f' % (lon, lat),
                       radius=str(radius))
        for p in d.get('你的结果字段', []):
            rows.append({
                'id': p['id'],
                'name': p['name'],
                'lon': float(p['lng']),
                'lat': float(p['lat']),
                'distance_m': int(p.get('distance') or 0),
                'address': p.get('address', ''),
                'category_hint': p.get('category', ''),   # ← 品类线索
                'dish_hints': p.get('dishes', []),        # ← 推荐菜线索
                'raw': p,
            })
        return rows[:limit]

    def walk_route(self, from_lonlat, to_lonlat):
        u"""② 真实路网步行距离（不是直线！直线会让额度算错）"""
        d = self._call('/你的步行路径端点',
                       origin='%f,%f' % from_lonlat,
                       destination='%f,%f' % to_lonlat)
        try:
            r = d['你的路径字段'][0]
            return {'distance_m': int(r['distance']), 'duration_s': int(r['duration'])}
        except (KeyError, IndexError, TypeError):
            return None

    # ── ②b（可选，★ 强烈建议）批量步行距离 ──────────────────
    #    ★ 为什么值得写：不写就退化成逐店请求，而限速器把墙钟钉死在
    #      请求数上（实测 104 家 × 1.2 秒 = 125 秒，占抓取的 93%）。
    #      批量接口一次能收几十上百个起点 → 2 次请求 ≈ 2.4 秒。
    #    ★ 红线：必须走 self._call_retry()，不得绕限速器
    #      （批量省的是「请求数」，不是「并发许可」）。
    #    ★ 单项算不出就返回 None，调用方会自动回退逐店，精度不降级。
    def walk_route_batch(self, from_lonlat, to_lonlats, chunk=100):
        dests = list(to_lonlats or [])
        out = [None] * len(dests)
        if not dests:
            return out
        center = '%f,%f' % tuple(from_lonlat)
        for base in range(0, len(dests), max(1, int(chunk))):
            batch = dests[base:base + chunk]
            # TODO 换成你这家的批量端点与参数名（例：origins / destination）
            origin_str = '|'.join('%f,%f' % (lo, la) for lo, la in batch)
            d = self._call_retry('/你的批量算路端点',
                                 origins=origin_str, destination=center)
            for r in ((d or {}).get('你的结果数组') or []):
                try:
                    i = int(r['origin_id']) - 1        # 高德从 1 开始编号
                    out[base + i] = {'distance_m': int(float(r['distance'])),
                                     'duration_s': int(float(r.get('duration') or 0))}
                except (TypeError, ValueError, KeyError, IndexError):
                    continue
        return out

    # ── ②c（可选）地址 → 坐标 ───────────────────────────────
    #    ★ `shoot.py`（一步出图：地址 → 图）依赖这个能力。
    #      不实现的话，只能用 fetch_pois_at.py 的坐标入口。
    #    ★ `city` 用于跨城市消歧（「观前街」多个城市都有）。
    #    ★ 坐标必须转成 **GCJ-02** 再返回，和 nearby_dining 口径一致。
    def geocode(self, address, city=None):
        q = {'address': address}
        if city:
            q['city'] = city
        d = self._call_retry('/你的地理编码端点', **q)
        # TODO 解析出经纬度，并转到 GCJ-02
        loc = ((d or {}).get('你的结果字段') or {}).get('location') or {}
        if not loc.get('lng'):
            return None
        return {'lon': float(loc['lng']), 'lat': float(loc['lat']),
                'formatted': address, 'level': u''}

    # ── ③ 私有类目 → 归一化业态（旧快照翻译用，见 geo.py 契约）──
    #    ★ 这一步**不能省**：没有它，你这家地图的店只有店名能兜底，
    #      实测分类覆盖率会从 82% 掉到 28%。
    #    归一化词表（只允许这几个值）：
    #      快餐 小吃 面馆 西餐 日料 韩餐 东南亚 火锅烧烤 咖啡 茶饮 面包甜点 中餐本帮
    #    报不出来返回 []（合法）—— 但别瞎猜。
    def industries_of(self, raw):
        if not isinstance(raw, dict):
            return []
        # TODO 把「你的类目字符串」翻译成这样：
        #     cats = raw.get('你的类目字段', '')
        #     if '日本料理' in cats:  return [u'日料']
        #     ...
        return []


# ── 注册（复制到 walkkcal/geo.py 的 get()）──────────────────
#    if key in ('你的服务商',):
#        from walkkcal.providers.geo_你的服务商 import YourProvider
#        return YourProvider()
