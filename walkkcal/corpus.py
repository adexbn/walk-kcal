# -*- coding: utf-8 -*-
u"""corpus —— 语料源接口（**菜名 → 热量**）

想换成大众点评 / 薄荷健康 / 自己的表？
──────────────────────────────────────
**不用改主流程。** 只要：

  1. 新建 `walkkcal/providers/corpus_你的源.py`，实现 `heat(dish)`
  2. 设环境变量 `WALKKCAL_CORPUS=你的源`
  3. 完成

分层链路（**这是全项目唯一查热量的入口**）
──────────────────────────────────────────
    lookup(dish, store_hint)
      ├─ L1  主语料源（provider.heat）        → 标「菜谱推算 / 官方」   ±25%
      ├─ L2  菜名关键词定品类（prior）         → 标「品类估算」        ±35%
      ├─ L3  门店类型定品类（prior）           → 标「同类估算」        ±45%
      └─ L4  都不中                            → 返回 None（**弃权**）

★ 换语料源只影响 L1；L2/L3 的兜底逻辑与语料无关，仍然生效。
  所以**换源不会把覆盖率打回原形**。
"""

from __future__ import annotations

import os

from walkkcal import prior


class CorpusProvider(object):
    u"""语料源基类。子类实现 heat() 即可。"""

    name = u'未命名'

    def heat(self, dish):
        u"""返回 {'kcal': int, 'label': str, 'source': str} 或 None

        ★ 返回 None 是**合法且鼓励**的 —— 上层会自动走 L2/L3 兜底，
          实在没有依据才弃权。不要为了「有数」编一个。
        """
        raise NotImplementedError


# ── 注册表 ──────────────────────────────────────────────
def get(name=None):
    u"""按名字取语料源。name 为空时读环境变量 WALKKCAL_CORPUS。

        WALKKCAL_CORPUS=xiachufang   （默认）
        WALKKCAL_CORPUS=dianping
    """
    key = (name or os.environ.get('WALKKCAL_CORPUS') or 'xiachufang').lower()
    if key in ('xiachufang', u'下厨房'):
        from walkkcal.providers.corpus_xiachufang import XiachufangProvider
        return XiachufangProvider()
    if key in ('dianping', u'大众点评'):
        from walkkcal.providers.corpus_dianping import DianpingProvider
        return DianpingProvider()
    raise ValueError(
        u'未知的语料源 %r。可用：xiachufang / dianping。'
        u'要接新的？照 walkkcal/providers/corpus_template.py 写一个，'
        u'然后在 walkkcal/corpus.py 的 get() 里加一行。' % key)


def lookup(dish, store_hint=None, provider=None):
    u"""★ 全项目唯一的「菜名 → 热量」入口（分层）。

    返回 {'kcal', 'label', 'tier'} 或 None（弃权）
    """
    prov = provider or get()
    # L1 主语料源
    r = prov.heat(dish)
    if r:
        return {'kcal': r['kcal'],
                'label': r.get('label', u'菜谱推算'),
                'tier': 'L1'}
    # L2 / L3 兜底（与语料源无关，永远生效）
    return prior.estimate(dish, store_hint)
