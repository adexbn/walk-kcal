# -*- coding: utf-8 -*-
u"""pack_nearby_dining —— 周边任意餐厅包（★ 差异化护城河）

数据源
──────
  · 门店 + 推荐菜：**当前地图 provider**（高德 / 百度 / …，见 `walkkcal/geo.py`）
    抓下来的快照（`data/detail.json`）
  · 热量：**当前语料 provider**（下厨房 / 大众点评 / 薄荷…，见 `walkkcal/corpus.py`）
      —— 本包**不再自带语料表**，统一走 `corpus.lookup()`（全项目唯一入口）

为什么这个包最重要
──────────────────
226 个同赛热量类项目**全都在算麦当劳自己的菜**（`list-nutrition-foods`
只有麦当劳 158 条）。而真实场景是：你 400 米外那家面馆、那家潮汕火锅多少卡？
**只有我们处理。**

★ 本包**不认识任何地图服务商**
────────────────────────────
快照里可能有服务商私有类目（高德 `type`、百度 `detail_info.type`）。
现场翻译由**当前 provider** 提供（`geo.get().industries_of`），
本包只管调用 —— 所以 `WALKKCAL_GEO=baidu` 时，读百度快照也照样能分类。

诚实的部分
──────────
中餐估算平均误差 25-28%。算不出时返回 None → 界面显示「算不出」，不猜。
"""
from __future__ import annotations

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from packs.base import BasePack  # noqa
from walkkcal import snapshot as snap  # noqa



def _short_name(name):
    u"""`味香斋(雁荡路店)` → `味香斋`（去掉分店后缀，用于菜品表兜底匹配）"""
    if not name:
        return u''
    for sep in (u'(', u'（'):
        name = name.split(sep)[0]
    return name.strip()



class NearbyDiningPack(BasePack):
    name = u'周边餐厅（地图 + 语料）'
    official = False

    @property
    def provides_dishes(self):
        u"""本渠道给不给推荐菜。

        ★ 快照里的 `dish_hints`（推荐菜）来自**地图 provider** ——
          高德给（POI 详情的 `tag`），百度不给（实测 2026-10-14）。
          所以这个能力位**委托给当前 provider**，不在本包写死。
        """
        try:
            from walkkcal import geo
            return bool(getattr(geo.get(), 'provides_dishes', False))
        except Exception:
            return False

    def __init__(self, snapshot_path=None, detail_path=None, menu_path=None):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data = os.environ.get('WALKKCAL_DATA') or os.path.join(root, 'data')
        if not os.path.isabs(data):
            data = os.path.join(root, data)
        self.detail_path = detail_path or os.path.join(data, 'detail.json')
        self.snapshot_path = snapshot_path or os.path.join(data, 'stores.json')
        # ★ 可选的「自带菜品表」—— 见下方说明
        self.menu_path = menu_path or os.environ.get('WALKKCAL_MENU') \
            or os.path.join(data, 'menu.json')
        self._menu = self._load_menu()
        self._detail = None
        self._pool = None

    # ── ★ 自带菜品表：把「店 → 推荐菜」补上 ────────────────────
    def _load_menu(self):
        u"""读可选的本地菜品表 `data/menu.json`。

        **为什么需要这个口子**
          地图里只有高德提供「这家店有什么推荐菜」。换成百度/腾讯后，
          链路只能退到「门店 + 业态 + 距离」（见 `provides_dishes`）。
          但如果**你自己**手上有菜品数据（手工整理、自家门店系统、
          或委托合规采集），喂进来就能补上这一层 —— 不依赖任何地图。

        格式（键可以是店铺 id，也可以是店名）：
            {
              "味香斋(雁荡路店)": ["麻酱捞面", "小牛汤", "炸猪排"],
              "B0HGZZ273A":      ["红烧牛肉面"]
            }

        ★ 匹配顺序：店 id → 完整店名 → 去掉分店后缀的店名
          （`味香斋(雁荡路店)` → `味香斋`），命中即用。
        ★ 本文件**只读用户自己提供的表**，不做任何抓取。
        """
        if not self.menu_path or not os.path.exists(self.menu_path):
            return {}
        try:
            d = json.load(io.open(self.menu_path, encoding='utf-8'))
        except Exception as e:
            # ★ 不要静默吞掉：菜品表写坏了要让人看见（曾因为少 import io 静默失效）
            print(u'   ⚠ 菜品表读不了（%s）：%s' % (self.menu_path, e))
            return {}
        if not isinstance(d, dict):
            return {}
        out = {}
        for k, v in d.items():
            if isinstance(v, dict):                 # 允许 {"菜": 卡} 形式
                v = list(v.keys())
            if isinstance(v, (list, tuple)):
                dishes = [str(x).strip() for x in v if str(x).strip()]
                if dishes:
                    out[str(k).strip()] = dishes
        return out

    def _menu_of(self, store):
        u"""按 id → 完整店名 → 去后缀店名 依次找菜品表"""
        if not self._menu:
            return []
        for key in (store.get('id'), store.get('name'), _short_name(store.get('name'))):
            if key and key in self._menu:
                return list(self._menu[key])
        return []

    # ── 类目翻译：委托当前地图 provider（本包不认识服务商）────
    def _translate(self, raw):
        try:
            from walkkcal import geo
            return geo.get().industries_of(raw)
        except Exception:
            return []

    def _detail_doc(self):
        if self._detail is None:
            self._detail = snap.read_detail(self.detail_path,
                                            translate=self._translate)
        return self._detail

    def pool(self):
        u"""淡点层用的**全量餐饮池**（含没有推荐菜、被选掉的店）。

        ★ 密度可视化必须用这个，不能用「可评估」的那批 ——
          只画有推荐菜的，密度感会明显变稀（实测踩过）。
        """
        if self._pool is None:
            self._pool = snap.read_stores(self.snapshot_path,
                                          translate=self._translate)
        return self._pool

    # ── 找店 ────────────────────────────────────────────────
    def search_stores(self, lon, lat, radius=1500):
        u"""读详情快照，转成 registry/select 需要的行。

        `radius` 真的生效 —— 用户点「别走太远」时上层传 radius=500。
        ★ 地图不给推荐菜时，用可选的本地菜品表补上（见 `_load_menu`）。
        """
        out = []
        for s in self._detail_doc()['local']:
            walk = s.get('distance_m')
            if radius and walk is not None and int(walk) > int(radius):
                continue
            dishes = list(s.get('dish_hints') or [])
            if not dishes:                       # ★ 地图没给 → 问本地表
                dishes = self._menu_of(s)
            out.append({'id': s['id'], 'name': s['name'],
                        'lon': s['lon'], 'lat': s['lat'],
                        'distance': walk,
                        'address': s.get('address') or u'',
                        'category_hint': s.get('category_hint') or u'',
                        'industries': list(s.get('industries') or []),
                        'dish_hints': dishes,
                        'cost': s.get('cost')})
        return out

    # ── 拿菜名 ──────────────────────────────────────────────
    def menu_of(self, store):
        u"""推荐菜 = 地图 provider 的 tag（网友推荐顺序，**不重排**）。

        连锁品牌的 tag ≈ 菜单项，本地小店的 tag ≈ 特色菜。
        """
        return list(store.get('dish_hints') or [])

    # ── 拿热量 ──────────────────────────────────────────────
    def heat_of(self, dish):
        u"""★ 返回 None —— 本包**不自己查热量**。

        热量统一由 `packs/registry.py` 走 `corpus.lookup()`（分层：
        L1 语料 → L2 菜名品类 → L3 门店类型 → L4 弃权）。
        这样换语料源（薄荷 / 大众点评）时，本包一行都不用改。
        """
        return None


# ── 向后兼容：老名字还在文档/脚本里出现过 ────────────────────
GaodeNearbyPack = NearbyDiningPack


if __name__ == '__main__':
    pack = NearbyDiningPack()
    stores = pack.search_stores(0, 0)
    pool = pack.pool()['all']
    print(u'%s' % pack.name)
    print(u'  可评估（有推荐菜）%d 家 ｜ 全量池 %d 家' % (len(stores), len(pool)))
    print(u'  自带菜品表: %s' % (u'有，%d 家' % len(pack._menu) if pack._menu
                                   else u'无（可放 data/menu.json）'))
    print(u'  当前地图 provider:', end=' ')
    try:
        from walkkcal import geo
        print(geo.get().name)
    except Exception as e:
        print(u'(取不到: %s)' % e)
    for s in stores[:5]:
        dishes = pack.menu_of(s)
        print(u'  %-22s %4sm 业态=%s 菜=%s'
              % (s['name'][:20], s['distance'], s['industries'], dishes[:3]))
