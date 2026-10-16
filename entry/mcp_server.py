# -*- coding: utf-8 -*-
u"""MCP 入口 —— 把walk-kcal 内核暴露为标准 MCP Server（stdio + JSON-RPC 2.0）

设计原则
────────
1. **零第三方依赖**：纯标准库实现 MCP 协议，`python entry/mcp_server.py` 即跑。
2. **共用内核**：额度算法/数据源包/判档全部来自 `walkkcal` / `packs`。
3. **MCP 调 MCP**：本 Server 内部会去调麦当劳 MCP（第二跳），
   把官方营养表 + 本地语料库估算**融合**成一个统一工具。

架构
────
    Skill / Bot / 任何 MCP 客户端
          ↓ tools/call
    walkkcal-mcp（本文件，MCP Server）
          ↓ tools/call（第二跳）
    麦当劳 MCP @ https://mcp.mcd.cn

工具
────
    walk_allowance    给定距离，算卡路里额度（纯本地，不联网）
    nearby_food       给定坐标，返回附近门店 + 每道菜能不能吃下
    what_can_i_eat    额度内推荐（跨数据源）
    explain_concept   科普 NEAT / MET（不消耗任何调用）
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from walkkcal import allowance, ALLOWANCE_BASE, KCAL_PER_80M  # noqa
from walkkcal import config  # noqa
from walkkcal import filter as flt  # noqa
from walkkcal import hints, reply, select  # noqa
from packs import registry  # noqa

# ★ 配置/密钥：环境变量 > WALKKCAL_CONFIG > config.local.json（见 walkkcal/config.py）。
#   Key 只在这些地方，**不写在 .py 里**。
config.load()

PROTOCOL = '2025-06-18'
DEFAULT_N = select.DEFAULT_N
DEFAULT_RADIUS = 1500


# ── 共享流水线：抓店 → 截断 → 选店 → 文案 → 选项 ────────────
def _pipeline(lon=None, lat=None, quota=None, industry=None,
              radius=DEFAULT_RADIUS, page=0, n=DEFAULT_N):
    u"""★ 三个入口共用的一条流水线（Skill / MCP / Bot 都调它）"""
    rows = registry.collect(lon, lat, radius)
    if quota:
        q = int(quota)
        for r in rows:
            r['quota'] = q
            for d in r['dishes']:
                if d['kcal'] is not None:
                    d['ok'] = d['kcal'] <= q
    rows = reply.trim_dishes(rows)
    origin = (lon, lat) if (lon and lat) else None
    cands = select.to_candidates(rows, origin)
    batch, meta = select.pick(cands, n, page, industry)
    nx = hints.next_actions(meta, rows, radius=radius)
    return rows, batch, meta, nx


def _dishes_of(r):
    return [{'name': d['name'], 'kcal': d['kcal'],
             'fit': bool(d.get('ok')), 'source': d.get('src')}
            for d in r['dishes'] if d['kcal'] is not None]


# ★ 门店行实际来自哪 —— **不许写死**（2026-10-16）
_SRC_LABEL = {'mcp': u'麦当劳 MCP（官方实时）',
              'snapshot': u'本地快照（examples/<场景>/*.json）'}


def _sources_of(rows):
    u"""按**实际**取到的 `src` 归纳来源，而不是照文档抄一句。

    以前这里写死 `['麦当劳 MCP', '高德 POI + 自建语料库']` —— 万一麦当劳那条
    回退成了快照，这句就是假话（正是「静默用快照冒充实时」的另一面）。
    """
    seen = []
    for r in rows:
        s = r.get('src') or u''
        if s not in seen:
            seen.append(s)
    if not seen:
        return []
    return [_SRC_LABEL.get(s, s) or u'未标注' for s in seen]


def _honesty(notes):
    u"""给 Agent 的一句「这次有没有回退」—— 让它可以照实转述给用户。"""
    if not notes:
        return u'没有回退：每条渠道都走了它该走的路。'
    return (u'★ 本次有回退（请如实告诉用户，不要省略）：'
            + u'；'.join(u'%s：%s' % kv for kv in notes.items()))


# ── 工具实现 ────────────────────────────────────────────────
def tool_walk_allowance(args):
    dist = float(args.get('distance_m', 500))
    quota = allowance(dist)
    return {
        'distance_m': dist,
        'base': ALLOWANCE_BASE,
        'walk_bonus': quota - ALLOWANCE_BASE,
        'quota': quota,
        'formula': u'额度 = %d + (距离//80+1)×%d' % (ALLOWANCE_BASE, KCAL_PER_80M),
        'note': u'额度由用户设定，步行只做加法。',
    }


def tool_nearby_food(args):
    lon = args.get('longitude')
    lat = args.get('latitude')
    quota = args.get('quota')
    industry = args.get('industry') or None
    radius = int(args.get('radius_m') or DEFAULT_RADIUS)
    page = int(args.get('page') or 0)

    rows, batch, meta, nx = _pipeline(lon, lat, quota, industry, radius, page)
    q = int(quota) if quota else (batch[0]['quota'] if batch else ALLOWANCE_BASE)

    stores = []
    for r in batch:
        known = [d for d in r['dishes'] if d['kcal'] is not None]
        stores.append({'name': r['name'], 'walk_m': r['walk'],
                       'quota': r['quota'],
                       'industries': flt.classify(r),
                       'fit_count': len([d for d in known if d.get('ok')]),
                       'known_count': len(known),
                       'dishes': _dishes_of(r)})

    out = reply.build(q, batch, meta, nx, pool_size=len(rows))
    _notes = out['meta'].get('source_notes') or {}
    out.update({
        'stores': stores,
        'count': len(stores),
        # ★ 2026-10-15：`meta.mode` 是「给**最终用户**的建议形态」——
        #   poster = 这轮适合出一张图做比较；text = 单店/单事实，文字就够。
        #   本入口**不返回图片**（MCP 规范也建议图片块只给 UI 看，
        #   一张 1080p 图 ≈12,000 token）；要出图由外壳自己渲染。
        'image_note': u'本入口不返回图片；meta.mode 是给最终用户的建议形态'
                      u'（poster=适合出图比较，text=纯文字即可）。',
        'sources': _sources_of(rows),
        'source_notes': _notes,
        'honesty': _honesty(_notes),
        'usage_note': u'想细化就再调一次本工具：industry=快餐/小吃/西餐…、'
                      u'radius_m=500、page=1（换一批）。'
                      u'next 里已给出建议的下一步参数。'
                      u'\n★ 若 `source_notes` 非空，说明有渠道回退到了本地快照 —— '
                      u'`text` 里已经写了这句，照实转述即可，别删。',
    })
    return out


def tool_what_can_i_eat(args):
    quota = int(args.get('quota', ALLOWANCE_BASE))
    industry = args.get('industry') or None
    radius = int(args.get('radius_m') or DEFAULT_RADIUS)
    # ★ 2026-10-16 修：这里以前**写死 `_pipeline(None, None, …)`** ——
    #   调用方传了经纬度也被丢掉，于是麦当劳实时路径永远报「没有坐标 ——
    #   翻译不出 city/keyword」，**就算 token 有效也照样回退**。
    #   实测：显式传 longitude/latitude 仍然回退，才查出是这一行。
    lon = args.get('longitude')
    lat = args.get('latitude')
    rows, batch, meta, nx = _pipeline(lon, lat, quota, industry, radius, 0)

    fits = []
    for r in batch:
        for d in r['dishes']:
            if d['kcal'] is not None and d['kcal'] <= quota:
                fits.append({'dish': d['name'], 'kcal': d['kcal'],
                             'store': r['name'], 'walk_m': r['walk']})
    fits.sort(key=lambda x: -x['kcal'])

    out = reply.build(quota, batch, meta, nx, pool_size=len(rows))
    _notes = out['meta'].get('source_notes') or {}
    out.update({
        'quota': quota, 'fits': fits[:20], 'total': len(fits),
        'note': u'只列额度内；算不出热量的菜不在结果里。',
        'sources': _sources_of(rows),
        'source_notes': _notes,
        'honesty': _honesty(_notes),
        'usage_note': u'想只看某个业态？再调一次并传 industry=快餐/西餐/…',
    })
    return out


def tool_explain(args):
    topic = args.get('topic', 'neat')
    if topic == 'neat':
        return {'title': 'NEAT',
                'essence': u'非运动活动消耗（走路/站立/通勤/做饭），'
                           u'占每日总消耗 15-30%，个体差异最大的一项。',
                'evidence': u'Levine 1999/2005：同样过食 1000 卡×8 周，'
                            u'增脂相差 10 倍，唯一解释变量是 NEAT。'}
    if topic == 'met':
        return {'title': 'MET',
                'key_fact': u'平地走 1 公里，从极慢到极快，总消耗只从 81→102 卡。',
                'why': u'消耗 = MET×体重×时长，速度升的同时时长在降，两者抵消。',
                'what_helps': [u'走更远（线性，唯一稳定有效）', u'上坡', u'负重']}
    return {'error': 'topic 只支持 neat / met'}


# ── MCP 协议 ────────────────────────────────────────────────
# ★★ 重要：inputSchema 里的 description **不是给程序看的，是给 Agent 看的**。
#   Agent 靠它判断「用户这句话该填哪个参数」。所以每句都要写清
#   「用户说什么 → 填这个参数」，而不是只写参数名的字面意思。
_IND_ENUM = flt.INDUSTRIES

TOOLS = [
    {'name': 'walk_allowance',
     'description': u'卡路里额度核算。给定步行距离，算出可用的本餐额度。'
                    u'当用户问"走过去能吃多少""额度还剩多少"时使用。',
     'inputSchema': {'type': 'object', 'properties': {
         'distance_m': {'type': 'number', 'description': u'步行距离（米）'}}}},
    {'name': 'nearby_food',
     'description': u'附近门店 + 每道菜能不能吃下。跨数据源（麦当劳 MCP + 周边本地餐厅）。'
                    u'当用户问"附近有什么能吃的""现在吃什么""有什么推荐的"时使用。'
                    u'\n\n★ 用户若给出限定条件，填对应参数（不要自己过滤结果）：'
                    u'\n  · "只看快餐/附近有什么快餐/想吃西餐/来点小吃" → industry'
                    u'\n  · "不想走太远/就附近/500 米内" → radius_m=500'
                    u'\n  · "还有别的吗/换一批/再看看" → page 加 1'
                    u'\n不带任何参数调用也能返回合理结果（默认半径 1500 米、第 1 批）。'
                    u'\n返回值里的 next 字段是建议的下一步参数，可直接采用。',
     'inputSchema': {'type': 'object', 'properties': {
         'longitude': {'type': 'number', 'description': u'用户所在经度（GCJ-02）'},
         'latitude': {'type': 'number', 'description': u'用户所在纬度（GCJ-02）'},
         'quota': {'type': 'number', 'description': u'本餐额度（卡）；不给则按距离自动算'},
         'industry': {'type': 'string', 'enum': _IND_ENUM,
                      'description': u'业态限定。用户说"只看快餐""附近有什么西餐"'
                                     u'"想吃日料"时填对应值。不填=不限。'},
         'radius_m': {'type': 'number',
                      'description': u'搜索半径（米），默认 1500。'
                                     u'用户说"别走太远""就近"时填 500。'},
         'page': {'type': 'number',
                  'description': u'批次号，从 0 开始。用户说"换一批""还有别的吗"时'
                                 u'把上次返回的 meta.page 加 1。'}}}},
    {'name': 'what_can_i_eat',
     'description': u'额度内能吃什么（跨数据源）。当用户问"我现在能点什么"'
                    u'"有什么便宜（低卡）的"时使用。'
                    u'\n\n用户给业态限定时填 industry；'
                    u'用户说"还有吗"时用 nearby_food 的 page 参数。',
     'inputSchema': {'type': 'object', 'properties': {
         'longitude': {'type': 'number',
                       'description': u'用户所在经度。**传了才能走麦当劳实时** —— '
                                      u'麦当劳 MCP 只吃文字地名，需要用它把坐标'
                                      u'翻译成 city+keyword；不传就只能用本地快照。'},
         'latitude': {'type': 'number',
                      'description': u'用户所在纬度，配 longitude 一起传。'},
         'quota': {'type': 'number', 'description': u'本餐额度（卡）'},
         'industry': {'type': 'string', 'enum': _IND_ENUM,
                      'description': u'业态限定，同 nearby_food。不填=不限。'},
         'radius_m': {'type': 'number', 'description': u'搜索半径（米），默认 1500'}}}},
    {'name': 'explain_concept',
     'description': u'科普 NEAT / MET 的含义与依据，不消耗任何 MCP 调用。',
     'inputSchema': {'type': 'object', 'properties': {
         'topic': {'type': 'string', 'enum': ['neat', 'met']}}}},
]

_DISPATCH = {
    'walk_allowance': tool_walk_allowance,
    'nearby_food': tool_nearby_food,
    'what_can_i_eat': tool_what_can_i_eat,
    'explain_concept': tool_explain,
}


def handle(msg):
    m = msg.get('method')
    if m == 'initialize':
        return {'protocolVersion': PROTOCOL,
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'walkkcal-mcp', 'version': '1.0.0'}}
    if m == 'tools/list':
        return {'tools': TOOLS}
    if m == 'tools/call':
        name = msg['params']['name']
        args = msg['params'].get('arguments', {})
        try:
            fn = _DISPATCH.get(name)
            r = fn(args) if fn else {'error': 'unknown tool ' + name}
            return {'content': [{'type': 'text',
                                 'text': json.dumps(r, ensure_ascii=False, indent=2)}]}
        except Exception as e:
            return {'content': [{'type': 'text',
                                 'text': json.dumps({'error': str(e)},
                                                    ensure_ascii=False)}],
                    'isError': True}
    return {}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if 'id' not in msg:
            continue
        out = handle(msg)
        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg['id'],
                                     'result': out}) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
