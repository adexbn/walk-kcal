# -*- coding: utf-8 -*-
u"""corpus_dianping —— 大众点评 / 薄荷健康（**桩**：买到数据后填这里）

为什么是个桩
────────────
大众点评的菜品味型/热量数据、薄荷的 `fc.boohee` 开放平台都需要**商务开通**，
我们没有授权，因此**没有实测过** —— 按本项目的规矩，不假装能用。

但接口已经留好：把 `heat()` 填完、设 `WALKKCAL_CORPUS=dianping`，
主流程**一行都不用改**。

一个提示（很重要）
──────────────────
不管换成哪个源，**L2/L3 兜底（`prior.py`）仍然生效**。
所以换源**不会**把覆盖率打回「一片算不出」——
新源覆盖不到的菜，还是会走品类/门店类型先验。

也就是说：**换语料源是「锦上添花」，不是「推倒重来」。**
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
_DATA = os.path.join(_ROOT, 'data')


class DianpingProvider(CorpusProvider):
    name = u'大众点评'

    def __init__(self, table_path=None, api_url=None, api_key=None):
        # 两种接法：本地导出的表（最简单）或直连 API（需授权）
        self.table_path = table_path or os.path.join(_DATA, 'dianping_heat.json')
        self.api_url = api_url or os.environ.get('DIANPING_API')
        self.api_key = api_key or os.environ.get('DIANPING_KEY')
        self._table = {}
        if os.path.exists(self.table_path):
            try:
                self._table = json.load(io.open(self.table_path, encoding='utf-8'))
            except Exception:
                self._table = {}

    def heat(self, dish):
        if not dish:
            return None
        # TODO ① 本地表命中（把大众点评的热量导出成 {菜名: 卡} 放 data/dianping_heat.json）
        v = self._table.get(dish)
        if v is not None:
            k = v if isinstance(v, int) else v.get('kcal')
            if k:
                return {'kcal': k, 'label': u'点评数据', 'source': self.name}

        # TODO ② 直连 API（需商务授权后填）
        #   if self.api_url and self.api_key:
        #       r = requests.get(self.api_url, params={'dish': dish},
        #                        headers={'Authorization': self.api_key}).json()
        #       if r.get('kcal'):
        #           return {'kcal': int(r['kcal']), 'label': u'点评数据',
        #                   'source': self.name}
        return None


if __name__ == '__main__':
    p = DianpingProvider()
    print(u'%s · 本地表 %d 条（桩：填 data/dianping_heat.json 或配 API）'
          % (p.name, len(p._table)))
    print(u'  heat(%s) → %s' % (u'红烧肉', p.heat(u'红烧肉')))
