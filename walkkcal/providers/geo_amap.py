# -*- coding: utf-8 -*-
u"""geo_amap —— 高德地图实现（默认服务商，已实测）

接口（由 walkkcal/geo.py 定义）：
    nearby_dining(lon, lat, radius)  → 归一化门店列表
    walk_route(from, to)             → 真实路网步行距离

高德相关的所有知识都**只在这个文件里**：
    · 域名 https://restapi.amap.com
    · 端点 /v3/place/around、/v3/place/detail、/v3/direction/walking
    · 字段 location / pois / tag / keytag / atag / biz_ext …
换成别家地图时，本文件原样保留即可（只要环境变量指向别的 provider）。
"""
from __future__ import annotations

import json
import math
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

# ── 直接执行本文件时，把仓库根加入 sys.path（打包导入时无副作用）──
import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from walkkcal import config  # noqa: E402

from walkkcal.geo import GeoProvider, RateLimiter

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATASETS = os.path.join(os.path.dirname(os.path.dirname(_HERE)), 'datasets')

AMAP_BASE = 'https://restapi.amap.com'
DINING_TYPES = '050000'          # 高德分类码：餐饮服务


# ══════════════════════════════════════════════════════════════════
# ★★ 高德类目 → 归一化业态（**高德专属知识，只在本文件**）
# ══════════════════════════════════════════════════════════════════
# 为什么放在这里而不是 walkkcal/filter.py：
#   高德 `type` 形如 `餐饮服务;外国餐厅;日本料理`，这套类目名是高德独有的。
#   若把它写进通用的 filter.py，换成百度后这 20 条规则全废
#   （实测：K11 218 家里 171 家次靠高德 type 分类，只剩店名兜底 → 覆盖 82%→28%）。
#
# 归一化后的词表（`walkkcal/filter.INDUSTRIES`）：
#   快餐 小吃 面馆 西餐 日料 韩餐 东南亚 火锅烧烤 咖啡 茶饮 面包甜点 中餐本帮
#
# 换别的服务商时，只要它的 provider 产出同样的 industries 字段，filter.py 一行不用改。

# 三级类目（最具体，优先）
AMAP_L3 = [
    (u'日本料理|寿司|日式', u'日料'),
    (u'韩国料理|韩式', u'韩餐'),
    (u'其它亚洲菜|东南亚|泰国|越南|新加坡|马来|印尼|印度', u'东南亚'),
    (u'西餐厅|法式|意式|德式|西班牙|地中海|俄式|葡式', u'西餐'),
    (u'咖啡厅', u'咖啡'),
    (u'冷饮店|茶艺馆', u'茶饮'),
    (u'糕饼店|面包|烘焙', u'面包甜点'),
    (u'甜品店', u'面包甜点'),
    (u'火锅店', u'火锅烧烤'),
    (u'快餐厅', u'快餐'),
    (u'休闲餐饮', u'小吃'),
    (u'中餐厅|综合酒楼|地方风味|特色|上海菜|广东菜|四川菜|湖南菜|'
     u'云贵菜|东北菜|西北菜|清真|粤菜|川菜|湘菜', u'中餐本帮'),
]

# 二级伞形类目（仅当三级一个都没命中时兜底）
AMAP_L2 = [
    (u'外国餐厅', u'西餐'),
    (u'中餐厅', u'中餐本帮'),
    (u'快餐厅', u'快餐'),
    (u'咖啡厅', u'咖啡'),
    (u'冷饮店', u'茶饮'),
    (u'糕饼店|甜品店', u'面包甜点'),
    (u'火锅店', u'火锅烧烤'),
    (u'休闲餐饮', u'小吃'),
]

_AMAP_L3_RE = [(re.compile(k), v) for k, v in AMAP_L3]
_AMAP_L2_RE = [(re.compile(k), v) for k, v in AMAP_L2]


def industries_of(amap_type):
    u"""高德 `type` → 归一化业态列表（可能多个，也可能为空）

    例：'餐饮服务;外国餐厅;日本料理' → ['日料']
        '餐饮服务;中餐厅;火锅店'     → ['火锅烧烤']
        '餐饮服务;餐饮相关场所;餐饮相关' → []（无信息，交给上层用 keytag/店名兜底）
    """
    out = []
    parts = (amap_type or '').split(u';')
    l2 = parts[1] if len(parts) > 1 else u''
    l3 = parts[2] if len(parts) > 2 else u''

    hit = False
    for rx, ind in _AMAP_L3_RE:
        if l3 and rx.search(l3):
            if ind not in out:
                out.append(ind)
            hit = True
    if not hit:
        for rx, ind in _AMAP_L2_RE:
            if l2 and rx.search(l2) and ind not in out:
                out.append(ind)
    return out


class AmapProvider(GeoProvider):
    name = u'高德地图'

    # ★★ 高德**提供**推荐菜：POI 详情的 `tag` 字段，官方定义为「特色菜」
    #    （如「烤鱼, 麻辣香锅, 老干妈回锅肉」）—— 这是本产品「每道菜多少卡」的输入。
    #    百度没有这个字段（实测），所以那边是 False。
    provides_dishes = True

    def __init__(self, key=None, qps_wait=None, min_interval=None,
                 max_requests=None):
        # ★ Key 不写在代码里：环境变量 > config.local.json（见 walkkcal/config.py）
        self.key = key or config.get('AMAP_KEY')
        # ★ 高德也有 QPS 限制（个人 Key 更紧）；逐店校正步行距离时最容易打满。
        #   把节流放进 _call()，任何调用方都绕不过。
        if min_interval is None:
            min_interval = qps_wait or float(
                os.environ.get('AMAP_MIN_INTERVAL') or 1.2)
        if max_requests is None:
            max_requests = int(os.environ.get('AMAP_MAX_REQUESTS') or 300)
        self.qps_wait = min_interval
        self.min_interval = min_interval
        self.limiter = RateLimiter(min_interval, max_requests)
        # ★ 第 63 轮：SSL 上下文改**惰性创建**（见下面的 ctx property）。
        #   建一次要加载整个系统证书库（实测 ~13 ms），而 provider 常被用于
        #   **纯本地翻译**（industries_of）—— 那不需要网络，不该为此付钱。
        self._ctx = None

    @property
    def ctx(self):
        u"""HTTPS 上下文（首次真正发请求时才创建）"""
        if self._ctx is None:
            self._ctx = ssl.create_default_context()
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE
        return self._ctx

    # ── HTTP ────────────────────────────────────────────────
    def _call(self, path, **q):
        if not self.key:
            raise SystemExit(
                u'未设置 AMAP_KEY。请在 https://lbs.amap.com 申请「Web 服务」Key 后，'
                u'二选一：\n'
                u'  ① 写进配置文件（推荐，不会跟着 .py 一起被提交）：\n'
                u'       cp config.example.json config.local.json  然后填 AMAP_KEY\n'
                u'  ② 或设环境变量：export AMAP_KEY=xxxxx')
        url = AMAP_BASE + path + '?' + urllib.parse.urlencode(dict(key=self.key, **q))
        # ★★ 限速在这里统一做 —— 任何调用方都绕不过
        self.limiter.wait()
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}),
            timeout=25, context=self.ctx).read().decode('utf-8', 'ignore')
        return json.loads(raw)

    def _call_retry(self, path, attempts=4, **q):
        u"""QPS 限流时指数退避重试（高德个人 Key 限流很紧）"""
        d = None
        for i in range(attempts):
            try:
                d = self._call(path, **q)
            except Exception:
                d = None
            if d and d.get('status') == '1':
                return d
            time.sleep(self.qps_wait * (i + 1))
        return d

    # ── 接口 ①：周边餐饮 ────────────────────────────────────
    def nearby_dining(self, lon, lat, radius=1500, limit=600, max_page=13):
        rows, seen = [], set()
        for page in range(1, max_page + 1):
            d = self._call_retry(
                '/v3/place/around', location='%f,%f' % (lon, lat),
                radius=str(radius), types=DINING_TYPES, extensions='all',
                offset='25', page=str(page), sortrule='distance')
            if not d or d.get('status') != '1':
                break
            # ★ 2026-10-15：这里原本还有一句 `time.sleep(self.qps_wait)`。
            #   删掉 —— `_call()` 里已经 `limiter.wait()` 过了，重复 sleep
            #   把每页间隔从 1.2s 变成 2.4s（分页 6 页白等 7 秒）。
            pois = d.get('pois') or []
            if not pois:
                break
            for p in pois:
                s = self._normalize(p, lon, lat)
                if s['id'] in seen:
                    continue
                seen.add(s['id'])
                rows.append(s)
                if len(rows) >= limit:
                    return rows
            if len(pois) < 25:
                break
        rows.sort(key=lambda x: x['distance_m'])
        return rows

    @staticmethod
    def _km(lat):
        return 111000.0 * math.cos(math.radians(lat))

    def _normalize(self, p, lon0, lat0):
        u"""★ 协议适配：高德响应 → 我们的标准结构（见 geo.py docstring）"""
        lo, la = [float(x) for x in (p.get('location') or '0,0').split(',')]
        dist = int(math.hypot((la - lat0) * 111000.0, (lo - lon0) * self._km(lat0)))
        # 高德 tag 常见形态："餐饮服务;中餐厅" 或 "红烧肉,鸡肉,椒盐排条"
        tag = (p.get('tag') or '').strip()
        dish_hints = [t.strip() for seg in tag.split(';') for t in seg.split(u',')
                      if t.strip()] if tag else []
        return {
            'id': p.get('id'),
            'name': p.get('name'),
            'lon': lo,
            'lat': la,
            'distance_m': dist,
            'address': p.get('address', ''),
            'category_hint': (p.get('keytag') or p.get('atag') or '').strip(),
            # ★ 业态（归一化）：高德类目在本文件翻译好，上层不用认识高德
            'industries': industries_of(p.get('type') or ''),
            'dish_hints': dish_hints,
            'raw': p,
        }

    # ── 接口 ②：步行距离（真实路网）─────────────────────────
    def walk_route(self, from_lonlat, to_lonlat):
        d = self._call_retry(
            '/v3/direction/walking',
            origin='%f,%f' % tuple(from_lonlat), destination='%f,%f' % tuple(to_lonlat))
        try:
            path = d['route']['paths'][0]
            return {'distance_m': int(path['distance']),
                    'duration_s': int(path['duration'])}
        except (KeyError, IndexError, TypeError):
            return None

    # ── 接口 ②b：**批量**步行距离（见 geo.py 契约）────────────
    def walk_route_batch(self, from_lonlat, to_lonlats, chunk=100):
        u"""批量步行距离 —— `GET /v3/distance`，一次最多 100 个起点。

        ★★ 这个方法是抓取提速的关键。原来的逐店 `/v3/direction/walking`
          每家一次请求，而限速器（1.2 秒/请求）把墙钟时间钉死：
          实测凌河路 104 家 = **125 秒**，占整个抓取的 93%。
          改用本接口后 104 家 = **2 次请求**（≈2.4 秒）。

        ★ 为什么可以「用起点当门店、终点当中心」：
          步行距离**近似对称**，而额度算的是「走过去多远」——
          方向差异（去的路和回的路）在市区尺度上忽略不计。
          高德这个接口本身就是按 `origin_id` 回填距离，与门店一一对应。

        ★ 必须走 `_call_retry` —— 省的是「请求数」，不是「并发许可」。
        """
        dests = list(to_lonlats or [])
        out = [None] * len(dests)
        if not dests:
            return out
        center = '%f,%f' % tuple(from_lonlat)
        step = max(1, int(chunk))
        for base in range(0, len(dests), step):
            batch = dests[base:base + step]
            origins = '|'.join('%f,%f' % (lo, la) for lo, la in batch)
            d = self._call_retry('/v3/distance', origins=origins,
                                 destination=center, type='3')
            for r in ((d or {}).get('results') or []):
                try:
                    i = int(r.get('origin_id')) - 1      # 高德从 1 开始编号
                    out[base + i] = {
                        'distance_m': int(float(r['distance'])),
                        'duration_s': int(float(r.get('duration') or 0)),
                    }
                except (TypeError, ValueError, KeyError, IndexError):
                    continue                          # 这一项算不出 → 留 None
        return out

    # ── 接口 ②c：地址 → 坐标（见 geo.py 契约）────────────────
    def geocode(self, address, city=None):
        u"""地址 → 坐标（`GET /v3/geocode/geo`）。

        ★ `city` 用于跨城市消歧：「观前街」在多个城市都有，
          传 `city='苏州'` 才能落到苏州。
        """
        q = {'address': address}
        if city:
            q['city'] = city
        d = self._call_retry('/v3/geocode/geo', **q)
        gs = (d or {}).get('geocodes') or []
        if not gs:
            return None
        loc = (gs[0].get('location') or '').split(',')
        if len(loc) < 2:
            return None
        try:
            lon, lat = float(loc[0]), float(loc[1])
        except ValueError:
            return None
        if not lon or not lat:
            return None
        return {'lon': lon, 'lat': lat,
                'formatted': gs[0].get('formatted_address') or '',
                'level': gs[0].get('level') or ''}

    # ── 接口 ③：旧快照的类目翻译（见 geo.py 契约）────────────
    def industries_of(self, raw):
        u"""高德类目 → 归一化业态。

        来源可能是原始 POI 响应，也可能是旧快照里的一行 —— 都取 `type`。
        没有 `type` 就返回 `[]`（**不瞎猜**：keytag／店名 由上层 `filter` 兜底，
        那里本来就是通用的，不需要本模块插手）。
        """
        if not isinstance(raw, dict):
            return []
        return industries_of(raw.get('type') or '')

    # ── 可选能力：把坐标说成人话（给长图的「当前位置」标签）──────
    def describe_point(self, lon, lat):
        u"""逆地理编码 → `上海·静安区宝山路街道`（拿不到返回 None）"""
        d = self._call_retry('/v3/geocode/regeo',
                             location='%f,%f' % (lon, lat), extensions='base')
        ac = ((d or {}).get('regeocode') or {}).get('addressComponent') or {}
        if not isinstance(ac, dict):
            return None

        def _s(v):
            if isinstance(v, list):
                v = v[0] if v else u''
            return (v or u'').strip()

        city = _s(ac.get('city')) or _s(ac.get('province'))
        label = u'%s·%s%s' % (city.replace(u'市', u''), _s(ac.get('district')),
                              _s(ac.get('township')))
        return label if len(label) > 3 else None

    # ── 可选能力 ③b：坐标 → 「只吃文字」渠道要的入参（见 geo.py 契约）──
    def locate_hint(self, lon, lat):
        u"""坐标 → `{'city','keyword','district','township','label'}`。

        用途只有一个：喂给**只吃文字的渠道**（当前就是麦当劳 MCP 的
        `query-nearby-stores`，入参 `city` + `keyword`，不收经纬度）。

        实现：`GET /v3/geocode/regeo`，`extensions=all` ——
        只有 `all` 才在 `regeocode` 里带 `roads`（周边道路）与 `pois`（周边 POI），
        而麦当劳要的 `keyword` 正是一个路名/地标名。

        ★ 没有 Key 时返回 **None**（不是抛错）：这是**可选能力**，
          「不可用」是它的合法返回值。（对比 `_call()` 对必需路径会抛 SystemExit。）
        ★ `keyword` 的取值顺序：最近道路名 → 最近 POI 名 → 街道/区名。
          再取不到就返回 None —— **不猜**（猜个错的 keyword 会搜到别的区）。
        """
        if not self.key:
            return None
        d = self._call_retry('/v3/geocode/regeo',
                             location='%f,%f' % (lon, lat), extensions='all')
        rc = (d or {}).get('regeocode') or {}
        ac = rc.get('addressComponent') or {}
        if not isinstance(ac, dict):
            return None

        def _s(v):
            if isinstance(v, list):
                v = v[0] if v else u''
            return (v or u'').strip()

        # 直辖市（北京/上海/天津/重庆）高德会把 city 返成空，得退到 province
        city = _s(ac.get('city')) or _s(ac.get('province'))
        if not city:
            return None
        city = city.replace(u'市', u'')
        district, township = _s(ac.get('district')), _s(ac.get('township'))

        def _nearest(items):
            u"""从 AMAP 的 `roads`/`pois` 数组里取**最近**那个的名字。

            ★ 有 `distance` 的按最小距离取；一个都没有 `distance` 时取第一个
              —— 高德这两个数组本来就是「由近到远」排的。
            """
            named, best, best_d = [], u'', None
            for it in (items or []):
                if not isinstance(it, dict):
                    continue
                nm = _s(it.get('name'))
                if not nm:
                    continue
                named.append(nm)
                try:
                    dd = float(it.get('distance'))
                except (TypeError, ValueError):
                    continue
                if best_d is None or dd < best_d:
                    best, best_d = nm, dd
            return best or (named[0] if named else u'')

        # ★ 道路优先：路名是「一两条街」的量级，跟麦当劳 keyword 的预期最贴
        keyword = _nearest(rc.get('roads')) or _nearest(rc.get('pois')) \
            or township or district
        if not keyword:
            return None
        return {'city': city, 'keyword': keyword,
                'district': district, 'township': township,
                'label': u'%s·%s%s' % (city, district, township)}

    # ── 便利：单店详情 ──────────────────────────────────────
    def store_detail(self, poi_id):
        u"""拉单个 POI 的详情（含推荐菜 tag / 人均 / 营业时间）。

        ★ 这是高德特有能力的**可选扩展**：换别家地图时若不实现，
          上层会自然退化到只用 nearby_dining 的结果。
        """
        d = self._call_retry('/v3/place/detail', id=poi_id, extensions='all')
        ps = (d or {}).get('pois') or []
        if not ps:
            return None
        p = ps[0]
        be = p.get('biz_ext') or {}
        loc = (p.get('location') or '').split(',')
        return {
            'id': poi_id,
            'name': p.get('name') or '',
            'tag': p.get('tag') or '',            # ★ 特色菜（= 推荐菜）
            'keytag': p.get('keytag') or '',
            'atag': p.get('atag') or '',
            'cost': be.get('cost'),
            'opentime': be.get('opentime2') or be.get('opentime') or '',
            'rating': p.get('rating'),
            'addr': p.get('address') or '',
            'lon': loc[0] if loc else '',
            'lat': loc[1] if len(loc) > 1 else '',
            'photos': len(p.get('photos') or []),
            'tel': p.get('tel') or '',
        }

    # ── 便捷：把结果存成快照（本项目的数据快照就是这么来的）──
    def dump(self, lon, lat, radius, path):
        rows = self.nearby_dining(lon, lat, radius)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'origin': [lat, lon], 'radius': radius, 'stores': rows},
                      f, ensure_ascii=False, indent=1)
        return rows


if __name__ == '__main__':
    p = AmapProvider()
    rows = p.nearby_dining(121.4700, 31.2209, 500)
    print(u'%s · 半径 500m 抓到 %d 家餐饮' % (p.name, len(rows)))
    for r in rows[:6]:
        print(u'  %-24s %4dm  %s | 菜:%s'
              % (r['name'][:22], r['distance_m'], r['category_hint'],
                 u','.join(r['dish_hints'][:3])[:30]))
