# -*- coding: utf-8 -*-
"""
批量版 dish_heat：把 N 道菜合并成一次语料库扫描。
原实现每道菜 open() 一遍 1.9GB JSONL —— 249 道菜就是 249 遍，必须合并。

用法:
    bh = batch_dish_heat([u'油爆虾', u'奥灶面', ...], index, corpus_path)
    bh[u'油爆虾'].median
"""
import io, json, os, sys, statistics, collections

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from walkkcal import core
from walkkcal import portion
from walkkcal import quality_gate as QG

# 常见做法前缀/后缀（剥掉后用于「同菜不同做法」的宽松匹配）
_COOK_WORDS = (
    u'香辣', u'蒜蓉', u'麻辣', u'清蒸', u'红烧', u'干锅', u'白灼', u'葱烤',
    u'葱烧', u'炭烤', u'碳烤', u'百里香烤', u'泡椒', u'豉汁', u'糖醋',
    u'家常', u'秘制', u'五香', u'咖喱', u'黑椒', u'孜然', u'麻辣香锅',
    u'水煮', u'干煸', u'爆炒', u'小炒', u'大盘', u'特色', u'正宗',
)


def _strip_cook(name):
    u'''剥掉做法前缀，用于判断「是不是同一道菜的不同做法」

    ★ bug⑯：语料库索引里全是「香辣小龙虾」「蒜蓉烤生蚝」，
      没有裸名「小龙虾」。若按「首字相同」判重，x小龙 与 x蒜蓉 全部被拒。
      剥掉做法后两边相同，就该认成同一道菜。
    '''
    n = name
    for _ in range(3):                      # 「麻辣香锅」这类可能叠两层
        for w in _COOK_WORDS:
            if n.startswith(w):
                n = n[len(w):]
                break
        else:
            break
    return n or name


# ★ 2026-10-12 尾部子串回退的**禁用词**：
#   这些词虽然是合法菜名，但作为「尾部子串」命中时语义太泛/易张冠李戴，
#   宁可不认（弃权）也不要给错数。
_SUBSTR_TRAP = set(u"""米饭 饭 面 粉 汤 粥 冰淇淋 沙冰 拼盘 套餐 小食
奶茶 咖啡 饮料 果汁 沙拉 刺身 水果 蔬菜 小菜 例汤 双拼 三拼 儿童餐
四喜 三鲜 全家福 套餐 自助 双人 单人""".split())


def resolve_targets(names, corpus_index):
    """把用户给的菜名映射到语料库里的标准菜名"""
    out = {}
    for t in names:
        if t in corpus_index:
            out[t] = t
            continue
        cands = [n for n in corpus_index
                 if (t in n or n in t) and min(len(t), len(n)) >= 2]
        # ★ bug⑧：子串模糊匹配会把不相干的菜配错。
        #   实测「奥灶面」在语料库 0 条记录，却因为 '奥' in '奥利奥面包'
        #   被匹配到「奥利奥面包」→ 面包的 618 卡被当成面的热量显示出来。
        #   判据：菜名必须是「首字相同 + 至少一半字符重合」，否则不认。
        # ★ bug⑯：光按「首字相同」会漏掉**做法前缀**的正常情况 ——
        #   索引里全是「香辣小龙虾」「蒜蓉烤生蚝」，没有裸名「小龙虾」，
        #   首字分别是「香」「蒜」，全部被拒 → 整道菜算不出（实测 12 道全灭）。
        #   放宽为：剥掉常见做法前缀后，两边相同即可。
        def _ok(c):
            if c[0] == t[0]:
                return len(set(c) & set(t)) >= max(2, int(len(t) * 0.5))
            return _strip_cook(c) == _strip_cook(t)
        good = [n for n in cands if _ok(n)]
        if good:
            # 优先「语料库收录多」的，其次「名字更接近的」
            out[t] = max(good, key=lambda x: (corpus_index[x],
                                              -abs(len(x) - len(t))))
            continue
        # ★ 2026-10-12 第 32 轮新增：**尾部子串回退**。
        #   病：大饭店推荐菜带品牌/修饰前缀，语料库没有整名也没有裸名
        #     （桂花红烧肉 / 老上海熏鱼 / 瀑布土豆丝 / 椰岛芒果肠粉），
        #     而 ±首字规则要求首字相同 → 全判「算不出」→ 整店假绿。
        #   解：取 t 的**尾部子串**去语料库找（中餐菜名 =「做法/修饰 + 主料」，
        #     主料在尾部），取**最长且原样存在于语料库**的那个子串。
        #   防误配（奥灶面 trap）：子串必须 ≥2 字、必须原样是语料条目的菜名、
        #     且不得是已知歧义陷阱词（_SUBSTR_TRAP）。
        _best = None
        for _i in range(1, len(t)):
            _sub = t[_i:]
            if len(_sub) < 2:
                break
            if _sub in corpus_index and _sub not in _SUBSTR_TRAP:
                _best = _sub
                break                      # i 递增 → 首个命中的即最长尾子串
        out[t] = _best
    return out


def batch_dish_heat(names, corpus_index, corpus_path, max_recipes=40, verbose=False):
    """一次扫描，返回 {原名: DishHeat}"""
    target_map = resolve_targets(names, corpus_index)
    needed = set(v for v in target_map.values() if v)
    # 反向索引：标准名 -> [用户原名...]
    rev = collections.defaultdict(list)
    for k, v in target_map.items():
        if v:
            rev[v].append(k)

    acc = {v: {'mids': [], 'lows': [], 'highs': [], 'unknown': collections.Counter(),
               'cooks': collections.Counter(), 'n': 0, 'skip': 0,
               'skip_main': 0, 'skip_range': 0} for v in needed}
    # 主料未解析的原始热量，用于诊断（不参与计算）
    acc_dbg = {v: [] for v in needed}

    # 预筛：把用户原名+标准名都作为子串，避免每行做多目标匹配
    # 但一个长字符串里可能有多个目标，用 in 检查仍需逐个 —— 用 Aho-Corasick 代价高，
    # 这里改用「先取行内是否含任一目标」的低成本判断：整行通常只有 1~2 个菜名
    targets_sorted = sorted(needed, key=len, reverse=True)

    with io.open(corpus_path, encoding='utf-8', errors='ignore') as f:
        for line in f:
            # ★ 先做一次廉价的多子串筛：任一目标都不在行内就跳过
            hit = None
            for t in targets_sorted:
                if t in line:
                    hit = t
                    break
            if hit is None:
                continue
            # 命中的目标可能不止一个（菜名互相包含，如「臭豆腐」/「长沙臭豆腐」）
            hits = [t for t in targets_sorted if t in line]
            if len(hits) == 1:
                cand = hits
            else:
                try:
                    r0 = json.loads(line)
                except Exception:
                    continue
                d0 = r0.get('dish') or r0.get('name') or ''
                if d0 == 'Unknown' or not d0.strip():
                    d0 = r0.get('name') or ''
                cand = [t for t in hits if d0 == t] or hits

            try:
                r = json.loads(line)
            except Exception:
                continue
            nm = r.get('name') or ''
            # ★ bug⑱：语料库部分记录的 `dish` 字段是字符串 'Unknown'（无效值），
            #   而 `name` 字段完好。实测「焖肉米线」9 行记录 dish 全是 'Unknown'，
            #   配料齐全（猪后臀尖300g + 米线200g）却被 `d != t` 全部跳过。
            d = r.get('dish') or nm
            if d == 'Unknown' or not d.strip():
                d = nm

            for t in cand:
                if t not in acc:
                    continue
                a = acc[t]
                if a['n'] >= max_recipes:
                    continue
                # ★ bug⑰：这里要求 `d == t` 精确相等，把上面的模糊匹配抵消了 ——
                #   resolve_targets 把「小龙虾」映射到标准名「小龙虾」（收录 5 条），
                #   但语料库 dish 字段写的是「香辣小龙虾」，全部被 `d != t` 跳过，
                #   结果：匹配成功、扫描 0 条、返回算不出（实测 11 道全灭）。
                #   放宽为：精确相等，或剥掉做法前缀后相等。
                if d != t and _strip_cook(d) != _strip_cook(t):
                    continue
                # ★ bug⑭：规格信息（如「8英寸」）只存在于用户原名，标准化时被抹掉了。
                #   同一标准名可能对应多个规格原名（8/10/12 英寸蛋糕），
                #   这里把所有原名都记下来，汇总时按原名各自重算份数。
                orig_hints = [o for o, v in target_map.items() if v == t]
                if core._COMPOSITE_TITLE.search(nm) and nm != d:
                    a['skip'] += 1
                    continue
                ings_raw = r.get('recipeIngredient') or []
                if len(ings_raw) >= 6 and core._looks_like_meal(ings_raw):
                    a['skip'] += 1
                    continue
                steps_text = ' '.join(str(x) for x in (r.get('recipeInstructions') or []))
                ck = core.classify_cook(d, steps_text)
                c = core._calc_one_recipe(r.get('recipeIngredient') or [], ck)
                if c is None:
                    continue
                raw_mid = (c['low'] + c['high']) / 2.0

                # ── 闸门 1：主料闸 ─────────────────────────────────────
                # 病根 B：配料写「适量肉」时主料热量整段漏算，
                #        算出来的数字看着正常但完全错（如白斩鸡 32 卡）
                parsed = [core.parse_ingredient(x) for x in r.get('recipeIngredient') or []]
                ok_main, main_kw, main_parsed = QG.main_ingredient_parsed(
                    r.get('recipeIngredient') or [], parsed)
                if not ok_main:
                    a['skip_main'] += 1
                    acc_dbg[t].append((raw_mid, u'主料未解析:%s' % (main_kw or '?')))
                    continue

                # ── 份量归一化（治病根 A） ───────────────────────────
                sv, who, who_g = portion.estimate_servings(
                    r.get('recipeIngredient') or [], parsed, d)
                # ★ bug⑭：规格型菜（8寸/10寸蛋糕）无法归一 —— 语料库配方没标自己的规格
                if not sv or sv <= 0:
                    a['skip'] += 1
                    continue

                a['mids'].append(raw_mid / sv)
                a['lows'].append(c['low'] / sv)
                a['highs'].append(c['high'] / sv)
                # ★ bug⑭：规格型菜名（8寸/10寸/12寸蛋糕）映射到同一标准名后份数不同，
                #   必须按「每个原名各自的份数」二次归一，
                #   否则三者算出完全相同的热量（实测都是 230 卡）。
                for oh in orig_hints:
                    a.setdefault('by_orig', {}).setdefault(oh, []).append(
                        (raw_mid, c['low'], c['high'], sv))
                if ck.get('cook'):
                    a['cooks'][ck['cook']] += 1
                for u in c['unknown']:
                    a['unknown'][u] += 1
                a['n'] += 1

    # ── 汇总成 DishHeat ──────────────────────────────────────────────
    result = {}
    for orig, std in target_map.items():
        # ★ bug⑭（拦截层）：规格型菜一律弃权。
        #   「生日蛋糕8英寸」「10英寸」在标准化时会映射到同一个「生日蛋糕」，
        #   而语料库配方没标自己的规格 → 两者必然算出同一个数（实测都是 230）。
        #   宁可显示「算不出」，也不给一个看着正常但错的数字。
        if portion._servings_from_name(orig):
            result[orig] = core.DishHeat(
                orig, None, None, None, 0, 'none',
                u'算不出 —— 规格型菜（%s）：语料库配方没标尺寸，'
                u'无法把「8寸」和「10寸」区分开' % orig[-5:])
            continue
        if std is None:
            result[orig] = core.DishHeat(
                orig, None, None, None, 0, 'none', u'语料库中没有这道菜')
            continue
        a = acc[std]

        # ★ bug⑭：若该菜有多个规格原名（如 8/10/12 英寸蛋糕），各自独立统计
        by_orig = a.get('by_orig') or {}
        keep_m, keep_l, keep_h = list(a['mids']), list(a['lows']), list(a['highs'])

        # ── 闸门 2：合理性闸 ────────────────────────────────────────
        # ★ 注意：必须用上面按原名重算过的 keep_*，不能退回 a['mids']
        lo_r, hi_r, cat = QG.category_of(orig)
        _m2, _l2, _h2 = [], [], []
        dropped = 0
        for m, l, h in zip(keep_m, keep_l, keep_h):
            if lo_r <= m <= hi_r:
                _m2.append(m); _l2.append(l); _h2.append(h)
            else:
                dropped += 1
        keep_m, keep_l, keep_h = _m2, _l2, _h2
        a['skip_range'] = dropped

        n = len(keep_m)
        if n < 2:
            detail = []
            if a['skip_main']:
                detail.append(u'%d 个做法主料没写克数（配料只写「适量」，热量大残）'
                              % a['skip_main'])
            if dropped:
                detail.append(u'%d/%d 个做法算出 %d–%d 卡，超出%s合理区间 %d–%d'
                              % (dropped, n + dropped,
                                 round(min(keep_m)) if keep_m else 0,
                                 round(max(keep_m)) if keep_m else 0,
                                 cat, lo_r, hi_r))
            if not detail:
                detail.append(u'仅 %d 个做法通过校验' % n)
            result[orig] = core.DishHeat(
                orig, None, None, None, 0, 'none',
                u'数据不足，不给数字 —— ' + u'；'.join(detail))
            continue

        med = statistics.median(keep_m)
        lows = sorted(keep_l); highs = sorted(keep_h)
        n0 = len(highs)
        lo = lows[max(0, int(n0 * 0.10))]
        hi = highs[min(n0 - 1, int(n0 * 0.90))]
        span = (hi - lo) / max(1.0, lo)      # 区间相对跨度

        # ★ 门槛只用「做法数」，区间宽度不作为弃权依据 —— 见 tier 级输出说明
        #   verify_gate 实测：拿跨度卡准确率会走到两个极端（放 65% 错 / 收 100% 弃权）
        conf = 'high' if n >= 6 else ('medium' if n >= 4 else 'low')

        top = a['unknown'].most_common(3)
        top_txt = (u'；未识别食材：' + u'、'.join(u'%s×%d' % (k, v) for k, v in top)) if top else u''
        skip_txt = (u'；已剔除 %d 条组合菜/套餐类' % a['skip']) if a['skip'] else ''
        gate_txt = (u'；已剔除 %d 条主料缺失、%d 条超合理区间'
                    % (a['skip_main'], dropped)) if (a['skip_main'] or dropped) else ''
        cook_txt = ''
        if a['cooks']:
            cook_txt = u'；做法：' + u'、'.join(
                u'%s×%d' % (k, v) for k, v in a['cooks'].most_common(3))

        # ★ 弃权只保留一条：可信做法不足 2 个（连中位数都不成立）
        if n < 2:
            result[orig] = core.DishHeat(
                orig, None, None, None, n, 'none',
                u'不显示数字 —— 可用做法不足 2 个，无法取中位数')
            continue

        basis = (u'语料库 %d 个可用做法（已按主料归一到一人份、剔除异常）%s%s%s%s'
                 % (n, cook_txt, gate_txt, skip_txt, top_txt))
        result[orig] = core.DishHeat(orig, med, lo, hi, n, conf, basis)

    if verbose:
        for k, v in result.items():
            print(k, v.confidence, v.median, v.low, v.high, v.method_count)
    return result
