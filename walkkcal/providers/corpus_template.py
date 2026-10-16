# -*- coding: utf-8 -*-
u"""corpus_template —— 接一个新语料源，20 行

步骤
────
1. 复制本文件为 `corpus_你的源.py`
2. 实现 `heat(dish)`：把菜名换成热量
3. 在 `walkkcal/corpus.py` 的 `get()` 里加一行
4. `export WALKKCAL_CORPUS=你的源`

就完了 —— 主流程一行都不用改。

★ 别忘了：你只负责 L1（直接命中）。
  覆盖不到的菜会自动走 L2（菜名品类）/ L3（门店类型）兜底，
  所以**不用追求 100% 覆盖**，也别为了「有数」编一个。
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
_ROOT = os.path.dirname(os.path.dirname(_HERE))


class YourCorpusProvider(CorpusProvider):
    name = u'你的语料源'

    def __init__(self, table_path=None):
        self.table_path = table_path or os.path.join(_ROOT, 'corpus', 'your_heat.json')
        self._table = {}
        if os.path.exists(self.table_path):
            self._table = json.load(io.open(self.table_path, encoding='utf-8'))

    def heat(self, dish):
        u"""返回 {'kcal': int, 'label': str, 'source': str} 或 None（弃权）"""
        v = self._table.get(dish)
        if v is None:
            return None                       # ← 拿不到就返回 None，别编
        k = v if isinstance(v, int) else v.get('kcal')
        if not k:
            return None
        return {'kcal': k, 'label': u'你的标签', 'source': self.name}


# ── 注册（复制到 walkkcal/corpus.py 的 get()）───────────────
#    if key in ('你的源',):
#        from walkkcal.providers.corpus_你的源 import YourCorpusProvider
#        return YourCorpusProvider()
