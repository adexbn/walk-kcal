# -*- coding: utf-8 -*-
u"""pack 基类 —— **通用插槽**：一个渠道一口气给「店铺 + 距离 + 菜品」。

设计约束
────────
1. **不强制联网**：search_stores 可以读本地快照（demo 场景必须能离线跑）。
2. **不强制有官方热量**：heat_of 返回 None 时，调用方回退到语料 + 分层估算。
3. **不猜**：拿不到就返回 None / 空列表，绝不编造。
"""
from __future__ import annotations


class BasePack(object):
    u"""通用插槽（渠道）基类。

    ★ 默认模型：**一个渠道同时给出三样**

        ① 店铺 + 距离    ← search_stores()
        ② 菜品（推荐菜）  ← menu_of()
        ③ 热量           ← heat_of()

    麦当劳、高德都是这种「三合一」渠道，所以默认就走这条路。
    启用哪些渠道由 `packs/registry.py` 的 `WALKKCAL_PACKS` 决定，**多个则合并**。

    ★ 凑不齐三样的渠道怎么办（两条路，都行）
        · 只实现它有的那部分，其余返回空 / None —— 上层会自动降级；
        · 或按 `docs/源装配契约.md` 把它拆成 place / menu / heat 再组装。

    子类必须覆盖：
        name                 渠道名（显示用）
        search_stores()      找店
        menu_of()            拿菜名
    可选覆盖：
        heat_of()            拿热量（默认 None → 回退语料 + 分层估算）
        provides_dishes      给不给「推荐菜」（默认 False，**必须显式声明**）
    """

    name = u'未命名渠道'

    # 这个渠道的数据是否「官方精确」（用于界面 badge 与置信度）
    official = False

    # ★ 本渠道给不给「推荐菜」。
    #   默认 **False** —— 宁可少声明，也不要默认 True 之后忘了改。
    #   渲染层现在**不单靠这个声明**（另有「按数据事实」的安全带，见 render.py），
    #   但「源装配」报告与启动自检会读它。
    provides_dishes = False

    def search_stores(self, lon, lat, radius=1500):
        u"""返回门店列表。每项至少含：id / name / lon / lat / (可选) address"""
        raise NotImplementedError

    def menu_of(self, store):
        u"""返回该店的菜品名列表（字符串）。拿不到返回 []"""
        raise NotImplementedError

    def heat_of(self, dish):
        u"""返回这道菜的热量（int 卡）。拿不到返回 None。

        ★ 返回 None 是**合法且鼓励**的 —— 非官方渠道会回退到
          `corpus.lookup()` 的分层估算，仍算不出就显示「算不出」，不猜。
        """
        return None
