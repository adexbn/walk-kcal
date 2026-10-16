# -*- coding: utf-8 -*-
u"""registry —— 把**启用的渠道**合并成统一的「门店 + 菜 + 热量」列表。

对外暴露三个函数：
    collect()    跨渠道聚合门店（启用的渠道会**合并**）
    describe()   当前「源装配」一行（让数据来源可见）
    get_packs()  实例化当前启用的渠道

★ 渠道启用制
────────────
`CHANNELS` 是渠道注册表；真正启用哪些由环境变量 `WALKKCAL_PACKS` 决定：

    不设                        → 默认 ['mcd', 'nearby']
    WALKKCAL_PACKS=mcd          只启用麦当劳
    WALKKCAL_PACKS=nearby       只启用周边餐厅（即「只要高德」）
    WALKKCAL_PACKS=mcd,nearby   两个都启用 → 合并

**麦当劳默认启用，是因为这是麦当劳程序员节参赛作品。**
启用的渠道会合并进同一个列表（麦当劳不占本地店配额，见 `select.py`）。

返回结构
────────
[
  {'id','name','cat','walk','quota','dishes':[{'name','kcal','ok','src'}]},
  ...
]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from walkkcal import allowance  # noqa
from walkkcal import config  # noqa
from walkkcal import corpus  # noqa
from walkkcal import prior  # noqa
from packs.pack_mcd import McdClient, McdPack  # noqa
from packs.pack_nearby_dining import NearbyDiningPack  # noqa


def _mcd_pack():
    u"""构造麦当劳渠道 —— ★ 默认离线（读快照），实时要显式打开。

    默认 `offline=True` 是**产品决定**：评委/访客不申请 Token 也要能看完整效果
    （见 `MCP_INTEGRATION.md`）。要跑官方实时：

        WALKKCAL_MCD_LIVE=1  +  MCD_MCP_TOKEN  +  AMAP_KEY

    ★ 实时找店为什么需要**两个** Key：`query-nearby-stores` 只吃文字
      （`city` + `keyword`），所以得先用 `AMAP_KEY` 把坐标逆地理编码。
      缺任何一个 → 回退快照，但原因会进 `notes()` → 进用户可见的文案
      （`walkkcal/reply.py::line_source_notes`）——**不再是静默的**。
    """
    if not config.enable_live():
        return McdPack(offline=True)
    token = (config.get('MCD_MCP_TOKEN') or u'').strip()
    return McdPack(client=(McdClient(token) if token else None), offline=False)


# ── 渠道注册表：加一个数据源就在这里加一行 ───────────────────
#    键 = 渠道名（供 WALKKCAL_PACKS 选择），值 = 构造函数
CHANNELS = {
    'mcd':    _mcd_pack,                       # 麦当劳官方营养表（默认读 MCP 快照）
    'nearby': lambda: NearbyDiningPack(),      # 周边餐厅（当前地图 provider + 语料）
}

# 默认启用（麦当劳在第一位 —— 参赛作品，默认让评委看到）
DEFAULT_CHANNELS = ['mcd', 'nearby']

# ★ 最近一次 `collect()` 各渠道留下的「为什么没用实时数据」说明（渠道短名 → 原因）。
#   为什么要有它：麦当劳的实时找店失败会**回退快照**，回退本身是对的，
#   但「本该实时、实际是快照」如果只存在 pack 的实例状态里，调用方就看不见 ——
#   那正是 2026-10-16 修掉的那个「静默用快照冒充实时」。这里把它抬到外面。
LAST_NOTES = {}


def notes():
    u"""`{渠道短名: 原因}` —— 只含**非 None** 的项（None = 走了该走的路）。"""
    return {k: v for k, v in LAST_NOTES.items() if v}


def enabled_names():
    u"""当前启用的渠道名列表（读 `WALKKCAL_PACKS`，不设则用默认）。"""
    raw = os.environ.get('WALKKCAL_PACKS')
    if raw is None or not raw.strip():
        return list(DEFAULT_CHANNELS)
    names = [x.strip().lower() for x in raw.split(',') if x.strip()]
    unknown = [n for n in names if n not in CHANNELS]
    if unknown:
        raise SystemExit(
            u'未知渠道 %s\n'
            u'  可用：%s\n'
            u'  例：WALKKCAL_PACKS=mcd            （只启用麦当劳）\n'
            u'      WALKKCAL_PACKS=nearby         （只启用周边餐厅）\n'
            u'      WALKKCAL_PACKS=mcd,nearby     （两个都启用，合并）'
            % (u' / '.join(unknown), u' / '.join(sorted(CHANNELS))))
    return names


def get_packs():
    u"""实例化当前启用的渠道（顺序同 `enabled_names()`）。"""
    return [CHANNELS[n]() for n in enabled_names()]


def _short(pack):
    u"""报告里用的短名：`周边餐厅（地图 + 语料）` → `周边餐厅`"""
    return (getattr(pack, 'name', u'') or u'').split(u'（')[0].split(u'(')[0].strip()


def describe(packs=None):
    u"""「源装配」一行 —— 让「启用了什么、每个渠道供哪些槽」可见。

    ★ 这是「每个数字带来源标签」原则上移一层：数据来自哪个渠道，也要看得到。
      退化（例如换到不提供推荐菜的渠道）会在这里显形，而不是静默变空。
    """
    if packs is None:
        packs = get_packs()
    if not packs:
        return u'源装配：★ 没有启用任何渠道（WALKKCAL_PACKS 为空）'

    parts = []
    for p in packs:
        marks = [u'店✓',
                 (u'菜✓' if getattr(p, 'provides_dishes', False) else u'菜✗'),
                 (u'热✓官方' if getattr(p, 'official', False) else u'热✓估算')]
        parts.append(u'%s(%s)' % (_short(p), u' '.join(marks)))
    line = u'源装配：' + u' ｜ '.join(parts)

    if not any(getattr(p, 'provides_dishes', False) for p in packs):
        line += (u'\n   ⚠ 没有渠道提供推荐菜 —— 只会展示「门店 + 距离 + 业态」')
    return line


def _store_hint(s):
    u"""拼出「门店线索」，给 L3 分层估算用（**结构化，不互相污染**）。

    ★ 只用**归一化字段** —— `industries`（provider 翻译好的业态）
      + `category_hint`（品类线索）+ 店名 + 推荐菜名。
      以前拼的是 `type;keytag;atag`（高德私有字段名），换地图后全空 →
      L3 兜底直接失效。
    """
    return {'industries': list(s.get('industries') or []),
            'text': u';'.join([s.get('category_hint') or u'',
                               s.get('name') or u'']),
            'dish_names': list(s.get('dish_hints') or [])}


def collect(lon=None, lat=None, radius=1500, corpus_path=None, quota_fn=None,
            corpus_provider=None, packs=None):
    u"""跨**启用的渠道**聚合门店，按额度算出每道菜能不能吃下。

    packs : 传入选定的渠道实例（测试用）；默认用 `get_packs()`。

    ★ 热量取值链（分层，每层带来源标签）：
        官方渠道（麦当劳） → pack.heat_of()         精确，不动
        其余渠道           → corpus.lookup()  ← ★ 全项目唯一入口
                              ├ L1 语料源
                              ├ L2 菜名品类
                              ├ L3 门店类型
                              └ L4 弃权
    ★ 换语料源 / 换地图 / 增减渠道，只改环境变量，本函数不用动。
    """
    quota_of = quota_fn or allowance
    stores = []
    LAST_NOTES.clear()
    for pack in (get_packs() if packs is None else packs):
        official = getattr(pack, 'official', False)
        for s in pack.search_stores(lon, lat, radius):
            q = quota_of(s.get('distance') or 0)
            hint = _store_hint(s)
            dishes = []
            for name in pack.menu_of(s):
                if not prior.is_menu_item(name):
                    continue          # 品类词 / 喝的 / 甜的 / 食材 —— 不是「一道菜」
                                      # ★ 2026-10-15 统一：以前这里只判 is_category_word，
                                      #   与 render 的私有过滤不一致 → 同一条数据
                                      #   在网页上被滤、在 MCP 上没滤。见 prior.is_menu_item
                if official:
                    k = pack.heat_of(name)
                    lab, tier = (u'MCP 官方' if k is not None else None), 'L1'
                else:
                    r = corpus.lookup(name, hint, provider=corpus_provider)
                    if r:
                        k, lab, tier = r['kcal'], r['label'], r['tier']
                    else:
                        k, lab, tier = None, None, None
                dishes.append({'name': name, 'kcal': k,
                               'ok': (k is not None and k <= q),
                               'src': lab, 'tier': tier})
            stores.append({'id': s['id'], 'name': s['name'],
                           'cat': getattr(pack, 'name', ''),
                           'walk': s.get('distance'), 'quota': q,
                           # ★ 透传**归一化**字段（不要再透传服务商私有字段名）：
                           #   选店的象限均衡要 lon/lat；业态筛选/分层兜底要
                           #   industries + category_hint
                           'lon': s.get('lon'), 'lat': s.get('lat'),
                           'industries': list(s.get('industries') or []),
                           'category_hint': s.get('category_hint') or u'',
                           'dish_hints': list(s.get('dish_hints') or []),
                           # ★ 这一行的门店来自哪：'mcp'（官方实时）/ 'snapshot'（本地快照）。
                           #   以前区分不了 —— 拿快照冒充实时就是这么发生的。
                           'src': s.get('src') or u'',
                           'is_mcd': ('麦当劳' in (s.get('name') or '') or
                                      official),
                           'dishes': dishes})
        LAST_NOTES[_short(pack)] = getattr(pack, 'last_note', None)
    return stores


if __name__ == '__main__':
    from walkkcal import geo
    print(describe())
    print(u'当前地图 provider：%s' % geo.get().name)
    print(u'当前语料 provider：%s' % corpus.get().name)
    print(u'麦当劳实时路径：%s' % (u'开（WALKKCAL_MCD_LIVE=1）'
                                  if config.enable_live() else u'关（默认，读快照）'))
    print(config.describe())
    rows = collect()
    print()
    print(u'── 数据来源 ──')
    for name, why in notes().items():
        print(u'注意｜%s：%s' % (name, why))      # ★ 「想走实时却没走成」要看得见
    if not notes():
        print(u'（没有回退：每条渠道都走了它该走的路）')
    _srcs = {}
    for r in rows:
        _srcs[r.get('src') or u'—'] = _srcs.get(r.get('src') or u'—', 0) + 1
    if _srcs:
        print(u'门店行来源：' + u' ｜ '.join(u'%s %d 家' % kv
                                            for kv in sorted(_srcs.items())))
    n_known = n_total = 0
    tiers = {}
    for r in rows:
        for d in r['dishes']:
            n_total += 1
            if d['kcal'] is not None:
                n_known += 1
            tiers[d.get('tier')] = tiers.get(d.get('tier'), 0) + 1
    print(u'聚合 %d 家门店' % len(rows))
    if n_total:
        print(u'菜名 %d 条：可算 %d (%.0f%%)  分层分布 %s'
              % (n_total, n_known, 100.0*n_known/n_total, tiers))
    for r in rows[:6]:
        known = [d for d in r['dishes'] if d['kcal']]
        fit = len([d for d in known if d['ok']])
        print(u'  %-26s %5sm 额度%4d 能吃 %d/%d'
              % (r['name'][:24], r['walk'], r['quota'], fit, len(known)))
