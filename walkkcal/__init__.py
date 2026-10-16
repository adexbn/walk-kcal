# -*- coding: utf-8 -*-
u"""walkkcal —— 额度驱动的吃饭决策引擎

一句话：你今天还能吃多少卡，取决于你愿意走多远。

模块地图
────────
  core          热量计算主逻辑（配料解析 / 烹饪判定 / 食材热量表 / 量词换算）
  portion       份量归一化（语料库 0% 有份量字段，靠主料推份数）
  quality_gate  两道质量闸门（主料闸 + 品类合理性闸）—— 算不出就弃权
  batch_heat    批量扫描（N 道菜 = 1 次语料库遍历）+ 尾部子串回退
  tiers         热量档位与额度判断
  allowance     额度公式（本模块，见下）
  fetch_detail  高德店铺详情 + 推荐菜抓取（象限配额）
  render        480px 竖屏渲染（图 + 两列卡片）
  icons         业态图标（形状编码业态）
  theme         VI 色板（颜色编码热量档位）
  olist         格式符校验容器（渲染必用）

设计原则
────────
  1. 额度由**用户设定**，不由算法推导 —— 走路只做加法
  2. 算不出就**弃权**，不猜一个看着正常的数
  3. 数据源可插拔（packs/）—— 麦当劳只是其中一个
"""
from __future__ import annotations

__version__ = '1.0.0'

# ── 额度公式（全项目唯一入口，改这里即可）──────────────────────
ALLOWANCE_BASE = 450      # 基础额度（卡）：一顿饭的预算基线
KCAL_PER_80M = 4          # 步行折算：每 80 米 +4 卡


def allowance(walk_m: float) -> int:
    u"""走过去能拿到的卡路里额度（卡）

    额度 = 基础额度 + 步行挣来的额度
    这是诚实的物理模型：走 80 米才挣 4 卡，走 1.3km 才够一个 485 卡的招牌堡。
    """
    return ALLOWANCE_BASE + (int(walk_m) // 80 + 1) * KCAL_PER_80M


def verdict(kcal: float, quota: float, tol: int = 50) -> str:
    u"""一道菜相对额度是「吃下 / 刚好 / 超了」"""
    if kcal > quota:
        return '超了'
    return '刚好' if abs(quota - kcal) <= tol else '吃下'
