# -*- coding: utf-8 -*-
"""
热量档位 —— 保留为「辅助表达」，主数字已改回中位数

用户决策（2026-10-10）：不因地方菜差异大而改成档位，
仍显示中位数 + 区间；档位只作为次要参考。

但实测结论保留在案，供后续参考：
`cmp_aggregate.py` 用 24 道校准菜对比四种聚合方式 ——
   中位数 28.0% / 均值 25.2% / 截尾均值 25.4% / 加权平均 25.4%（平均相对误差）
四种方式差异在 3 个百分点内，**换聚合方式救不了数据源问题**。
选中位数的理由不是它更准，而是它对离群值最稳健、且最容易向用户解释。

「档位」在 UI 上仍有用：它承载了「相对额度」这一层判断
（能吃 / 勉强 / 超了），这部分不依赖绝对值精度。
"""

# 档位边界（kcal，一人份）
TIERS = [
    (120, u'很低', u'几乎不占额度'),
    (250, u'较低', u'额度里很宽松'),
    (400, u'中等', u'大致能吃下'),
    (600, u'偏高', u'额度会很紧'),
    (10 ** 9, u'很高', u'基本超额度'),
]

TIER_COLOR = {
    u'很低': '#2e7d32',
    u'较低': '#558b2f',
    u'中等': '#ef6c00',
    u'偏高': '#e65100',
    u'很高': '#c62828',
}


def tier_of(kcal):
    """按中位数定档"""
    if kcal is None:
        return None, None, None
    for hi, name, note in TIERS:
        if kcal < hi:
            return name, hi, note
    return u'很高', 10 ** 9, u'基本超额度'


def tier_range_name(low, high):
    """区间跨档时用「跨 N 档」表达，比单点更诚实"""
    if low is None or high is None:
        return None
    i_lo = next(i for i, (h, n, _) in enumerate(TIERS) if low < h)
    i_hi = next(i for i, (h, n, _) in enumerate(TIERS) if high < h)
    if i_lo == i_hi:
        return TIERS[i_lo][1]
    return u'%s→%s' % (TIERS[i_lo][1], TIERS[i_hi][1])


def assess_fits(kcal, allowance):
    """
    相对额度判断。返回 (状态, 说明)
    状态：吃 / 勉强 / 超了 / 数据不足
    """
    if kcal is None:
        return u'数据不足', u'算不出来'
    r = kcal / float(allowance)
    if r <= 0.85:
        return u'能吃', u'占用额度 %.0f%%' % (r * 100)
    if r <= 1.15:
        return u'勉强', u'占用额度 %.0f%%，基本贴线' % (r * 100)
    return u'超了', u'占用额度 %.0f%%' % (r * 100)
