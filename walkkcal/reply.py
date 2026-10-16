# -*- coding: utf-8 -*-
u"""reply —— ★ 统一返回契约：一次调用返回 `text / image / meta / next` 四件套

设计立场（见 docs/交互模式.md）
──────────────────────────────
**图是结论，话是推理，选项是下一步。缺任何一个，对话就断了。**

现实里最常见的错误是「只返回一张图」—— 用户看完不知道下一步能干嘛，
对话就在那里断掉了。

所以三入口（Skill / MCP / Bot）共用这一份契约：

    {
      "text":  u'额度 466 卡。附近 218 家挑了 15 家，最合适的是「…」。要只看快餐吗？',
      "image": 'out/k11.png',          # 可选，纯文本渠道可省
      "meta":  {'total':218,'shown':15,'page':0,'pages':15,'keyword':u'全部'},
      "next":  [{'label':u'只看快餐','params':{...}}, ...]
    }

三入口怎么渲染
──────────────
    Skill → Agent 读 text 自己组织语言，image 作附件
    MCP   → 原样返回（Agent 自己决定怎么用）
    Bot   → caption=text，photo=image，next 变成按钮

一句话的三段式
──────────────
    「结论 ｜ 依据 ｜ 下一步」
    额度 466 卡（走 308 米换来 16 卡）。
    附近 218 家里挑了 15 家，最合适的是「河南酸菜红烧牛肉面」——走 308 米，5 道都在额度内。
    要只看快餐吗？

★ 第四段（可选，默认不出现）：「数据来源说明」
    **只在「本想走实时、实际用了本地快照」时才加一句**（见 `line_source_notes`）。
    正常跑（默认离线快照）一个字都不会多。
"""

from __future__ import annotations

# ── 直接执行本文件时，把仓库根加入 sys.path（打包导入时无副作用）──
import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from walkkcal import hints  # noqa: E402

# ★ 每店最多展示几道菜（与长图脚注「每店最多 8 道」保持一致）
DISH_MAX = 8


def trim_dishes(rows, max_n=DISH_MAX):
    u"""把每家店的菜截到 max_n 道 —— **必须在选店之前做**。

    否则 fit_n 会把没展示的菜也算进去（实测出现过「34 道在额度内」）。
    按高德 tag 的推荐顺序保留前 N 道，与长图口径一致。
    """
    for r in rows:
        ds = r.get('dishes') or []
        if len(ds) <= max_n:
            continue
        # 与长图同规则：**全超额度时**，末 2 位换成额度内的菜
        #   ★ 但若压根没有额度内的菜（fit 为空），不能换 —— 直接截断，
        #     否则会平白少给 2 道（实测：12 道全超 → 只剩 6 道而不是 8 道）
        all_over = all(d.get('kcal') is not None and not d.get('ok') for d in ds)
        fit = [d for d in ds if d.get('kcal') is not None and d.get('ok')]
        if all_over and len(fit) >= 2:
            r['dishes'] = ds[:max_n - 2] + fit[:2]
        else:
            r['dishes'] = ds[:max_n]
    return rows


def _top_industries(batch, top=2, min_n=2):
    u"""本批里最多的 1~2 个业态 —— 把「挑了 15 家」说得更具体。

    ★ min_n=2：只出现 1 家的业态是噪音，不如不说。
    """
    try:
        from walkkcal import filter as _f
    except Exception:
        return []
    cnt = {}
    for s in batch:
        for ind in _f.classify(s):
            cnt[ind] = cnt.get(ind, 0) + 1
    rows = sorted(cnt.items(), key=lambda x: -x[1])
    # ★ 2026-10-15：把数字放进括号 —— 「10 家中餐本帮」拼进句子会读成
    #   「这一批里，10 家中餐本帮」（数字贴着「里，」很别扭）；
    #   写成「中餐本帮（10 家）」才是人的说法。
    return [u'%s（%d 家）' % (k, v) for k, v in rows[:top] if v >= min_n]


def best_store(batch):
    u"""本批里最值得推荐的那家：能吃最多 → 最近

    ★ 与 select.py 的排序口径一致，避免「排序说一套、文案说另一套」。
    """
    if not batch:
        return None
    return sorted(batch, key=lambda s: (-(s.get('fit_n') or 0),
                                        s.get('walk') or 0))[0]


def line_quota(quota, walk_m=None):
    u"""第一段：额度（+ 步行换来的增量）

    ★ 口语化（2026-10-15）：说「按 350 卡给你挑」，而不是「额度 350 卡。」
      用户在对话里说的是「我今天只想吃 350 卡左右的午餐」，
      回答就该**接着他的话往下说**，而不是报一个字段值。
    """
    if walk_m:
        mins = int(walk_m) // 80 + 1
        return u'按 %d 卡给你挑。这趟走 %d 米，多换 %d 卡。' % (
            quota, walk_m, mins * 4)
    return u'按 %d 卡给你挑。' % quota


def fit_dishes(store):
    u"""这家店里**在额度内**的那些菜。

    ★ `ok` 由调用方（pipeline）预置；没预置时按 `quota` 现算 ——
      免得文案层因为少一个字段就静默不说（「少了什么」比「说错什么」更难发现）。
    """
    q = store.get('quota') or 0
    out = []
    for d in store.get('dishes', []):
        if d.get('kcal') is None:
            continue
        ok = d.get('ok')
        if ok is None:
            ok = d['kcal'] <= q
        if ok:
            out.append(d)
    return out


def official_store(batch, exclude=None):
    u"""本批里**有官方热量表**的那家（麦当劳）。

    ★ 为什么要单独说一句（2026-10-15）：
      它是全链路**唯一**拿到官方营养值的渠道（MCP `list-nutrition-foods` 快照），
      其余店的热量都是分层估算（L1 菜谱推算 / L2 品类估算 / L3 同类兜底）。
      推荐里如果不提，等于把「数据最准的那一家」藏起来了。
    """
    for s in batch:
        if s is exclude:
            continue
        hot = fit_dishes(s)
        if hot and any(d.get('src') == u'MCP 官方' for d in hot):
            return s
    return None


def line_result(batch, meta, pool_size=None):
    u"""第二段：多少家里挑了哪些 + 最推荐哪家 + 官方热量那家

    ★ 数字要如实：附近 N 家 → 进入候选 M 家 → 本批列 K 家。
      M < N 说明「有店被规则选掉了」（不是算不出），必须在文案里讲清。
    ★ 口语化（2026-10-15）：去掉「进入候选」「封顶」「麦当劳最多占 1/3」
      这类**内部术语** —— 用户不需要知道我们的实现，只需要知道**发生了什么**。
      数字一个不少，说法换成人的说法。
    """
    kw = meta.get('keyword') or u'全部'
    total = meta.get('total', len(batch))          # 进入展示的候选（已封顶）
    cand = meta.get('candidates') or total         # 封顶前候选
    shown = meta.get('shown', len(batch))
    pages = meta.get('pages', 1)
    page = meta.get('page', 0) + 1

    # ★ 第 63 轮：隐去「只卖喝的」要说出来 —— 否则用户会问「为什么没有星巴克」
    _hid = meta.get('excluded_drink') or 0
    _hid_txt = (u'（另有 %d 家只卖饮品或甜点的，先没算进来）' % _hid
                if _hid else u'')

    if kw not in (u'全部', None):
        head = u'附近 %d 家%s' % (total, kw) + _hid_txt
    elif pool_size and pool_size > total:
        head = u'附近 %d 家餐厅，我挑了 %d 家%s' % (pool_size, total, _hid_txt)
    elif cand > total:
        head = u'%d 家里选出 %d 家%s' % (cand, total, _hid_txt)
    else:
        head = u'共 %d 家%s' % (total, _hid_txt)

    if pages > 1:
        text = u'%s，先看第 %d 批（共 %d 批）。' % (head, page, pages)
    else:
        text = u'%s。' % head

    # ★ 已筛业态时不再报业态分布 —— 那会重复且引入噪音
    #   （筛「西餐」时冒出「7 家日料」，因为分类是多标签的）
    if kw in (u'全部', None):
        inds = _top_industries(batch)
        if inds:
            text += u'这一批主要是%s。' % u'、'.join(inds)

    b = best_store(batch)
    if b:
        _fit = b.get('fit_n') or 0
        if _fit > 0:
            text += u'最推荐「%s」：走 %d 米，有 %d 道菜在额度内。' % (
                b['name'], b.get('walk') or 0, _fit)
        else:
            text += u'最近的是「%s」，走 %d 米。' % (
                b['name'], b.get('walk') or 0)

    # ★ 官方热量那家单独说一句（见 official_store 的理由）
    _off = official_store(batch, exclude=b)
    if _off:
        _ok = fit_dishes(_off)[:3]
        if _ok:
            text += u'「%s」也在这一批：走 %d 米，它家的热量是官方给的实数' \
                    u'（别的店只能估算）—— %s，都在额度内。' % (
                        _off['name'], _off.get('walk') or 0,
                        u'、'.join(u'%s %d 卡' % (d['name'], d['kcal'])
                                   for d in _ok))
    return text


# ★ 选项（第一人称）→ 回问句（第二人称）
#   同一件事，从谁的口里说出来不一样：
#   按钮是「我想看看别的」（用户会说的话），回问是「要我再翻一批给你看看吗？」
#   key 用 kind 而不是 label —— 文案改了很多轮，kind 一次没改过。
_ASK = {
    'page':   u'要我再翻一批给你看看吗？',
    'radius': u'不想走太远吗？',
    'reset':  u'要看看全部的吗？',
}


def line_next(next_list):
    u"""第三段：把第一个选项变成一句问句（**用动机语言，不用参数语言**）"""
    if not next_list:
        return u''
    first = next_list[0]
    kind = first.get('kind')
    if kind in _ASK:
        return _ASK[kind]
    # 「只想吃中餐本帮」→「只想吃中餐本帮吗？」
    return u'%s吗？' % first['label']


# ── 数据来源说明（★ 2026-10-16：`last_note` 终于走到用户眼前）────────

def _source_notes(notes=None):
    u"""取「为什么没用实时数据」的原因表 `{渠道名: 原因}`。

    `notes=None`  → 回 `packs.registry` 最近一次 `collect()` 留下的；
    `notes={...}` → 原样用（测试注入）；
    两者都为空 → `{}`。
    """
    if isinstance(notes, dict):
        return {k: v for k, v in notes.items() if v}
    if notes is not None:
        return {}
    try:
        from packs import registry
        return dict(registry.notes())
    except (Exception, SystemExit):
        # registry 也读不到（例如只 import 了 reply）→ 不因此报错，
        # 只是这一轮没有来源说明可说。
        return {}


def line_source_notes(notes=None):
    u"""★ 第四段（可选）：数据来源说明 —— 只在「本想走实时、实际用了快照」时出现。

    为什么必须有这一句
    ──────────────────
    `McdPack` 里曾有一条 `except Exception: pass`：调 MCP 失败就**无声地**
    改读本地快照。用户看到「麦当劳走 807 米」，以为那是官方实时门店，
    其实是一份旧存档（Trae 实测里宿主为此道歉，还把理由猜错了）。

    原因后来存进了 `registry.notes()` —— 但**存在 Python 变量里的东西，
    用户看不见，等于还是静默的**。所以最后一步必须落在**文案**上：
    调用方（三入口）什么都不用做，只要用 `build()`。

    ★ 保持安静：默认配置（`registry` 里 `McdPack(offline=True)`）是**设计如此**，
      不算回退 → 返回空串，正文一个字都不变。
    """
    n = _source_notes(notes)
    if not n:
        return u''
    parts = [u'%s：%s' % (name, why) for name, why in n.items()]
    return (u'\nⓘ 数据来源：这次没能走实时，用的是本地快照 —— '
            + u'；'.join(parts) + u'。')


MODE_TEXT = 'text'
MODE_POSTER = 'poster'


def choose_mode(batch=None, meta=None, store=None):
    u"""★★ 输出形态判据（2026-10-15）。

    > **需要同时比较 ≥2 个对象 → 出图；只有一个对象或一个事实 → 纯文字。**

    为什么要有这条：契约里 `image` **本来就是可选**（`build(..., image=None)`），
    MCP 入口**本来也不出图**，`describe_store()` 也早就是纯文字模式 ——
    但「什么时候该出图」以前一处都没写，完全取决于入口和手感。

    外部依据：
      · **ChatGPT 的「智能界面」**（GPT-6）：核心是**由模型判断何时需要界面** ——
        「该给图表时只给文字，用户觉得不方便；**只需一句话时却弹出一整套界面，
        又多此一举**」。这正是我们缺的那条判据。
      · **MCP 规范**：图片块建议加 `annotations.audience=["user"]`（只给界面看），
        因为一张 1080p 图的 base64 约 **12,000 token**，会污染后续每一轮的上下文
        —— 图片是**给人的交付物**，不是给 Agent 的返回内容。
      · 认知负荷研究：视觉的优势场景是**信息量大 + 需要比较**（并行扫视），
        单点事实用图属于过度交付。

    ★ 三档：
      `text`   用户已收敛（点名一家店）或候选只有 0~1 家 → 纯文字
      `poster` 要比较（零参数触发 / 换一批 / 换业态）→ 图 + 一句话 + 选项
      `poster` 要带走或转发 → 图（脚注自带入口，是唯一能脱离对话传播的形态）

    ★ 不要反向过度：图是「转发」场景的唯一解，别为了省事把所有场景降级成文字。
    """
    if store is not None:
        return MODE_TEXT                 # 单店详情：一个对象，出图是过度交付
    n = len(batch or [])
    if meta:
        try:
            n = min(n, int(meta.get('shown', n)))
        except (TypeError, ValueError):
            pass
    return MODE_POSTER if n >= 2 else MODE_TEXT


def build(quota, batch, meta, next_list=None, pool_size=None,
          walk_m=None, image=None, mode=None, notes=None):
    u"""组装统一返回。三入口都调这一个函数。

    mode  : `text` / `poster`；不传则按 `choose_mode()` 自动判定，并写进 meta。
    notes : `{渠道名: 原因}` —— 「本想走实时却回退了」的原因。不传则自动回
            `packs.registry.notes()`（见 `line_source_notes`）。

    ★ 四段式：**结论 ｜ 依据 ｜ 来源 ｜ 下一步**。
      第三段（来源）默认不出声；只有真的回退过才出现。
    """
    n = _source_notes(notes)
    text = (line_quota(quota, walk_m) +
            line_result(batch, meta, pool_size) +
            line_source_notes(n) +
            line_next(next_list or []))
    m = dict(meta)
    m['mode'] = mode or choose_mode(batch, meta)
    # ★ 机器可读的同一件事：Agent 转述文案时若嫌长，可以直接读这个字段。
    m['source_notes'] = n
    return {
        'text': text,
        'image': image,
        'meta': m,
        'next': list(next_list or []),
    }


def describe_store(store, quota=None):
    u"""第三轮「收敛」用：单店文字详情（**不必再出图**）

    ★ 口语化（2026-10-15）：说「这顿按 350 卡算 / 能点的有 3 道」，
      而不是「额度 350 卡 / 额度内 3 道」—— 后者是字段名，不是人话。
    """
    q = quota or store.get('quota') or 0
    dishes = [d for d in store.get('dishes', []) if d.get('kcal') is not None]
    fit = [d for d in dishes if d.get('kcal') <= q]
    over = [d for d in dishes if d.get('kcal') > q]
    lines = [u'「%s」走 %d 米，这顿按 %d 卡算。'
             % (store['name'], store.get('walk') or 0, q)]
    if fit:
        lines.append(u'能点的有 %d 道：' % len(fit) +
                     u'、'.join(u'%s %d 卡' % (d['name'], d['kcal'])
                                for d in fit))
    else:
        lines.append(u'%d 卡以内暂时没有能点的。' % q)
    if over:
        lines.append(u'超出这顿的 %d 道：' % len(over) +
                     u'、'.join(u'%s %d 卡' % (d['name'], d['kcal'])
                                for d in over))
    if not dishes:
        lines.append(u'这家店的推荐菜暂时算不出热量。')
    # ★ 官方热量要标出来 —— 这是我们唯一「不是估算」的地方
    if any(d.get('src') == u'MCP 官方' for d in dishes):
        lines.append(u'这几道的热量来自麦当劳官方营养表（别的店只能估算）。')
    return u'\n'.join(lines)


if __name__ == '__main__':
    import io
    import json
    import os
    import re
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from walkkcal import corpus as _corpus
    from walkkcal import filter as F  # noqa
    from walkkcal import prior as _prior
    from walkkcal import select as S
    from walkkcal import snapshot as snap

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    K11 = os.path.join(root, 'data', 'k11')
    # ★ 通过 snapshot 层读（唯一认快照格式的地方）
    doc = snap.read_detail(os.path.join(K11, 'detail.json'))
    pool = doc['mcd'] + doc['local']

    def build_row(s, quota=466):
        hint = {'industries': list(s.get('industries') or []),
                'text': u';'.join([s.get('category_hint') or u'',
                                   s.get('name') or u'']),
                'dish_names': list(s.get('dish_hints') or [])}
        ds = []
        for c in (s.get('dish_hints') or []):
            if _prior.is_category_word(c):
                continue
            r = _corpus.lookup(c, hint)
            if r:
                ds.append({'name': c, 'kcal': r['kcal'],
                           'ok': r['kcal'] <= quota,
                           'src': r['label'], 'tier': r['tier']})
        return {'name': s['name'], 'walk': int(s.get('distance_m') or 0),
                'quota': quota,
                'lon': float(s.get('lon') or 0),
                'lat': float(s.get('lat') or 0),
                'industries': list(s.get('industries') or []),
                'category_hint': s.get('category_hint') or u'',
                'dishes': ds}

    rows = trim_dishes([build_row(s) for s in pool])
    cands = S.to_candidates(rows, origin=(121.47, 31.2209))

    print(u'── 第 1 轮：零参数触发 ──')
    batch, meta = S.pick(cands, 15, 0, None)
    nx = hints.next_actions(meta, rows, radius=1500)
    print(build(466, batch, meta, nx, pool_size=len(pool), walk_m=308)['text'])
    print(u'   按钮：' + u' ｜ '.join(x['label'] for x in nx))

    print(u'\n── 第 2 轮：用户点「只看西餐」──')
    batch, meta = S.pick(cands, 15, 0, u'西餐')
    nx = hints.next_actions(meta, None, radius=1500)
    print(build(466, batch, meta, nx, pool_size=len(pool))['text'])
    print(u'   按钮：' + u' ｜ '.join(x['label'] for x in nx))

    print(u'\n── 第 3 轮：收敛到单店（纯文字，不再出图）──')
    b = best_store(batch)
    if b:
        print(describe_store(b, 466))
