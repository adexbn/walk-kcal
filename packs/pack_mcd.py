# -*- coding: utf-8 -*-
u"""pack_mcd —— 麦当劳包（★ 全项目唯一必须用 MCP 的包）

数据源
──────
  · 营养表：MCP `list-nutrition-foods`（官方 158 条，含蛋白/脂肪/碳水/钠/钙）
  · 门店：  MCP `query-nearby-stores`（或本地快照回退）

为什么这个包特殊
────────────────
赛事硬性要求「须真实使用麦当劳 MCP 能力」。
本包把 MCP 调用封装在 `McdClient` 里，其余部分（额度算法 / 渲染 /
周边包）与品牌完全解耦 —— 这就是「麦当劳是数据源，不是产品边界」的实现。

离线可用
────────
demo 场景不填 Token 也能跑：`OFFLINE=True` 时读 `packs/mcd_nutrition_snapshot.py`
的实测快照，并在界面标注快照日期。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from packs.base import BasePack  # noqa
from packs import mcd_nutrition_snapshot as SNAP  # noqa
from walkkcal import config as _config  # noqa

# ★ 铁律 3：一切输出都要屏蔽 Key —— 报错文本/URL 里可能带上它。
#   ★ 2026-10-16：密钥来源从「只认环境变量」改成 `config.secret_values()`，
#     它同时覆盖**配置文件**（config.local.json）与名字像密钥的环境变量
#     （`*_KEY` / `*_TOKEN` / `*_AK` …），且长值优先替换。
_SECRET_ENVS = ('MCD_MCP_TOKEN', 'AMAP_KEY', 'BAIDU_AK')


def _redact(text):
    u"""把文本里出现过的密钥替换成占位符 —— 转发到 `config.redact()`。

    （密钥的唯一集合在 `walkkcal/config.py`；屏蔽逻辑跟着它走才不会漂。）
    """
    return _config.redact(text)


class McdClient(object):
    u"""麦当劳 MCP 客户端（JSON-RPC over HTTP）。

    ★ 已实测坑：`list-nutrition-foods` 返回的是**自定义文本格式**，
      不是 JSON。格式：`name,kcal,protein,fat,carb,sodium,calcium`（9 列）。
      必须用 find 定位 `{"success"`，不能用 rfind('{') ——
      嵌套结构里也有 `{`，rfind 会抓错。
    """

    URL = 'https://mcp.mcd.cn'

    def __init__(self, token=None):
        # ★ Key 不写在代码里：`MCD_MCP_TOKEN` 来自环境变量或配置文件
        #   （见 `walkkcal/config.py`；环境变量优先）。
        self.token = token or _config.get('MCD_MCP_TOKEN')

    def call(self, tool, args=None):
        u"""调一个 MCP 工具，返回解析后的 dict。无 Token 时抛异常。"""
        import json
        import urllib.request
        if not self.token:
            raise RuntimeError(u'无 MCD_MCP_TOKEN —— 设环境变量，'
                               u'或写进 config.local.json（见 walkkcal/config.py）')
        body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': tool, 'arguments': args or {}}}
        req = urllib.request.Request(
            self.URL, data=json.dumps(body).encode('utf-8'),
            headers={'Authorization': 'Bearer ' + self.token,
                     'Content-Type': 'application/json',
                     'Accept': 'application/json, text/event-stream',
                     'MCP-Protocol-Version': '2025-06-18'})
        raw = json.loads(urllib.request.urlopen(req, timeout=30)
                         .read().decode('utf-8', 'ignore'))
        txt = raw['result']['content'][0]['text']
        dec = json.JSONDecoder()
        for marker in ('{"success"', '{"code"', '{"date"'):
            i = txt.find(marker)
            if i >= 0:
                out, _ = dec.raw_decode(txt[i:])
                if out.get('data') is not None or out.get('success') is not None:
                    return out
        return {'_raw': txt}

    @staticmethod
    def parse_nutrition(data_str):
        u"""解析官方营养表文本（9 列 CSV）。"""
        rows, seen = [], set()
        for line in (data_str or '').split('\n'):
            line = line.strip()
            if not line or ',' not in line:
                continue
            p = line.split(',')
            if len(p) < 9:
                continue
            try:
                name = p[0].strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                rows.append({'name': name, 'kcal': int(p[3]), 'protein': int(p[4]),
                             'fat': int(p[5]), 'carb': int(p[6]),
                             'sodium': int(p[7]), 'calcium': int(p[8])})
            except (ValueError, IndexError):
                continue
        return rows


class McdPack(BasePack):
    name = u'麦当劳'
    official = True
    provides_dishes = True          # ★ 官方菜单：明确声明给「推荐菜」

    # ★★ 实时找店的作用域：**只影响文字链**（2026-10-16 定案）
    #   图片链（`walkkcal/render.py`）读的是 `data/<场景>/stores.json` 快照，
    #   **不经过本 pack** —— 所以「打开实时」= 文字链换成官方门店、图片链还是快照，
    #   两边会不一致（撞铁律 4）。目前**故意不自动同步**（往用户场景目录写快照
    #   属于「写用户数据」，得先想清楚），但这件事必须是**显式**的：
    #   · 默认配置（`registry.py` 的 `McdPack(offline=True)`）两侧同源，由
    #     `tests/test_gate.py::test_mcd_two_chains_same_source` 守着；
    #   · 打开实时后文字链的每一行都带 `src='mcp'`，一眼能看出换了源。
    LIVE_SCOPE = 'text-chain-only'

    def __init__(self, client=None, offline=True):
        self.client = client
        self.offline = offline
        self._live = {}          # 实时拉到的营养表（name → dict）
        # ★ 最近一次「为什么没用实时」的人话原因（None = 用上了 / 走的就是快照模式）。
        #   以前这里是一个 `except Exception: pass` —— 静默吞掉，调用方
        #   拿到快照却以为在用官方实时。现在原因必须留下来。
        self.last_note = None

    # ── 找店 ────────────────────────────────────────────────
    def search_stores(self, lon, lat, radius=1500):
        u"""返回门店列表。**入参是经纬度** —— 翻译成关键词是本包自己的事。

        ★★ 为什么翻译在包内做，而不是把通用接口改成 `(city, keyword)`
        ─────────────────────────────────────────────────────────
        通用插槽的契约是 `search_stores(lon, lat, radius)`，**全项目统一用经纬度**
        （`registry` / `select` / `render` 都只认它）。经纬度是普适的，将来接的
        渠道大多支持它 —— **接口不该为某一家降级**。

        而麦当劳 MCP 的 `query-nearby-stores` 是**唯一只吃文字**的渠道：
        入参只有 `city` + `keyword`（`searchType=2` 时两者必填），
        返回值里**没有门店坐标**，所以也无法反向补齐。

        → **这是麦当劳的缺陷，不是我们接口的缺陷。** 所以「经纬度 → 文字地标」
          这一步关在 `McdPack` 里做（见 `locate_hint()`），包外面一行都不用改。

        ★ 实时路径需要**两个 Key**，缺一不可：
            ① `MCD_MCP_TOKEN`  调得动 MCP
            ② `AMAP_KEY`       把坐标翻译成 `city`/`keyword`（逆地理编码）
          缺任何一个 → **把原因写进 `self.last_note`** 再回退快照，
          **不再静默**：回退的每一行都带 `src='snapshot'`。

        ★ `last_note` 只在**「想走实时却走不成」**时才有值 ——
          `offline=True`（默认）是设计如此，不算回退，保持 None，
          否则每次正常跑都会带一句无意义的提示。
        """
        if self.offline:
            self.last_note = None          # 设计如此，不是回退
        elif not self.client:
            self.last_note = (u'没有可用的麦当劳 MCP 客户端'
                              u'（缺 MCD_MCP_TOKEN）→ 只能走本地快照')
        else:
            rows, why = self._live_stores(lon, lat)
            self.last_note = why
            if rows:
                return rows
        return self._snapshot_stores(radius)

    # ── 实时找店（默认关闭；开启需 MCD_MCP_TOKEN + AMAP_KEY）──────
    def _live_stores(self, lon, lat):
        u"""坐标 →（翻译）→ MCP。返回 `(门店列表, 没用上实时时的原因)`。"""
        if lon is None or lat is None:
            return [], u'没有坐标 —— 翻译不出 city/keyword'
        hint = self.locate_hint(lon, lat)
        if hint is None:
            return [], (u'拿不到 city/keyword：麦当劳 MCP 不收经纬度，'
                        u'得先做逆地理编码，而这需要 AMAP_KEY')
        # ★ 官方 schema：searchType=2 时 city/keyword 必填，beType 永远必传。
        #   以前这里传的是 {searchType, longitude, latitude} —— 三个参数全错，
        #   而且异常被 `except: pass` 吞掉 → 静默用快照冒充官方实时。
        try:
            r = self.client.call('query-nearby-stores', {
                'searchType': 2, 'city': hint['city'],
                'keyword': hint['keyword'], 'beType': 1})
        except Exception as e:                      # ★ 不吞：原因留下来
            return [], u'MCP query-nearby-stores 失败：%s: %s' % (
                type(e).__name__, _redact(e))
        out = []
        for s in (r.get('data') or []):
            out.append({'id': s.get('storeCode') or s.get('storeName'),
                        'name': s.get('storeName'),
                        # ★ MCP 不返回门店坐标（实测 schema 里就没有经纬度字段）
                        'lon': None, 'lat': None,
                        'distance': s.get('distance'),
                        # ★ 诚实标注：这个 distance 是相对 `keyword` 那个地标算的，
                        #   不是从用户脚下算的（差一个「到最近路口」的量级）
                        'distance_note': u'相对「%s」' % hint['keyword'],
                        'address': s.get('address') or u'',
                        'src': 'mcp'})
        if not out:
            return [], (u'MCP 返回 0 家门店（city=%s keyword=%s）'
                        % (hint['city'], hint['keyword']))
        return out, None

    def locate_hint(self, lon, lat):
        u"""借**地图 provider** 把坐标翻译成 MCP 要的 `city`/`keyword`。

        「坐标 → 文字地标」是地图服务商的私有能力（各家逆地理编码的字段、
        质量、配额都不一样），所以翻译函数由 provider 提供 ——
        和 `geo.industries_of()` 同一个道理。

        没有 provider / 该 provider 不支持 / 没有 Key → 返回 None（**不猜**）。
        """
        try:
            from walkkcal import geo
            prov = geo.get()
        except (Exception, SystemExit):
            return None
        fn = getattr(prov, 'locate_hint', None)
        if fn is None:
            return None
        try:
            return fn(lon, lat)
        except (Exception, SystemExit):
            # 某些 provider 没 Key 时会抛 SystemExit —— 那是「能力不可用」，
            # 不是本流程的故障。返回 None，由上层给出明确原因。
            return None

    def _snapshot_stores(self, radius=1500):
        u"""读快照。★ 第 51 轮：`radius` 生效 —— 「别走太远」时麦当劳也要筛。

        ★ 2026-10-13：改走 `walkkcal/snapshot.py`（唯一认快照格式的地方），
          并跟随 `WALKKCAL_DATA` 场景目录 —— 以前写死 `data/stores.json`，
          切到别的点位时麦当劳还是旧点位的门店。
        """
        from walkkcal import snapshot as snap
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data = os.environ.get('WALKKCAL_DATA') or os.path.join(root, 'data')
        if not os.path.isabs(data):
            data = os.path.join(root, data)
        doc = snap.read_stores(os.path.join(data, 'stores.json'))
        out = []
        for s in doc['mcd']:
            walk = s.get('distance_m')
            if radius and walk is not None and int(walk) > int(radius):
                continue
            out.append({'id': s['id'], 'name': s['name'], 'lon': s['lon'],
                        'lat': s['lat'], 'distance': walk,
                        'address': s.get('address') or u'',
                        'industries': list(s.get('industries') or []),
                        'category_hint': s.get('category_hint') or u'',
                        'dish_hints': list(s.get('dish_hints') or []),
                        'src': 'snapshot'})
        return out


    # ── 拿菜名 ──────────────────────────────────────────────
    def menu_of(self, store):
        u"""麦当劳 demo 用固定套餐组合（无官方推荐顺序字段 —— 已实测确认）。"""
        return list(_MCD_COMBOS.get(store.get('combo_idx', 0), []))

    # ── 拿热量（官方精确）──────────────────────────────────
    def heat_of(self, dish):
        v = self._nutrition(dish)
        return v['kcal'] if v else None

    def _nutrition(self, dish):
        u"""先查实时表，再查快照。找不到返回 None（不猜）。"""
        if dish in self._live:
            return self._live[dish]
        snap = SNAP.nutrition_of(dish)
        if snap:
            return {'name': dish, 'kcal': snap[0], 'protein': snap[1],
                    'fat': snap[2], 'carb': snap[3],
                    'sodium': snap[4], 'calcium': snap[5],
                    'source': 'MCP 快照 %s' % SNAP.SNAPSHOT_DATE}
        return None

    def refresh(self):
        u"""从 MCP 拉最新营养表并缓存（有 Token 时调用）。"""
        if not self.client or self.offline:
            return 0
        r = self.client.call('list-nutrition-foods', {})
        rows = self.client.parse_nutrition(r.get('data', ''))
        self._live = {x['name']: x for x in rows}
        return len(self._live)


# ── 麦当劳推荐组合（MCP 无推荐顺序字段，此为项目自编，非官方）──
_MCD_COMBOS = {
    0: [u'麦香鱼', u'麦辣鸡腿堡', u'薯条（中）', u'迷你草莓新地'],
    1: [u'麦鸡翅（3块）', u'麦香鱼', u'麦辣鸡腿堡', u'可口可乐'],
    2: [u'麦香鱼', u'麦鸡腿汉堡', u'薯条（中）', u'香芋派'],
    3: [u'麦满分', u'麦香鱼', u'薯条（中）', u'圆筒冰淇淋'],
    4: [u'麦香鱼', u'麦辣鸡腿堡', u'可口可乐', u'麦旋风'],
}


if __name__ == '__main__':
    pack = McdPack()
    print(u'麦当劳包 · 快照日期 %s' % SNAP.SNAPSHOT_DATE)
    for c in range(5):
        combo = _MCD_COMBOS[c]
        tot = sum(pack.heat_of(d) or 0 for d in combo)
        print(u'  套餐%d: %s  → 合计 %d 卡'
              % (c + 1, u' + '.join(combo), tot))
