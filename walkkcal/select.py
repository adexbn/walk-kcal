# -*- coding: utf-8 -*-
u"""select —— 「附近几百家，只显示 N 家」的**显式选店规则** + 翻页

用户需求
────────
① 「接受换一批」                        → `pick(..., page=1)` 取下一批
② 「附近西餐就只列西餐」                → 配合 walkkcal/filter.py 先筛业态

为什么需要
──────────
市中心一个红绿灯路口，500 米内就能有 **近 200 家餐饮**（实测 K11：196 家）。
全画出来 → 地图糊成一团、卡片列表滚不到头。

所以必须「选」。但**选谁不能是黑箱** —— 评委/用户要能问「为什么是这 15 家」。

选店规则（四步，全部可复述）
────────────────────────────
  ① **麦当劳保底**：每个象限最多留 1 家，且**每批**总数 ≤ 1/3
     （项目主张：麦当劳是低热量选项 —— 但不能变成「麦当劳推荐器」）
  ② **象限均衡**：四象限轮转取，保证地图不偏（不会全挤在一边）
  ③ **序**：同象限内按「额度内能吃几道 ↓、距离 ↑」排
     （先给「你吃得下」的，再看远近）
  ④ **封顶 + 翻页**：每批 N 家（默认 15，480px 竖屏的可读上限）；
     翻页时**按同一个全序切片** —— 保证「换一批」是有序的下一个区间，
     不是随机重抽（随机会让用户觉得「怎么又出来一遍」）。

与「弃权」一致的原则
────────────────────
选掉的店不是「算不出」，只是「没位置」。界面必须**如实显示总数**：
    「附近 233 家餐饮 · 为你选出 15 家」
不让用户以为附近只有 15 家。
"""

from __future__ import annotations

# ── 直接执行本文件时，把仓库根加入 sys.path（打包导入时无副作用）──
import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

DEFAULT_N = 15
MCD_MAX_RATIO = 3         # ★ 麦当劳最多占 1/3（否则会变成「麦当劳推荐器」）


def _sort_key(s):
    u"""同象限内的排序：能吃得多 → 近"""
    return (-(s.get('fit_n') or 0), s.get('walk') or 0)


def _rotate(cands):
    u"""象限轮转 → 稳定全序（纯轮转，不做品牌限制）"""
    buckets = {0: [], 1: [], 2: [], 3: []}
    for s in cands:
        buckets.setdefault(s.get('quad', 0), []).append(s)
    for q in buckets:
        buckets[q].sort(key=_sort_key)

    out, used = [], set()
    idx = {q: 0 for q in buckets}
    while True:
        progressed = False
        for q in sorted(buckets):
            lst = buckets[q]
            while idx[q] < len(lst) and id(lst[idx[q]]) in used:
                idx[q] += 1
            if idx[q] < len(lst):
                out.append(lst[idx[q]])
                used.add(id(lst[idx[q]]))
                idx[q] += 1
                progressed = True
        if not progressed:
            break
    return out


def order(cands):
    u"""把候选排成**稳定全序**，并在这一层就把麦当劳密度压到 ≤ 1/3。

    ★ 为什么必须在 order() 里做，而不是在 pick() 里对每批做：
      曾经在 pick() 里对单批做「麦当劳超了就换成本地店」，
      但替换要**从批次外借店** → 每批都借同一批（全序最前面那些）
      → **翻页严重串批**（第 1/2/3 批都出现同几家）。
      实测中出过问题。放到全序阶段，切片天然不重叠。

    做法：把本地店按轮转序排开，每隔 (ratio-1) 家插入 1 家麦当劳；
    麦当劳总数上限 = len(本地店) / (ratio-1) —— 这样**每一页都恰好 ≤ 1/3**。
    上限之外的麦当劳不进入展示候选（诚实地说：这是「没位置」，不是「算不出」）。
    """
    seq = _rotate(cands)
    loc = [s for s in seq if not s.get('is_mcd')]
    mcd = [s for s in seq if s.get('is_mcd')]
    per = MCD_MAX_RATIO - 1                       # 每 per 家本地店配 1 家麦当劳
    cap = max(1, len(loc) // per) if loc else len(mcd)
    mcd = mcd[:cap]

    out, mi = [], 0
    for i, s in enumerate(loc):
        out.append(s)
        if (i + 1) % per == 0 and mi < len(mcd):
            out.append(mcd[mi])
            mi += 1
    out.extend(mcd[mi:])
    return out


DRINK_ENV = 'WALKKCAL_DRINK'      # 设 =1 时，「只卖喝的」也进候选


def _dish_names(store):
    u"""取出这家店的全部推荐菜名（`dish_hints` / `dishes` 两种字段都认）。"""
    out = [n for n in (store.get('dish_hints') or []) if n]
    for d in (store.get('dishes') or []):
        n = d.get('name') if isinstance(d, dict) else d
        if n:
            out.append(n)
    return out


def _is_drink_only(store):
    u"""这家店是不是「只卖喝的」—— 该从「现在吃什么」里隐去。

    ★★ 用户 2026-10-15 问：「饮料名字很有诱惑性，你怎么知道是饮料还是面包？」
      答：**不靠菜名猜**。菜名两边都不可靠 ——
        「多肉葡萄」「芝芝绿妍茶后」看不出是喝的；
        「茶香鸡」「米酿圆子」又会被「茶」「酿」误导。
      主判据是**店铺的官方类目**：高德说这家是「茶饮」，它就是饮品店。

    ★ 但类目会误判（实测 Chili's 美式餐厅被评为「咖啡」、
      阿喵说麻辣香锅被评为「茶饮」），所以加两层保护：

        ① 店铺同时挂着任何**正餐业态** → 不算「只卖喝的」
           （挡住 Chili's / 阿喵说 这类类目误判）
        ② 推荐菜里有**能当一餐的固体** → 放行
           （为 Tims 这类「咖啡店但真卖贝果三明治」留活口）

    ★ 方向是不对称的：误隐 < 误留。
      少看到一个选项，好过让人以为「一杯奶茶算一顿饭」。
    """
    from walkkcal import filter as _f
    inds = set(_f.classify(store))
    if inds & _f.MEAL_INDUSTRIES:
        return False                       # ① 有正餐标志 → 不是只卖喝的
    if not (inds & _f.DRINK_ONLY_INDUSTRIES):
        return False                       # 不是饮品类目 → 不适用
    for n in _dish_names(store):
        if _f.has_meal_word(n):
            return False                   # ② 有能当一餐的 → 留下
    return True


def include_drink_only():
    u"""`WALKKCAL_DRINK=1` → 不排除「只卖喝的」"""
    return (_os.environ.get(DRINK_ENV) or '').strip().lower() in (
        '1', 'true', 'yes', 'on')


CLOSED_ENV = 'WALKKCAL_CLOSED'      # 设 =1 时，店名标着「暂停营业」的也进候选（排查用）


def include_closed_stores():
    u"""`WALKKCAL_CLOSED=1` → 不排除店名里标了「暂停营业/已关闭」的店

    ★ 名字里带 stores 是为了避开 `pick()` 的 `include_closed` 参数
      （与 `include_drink` / `include_drink_only()` 的惯例一致）。
    """
    return (_os.environ.get(CLOSED_ENV) or '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _is_closed(store):
    u"""这家店在店名里被标了营业状态吗（`snapshot.parse_status` 解析出来的）

    ★★ 为什么值得一用：地图源**没有**营业状态字段，但偶尔把状态写进店名
      （「黄山菜饭(凌河路店)(暂停营业)」）。全库命中 ≈0.07% ——
      **不能**当通用关店判据，但**命中即确定**，而「推荐一家暂停营业的店」
      是明确的错。零成本的正样本，用。
    """
    return bool((store or {}).get('status'))


def pick(cands, n=DEFAULT_N, page=0, keyword=None, include_drink=None,
         include_closed=None):
    u"""取第 page 批（从 0 开始）。

    keyword       : 业态限定（见 walkkcal/filter.py）；None / '全部' 表示不限。
    include_drink : 是否把「只卖喝的」算进候选。
                    不传 → 读 WALKKCAL_DRINK；**点名业态时强制包含**
                    （用户点「咖啡」就是要看咖啡店，不能再排除）。
    include_closed: 是否把「店名标了暂停营业」的店算进候选。
                    不传 → 读 WALKKCAL_CLOSED（默认排除）。
    返回 (batch, meta)；meta 里 `excluded_drink` / `excluded_closed` = 各被排除的家数。
    """
    kw = keyword if (keyword and keyword != u'全部') else None
    excluded_drink = 0
    excluded_closed = 0
    # ── ① 店名里标了营业状态的：先剔（与业态无关，任何情况都不该推荐）──
    if include_closed is None:
        include_closed = include_closed_stores()
    if not include_closed:
        kept = [c for c in cands if not _is_closed(c)]
        # 兜底同「只卖喝的」：剔完连一页都凑不满 → 退回不过滤
        if len(kept) >= n:
            excluded_closed = len(cands) - len(kept)
            cands = kept
    if kw:
        from walkkcal import filter as _f
        cands = _f.apply(cands, kw)
    else:
        if include_drink is None:
            include_drink = include_drink_only()
        if not include_drink:
            kept = [c for c in cands if not _is_drink_only(c)]
            # 兜底：排除完连一页都凑不满 → 退回不过滤（宁可出图，不要空白）
            if len(kept) >= n:
                excluded_drink = len(cands) - len(kept)
                cands = kept
    raw_total = len(cands)
    allc = order(cands)
    total = len(allc)
    pages = max(1, (total + n - 1) // n)
    page = max(0, min(page, pages - 1))
    batch = allc[page * n:(page + 1) * n]
    return batch, {'total': total, 'shown': len(batch), 'page': page,
                   'pages': pages, 'keyword': keyword or u'全部',
                   'candidates': raw_total, 'excluded_drink': excluded_drink,
                   'excluded_closed': excluded_closed}


def _f(v):
    u"""容错转 float —— 数据快照里的经纬度可能是字符串（麦当劳包就是）"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_candidates(rows, origin=None):
    u"""把 registry 的门店行转成 pick() 需要的候选（补 quad / fit_n）。

    rows   : packs.registry.collect() 的返回
    origin : (lon, lat) 参照点（一般是用户位置）；不给就用门店群的质心
    """
    import math
    pts = [(_f(r.get('lon')), _f(r.get('lat'))) for r in rows]
    pts = [p for p in pts if p[0] is not None and p[1] is not None]
    if origin and _f(origin[0]) is not None and _f(origin[1]) is not None:
        lon0, lat0 = _f(origin[0]), _f(origin[1])
    elif pts:
        lon0 = sum(p[0] for p in pts) / len(pts)
        lat0 = sum(p[1] for p in pts) / len(pts)
    else:
        lon0 = lat0 = 0.0
    kx = 111000.0 * math.cos(math.radians(lat0))

    out = []
    for r in rows:
        known = [d for d in r.get('dishes', []) if d.get('kcal') is not None]
        c = dict(r)
        c['fit_n'] = len([d for d in known if d.get('ok')])
        lo, la = _f(r.get('lon')), _f(r.get('lat'))
        if lo is not None and la is not None:
            b = math.degrees(math.atan2((lo - lon0) * kx,
                                        (la - lat0) * 111000.0)) % 360.0
            c['quad'] = 0 if b < 90 else (1 if b < 180 else (2 if b < 270 else 3))
        else:
            c['quad'] = 0
        out.append(c)
    return out


if __name__ == '__main__':
    import os
    from walkkcal import snapshot as snap
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = os.environ.get('WALKKCAL_DATA') or 'data/k11'
    if not os.path.isabs(data):
        data = os.path.join(root, data)
    # ★ 通过 snapshot 层读（唯一认快照格式的地方）
    pool = snap.read_stores(os.path.join(data, 'stores.json'))['all']
    cands = []
    for i, s in enumerate(pool):
        cands.append({'name': s['name'], 'quad': i % 4,
                      'walk': s.get('distance_m') or 0,
                      'fit_n': 3 if s['is_mcd'] else 1,
                      'is_mcd': s['is_mcd'],
                      'industries': list(s.get('industries') or []),
                      'category_hint': s.get('category_hint') or u''})
    for kw in (None, u'西餐', u'快餐'):
        b, m = pick(cands, 15, 0, kw)
        print(u'【%s】共 %d 家可评估 → %d 批，本批 %d 家'
              % (m['keyword'], m['total'], m['pages'], m['shown']))
        for s in b[:6]:
            print(u'    %-24s %4dm %s' % (s['name'][:22], s['walk'],
                                          u'麦当劳' if s['is_mcd'] else ''))
        if m['pages'] > 1:
            b2, _ = pick(cands, 15, 1, kw)
            print(u'    ── 换一批（第 2 批）第一家和第一批不同吗？ %s'
                  % (b2[0]['name'] != b[0]['name']))
