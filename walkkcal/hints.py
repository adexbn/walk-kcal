# -*- coding: utf-8 -*-
u"""hints —— 动态生成「下一步可以问什么」（三个入口共用）

为什么需要这个模块
──────────────────
对话是**渐进细化**的，而长图是**一次性固化**的 —— 这是本项目的根本张力。

解法：**长图是对话某一刻的快照，而对话要教会用户下一步怎么问。**

但「下一步」不能写死。用户不知道有哪些业态可选；如果罗列全部参数
（`industry=快餐|小吃|西餐|…, radius=500, page=0..N`），没人会读。

所以：**从当前这批数据里算出最值得推荐的 2-3 个方向**。

设计原则
────────
1. **给具体选项，不给参数表** —— 「只想吃快餐」而不是「industry=快餐」
2. **用动机的语言，不用参数的语言** —— 「不想走太远」而不是「半径缩小到 500 米」
   （走远是**卖点**，不该被说成「限制」）
   ★★ 2026-10-15 改：文案一律**第一人称口语** —— 选项写的是**用户会说的话**，
      不是系统给的动作名。「换一批」→「我想看看别的」；「别走太远」→「不想走太远」；
      「只看X」→「只想吃X」；「看全部」→「都看看」。
      理由：这些标签会直接变成按钮文字（Bot inline keyboard）和回复里的选项，
      写成动作名是**机器的口吻**；写成人话，用户是「接着自己的话说」，
      而不是「执行一个命令」。`kind`/`params` 不变，纯文案层改动。
3. **动态** —— K11 咖啡多就提示「只看咖啡」；学校附近就提示「只看快餐」
4. **可发现但不强制** —— 基础调用零参数也能出结果，细化是渐进的
三个入口怎么用它
────────────────
    Skill → 写进回复文本（Agent 自己组织语言）
    MCP   → 放进返回值 next_hints（Agent 读 schema 就知道了）
    Bot   → 变成 inline keyboard 按钮（**零打字成本，转化率最高**）
"""

from __future__ import annotations

# 走多远算「有点远了」——超过就提示「不想走太远」
FAR_ENOUGH_M = 600
RECENT_RADIUS = 500
# 某业态至少这么多家才值得单独提示
MIN_FOR_INDUSTRY_HINT = 3
MAX_HINTS = 3


def next_actions(meta, pool=None, counts=None, radius=None):
    u"""生成下一步建议。

    meta   : select.pick 返回的 meta（含 total/page/pages/keyword）
    pool   : 当前候选门店列表（用来算「最值得提示的业态」）
    counts : 业态计数（filter.counts 的结果）；不给就从 pool 现算
    radius : 当前搜索半径（米），用于判断要不要提示「不想走太远」

    返回 [{'label': str, 'kind': str, 'params': {...}}]
      kind: 'page' | 'industry' | 'radius' | 'reset'
      params: 可直接合并进下一次调用的参数字典
    """
    out = []
    kw = meta.get('keyword') or u'全部'

    # ① 还有下一批 → 我想看看别的
    if meta.get('pages', 1) > 1:
        nxt = (meta.get('page', 0) + 1) % meta['pages']
        out.append({'label': u'我想看看别的', 'kind': 'page',
                    'params': {'page': nxt}})

    # ② 当前没筛业态 → 提示「只想吃 X」（挑最多的那个业态）
    if kw in (u'全部', None) and pool:
        if counts is None:
            from walkkcal import filter as _f
            counts = _f.counts(pool)
        best = [(k, v) for k, v in counts.items()
                if v >= MIN_FOR_INDUSTRY_HINT]
        if best:
            best.sort(key=lambda x: -x[1])
            top = best[0][0]
            out.append({'label': u'只想吃%s' % top, 'kind': 'industry',
                        'params': {'industry': top}})

    # ③ 已筛业态 → 提示「都看看」
    if kw not in (u'全部', None):
        out.append({'label': u'都看看', 'kind': 'reset',
                    'params': {'industry': None, 'page': 0}})

    # ④ 走太远了 → 提示「不想走太远」
    if radius and radius > RECENT_RADIUS and kw in (u'全部', None):
        out.append({'label': u'不想走太远', 'kind': 'radius',
                    'params': {'radius_m': RECENT_RADIUS, 'page': 0}})

    return out[:MAX_HINTS]


def as_text(hints, prefix=u'还可以这样问'):
    u"""给 Skill / 纯文本场景用：拼成一行人话"""
    if not hints:
        return u''
    return u'%s：%s' % (prefix, u' · '.join(h['label'] for h in hints))


if __name__ == '__main__':
    import io
    import json
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from walkkcal import filter as F
    from walkkcal import select as S
    from walkkcal import snapshot as snap

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = os.environ.get('WALKKCAL_DATA') or 'data/k11'
    if not os.path.isabs(data):
        data = os.path.join(root, data)
    # ★ 通过 snapshot 层读（唯一认快照格式的地方）
    pool = snap.read_stores(os.path.join(data, 'stores.json'))['all']
    cands = [{'name': s['name'], 'quad': i % 4,
              'walk': s.get('distance_m') or 0,
              'fit_n': 1, 'is_mcd': s['is_mcd'],
              'industries': list(s.get('industries') or []),
              'category_hint': s.get('category_hint') or u''}
             for i, s in enumerate(pool)]

    for kw, page in [(None, 0), (u'西餐', 0)]:
        b, m = S.pick(cands, 15, page, kw)
        h = next_actions(m, pool, radius=1500)
        print(u'【%s 第%d批】%d 家' % (m['keyword'], m['page'] + 1, m['shown']))
        for x in h:
            print(u'    %-10s (%s) %s' % (x['label'], x['kind'], x['params']))
        print(u'    → 文本：%s' % as_text(h))
