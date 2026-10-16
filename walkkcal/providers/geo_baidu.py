# -*- coding: utf-8 -*-
u"""geo_baidu —— 百度地图实现（★ 2026-10-14 已用真实 AK 实测）

接口（由 walkkcal/geo.py 定义）：
    nearby_dining(lon, lat, radius)  → 归一化门店列表
    walk_route(from, to)             → 真实路网步行距离
    industries_of(raw)               → 私有类目 → 归一化业态

百度相关的所有知识都**只在这个文件里**。

实测结论（2026-10-14，K11 半径 600m）
────────────────────────────────────
| 能力 | 高德 | 百度 |
|---|---|---|
| 找店 | `/v3/place/around` + **分类码** `types=050000`（一次拿全餐饮） | `/place/v2/search` + **关键词**（无分类码）|
| 召回量（同半径） | **196 家** | **54 家**（多词并集后）|
| 分页 | offset 25 × page ≤100 | ★ page_size **上限 20**；某些 query 第 2 页直接空 |
| 类目 | `type` 三级 `餐饮服务;外国餐厅;日本料理` | `classified_poi_tag` 三级 `美食;快餐店;中式快餐` + `label` |
| **推荐菜** | ✅ `tag` = 「烤鱼, 麻辣香锅,…」 | ❌ **没有** |
| 人均 / 评分 | `biz_ext.cost` / `rating` | `detail_info.price` / `overall_rating` |
| 额外 | — | 服务标签 `featured_service`（有包厢…）、场景标签 `atmosphere`（朋友聚餐…）|
| 坐标系 | GCJ-02 | **BD-09**（必须转，否则整体偏 500m 量级）|

★★ 最重要的一条：**百度没有「推荐菜」**
    `/place/v2/detail` 的 `detail_info` 全部字段里没有任何菜品字段
    （`tag` 是类目串 `美食;中餐厅`，`featured_service`/`atmosphere` 是
    服务/场景标签）。抽查 16 家，无一家有菜名。
    → 所以本 provider 的 `provides_dishes = False`，
      上层只能做到「门店 + 业态 + 距离」，出不了「每道菜多少卡」的卡片。

    这**恰好证明了 `packs/` 的分层是对的**：
      「店在哪」是地图的活，「这家店有什么菜」是内容源的活 ——
      高德只是碰巧把两者打包在了一起。

★ 召回策略（关键词检索的代价）
    百度没有「餐饮分类码」，只能按关键词搜。实测 `美食` 只回 36 家、
    `餐厅` 回 61 家，所以本 provider 用**多关键词并集 + 去重**提高召回。

★★ 并发限速（★ 这条是实测才确认的，2026-10-15）
    百度个人 AK 的**并发上限是 3**。超了不会立刻报错，而是**发短信告警**
    （「并发量超过上限 3/3，需要回复工单」），再严重才拒绝服务。
    实测经过：跑了几个探针脚本（每个循环 `sleep 0.30~0.35`）+
    本 provider 默认 `qps_wait=0.35` ≈ 每秒 3 个请求，
    正好贴着上限跑了几百次 → 收到告警短信。

    所以现在：
      · `_call()` 里内置**全局串行限速器**，任何调用路径都绕不过
      · 默认 1 次/秒（`BAIDU_MIN_INTERVAL` 可调），并发恒为 1
      · 单进程请求总数上限 `BAIDU_MAX_REQUESTS`（默认 300），防止跑飞
      · 识别到限流关键词时**长退避重试**，连续失败就带着可操作提示退出

    教训：**个人 Key 的 QPS 假设必须保守到「宁慢勿快」**。
    地图接口的配额是按「并发」算的，不是按「总量」算的。
"""
from __future__ import annotations

import json
import math
import os
import re
import ssl
import threading
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
from walkkcal.geo import GeoProvider, RateLimiter, new_limiter  # noqa: E402

BAIDU_BASE = 'https://api.map.baidu.com'
PAGE_SIZE = 20                      # ★ 百度硬上限：给 50 也只回 20
MAX_PAGE = 10

DEFAULT_MIN_INTERVAL = 1.0          # 秒/请求（≈1 QPS，并发恒为 1）
DEFAULT_MAX_REQUESTS = 300          # 单进程请求总数上限（防跑飞）
THROTTLE_COOLDOWN = 5.0             # 命中限流后的冷却基数（秒）

# 百度没有餐饮分类码，只能按关键词搜 → 多词并集提高召回。
# ★ 实测「小吃/火锅」等词在「美食+餐厅」之后新增 0 家，所以默认只留 4 个词：
#   每个词都要翻页，词越多请求越多，而个人 Key 的并发很紧。
DINING_QUERIES = [u'美食', u'餐厅', u'快餐', u'咖啡']

# classified_poi_tag 的一级类目只有这两个算「餐饮」
DINING_HEADS = (u'美食', u'餐饮')

# 百度限流/超限时 message 里会出现这些词（不依赖具体 status 码，避免猜错）
THROTTLE_HINTS = (u'并发', u'上限', u'超限', u'频繁', u'限流', u'配额',
                  u'too fast', u'too many', u'qps', u'limit', u'quota')


class BaiduThrottled(RuntimeError):
    u"""百度返回了限流/超限类错误 —— 需要冷却后重试。"""


class BaiduProvider(GeoProvider):
    name = u'百度地图'

    # ★ 百度**不提供推荐菜**（实测 2026-10-14，见模块 docstring）
    provides_dishes = False

    def __init__(self, ak=None, qps_wait=None, min_interval=None,
                 max_requests=None):
        # ★ Key 不写在代码里：环境变量 > config.local.json（见 walkkcal/config.py）
        self.ak = ak or config.get('BAIDU_AK')
        # ★ 兼容旧参数名 qps_wait（以前是 0.35，太快了 —— 见模块 docstring）
        if min_interval is None:
            min_interval = qps_wait if qps_wait else \
                float(os.environ.get('BAIDU_MIN_INTERVAL')
                      or DEFAULT_MIN_INTERVAL)
        if max_requests is None:
            max_requests = int(os.environ.get('BAIDU_MAX_REQUESTS')
                               or DEFAULT_MAX_REQUESTS)
        self.qps_wait = min_interval           # 保留属性名，向后兼容
        self.limiter = RateLimiter(min_interval, max_requests)
        self.min_interval = min_interval       # 供上层（如 fetch CLI）读取
        # ★ 第 63 轮：SSL 上下文改**惰性创建**（见下面的 ctx property）——
        #   建一次要加载整个系统证书库（实测 ~13 ms）。
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
        if not self.ak:
            raise SystemExit(u'未设置 BAIDU_AK。请在 https://lbsyun.baidu.com 申请后，'
                             u'二选一：\n'
                             u'  ① 写进 config.local.json 的 BAIDU_AK（推荐）；\n'
                             u'  ② 或设环境变量：export BAIDU_AK=xxxxx')
        q.setdefault('output', 'json')
        url = BAIDU_BASE + path + '?' + urllib.parse.urlencode(dict(ak=self.ak, **q))
        # ★★ 限速在这里统一做 —— 任何调用方都绕不过
        self.limiter.wait()
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}),
            timeout=25, context=self.ctx).read().decode('utf-8', 'ignore')
        try:
            d = json.loads(raw)
        except Exception:
            raise SystemExit(u'百度返回的不是 JSON（可能是 AK 校验页）。前 200 字：\n%s'
                             % raw[:200])
        if isinstance(d, dict) and d.get('status') not in (0, '0', None):
            msg = u'%s' % (d.get('message') or u'')
            low = msg.lower()
            # ★ 限流/超限：单独抛，交给上层长退避；不要当成配置错误
            if any(h in low or h in msg for h in THROTTLE_HINTS):
                raise BaiduThrottled(
                    u'百度限流/超限：status=%s message=%r（已发 %d 次请求）'
                    % (d.get('status'), msg, self.limiter.count))
            raise SystemExit(
                u'百度接口错误 status=%s message=%r\n'
                u'  常见原因：AK 未开「服务端」权限 / 未开通「地点检索」「轻量级步行导航」/\n'
                u'  白名单 IP 未加 / 今日配额用完。\n'
                u'  原始 URL 贴浏览器可见详情：\n  %s'
                % (d.get('status'), d.get('message'), url))
        return d

    def _call_retry(self, path, attempts=3, **q):
        u"""重试策略：配置类错误立刻抛；限流类长退避；网络类短退避。"""
        last = None
        for i in range(attempts):
            try:
                return self._call(path, **q)
            except SystemExit:
                raise                      # 配置/保险丝类错误不重试
            except BaiduThrottled as e:
                last = e
                if i == attempts - 1:
                    break
                # 限流要退得**比 QPS 间隔久得多**，否则越试越糟
                time.sleep(THROTTLE_COOLDOWN * (i + 1))
            except Exception as e:
                last = e
                if i == attempts - 1:
                    break
                time.sleep(self.limiter.min_interval * (i + 1))
        if isinstance(last, BaiduThrottled):
            raise SystemExit(
                u'⛔ 百度连续 %d 次限流，本次运行中止（不再重试，避免把配额打得更死）。\n'
                u'   原因：个人 AK 的**并发上限是 3**，超了会收到告警短信。\n'
                u'   建议：① 等 5~10 分钟再跑；② 调大间隔 export BAIDU_MIN_INTERVAL=2；\n'
                u'         ③ 只抓小半径（radius 小 → 请求少）。\n'
                u'   原始错误：%s' % (attempts, last))
        return None

    # ── 接口 ①：周边餐饮 ────────────────────────────────────
    def nearby_dining(self, lon, lat, radius=1500, limit=600,
                      queries=None, on_progress=None):
        u"""多关键词并集检索（百度没有餐饮分类码，只能这样提高召回）。

        ★ 三处与高德的结构差异：
          · 经纬度顺序相反：百度是 `lat,lng`
          · 坐标是 BD-09，必须转 GCJ-02
          · `page_size` 上限 20；部分 query 第 2 页直接空 → 拿不到就停，不报错
        ★ 节奏由 `self.limiter` 统一控制，**本函数不再自己 sleep**
          （以前每层都 sleep，多层叠加后仍然贴着并发上限跑，曾经出过问题）。
        """
        rows, seen = [], set()
        for kw in (queries or DINING_QUERIES):
            got = 0
            for page in range(1, MAX_PAGE + 1):
                d = self._call_retry(
                    '/place/v2/search', query=kw,
                    location='%f,%f' % (lat, lon),      # ★ lat,lng
                    radius=str(radius), scope='2',
                    page_size=str(PAGE_SIZE), page_num=str(page))
                if not d:
                    break
                results = d.get('results') or []
                if not results:
                    break
                for p in results:
                    sid = p.get('uid')
                    if not sid or sid in seen:
                        continue
                    s = self._normalize(p)
                    if s is None:                   # 不是餐饮（便利店之类）
                        continue
                    seen.add(sid)
                    rows.append(s)
                    got += 1
                    if len(rows) >= limit:
                        return self._finish(rows)
                # ★ 本页一条新的都没有 → 再翻也是白翻（省请求 = 省并发）
                if got == 0:
                    break
                if len(results) < PAGE_SIZE:
                    break
            if on_progress:
                on_progress(kw, got, len(rows))
        return self._finish(rows)

    @staticmethod
    def _finish(rows):
        rows.sort(key=lambda x: x['distance_m'])
        return rows

    def _normalize(self, p):
        u"""★ 协议适配：百度响应 → 标准结构（见 geo.py docstring）"""
        loc = p.get('location') or {}
        di = p.get('detail_info') or {}
        ctag = di.get('classified_poi_tag') or u''
        label = di.get('label') or u''
        raw_tag = di.get('tag') or u''
        if not is_dining(ctag, label, raw_tag):
            return None
        gx, gy = _bd09_to_gcj02(loc.get('lng'), loc.get('lat'))
        return {
            'id': p.get('uid'),
            'name': p.get('name'),
            'lon': gx,
            'lat': gy,
            # 百度返回的是**直线**距离；真实路网由 walk_route 校正
            'distance_m': int(di.get('distance') or 0),
            'address': p.get('address') or u'',
            # 品类线索：叶子类目名最有用（「法国菜」「四川火锅」）
            'category_hint': _clean_hint(label, ctag),
            # ★ 归一化业态：百度类目在本文件翻译好，上层不认识百度
            'industries': industries_of(ctag, label, raw_tag),
            # ★★ 百度**没有推荐菜** —— 空列表是诚实的，不编
            'dish_hints': [],
            'raw': p,
        }

    # ── 接口 ②：步行距离（真实路网）─────────────────────────
    def walk_route(self, from_lonlat, to_lonlat):
        d = self._call_retry(
            '/directionlite/v1/walking',
            origin='%f,%f' % (from_lonlat[1], from_lonlat[0]),      # lat,lng
            destination='%f,%f' % (to_lonlat[1], to_lonlat[0]))
        try:
            route = d['result']['routes'][0]
            return {'distance_m': int(route['distance']),
                    'duration_s': int(route['duration'])}
        except (KeyError, IndexError, TypeError):
            return None

    # ── 接口 ②b：**批量**步行距离（见 geo.py 契约）────────────
    def walk_route_batch(self, from_lonlat, to_lonlats, chunk=50):
        u"""批量步行距离 —— `GET /routematrix/v2/walking`。

        ★ 百度的上限是 **起点数 × 终点数 ≤ 50**（不是高德那种 100 个起点）。
          所以 104 家要 3 批（50+50+4），仍远好于 104 次单点请求。

        ★ 结果顺序：`result` 是按「行优先」展开的 origins×destinations 矩阵。
          这里终点固定 1 个，所以 `result[i]` 就是第 i 个起点到中心的结果。

        ★ 坐标系：**与 `walk_route` 保持同一口径**（都在 GCJ-02 上直传）。
          百度理论上期望 BD-09，这里不做单方面转换 —— 两条路径一致才有意义，
          坐标系修正是独立议题（见 docs/服务商边界.md 的 TODO）。
        """
        dests = list(to_lonlats or [])
        out = [None] * len(dests)
        if not dests:
            return out
        o = '%f,%f' % (from_lonlat[1], from_lonlat[0])       # lat,lng
        step = max(1, int(chunk))
        for base in range(0, len(dests), step):
            batch = dests[base:base + step]
            origins = '|'.join('%f,%f' % (la, lo) for lo, la in batch)
            d = self._call_retry('/routematrix/v2/walking',
                                 origins=origins, destinations=o)
            for i, r in enumerate((d or {}).get('result') or []):
                if base + i >= len(dests):
                    break
                try:
                    out[base + i] = {
                        'distance_m': int(r['distance']['value']),
                        'duration_s': int((r.get('duration') or {}).get('value') or 0),
                    }
                except (TypeError, ValueError, KeyError):
                    continue                             # 这一项算不出 → 留 None
        return out

    # ── 接口 ②c：地址 → 坐标（见 geo.py 契约）────────────────
    def geocode(self, address, city=None):
        u"""地址 → 坐标（`GET /geocoding/v3/`），返回**已转 GCJ-02** 的坐标。

        ★ 百度返回的是 BD-09，必须转 —— 与 `nearby_dining` 口径一致，
          否则地址解析出来的点会和门店图层差 500m 量级。
        """
        q = {'address': address}
        if city:
            q['city'] = city
        d = self._call_retry('/geocoding/v3/', **q)
        res = (d or {}).get('result') or {}
        loc = res.get('location') or {}
        gx, gy = _bd09_to_gcj02(loc.get('lng'), loc.get('lat'))
        if gx is None or gy is None:
            return None
        return {'lon': gx, 'lat': gy, 'formatted': address,
                'level': res.get('level') or u''}

    # ── 接口 ③：旧快照的类目翻译（见 geo.py 契约）────────────
    def industries_of(self, raw):
        u"""旧快照现场翻译：兼容嵌套的 `detail_info` 与平铺字段。"""
        if not isinstance(raw, dict):
            return []
        di = raw.get('detail_info') or {}
        return industries_of(
            di.get('classified_poi_tag') or raw.get('classified_poi_tag') or u'',
            di.get('label') or raw.get('label') or u'',
            di.get('tag') or raw.get('tag') or raw.get('type') or u'')

    # ── 附加能力：单店详情 ──────────────────────────────────
    def store_detail(self, poi_id):
        u"""拉单个 POI 的详情。

        ★ 实测要点：百度的详情在 **`result`** 里，不在顶层
          （高德在 `pois[0]`，百度在 `result`）。
        ★ 这里**没有推荐菜** —— `tag` 返回的是类目串，别当菜名用。
        """
        d = self._call_retry('/place/v2/detail', uid=poi_id, scope='2')
        res = (d or {}).get('result') or {}
        di = res.get('detail_info') or {}
        loc = res.get('location') or {}
        gx, gy = _bd09_to_gcj02(loc.get('lng'), loc.get('lat'))
        ctag = di.get('classified_poi_tag') or u''
        label = di.get('label') or u''
        return {
            'id': poi_id,
            'name': res.get('name') or u'',
            # ⚠ 下面三个是**类目**不是菜品（保持字段名只为兼容上层）
            'tag': u'',                       # ★ 百度无推荐菜 → 空
            'keytag': _clean_hint(label, ctag),
            'atag': u'',
            'industries': industries_of(ctag, label, di.get('tag') or u''),
            'cost': di.get('price') or u'',
            'opentime': di.get('shop_hours') or u'',
            'rating': di.get('overall_rating') or None,
            'addr': res.get('address') or u'',
            'lon': gx if gx is not None else '',
            'lat': gy if gy is not None else '',
            'photos': int(di.get('image_num') or 0),
            'tel': res.get('telephone') or u'',
            # 百度独有、可做产品彩蛋：服务标签 / 场景标签
            'service_tags': list(di.get('featured_service') or []),
            'scene_tags': list(di.get('atmosphere') or []),
            'raw_detail_info': di,            # 排查用：看真实键名
        }


def _leaf(classified_poi_tag):
    u"""取分类串的最后一级：`美食;中餐馆;火锅店;四川火锅` → `四川火锅`"""
    parts = [x for x in (classified_poi_tag or u'').split(u';') if x]
    return parts[-1] if len(parts) > 1 else u''


def _bd09_to_gcj02(lng, lat):
    u"""BD-09 → GCJ-02（百度坐标系转换，约 500m 级别的偏移）

    ★ 不转的话，门店会整体偏移，地图上位置全是错的。
    """
    if lng is None or lat is None:
        return None, None
    x_pi = math.pi * 3000.0 / 180.0
    x, y = float(lng) - 0.0065, float(lat) - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * x_pi)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * x_pi)
    return z * math.cos(theta), z * math.sin(theta)


if __name__ == '__main__':
    p = BaiduProvider()
    rows = p.nearby_dining(121.4700, 31.2209, 600,
                           on_progress=lambda kw, got, tot: print(
                               u'  query=%-4s 新增 %2d 家（累计 %d）' % (kw, got, tot)))
    print(u'%s · 半径 600m · 多词并集 %d 家餐饮' % (p.name, len(rows)))
    print(u'  推荐菜：%s' % (u'有' if p.provides_dishes else u'★ 本服务商不提供'))
    for r in rows[:10]:
        print(u'  %-24s %4dm  %-6s | %s'
              % (r['name'][:22], r['distance_m'],
                 u'/'.join(r['industries']), r['category_hint']))
