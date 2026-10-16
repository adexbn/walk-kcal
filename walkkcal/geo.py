# -*- coding: utf-8 -*-
u"""geo —— 地图服务商接口（**找店 + 步行距离**）

想换成百度/腾讯/Google 地图？
────────────────────────────
**不用改任何主流程代码。** 只要：

  1. 新建 `walkkcal/providers/geo_百度.py`，实现下面两个方法
  2. 设环境变量 `WALKKCAL_GEO=百度` + 对应的 Key
  3. 完成

主流程（render / select / registry）只认这个接口，不知道背后是谁。

标准返回结构（**所有 provider 必须归一化成这个**）
────────────────────────────────────────────────
nearby_dining() → [
    {
      'id':        'B0HGZZ273A',        # 唯一 id（去重/关联用）
      'name':      '惠香缘红烧牛肉面',
      'lon':       121.4698,            # GCJ-02 经纬度
      'lat':       31.2502,
      'distance_m': 121,                # 到中心的步行/直线距离（米）
      'address':   '虬江路1230',
      'category_hint': u'牛肉面',        # 品类线索（keytag 之类）+ 分层估算 L3 用
      'industries': [u'面馆'],           # ★★ 业态（归一化词表）—— 见下
      'dish_hints': [u'红烧牛肉面'],      # 推荐菜线索（tag）
      'raw': {...}                       # 原始响应，便于排查
    }, ...
]

★★ `industries` 是**你的职责**，因为各家的类目体系不一样
────────────────────────────────────────────────────────
高德把餐饮分成 `餐饮服务;外国餐厅;日本料理` 这种三级类目，
百度、腾讯各有自己的一套。这套「类目名」是**服务商私有知识**，
所以翻译工作必须在本文件（provider）里做完，上层只认归一化结果。

**归一化词表**（只允许这几个值，见 `walkkcal/filter.INDUSTRIES`）：

    快餐 · 小吃 · 面馆 · 西餐 · 日料 · 韩餐 · 东南亚
    火锅烧烤 · 咖啡 · 茶饮 · 面包甜点 · 中餐本帮

其中「西餐」在筛选时是**聚合键**（= 欧美菜 ∪ 日料 ∪ 韩餐 ∪ 东南亚），
但你**只报具体值**（日料店就报 `['日料']`），聚合由 `filter.GROUPS` 处理。

**报不出来怎么办**：返回**空列表** `[]` 是合法的 ——
上层会退到 `category_hint`（keytag）和店名关键词兜底。
**但不要瞎猜**（猜错会污染筛选，比留空更糟）。

为什么这条要写进契约
────────────────────
实测（K11 218 家）：高德 `type` 贡献了 171 家次的分类；
若换成不产出 `industries` 的 provider，只剩店名兜底 →
分类覆盖率从 **82% 掉到 28%**。所以这不是可选项。
"""

from __future__ import annotations

import os
import threading
import time

# ── 公共限速器（所有地图 provider 共用）─────────────────────────
DEFAULT_MIN_INTERVAL = 1.0      # 秒/请求
DEFAULT_MAX_REQUESTS = 300      # 单进程请求总数上限


class RateLimiter(object):
    u"""全局串行限速器：任意两次请求之间至少间隔 `min_interval` 秒。

    ★ 为什么放在接口层而不是某个 provider 里：
      地图服务商的配额普遍按**并发**算，不是按总量算。
      只要有一条调用路径忘了自己 sleep（例如逐店校正步行距离），
      就会瞬间打满并发 —— 而且**不报错**，只是发告警短信/悄悄降级。

      实测（2026-10-15）：百度个人 AK 并发上限 3，
      探针循环 `sleep 0.30` ≈ 3 QPS 贴边跑了几百次 → 收到告警短信。

    用法：在发请求前 `limiter.wait()`。放进 provider 的 `_call()` 里，
    任何调用方都绕不过（包括将来新增的）。
    """

    def __init__(self, min_interval=DEFAULT_MIN_INTERVAL,
                 max_requests=DEFAULT_MAX_REQUESTS):
        self.min_interval = max(0.0, float(min_interval))
        self.max_requests = int(max_requests) if max_requests else 0
        self._lock = threading.Lock()
        self._last = 0.0
        self.count = 0

    def wait(self):
        u"""取到一次「发请求的许可」。超上限直接抛错，不静默继续。"""
        with self._lock:
            if self.max_requests and self.count >= self.max_requests:
                raise SystemExit(
                    u'⛔ 本次运行的请求数已达上限 %d（防跑飞的保险丝）。\n'
                    u'   地图服务商的配额按**并发**算，跑飞会被限流/告警。\n'
                    u'   确认要更多：调大上限再跑。' % self.max_requests)
            gap = self.min_interval - (time.time() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.time()
            self.count += 1
            return self.count


def new_limiter(min_interval=None, max_requests=None, env_prefix='WALKKCAL'):
    u"""按「参数 → 环境变量 → 默认值」建一个限速器。

    环境变量：`<PREFIX>_MIN_INTERVAL` / `<PREFIX>_MAX_REQUESTS`
    """
    if min_interval is None:
        min_interval = float(os.environ.get(env_prefix + '_MIN_INTERVAL')
                             or DEFAULT_MIN_INTERVAL)
    if max_requests is None:
        max_requests = int(os.environ.get(env_prefix + '_MAX_REQUESTS')
                           or DEFAULT_MAX_REQUESTS)
    return RateLimiter(min_interval, max_requests)


class GeoProvider(object):
    u"""地图服务商基类。子类必须实现两个方法。"""

    name = u'未命名'

    # ★★ 能力声明：这家地图**有没有「推荐菜」数据**
    #   高德有（POI 详情的 `tag` 字段，官方定义为「特色菜」）→ True
    #   百度**没有**（实测 2026-10-14：`detail_info` 里 `tag` 是类目串，
    #     `featured_service`/`atmosphere` 是服务/场景标签，都不是菜名）→ False
    #   上层据此决定：能不能出「每道菜多少卡」的卡片，还是只能到门店/业态一层。
    provides_dishes = False

    def nearby_dining(self, lon, lat, radius=1500, limit=600):
        u"""返回周边的**餐饮**门店列表（归一化结构，见模块 docstring）"""
        raise NotImplementedError

    def walk_route(self, from_lonlat, to_lonlat):
        u"""步行路径。返回 {'distance_m': int, 'duration_s': int}

        ★ 必须是**真实路网**距离（不是直线）——
          直线会让额度算错（实际要绕路，走的比看着远）。
        """
        raise NotImplementedError

    # ── 可选能力 ②b：批量步行距离 ────────────────────────────
    def walk_route_batch(self, from_lonlat, to_lonlats, chunk=100):
        u"""批量步行距离 —— 返回与 `to_lonlats` **等长**的列表，
        每项是 {'distance_m','duration_s'} 或 None（该点算不出）。

        ★★ 为什么要有这个方法（2026-10-15）：
          逐店调 `walk_route` 时，**限速器把墙钟时间钉死在请求数上** ——
          实测凌河路场景 104 家 × 1.2 秒 = **125 秒**，占了整个抓取的 93%。
          而地图服务商普遍有**批量算路**接口（高德 `/v3/distance` 一次
          收 100 个起点），104 家只需 2 次请求 → 2.4 秒。

        ★ 默认实现 = **逐个调 `walk_route`**。
          所以第三方 provider 不实现它也不会坏，只是慢。
          调用方一律用 `getattr(prov, 'walk_route_batch', None)` 探测。

        ★ 实现者注意：**必须走 provider 自己的 `_call()`/`_call_retry()`**，
          不得绕过限速器 —— 批量接口省的是「请求数」，不是「并发许可」。
        """
        return [self.walk_route(from_lonlat, d) for d in (to_lonlats or [])]

    # ── 可选能力 ②c：地址 → 坐标 ─────────────────────────────
    def geocode(self, address, city=None):
        u"""地址字符串 → 坐标。返回
        {'lon': float, 'lat': float, 'formatted': str, 'level': str} 或 None。

        ★ 可选能力。没有它时，上层只能用「已经是坐标」的入口
          （见 `walkkcal/fetch_pois_at.py`），用户得自己查经纬度。
          有了它，`walkkcal/shoot.py` 才能做到「一句话地址出图」。

        ★ `city` 用于**跨城市消歧**：`观前街` 这类路名多个城市都有，
          传 `city='苏州'` 才能落到苏州而不是先撞上上海/其他城市。
        """
        return None

    # ── 可选能力 ③：把坐标说成人话 ──────────────────────────
    def describe_point(self, lon, lat):
        u"""坐标 → 人类可读地名（形如 `上海·静安虬江路`）。拿不到返回 None。

        ★ 这是**可选**的。用途只有一个：给渲染出的长图顶端写「当前位置」。
          没有它时上层退回「纬度,经度（服务商）」—— 能跑，但标签很难看
          （实测中出过问题：新场景的头图写成 `31.2325,121.3817（高德地图）`）。
        """
        return None

    # ── 可选能力 ③b：坐标 → 「只吃文字」渠道要的入参 ──────────────
    def locate_hint(self, lon, lat):
        u"""坐标 → `{'city','keyword','district','township','label'}`。拿不到返回 None。

        ★★ 为什么要单开这个方法（2026-10-16 定案）
        ────────────────────────────────────────────
        本项目**通用接口一律用经纬度** —— `BasePack.search_stores(lon, lat, radius)`、
        `GeoProvider.nearby_dining(lon, lat, ...)`。理由很直白：**经纬度是普适的**，
        将来接的渠道大多支持它，**接口不该为某一家降级**。

        但**有的渠道只吃文字**：麦当劳 MCP 的 `query-nearby-stores` 入参只有
        `city` + `keyword`（`searchType=2` 时两者必填、`beType` 也必传），
        **完全不收经纬度**，返回值里也**没有门店坐标** —— 所以没法反向补齐。

        → 这是**那个渠道的缺陷，不是通用接口的缺陷**。
          所以「坐标 → 文字地标」这一步由**消费方**（`packs/pack_mcd.py`）
          在自己的适配层里做，通道外面一行都不用改。

        而「坐标 → 文字」是**地图服务商的私有能力**（各家逆地理编码的字段、
        质量、配额都不一样），所以翻译函数放在 provider 里 ——
        和 `industries_of()` 同一个道理：**服务商私有知识不出 provider**。

        ★ 返回的 `keyword` 是**离该坐标最近的道路名**（其次最近 POI 名），
          因为麦当劳的 `keyword` 是「找门店的参照点」，一个路名/地标刚好对得上。
          它是**近似**：坐标与 keyword 之间还差几十米，用它的 `distance`
          会带上这点误差 —— 消费方必须如实标注，不能当成精确步行距离。
        """
        return None

    def industries_of(self, raw):
        u"""把本服务商的**私有类目**翻译成归一化业态 —— 见模块 docstring。

        raw 是原始记录（可以是从旧快照里读出来的那行）。
        返回 [业态] 或 []（报不出来是合法的）。

        ★ 为什么是**方法**而不是只做 `nearby_dining` 里的翻译：
          旧快照是**抓取时**的古早格式，字段里只有服务商私有类目。
          上层（`packs/`）读旧快照时需要**现场翻译**，但它不该认识服务商
          —— 所以由当前 provider 提供这个翻译函数，包只管调用。
        """
        return []


# ── 注册表：加地图服务商就在这里加一行 ────────────────────────
_ALIAS = {'gaode': 'amap', u'高德': 'amap', u'百度': 'baidu'}
_CACHE = {}


def _factories():
    u"""名字 → 构造函数。**只在真要用时才 import**（避免拖入 urllib/ssl）。"""
    def _amap():
        from walkkcal.providers.geo_amap import AmapProvider
        return AmapProvider()

    def _baidu():
        from walkkcal.providers.geo_baidu import BaiduProvider
        return BaiduProvider()

    return {'amap': _amap, 'baidu': _baidu}


def clear_cache():
    u"""清掉已建的 provider 实例。

    ★ 测试里切换 `WALKKCAL_GEO` 之后必须调它，否则会拿到上一个 provider。
    """
    _CACHE.clear()


def get(name=None):
    u"""按名字取地图 provider。name 为空时读环境变量 WALKKCAL_GEO。

        WALKKCAL_GEO=amap    （默认）
        WALKKCAL_GEO=baidu

    ★★ **同一名字返回同一实例**（第 63 轮加）。为什么必须缓存：

        provider 的 `__init__` 会建 SSL 上下文，而
        `ssl.create_default_context()` 要加载整个系统证书库（实测 ~13 ms）。
        `industries_of()` 是**本地字符串翻译**，却被按门店调用 ——
        渲染 255 家门店 = 255 次实例化 = **3.4 秒**（cProfile 实测，
        其中 21845 次 `load_verify_locations`）。
        缓存后这部分恒定 ~0 ms。
    """
    key = (name or os.environ.get('WALKKCAL_GEO') or 'amap')
    key = _ALIAS.get(key.strip().lower(), key.strip().lower())
    if key not in _CACHE:
        f = _factories().get(key)
        if f is None:
            raise ValueError(
                u'未知的地图服务商 %r。可用：amap / baidu。'
                u'要接新的？照 walkkcal/providers/geo_template.py 写一个，'
                u'然后在 walkkcal/geo.py 的 get() 里加一行。' % key)
        _CACHE[key] = f()
    return _CACHE[key]
