# -*- coding: utf-8 -*-
u"""packs —— 数据源扩展包

为什么要有这一层
────────────────
麦当劳 MCP 开放的只是**它自己的**营养表。但真实场景是：
你 400 米外那家面馆、那家潮汕火锅，多少卡？**没有人做这件事。**

所以我们把数据源做成**可插拔**的：麦当劳只是其中一个包。
接一个新品牌 = 写一个 30 行的子类。

架构
────
    walkkcal.render
          ↑ 读
    packs.registry  ← 汇总所有包
          ↑
    pack_mcd / pack_gaode_nearby / pack_yourbrand

每个包只需实现三个方法（见 `base.py`）：
    search_stores(lon, lat, radius)  找店
    menu_of(store)                   拿到菜名列表
    heat_of(dish)                    拿到热量（官方表 or 通用估算）

其中 heat_of 是**分层的**：包里没有官方数据时，自动回退到
`walkkcal.core` 的通用估算（份量归一 + 质量闸门 + 弃权）。
"""
from packs.base import BasePack  # noqa

__all__ = ['BasePack']
