# -*- coding: utf-8 -*-
u"""业态图标 v6 —— 纯线条（描边为主，单一颜色）2026-10-12

★ 用户要求（2026-10-12）：
  「简洁明了（线条为主，单一颜色，满足后续根据热量调整颜色的目的）」
  → **全部 fill="none"，只画轮廓线**；描边色由调用方传入（= 热量档位色）。
  这样「形状=业态、颜色=热量」两套编码完全解耦，加业态只需加函数。

★ 三个渲染器各自的易错点（pillow-lunasvg）：
  1. **三次贝塞尔 C 渲染错误** → 只用直线、椭圆、圆。
  2. **`Z` 闭合不生效** → 多边形要显式把首点再写一遍。
  3. **大块白色遮罩会吃掉图形** → 纯线框方案天然避开了这个问题。

★ 统一占位框 2.0r × 2.0r：
  旧版 store 只有 1.06r 高、drink 2.02r 高，视觉差近 2 倍 ——
  这是「大小不统一」的真正根源，不是形状问题。

★ 描边宽度：**r × 0.115**（纯线框比实心描边略粗一点更清晰；
  早先试过 r×0.26~0.30，描边吃掉主体一半，完全糊掉）。

图形由用户在参考图上指定：
  餐饮 → 米饭 + 筷子（3行4）｜饮品 → 茶杯（3行5）
  快餐 → 汉堡（5行3）｜超市 → 购物车（沿用）
"""
import math

INK = '#16191d'
BOX = 2.00          # 统一占位框边长（相对 r）


def _path(o, pts, stroke, sw, close=True):
    u'''折线/多边形轮廓。close=True 时显式回到起点（渲染器不认 Z）。'''
    d = 'M ' + ' L '.join('%.1f %.1f' % p for p in pts)
    if close:
        d += ' L %.1f %.1f Z' % pts[0]
    o.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.1f" '
             'stroke-linejoin="round" stroke-linecap="round"/>' % (d, stroke, sw))


def _ln(o, x1, y1, x2, y2, c, w, cap='round'):
    o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
             'stroke-width="%.1f" stroke-linecap="%s"/>' % (x1, y1, x2, y2, c, w, cap))


def _ell(o, cx, cy, rx, ry, stroke, sw, fill='none'):
    o.append('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="%s" '
             'stroke="%s" stroke-width="%.1f"/>' % (cx, cy, rx, ry, fill, stroke, sw))


def _arc(cx, cy, rx, ry, a0, a1, n=24):
    u'''椭圆弧采样点（用折线近似，渲染器不认 A 指令）'''
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / float(n))),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / float(n))))
            for i in range(n + 1)]


def badge(o, x, y, r, idx, ink=INK, weight=700, fs_mul=1.34, sw_mul=0.16):
    u'''编号：压在图形正中，白字 + 深色描边。'''
    if not idx:
        return
    o.append('<text x="%.1f" y="%.1f" font-size="%.1f" font-weight="%d" '
             'fill="#ffffff" stroke="%s" stroke-width="%.1f" '
             'paint-order="stroke" text-anchor="middle" '
             'dominant-baseline="central">%d</text>'
             % (x, y + r * 0.04, r * fs_mul, weight, ink, r * sw_mul, idx))


# ═══════════════════════════════════════════════════════════════════
# 餐饮 —— 一碗米饭（2026-10-12 v13 · 提高识别度）
# ★ 用户反馈：「看不出是米饭，缩略图像咖啡豆/树叶」。
#   根因：v12 的碗身是**半圆**、米饭是**对称圆拱** → 整体一个鼓起的椭圆，
#         没有碗的识别特征。
# ★ 真实米饭碗图标（Font Awesome / Boxicons）的识别特征：
#   ① **碗身是梯形/碗形**（上宽下窄，两侧近直线）—— 不是圆
#   ② **碗口是一条清晰的水平线**
#   ③ 米饭堆在碗口上，且有**几粒米**点缀（决定性的识别线索）
#   → v13：梯形碗 + 平碗口 + 米饭堆 + 3 粒米
# ═══════════════════════════════════════════════════════════════════
def ico_restaurant(o, x, y, r, fill, idx):
    sw = r * 0.115
    # ── 碗：梯形碗身（上宽下窄）──
    _top = y + r * 0.06                 # 碗口高度
    _bot = y + r * 0.62                 # 碗底
    _bw = r * 0.84                      # 碗口半宽
    _dw = r * 0.56                      # 碗底半宽（收窄 → 碗形）
    # 碗身：左壁 → 底 → 右壁（近直线，底部小圆角）
    _path(o, [(x - _bw, _top), (x - _bw * 0.86, _bot - r * 0.10),
              (x - _dw * 0.60, _bot), (x + _dw * 0.60, _bot),
              (x + _bw * 0.86, _bot - r * 0.10), (x + _bw, _top)],
          fill, sw, close=False)
    # 碗口：一条清晰的水平线
    _ln(o, x - _bw, _top, x + _bw, _top, fill, sw)
    # ── 米饭：碗口上方的圆润堆（比碗口略窄）──
    _apex = r * 0.52
    _xr = _bw * 0.82
    _n = 18
    _pts = []
    for _i in range(_n + 1):
        _t = _i / float(_n)
        _px = x - _xr + 2 * _xr * _t
        _py = _top - _apex * (math.sin(math.pi * _t) ** 0.62)
        _pts.append((_px, _py))
    _path(o, _pts, fill, sw, close=False)
    # ── 3 粒米（决定性的识别线索：一看就知道是"饭"不是"树叶"）──
    _gy = _top - _apex * 0.62
    for _gx, _gy2 in [(-0.30, 0.20), (0.02, 0.06), (0.34, 0.22)]:
        _ell(o, x + _xr * _gx, _top - _apex * (0.62 - _gy2), r * 0.07, r * 0.045,
             fill, sw * 0.55)
    badge(o, x, y, r, idx)


# ═══════════════════════════════════════════════════════════════════
# 饮品 —— 茶杯（参考图 3 行 5）：杯 + 把手 + 茶碟 + 三缕蒸汽
# ═══════════════════════════════════════════════════════════════════
def ico_drink(o, x, y, r, fill, idx):
    sw = r * 0.115
    cw = r * 0.56
    ct, cb = y - r * 0.22, y + r * 0.50
    # 杯身（上宽下窄）
    _path(o, [(x - cw, ct), (x + cw, ct),
              (x + cw * 0.80, cb), (x - cw * 0.80, cb)], fill, sw)
    # 杯口
    _ell(o, x, ct, cw, cw * 0.22, fill, sw)
    # 把手（右侧半圆，折线近似）
    hx = x + cw
    _path(o, _arc(hx + r * 0.16, (ct + cb) / 2.0, r * 0.26, (cb - ct) * 0.30,
                  300, 60, 16), fill, sw, close=False)
    # 茶碟（底部扁椭圆）
    _ell(o, x, y + r * 0.68, r * 0.78, r * 0.15, fill, sw)
    # 杯底到碟的短柱
    _ln(o, x - cw * 0.42, cb, x - cw * 0.42, y + r * 0.58, fill, sw)
    _ln(o, x + cw * 0.42, cb, x + cw * 0.42, y + r * 0.58, fill, sw)
    # 三缕蒸汽
    for _k, _dx in enumerate((-0.22, 0.0, 0.22)):
        _ln(o, x + _dx * r, ct - r * 0.24,
            x + _dx * r, ct - r * 0.64 - _k * r * 0.04, fill, sw)
    badge(o, x, y, r, idx)


# ═══════════════════════════════════════════════════════════════════
# 快餐 —— 汉堡（参考图 5 行 3）：半圆上包 + 波折生菜 + 下包 + 芝麻
# ═══════════════════════════════════════════════════════════════════
def ico_fastfood(o, x, y, r, fill, idx):
    sw = r * 0.115
    h = r * 0.60
    # 上层面包（半圆 + 底边）
    top_arc = _arc(x, y - h * 0.16, r * 0.86, h * 0.78, 180, 360, 22)
    _path(o, top_arc + [(x + r * 0.86, y - h * 0.16 + h * 0.10),
                        (x - r * 0.86, y - h * 0.16 + h * 0.10)], fill, sw)
    # 芝麻
    for _dx, _dy in ((-0.40, -0.62), (-0.08, -0.76), (0.30, -0.60)):
        _ln(o, x + _dx * r, y + _dy * h, x + _dx * r, y + _dy * h + r * 0.09,
            fill, sw * 0.95)
    # 生菜（波折线）
    _path(o, [(x - r * 0.92, y - h * 0.06), (x - r * 0.62, y - h * 0.24),
              (x - r * 0.31, y - h * 0.06), (x, y - h * 0.24),
              (x + r * 0.31, y - h * 0.06), (x + r * 0.62, y - h * 0.24),
              (x + r * 0.92, y - h * 0.06)], fill, sw, close=False)
    # 肉饼（细长椭圆）
    _ell(o, x, y + h * 0.22, r * 0.88, h * 0.14, fill, sw * 0.9)
    # 下层面包（上宽下窄）
    _path(o, [(x - r * 0.84, y + h * 0.46), (x + r * 0.84, y + h * 0.46),
              (x + r * 0.72, y + h * 0.92), (x - r * 0.72, y + h * 0.92)],
          fill, sw)
    badge(o, x, y, r, idx)


# ═══════════════════════════════════════════════════════════════════
# 火锅/烧烤 —— 锅 + 两侧把手 + 蒸汽（与碗、汉堡明确区分）
# ═══════════════════════════════════════════════════════════════════
def ico_hotpot(o, x, y, r, fill, idx):
    u'''火锅：宽口锅身 + 左右双耳把手 + 锅盖把手 + 上升蒸汽。'''
    sw = r * 0.115
    cw = r * 0.66                      # 锅半宽
    top, bot = y - r * 0.10, y + r * 0.56
    # 锅身（上宽下窄的碗状）
    _path(o, [(x - cw, top), (x + cw, top),
              (x + cw * 0.66, bot), (x - cw * 0.66, bot)], fill, sw)
    # 锅口（扁椭圆）
    _ell(o, x, top, cw, cw * 0.26, fill, sw)
    # 左右双耳把手（小圆环）
    _ell(o, x - cw - r * 0.13, top, r * 0.16, r * 0.11, fill, sw)
    _ell(o, x + cw + r * 0.13, top, r * 0.16, r * 0.11, fill, sw)
    # 三缕蒸汽
    for _dx, _h in ((-0.26, 0.34), (0.0, 0.46), (0.26, 0.34)):
        _ln(o, x + _dx * r, top - r * 0.16,
            x + _dx * r, top - r * 0.16 - _h * r, fill, sw)
    badge(o, x, y, r, idx)


# ═══════════════════════════════════════════════════════════════════
# 超市 —— 购物车（沿用 v4）：车斗 + 网格 + 推把 + 两轮
# ═══════════════════════════════════════════════════════════════════
def ico_store(o, x, y, r, fill, idx):
    sw = r * 0.115
    h = r * 0.62
    top, bot = y - h * 0.52, y + h * 0.66
    _path(o, [(x - r * 0.54, top), (x + r * 0.62, top),
              (x + r * 0.46, bot), (x - r * 0.38, bot)], fill, sw)
    _ln(o, x - r * 0.50, top + r * 0.26, x + r * 0.56, top + r * 0.26, fill, sw * 0.85)
    _ln(o, x + r * 0.04, top + r * 0.04, x + r * 0.04, bot - r * 0.04, fill, sw * 0.85)
    # 推把
    _ln(o, x - r * 0.54, top + r * 0.03, x - r * 0.90, top + r * 0.03, fill, sw)
    _ln(o, x - r * 0.90, top + r * 0.03, x - r * 0.78, top + r * 0.34, fill, sw)
    # 两轮（轮子是识别关键）
    for _k in (-0.34, 0.34):
        _ell(o, x + _k * r * 1.06, bot + r * 0.34, r * 0.20, r * 0.20, fill, sw)
    badge(o, x, y, r, idx)


ICONS = {
    'restaurant': ico_restaurant,   # 餐饮 —— 米饭 + 筷子
    'cafe': ico_drink,              # 饮品 —— 茶杯
    'bar': ico_drink,               # 外带饮品 —— 同一个茶杯
    'store': ico_store,             # 超市 —— 购物车
    'fastfood': ico_fastfood,       # 快餐 —— 汉堡
    'bakery': ico_fastfood,         # 面包甜点 → 汉堡
    'hotpot': ico_hotpot,           # 火锅/烧烤 —— 锅
}


# ═══════════════════════════════════════════════════════════════════
# 定位针 —— 出发点（圆头 + 尖底 + 白描边）
# ═══════════════════════════════════════════════════════════════════
def pin(o, x, y, r, color='#0ac89e'):
    hr = r * 0.64
    cy = y - r * 0.26
    tip = y + r * 1.26
    o.append('<path d="M %.1f %.1f L %.1f %.1f L %.1f %.1f Z" fill="#ffffff"/>'
             % (x - hr * 1.18, cy + hr * 0.40, x + hr * 1.18, cy + hr * 0.40, x, tip))
    o.append('<path d="M %.1f %.1f L %.1f %.1f L %.1f %.1f Z" fill="%s"/>'
             % (x - hr * 0.86, cy + hr * 0.40, x + hr * 0.86, cy + hr * 0.40,
                x, tip, color))
    o.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#ffffff"/>'
             % (x, cy, hr + r * 0.20))
    o.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s"/>' % (x, cy, hr, color))
    o.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#ffffff"/>'
             % (x, cy, hr * 0.44))