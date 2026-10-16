# -*- coding: utf-8 -*-
u"""pack_template —— 30 行接一个新数据源

用法
────
1. 复制本文件为 `pack_yourbrand.py`
2. 改 `name`、`search_stores`、`menu_of`、`heat_of` 四处
3. 在 `packs/registry.py` 里注册一行

就完了 —— walkkcal 会自动把你的品牌门店，和麦当劳门店一起
参与「按额度排序」。

例子：接一个叫「星巴克」的品牌，官方有营养表 API。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from packs.base import BasePack  # noqa


class YourBrandPack(BasePack):

    name = u'你的品牌'
    official = False          # 有官方营养表就写 True

    def search_stores(self, lon, lat, radius=1500):
        u"""① 找店：MCP / HTTP API / 本地快照，都行。"""
        # r = self.mcp.call('search-stores', {'lon': lon, 'lat': lat})
        # return [{'id': s['id'], 'name': s['name'],
        #          'lon': s['lon'], 'lat': s['lat']} for s in r['data']]
        return []

    def menu_of(self, store):
        u"""② 拿菜名：不同 MCP 字段名不一样，适配一下。"""
        # return [i['name'] for i in store.get('menu', [])]
        return []

    def heat_of(self, dish):
        u"""③ 算热量：有官方表就直接用；没有返回 None，
        调用方会自动回退到 `corpus.lookup()` 的语料 + 分层估算。"""
        # return self.official_table.get(dish)
        return None


# ── 注册（在 registry.py 的 CHANNELS 里加一行）──────────────
#    1) from packs.pack_yourbrand import YourBrandPack
#    2) CHANNELS['yourbrand'] = lambda: YourBrandPack()
#    3) 要默认启用就加进 DEFAULT_CHANNELS
#    用的时候：WALKKCAL_PACKS=mcd,yourbrand
