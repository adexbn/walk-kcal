# -*- coding: utf-8 -*-
u"""snapshot —— 快照读写（**唯一**认快照格式的地方）

为什么需要这一层
────────────────
历史上留了**三套互不相同的快照格式**（实测）：

    ① datasets/*.json          fetcher 产物，已是标准结构
                               {'origin', 'radius', 'provider', 'stores':[
                                   {'id','name','lon','lat','distance_m',
                                    'category_hint','dish_hints','industries'}]}
    ② data/stores.json         池子（淡点层用）
                               {'origin','mcd_stores':[{'walk','straight','tag'}],
                                'local_shops':[{'walk','type','tag'}]}
    ③ data/detail.json         详情（卡片用）
                               {'origin','mcd':[{'walk','tag','keytag','atag'}],
                                'local':[{'walk','tag','keytag','atag','cost'}]}

字段名对不上（`walk` vs `distance_m`、`tag` vs `dish_hints`）。
**后果**：换地图后 provider 产出的标准结构，读快照的一侧认不出来 → 0 家店。

本模块的职责
────────────
    read_stores(path)  → {'origin', 'mcd', 'local', 'all', 'scenes'}
    read_detail(path)  → {'origin', 'mcd', 'local'}
    write(path, doc)   → 一律按**标准结构**写

所有返回的门店都是**归一化结构**：

    {
      'id': str, 'name': str,
      'lon': float, 'lat': float,
      'distance_m': int|None,      # 步行距离（有则优先用，没有退回直线）
      'straight_m': int|None,      # 直线距离
      'address': str,
      'category_hint': str,        # 品类线索（原 keytag/atag/detail_info.tag）
      'dish_hints': [str],         # 推荐菜（原 tag / dish_tag）
      'industries': [str],         # 归一化业态（★ 由 provider 产出，见 geo.py）
      'cost': str|None,
      'is_mcd': bool,
      'raw': dict,                 # 原始记录，便于排查
    }

★ 本模块**不认识任何地图服务商**：只做「字段搬家 + 默认值」。
  服务商私有类目 → `industries` 的翻译在各自的 provider 里完成。
★ 旧格式快照**仍可读**（自动适配），所以本层可以逐步替换、不必一次性迁移。
"""

from __future__ import annotations

import io
import json
import os

# ── 小工具 ────────────────────────────────────────────────
_MCD_KEYS = (u'麦当劳', u'McDonald', u'MCDONALD')


def _f(v):
    u"""宽松转 float；拿不到返回 None（经纬度不能瞎猜 0）"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def split_dishes(v):
    u"""推荐菜字段 → 列表（**已去重，保持顺序**）。

    源可能是字符串（'红烧肉,油爆虾'）或已是列表；
    兼容半角/全角逗号、分号。

    ★ 去重是必需的：数据源原文就有重复 —— 实测辛香汇的「毛血旺」出现两次、
      「贯贯吉清真餐厅」有 4 个重复项。重复会白占一行、还被算两次热量。
      （渲染器的构建期自检抓到的，见 `render.py` 的「菜品有重复」。）
    ★ 去重放在本层（防腐层）：所有下游（卡片 / 语料 / 热量）一次受益。
    """
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        items = [str(x) for x in v]
    else:
        s = str(v)
        for sep in (u'，', u';', u'；', u'、'):
            s = s.replace(sep, u',')
        items = s.split(u',')
    seen, out = set(), []
    for x in items:
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _blank(**kw):
    base = {'id': None, 'name': u'', 'lon': None, 'lat': None,
            'distance_m': None, 'straight_m': None, 'address': u'',
            'category_hint': u'', 'dish_hints': [], 'industries': [],
            'cost': None, 'is_mcd': False, 'raw': {},
            'name_clean': u'', 'status': None}
    base.update(kw)
    return base


# ── 店名里的营业状态（2026-10-15 实测发现）────────────────────
#   ★★ 地图源**没有**营业状态字段（高德 v3 45 个字段、v5 的 business、百度详情都没有），
#      但偶尔把状态**写进店名**：
#        「黄山菜饭(凌河路店)(暂停营业)」
#        「八喜潮皇动物奶油冰淇淋蛋糕(珠江新城店)(暂停营业)」
#      全库 5475 条门店记录里命中 **2 家（≈0.07%）**。
#
#   → 频率太低，**当不了通用关店判据**（不能因为没标就认为在营业）；
#     但它是「零成本、命中即确定」的**正样本**，没理由不用。
#   → 放在**读路径**（`adapt`）而不是写路径：老快照不重新抓也能受益。
#   → ⚠ 顺带修一个隐性 bug：`render.clean_name` 显示店名时按 `(` 截断，
#     正好把这个标记删掉 —— 我们读得到，却主动丢了。
STATUS_WORDS = ((u'暂停营业', 'paused'), (u'已关闭', 'closed'),
                (u'已停业', 'closed'), (u'停业', 'closed'),
                (u'歇业', 'closed'), (u'闭店', 'closed'), (u'搬迁', 'moved'))


def parse_status(name):
    u"""店名 → (干净店名, 营业状态)。状态 ∈ {None, 'paused', 'closed', 'moved'}"""
    n = name or u''
    st = None
    for w, s in STATUS_WORDS:
        if w in n:
            st = s
            n = n.replace(w, u'')
            break
    for pair in (u'()', u'（）', u'( )', u'（  ）'):
        n = n.replace(pair, u'')
    return n.strip(), st


# ── 三种格式的适配器 ───────────────────────────────────────
# ★ 字段别名表 —— **逐字段兜底**，而不是按格式分支
#   理由：三种格式字段名不同，但语义一一对应；任何一行都可能只填了一半。
#   按格式分支会漏（实测中出过问题：行里有 `industries` 就走进标准分支，
#   结果 `walk` 被无视 → 全部距离为 None → 距离筛选/地图投影全废）。
_ALIAS = {
    'id': ('id', 'uid', 'poi_id', 'storeCode'),
    'name': ('name', 'storeName'),
    'lon': ('lon', 'lng', 'longitude'),
    'lat': ('lat', 'latitude'),
    'distance_m': ('distance_m', 'walk', 'dist', 'distance'),
    'straight_m': ('straight_m', 'straight'),
    'address': ('address', 'addr'),
    'category_hint': ('category_hint', 'keytag'),
    'dish_hints': ('dish_hints', 'tag', 'dish_tag', 'specials'),
    'cost': ('cost', 'avg_price', 'price'),
}
# atag 单独处理：它是**推荐菜名列表**，语义上是菜不是品类（此处曾出过错）
_DISH_ALIAS_TAIL = ('atag',)


def _pick(row, field):
    for k in _ALIAS.get(field, ()): 
        v = row.get(k)
        if v not in (None, u'', [], {}):
            return v
    return None


def adapt(row, is_mcd=False, translate=None):
    u"""任意格式的一行 → 归一化门店。**已归一化的再过一遍也无副作用**。

    translate : 可选。`translate(raw_row) → [业态]`，
                用于把**旧快照**里的服务商私有类目现场翻译成归一化业态。
                ★ 由调用方注入（包知道自己接的是谁），本模块不认识任何服务商。
    """
    if not isinstance(row, dict):
        return None
    raw = row.get('raw') if isinstance(row.get('raw'), dict) else row
    name = _pick(row, 'name') or raw.get('name') or u''
    mcd = bool(is_mcd or any(k in name for k in _MCD_KEYS))

    straight = _i(_pick(row, 'straight_m'))
    dist = _i(_pick(row, 'distance_m'))
    dish = split_dishes(_pick(row, 'dish_hints'))
    if not dish:
        for k in _DISH_ALIAS_TAIL:
            dish = split_dishes(row.get(k))
            if dish:
                break

    inds = list(row.get('industries') or [])
    if not inds and translate:
        try:
            inds = list(translate(raw) or [])
        except Exception:
            inds = []

    return _blank(
        id=_pick(row, 'id'), name=name,
        name_clean=_pick(row, 'name_clean') or parse_status(name)[0],
        status=row.get('status') or parse_status(name)[1],
        lon=_f(_pick(row, 'lon')), lat=_f(_pick(row, 'lat')),
        distance_m=dist if dist is not None else straight,
        straight_m=straight,
        address=_pick(row, 'address') or u'',
        category_hint=_pick(row, 'category_hint') or u'',
        dish_hints=dish,
        industries=inds,
        cost=_pick(row, 'cost'),
        is_mcd=mcd, raw=raw)


def _origin_of(doc):
    o = doc.get('origin') or {}
    if isinstance(o, (list, tuple)) and len(o) >= 2:      # [lat, lon]
        return {'name': u'', 'lat': _f(o[0]), 'lon': _f(o[1])}
    return {'name': o.get('name') or u'',
            'lon': _f(o.get('lon')), 'lat': _f(o.get('lat'))}


def _load(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        return json.load(io.open(path, encoding='utf-8'))
    except Exception:
        return {}


# ── 对外接口 ──────────────────────────────────────────────
def read_stores(path, translate=None):
    u"""读「池子」快照（淡点层 + 麦当劳门店）。

    兼容 `data/stores.json`（池子格式）与标准结构（`stores: [...]`）。
    返回 {'origin', 'mcd', 'local', 'all', 'scenes'}
    """
    doc = _load(path)
    if not doc:
        return {'origin': {'name': u'', 'lon': None, 'lat': None},
                'mcd': [], 'local': [], 'all': [], 'scenes': []}

    if doc.get('stores'):                       # 标准结构
        rows = [r for r in (adapt(x, translate=translate)
                            for x in doc['stores']) if r]
        mcd = [r for r in rows if r['is_mcd']]
        local = [r for r in rows if not r['is_mcd']]
        return {'origin': _origin_of(doc), 'mcd': mcd, 'local': local,
                'all': rows, 'scenes': doc.get('scenes') or []}

    mcd = [r for r in (adapt(x, True, translate)
                       for x in doc.get('mcd_stores') or []) if r]
    local = [r for r in (adapt(x, False, translate)
                         for x in doc.get('local_shops') or []) if r]
    return {'origin': _origin_of(doc), 'mcd': mcd, 'local': local,
            'all': mcd + local, 'scenes': doc.get('scenes') or []}


def merge_mcd(*groups):
    u"""把多个来源的麦当劳门店合并成一份（按 id、退到店名去重，**保持原顺序**）。

    ★ 为什么需要（2026-10-16 实测暴露）
    ────────────────────────────────────
    麦当劳有**两个文件**在供数，而两条链各读一边：

        文字链  `packs/pack_mcd.py::_snapshot_stores()`  → `stores.json`（**池子**）
        图片链  `walkkcal/render.py`                     → `detail.json`（**明细**）

    两个文件的**采集半径本来就不一样**（实测：池子 ≈1.4 km / 明细 ≈0.8 km），
    所以「条数」天然对不上 —— 那**不是** bug（默认场景 31 vs 6 就是这种）。
    真 bug 是：某个店**落在池子范围内、却越过明细的采集线**时，
    文字链说得出、图里画不出。实测 `data/linghelu`：
    「麦当劳(浦东融创精彩天地餐厅)」走 **807 m**，只存在于池子里 →
    宿主（Trae CN）为此向用户道歉，而且编了个错理由（猜「800 米半径挡住」）。

    ★ 为什么是**并集**而不是二选一
    ──────────────────────────────
    任一侧多出来的店都不该丢。实测当前 6 个场景，并集 == 池子那一侧，
    所以对出图内容是**零改动**（默认场景 1500 m 内仍是同样那 5 家；
    k11 / jintie / mchq / baidu_k11 全等；只有 linghelu 从 0 家变 1 家 —— 正是要修的那个）。

    ★ 判据 / 回归
    ──────────────
    `tests/test_gate.py::test_mcd_two_chains_same_source`
    （断言两条链在**展示半径内**是同一批店）。
    """
    out, seen = [], set()
    for g in groups:
        for s in (g or []):
            key = s.get('id') or s.get('name')
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    return out


def read_detail(path, translate=None):
    u"""读「详情」快照（卡片用）。

    兼容 `data/detail.json`（mcd/local）与标准结构。
    返回 {'origin', 'mcd', 'local'}
    """
    doc = _load(path)
    if not doc:
        return {'origin': {'name': u'', 'lon': None, 'lat': None},
                'mcd': [], 'local': []}

    if doc.get('stores') and not (doc.get('mcd') or doc.get('local')):
        rows = [r for r in (adapt(x, translate=translate)
                            for x in doc['stores']) if r]
        return {'origin': _origin_of(doc),
                'mcd': [r for r in rows if r['is_mcd']],
                'local': [r for r in rows if not r['is_mcd']]}

    mcd = [r for r in (adapt(x, True, translate)
                       for x in doc.get('mcd') or []) if r]
    local = [r for r in (adapt(x, False, translate)
                         for x in doc.get('local') or []) if r]
    return {'origin': _origin_of(doc), 'mcd': mcd, 'local': local}


def to_standard(store):
    u"""归一化门店 → 标准结构（写快照时用）"""
    return {'id': store.get('id'), 'name': store.get('name'),
            'lon': store.get('lon'), 'lat': store.get('lat'),
            'distance_m': store.get('distance_m'),
            'straight_m': store.get('straight_m'),
            'address': store.get('address') or u'',
            'category_hint': store.get('category_hint') or u'',
            'dish_hints': list(store.get('dish_hints') or []),
            'industries': list(store.get('industries') or []),
            'cost': store.get('cost')}


def write(path, origin, stores, extra=None):
    u"""按**标准结构**写快照（新抓取一律用这个，别再各写各的）。

    origin : {'name','lon','lat'} 或 (lon, lat)
    stores : 归一化门店列表（或 provider.nearby_dining 的原始输出）
    """
    if isinstance(origin, (list, tuple)):
        origin = {'name': u'', 'lon': origin[0], 'lat': origin[1]}
    doc = {'origin': origin or {},
           'stores': [to_standard(s if 'distance_m' in s else (adapt(s) or {}))
                      for s in (stores or [])]}
    if extra:
        doc.update(extra)
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    io.open(path, 'w', encoding='utf-8').write(
        json.dumps(doc, ensure_ascii=False, indent=1))
    return path


def snapshot_of(store, lon0=None, lat0=None):
    u"""按 stores.json/detail.json 的**旧版布局**写回（迁移脚本用，逐步淘汰）"""
    row = dict(store.get('raw') or {})
    row.update({'id': store.get('id'), 'name': store.get('name'),
                'lon': store.get('lon'), 'lat': store.get('lat'),
                'addr': store.get('address') or u'',
                'walk': store.get('distance_m'),
                'tag': u','.join(store.get('dish_hints') or []),
                'industries': list(store.get('industries') or [])})
    if store.get('cost') is not None:
        row['cost'] = store['cost']
    return row


if __name__ == '__main__':
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for label, path, fn in [
            (u'池子 data/stores.json', os.path.join(root, 'data', 'stores.json'),
             read_stores),
            (u'K11 池子', os.path.join(root, 'data', 'k11', 'stores.json'),
             read_stores),
            (u'详情 data/detail.json', os.path.join(root, 'data', 'detail.json'),
             read_detail),
            (u'K11 详情', os.path.join(root, 'data', 'k11', 'detail.json'),
             read_detail)]:
        d = fn(path)
        print(u'%s' % label)
        print(u'   origin=%s' % (d['origin'].get('name') or
                                 (u'%.4f,%.4f' % (d['origin']['lon'], d['origin']['lat']))))
        for k in ('mcd', 'local'):
            rows = d[k]
            has_ind = len([r for r in rows if r['industries']])
            has_dish = len([r for r in rows if r['dish_hints']])
            has_walk = len([r for r in rows if r['distance_m'] is not None])
            print(u'   %-5s %3d 家 ｜ 有业态 %3d ｜ 有菜 %3d ｜ 有距离 %3d'
                  % (k, len(rows), has_ind, has_dish, has_walk))
        if d['local']:
            s = d['local'][0]
            print(u'   样例: %s | %.5f,%.5f | %sm | 业态=%s | 菜=%s'
                  % (s['name'][:20], s['lon'] or 0, s['lat'] or 0,
                     s['distance_m'], s['industries'], s['dish_hints'][:3]))
