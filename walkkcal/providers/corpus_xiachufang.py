# -*- coding: utf-8 -*-
u"""corpus_xiachufang —— 下厨房语料库实现（默认语料源，已实测）

数据来源
────────
  · `corpus/local_dish_heat.json` —— 已批量算好的本地热量表（194 条，**开箱即用**）
  · `datasets/xcx_dishes.json`   —— 菜名索引（28.4 万条，可选）
  · `datasets/XiaChuFang/data/recipe_corpus_full.json` —— 2GB 原始语料（可选）

★ 为什么要三档
    只带 `corpus/local_dish_heat.json`（几十 KB）就能跑 demo；
    要覆盖新点位，再用 `walkkcal/batch_heat.py` 扫原始语料补表。

换语料源只影响 L1；L2/L3 兜底（prior）与语料无关，仍然生效。
"""
from __future__ import annotations

import io
import json
import os

# ── 直接执行本文件时，把仓库根加入 sys.path（打包导入时无副作用）──
import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from walkkcal.corpus import CorpusProvider

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))          # walk-kcal/
_CORPUS = os.path.join(_ROOT, 'corpus')
_DATASETS = os.path.join(os.path.dirname(_ROOT), 'datasets')


def scan_dish_heat(dish_name, corpus_index, corpus_path,
                   max_recipes=40):
    u"""**扫原始语料 JSONL** 现算一道菜的热量（下厨房 schema）。

    ★ 2026-10-13 从 `walkkcal/core.py` 移到这里 ——
      因为它读的是**下厨房私有的 JSONL 格式**（`recipeIngredient` /
      `recipeInstructions`），不该待在品牌与数据源都无关的内核里。
      core.py 只保留纯数学（食材表 / 量词 / 份量 / 用油模型）。

    ★ 这是**离线批量预计算**用的慢路径：要扫 1.9GB。
      运行时永远走已算好的表（`heat()`），不走这里。
      批量用法见 `walkkcal/batch_heat.py`。
    """
    from walkkcal import core

    target = dish_name
    if target not in corpus_index:
        cands = [n for n in corpus_index
                 if (target in n or n in target) and min(len(target), len(n)) >= 2]
        if not cands:
            return core.DishHeat(dish_name, None, None, None, 0, 'none',
                                 u'语料库中没有这道菜')
        target = max(cands, key=lambda x: corpus_index[x])

    mids, lows, highs, unknowns = [], [], [], {}
    n_skip_composite = 0
    cooks = {}
    n = 0
    with io.open(corpus_path, encoding='utf-8', errors='ignore') as f:
        for line in f:
            if target not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            nm = r.get('name') or ''
            d = r.get('dish') or nm
            if d != target:
                continue
            # bug⑥：菜名只是配料之一时必须剔除
            if core._COMPOSITE_TITLE.search(nm) and nm != d:
                n_skip_composite += 1
                continue
            # bug⑦：配料里出现了明显不属于这道菜的完整食材组 → 剔除
            ings_raw = r.get('recipeIngredient') or []
            if len(ings_raw) >= 6 and core._looks_like_meal(ings_raw):
                n_skip_composite += 1
                continue
            steps_text = ' '.join(str(x) for x in (r.get('recipeInstructions') or []))
            ck = core.classify_cook(d, steps_text)
            c = core._calc_one_recipe(r.get('recipeIngredient') or [], ck)
            if c is None:
                continue               # bug③：剔除无效做法
            mids.append((c['low'] + c['high']) / 2.0)
            lows.append(c['low'])
            highs.append(c['high'])
            if ck.get('cook'):
                cooks[ck['cook']] = cooks.get(ck['cook'], 0) + 1
            for u in c['unknown']:
                unknowns[u] = unknowns.get(u, 0) + 1
            n += 1
            if n >= max_recipes:
                break

    import statistics
    if n < 2:
        return core.DishHeat(dish_name, None, None, None, 0, 'none',
                             u'找到菜名但仅 %d 个做法可计算（至少需 2 个才能取中位数）' % n)

    med = statistics.median(mids)
    lows.sort(); highs.sort()
    n0 = len(highs)
    lo = lows[max(0, int(n0 * 0.10))]
    hi = highs[min(n0 - 1, int(n0 * 0.90))]
    if n >= 8 and hi / max(1.0, lo) < 2.5:
        conf = 'high'
    elif n >= 3:
        conf = 'medium'
    else:
        conf = 'low'

    top = sorted(unknowns.items(), key=lambda x: -x[1])[:3]
    top_txt = (u'；未识别食材：' + u'、'.join(u'%s×%d' % (k, v) for k, v in top)) if top else u''
    skip_txt = (u'；已剔除 %d 条组合菜/异类做法' % n_skip_composite) if n_skip_composite else u''
    ck_txt = (u'；做法分布：' + u'、'.join(u'%s×%d' % (k, v)
                for k, v in sorted(cooks.items(), key=lambda x: -x[1])[:3])) if cooks else u''
    basis = (u'语料库 %s「%d 个可计算做法」的中位数；'
             u'区间 %d–%d 取各做法 10/90 分位%s%s%s'
             % (target, n, round(lo), round(hi), ck_txt, skip_txt, top_txt))
    return core.DishHeat(dish_name, med, lo, hi, n, conf, basis,
                         [k for k, _ in top])


class XiachufangProvider(CorpusProvider):
    name = u'下厨房'

    def __init__(self, table_path=None, index_path=None):
        self.table_path = table_path or os.path.join(_CORPUS, 'local_dish_heat.json')
        self.index_path = index_path or os.path.join(_DATASETS, 'xcx_dishes.json')
        self._table = self._load(self.table_path) or {}
        self._index = None          # 懒加载（8.8MB，用到才读）

    @staticmethod
    def _load(path):
        if path and os.path.exists(path):
            try:
                return json.load(io.open(path, encoding='utf-8'))
            except Exception:
                return None
        return None

    @property
    def index(self):
        if self._index is None:
            self._index = self._load(self.index_path)
            if isinstance(self._index, dict):
                self._index = set(self._index.keys())
            elif self._index is None:
                self._index = set()
        return self._index

    def heat(self, dish):
        u"""L1：先查已算好的表，再查菜名索引（命中索引但没算过 → 交给上层兜底）"""
        if not dish:
            return None
        v = self._table.get(dish)
        if isinstance(v, int):
            return {'kcal': v, 'label': u'菜谱推算', 'source': self.name}
        if isinstance(v, dict) and v.get('kcal'):
            return {'kcal': v['kcal'],
                    'label': v.get('label', u'菜谱推算'), 'source': self.name}
        # 精确没中 → 尾部子串回退（中餐菜名 = 「做法/修饰 + 主料」，主料在尾部）
        for i in range(1, len(dish)):
            sub = dish[i:]
            if len(sub) >= 2 and sub in self._table:
                v = self._table[sub]
                k = v if isinstance(v, int) else v.get('kcal')
                if k:
                    return {'kcal': k,
                            'label': u'菜谱推算（子串 %s）' % sub,
                            'source': self.name}
        return None


if __name__ == '__main__':
    p = XiachufangProvider()
    print(u'%s · 本地表 %d 条' % (p.name, len(p._table)))
    for d in [u'红烧牛肉面', u'桂花红烧肉', u'多肉葡萄']:
        print(u'  %-10s → %s' % (d, p.heat(d)))
