# -*- coding: utf-8 -*-
u"""filter —— 业态归类与筛选（「附近西餐」只显示西餐）

★ 归一化词表（12 个业态）
────────────────────────────────────────────────────────
    快餐 · 小吃 · 面馆 · 西餐 · 日料 · 韩餐 · 东南亚
    火锅烧烤 · 咖啡 · 茶饮 · 面包甜点 · 中餐本帮

其中「西餐」在筛选时是**聚合键**：
    西餐（聚合）= 欧美菜 ∪ 日料 ∪ 韩餐 ∪ 东南亚
用户既能点「西餐」不挑，也能点「日料」只要日料。见 `GROUPS`。

★ 「西餐」的口径：世俗逻辑 = 中餐之外的「正餐」
────────────────────────────────────────────────
2026-10-13 用户裁定：**按普通大众的世俗理解，不按分类学。**

    含：欧美菜（法/意/美/德/西/地中海）· 日料 · 韩餐 · 东南亚
    不含：快餐 · 小吃 · 面馆 · 火锅烧烤 · 咖啡 · 茶饮 · 面包甜点 · 中餐

**快餐不往上升**：快餐本身分中式/西式，但我们**只做到「快餐」这一层**。
所以 **麦当劳 = 快餐，不是西餐**。只有**正餐**里的非中餐才算西餐。

为什么**不**做字面的「非中餐」：
    K11 实测 218 家里，字面非中餐 = **149 家（68%）** ——
    连喜茶、星巴克、蛋糕店都算"西餐"，点「西餐」和点「全部」没区别。

★ 三个判定层（本文件**不认识任何地图服务商**）
────────────────────────────────────────────────
    ① `store['industries']`   ← ★ provider 归一化好的（最可靠）
    ② `store['category_hint']` ← 品类线索（由 snapshot 层归一化，见下）
    ③ 店名关键词               ← 品牌兜底（唯一不依赖数据源的一层）

**为什么 ① 要由 provider 供给**：高德 `type` 是 `餐饮服务;外国餐厅;日本料理`
这种三级类目，这套类目名是**高德私有知识**。翻译工作放在
各自的 `walkkcal/providers/geo_*.py` 里，本文件只认归一化词表。

**为什么字段名不再叫 keytag**：`keytag` 是高德的字段名，`atag` 也是。
本文件以前读它们，等于把「某一家地图的字段名」写进通用层。
现在这两件事都由 `walkkcal/snapshot.py`（防腐层）做完 ——
它把 `keytag`/`atag`/`tag` 等历史字段名统一成
`category_hint` / `dish_hints`。**本文件只认归一化后的名字。**

实测（K11 218 家）：高德 type 贡献 171 家次分类；若 provider 不产出
`industries`，只剩店名兜底 → 覆盖率 **82% → 28%**。
（换百度时，只要它的 provider 产出同样的 `industries`，本文件一行不用改。）
"""

from __future__ import annotations

import re

# ── 归一化词表（顺序即 UI 顺序）─────────────────────────────
INDUSTRIES = [
    u'快餐', u'小吃', u'面馆',
    u'西餐', u'日料', u'韩餐', u'东南亚', u'火锅烧烤',
    u'咖啡', u'茶饮', u'面包甜点', u'中餐本帮',
]

# ── 聚合键：点一个，命中一族 ─────────────────────────────────
#   ★ 「西餐」= 非中餐的「正餐」。
#     快餐/小吃/面馆/火锅/咖啡/茶饮/甜点 **都不进这个集合** ——
#     它们是独立的业态层，不往上升（麦当劳 = 快餐，不是西餐）。
GROUPS = {
    u'西餐': [u'西餐', u'日料', u'韩餐', u'东南亚'],
}

# ── 「只卖喝的」判定（`select.py` 用它决定要不要把这家隐去）───────
#
#   ★★ 为什么**不能**靠菜名识别饮品（用户 2026-10-15 问到点上）：
#      饮品名「很有诱惑性」—— 「多肉葡萄」「奇兰粉芭乐」「杨枝甘露」
#      「芝芝绿妍茶后」，凭名字根本看不出是喝的还是吃的。
#      反过来「茶香鸡」「米酿圆子」也含「茶」「酿」。
#      **靠菜名猜品类，必然两边都错。**
#
#   ✔ 可靠的做法：**店铺的官方类目**说了算 ——
#      高德把这家店归到「茶饮」，那它就是卖饮品的店，
#      跟它某个菜叫得多花哨无关。
#
#   但类目也会错（实测 Chili's 美式餐厅被标成「咖啡」、
#   阿喵说麻辣香锅被标成「茶饮」）→ 所以还要两层保护：
#
#     第 ① 层  只要店铺**同时**挂着任何正餐业态（见 MEAL_INDUSTRIES），
#              就**不算**「只卖喝的」→ 挡住 Chili's / 阿喵说 这类误判。
#     第 ② 层  菜名里有「能当一餐的固体」（MEAL_WORDS）→ 放行。
#              这是为了 Tims 这类「咖啡店但真卖贝果三明治」。
#
#   ★ 两层都过（挂着饮品类目 + 没有一样固体）才隐去。
#   ★ 方向是不对称的：**误隐的代价 < 误留的代价**（少看到一个选项
#     vs 让人以为「一杯奶茶算一顿饭」）。
DRINK_ONLY_INDUSTRIES = frozenset([u'咖啡', u'茶饮', u'面包甜点'])

# 正餐业态：只要店挂着其中之一，就不可能是「只卖喝的」
#
# ★★ 2026-10-15 用户裁决：**「小吃」不算正餐业态**（从下表移除）。
#   原因：高德的「小吃」是个**过宽的筐** —— 茶话弄/霸王茶姬的 `type` 归小吃，
#   `category_hint` 却是「奶茶/茶饮」。把它当正餐凭证，「挂着饮品业态」的茶饮店
#   会被第 ① 层全部放行（全场景实测 54 家，其中约 21 家是纯茶饮品牌）。
#
#   ★ 「快餐」**保留** —— 快餐 ≠ 小吃（麦当劳、肯德基、吉野家、正新鸡排都是快餐，
#     它们是真能当一餐的）。去掉的只有「小吃」这一个。
#   ★ 「小吃」仍在 `INDUSTRIES` 里（图例、业态筛选照用），只是不再充当正餐凭证。
#   ★ 副作用可控：纯小吃店（沙县、生煎）本来就不在 DRINK_ONLY_INDUSTRIES 里，
#     第 ② 步就不适用它们 —— **只有「小吃 + 饮品业态」同时出现才会被隐去**。
MEAL_INDUSTRIES = frozenset([
    u'快餐', u'面馆', u'西餐', u'日料', u'韩餐', u'东南亚',
    u'火锅烧烤', u'中餐本帮',
])

# ★ 菜品信号表（MEAL_WORDS / has_meal_word）已上移到 `prior.py` ——
#   它属于「菜品级判断」，和 `is_menu_item` 放一起才不会出现两份不一致的词表。
#   这里保留转发，避免破坏既有调用方（`select.py` / 测试）。
from walkkcal.prior import MEAL_WORDS, has_meal_word  # noqa: E402,F401


# ── ② 品类线索（category_hint）→ 业态 ────────────────────────
#   ★ 只用品类线索，**不能用推荐菜名** —— 实测推荐菜名列表
#     （如「美式咖啡,拿铁,…」）会把咖啡店误判成「美式西餐」。
#     snapshot 层已把推荐菜归到 dish_hints，不会混进 category_hint。
TAG_MAP = [
    (u'汉堡|炸鸡|披萨|西式快餐', u'快餐'),
    (u'小吃|生煎|锅贴|包子|煎饼|卤味|鸭脖', u'小吃'),
    (u'面|粉|米线|拉面', u'面馆'),
    (u'咖啡', u'咖啡'),
    (u'饮品|茶|奶茶', u'茶饮'),
    (u'面包|蛋糕|烘焙|甜点|甜品', u'面包甜点'),
    (u'火锅|涮|烧烤|烤肉|串', u'火锅烧烤'),
    (u'日本料理|日料|寿司|刺身|居酒屋', u'日料'),
    (u'韩国|韩式', u'韩餐'),
    (u'泰国|越南|东南亚|新马|咖喱', u'东南亚'),
    # ★ 不含「美式」—— 会把「美式咖啡」误判成西餐
    (u'西餐|牛排|意式|法式|德式|西班牙|地中海', u'西餐'),
    (u'上海菜|本帮|中餐|家常|江浙|粤菜|川菜|湘菜|云贵|清真', u'中餐本帮'),
]

# ── ③ 店名关键词（品牌兜底，不依赖任何数据源）────────────────
WEST_FAST = (u'麦当劳|肯德基|汉堡王|华莱士|德克士|塔斯汀|赛百味|'
             u'必胜客|比萨|披萨|汉堡|KFC|Subway|Burger')
NAME_MAP = [
    (WEST_FAST, u'快餐'),
    (u'星巴克|瑞幸|Manner|Costa|太平洋咖啡|%Arabica|Tims|coffee|Coffee',
     u'咖啡'),
    (u'喜茶|奈雪|蜜雪冰城|乐乐茶|茶百道|古茗|沪上阿姨|一点点|书亦|霸王茶姬',
     u'茶饮'),
    (u'海底捞|呷哺|小龙坎|蜀大侠|楠火锅|朱光玉|捞王', u'火锅烧烤'),
    (u'寿司|居酒屋|烧鸟|和食|日料|一番|食其家|吉野家|Sushi', u'日料'),
    (u'韩国|韩式|首尔|Seoul|部队锅', u'韩餐'),
    (u'泰国|泰式|越南|Pho|新马|马来|印尼|Singapore', u'东南亚'),
    (u'兰州拉面|沙县小吃|黄焖鸡|张亮麻辣烫|杨国福|米线|面馆|拉面',
     u'面馆'),
    (u'沙县|小吃|生煎|锅贴|汤包|馄饨', u'小吃'),
]

_TAG_RE = [(re.compile(k), v) for k, v in TAG_MAP]
_NAME_RE = [(re.compile(k, re.I), v) for k, v in NAME_MAP]


def _add(out, tag):
    if tag and tag in INDUSTRIES and tag not in out:
        out.append(tag)


def classify(store):
    u"""返回该门店所属的业态列表（可能多个，可能为空）

    ★ 本函数**不认识任何地图服务商** —— 高德类目的翻译在
      `providers/geo_amap.py` 里做完，这里只消费归一化结果。
    """
    out = []
    # ① provider 归一化结果（最可靠）
    inds = store.get('industries')
    if isinstance(inds, (list, tuple)):
        for ind in inds:
            _add(out, ind)
    elif isinstance(inds, str):          # 容错：单个字符串
        _add(out, inds)

    # ② 品类字段（已由 snapshot 层归一化成 category_hint）
    tag = store.get('category_hint') or u''
    for rx, ind in _TAG_RE:
        if rx.search(tag):
            _add(out, ind)

    # ③ 店名（品牌识别 —— 唯一不依赖数据源的一层）
    nm = store.get('name') or u''
    for rx, ind in _NAME_RE:
        if rx.search(nm):
            _add(out, ind)
    return out


def match(store, keyword):
    u"""这家店是否属于 keyword 业态。

    keyword 若在 GROUPS 里（如「西餐」），按**聚合**判定；
    否则按细标签判定。空 / 「全部」恒为 True。
    """
    if not keyword or keyword in (u'全部', u'all'):
        return True
    tags = set(classify(store))
    if keyword in GROUPS:
        return bool(tags & set(GROUPS[keyword]))
    return keyword in tags


def apply(stores, keyword):
    u"""按业态筛选门店列表"""
    return [s for s in stores if match(s, keyword)]


def counts(stores):
    u"""统计各业态门店数（筛选条上显示「快餐 (18)」用）。

    ★ 「西餐」既是**细标签**（欧美菜）又是**聚合键** ——
      如果两边都累加就会算两遍（实测：真值 21 家，被算成 29 家）。
      做法：聚合键只在第二遍统计，细标签那一遍跳过它。
    """
    c = {}
    for s in stores:
        for ind in classify(s):
            if ind in GROUPS:
                continue                       # 交给下面按聚合口径算
            c[ind] = c.get(ind, 0) + 1
    for s in stores:
        tags = set(classify(s))
        for g, members in GROUPS.items():
            if tags & set(members):
                c[g] = c.get(g, 0) + 1
    return c


def coverage(stores):
    u"""分类覆盖率自检 —— 有多少家**一个业态都没打上**。

    ★ 换地图服务商后必须看这个数：provider 不产出 `industries` 时，
      覆盖率会明显掉（实测 K11：82% → 28%）。
    """
    n = len(stores)
    if not n:
        return {'total': 0, 'tagged': 0, 'untagged': 0, 'rate': 0.0, 'samples': []}
    tagged = [s for s in stores if classify(s)]
    unt = [s for s in stores if not classify(s)]
    return {'total': n, 'tagged': len(tagged), 'untagged': len(unt),
            'rate': 100.0 * len(tagged) / n,
            'samples': [s.get('name') for s in unt[:5]]}


if __name__ == '__main__':
    import io
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = json.load(io.open(os.path.join(root, 'examples', 'k11', 'stores.json'),
                          encoding='utf-8'))
    pool = d['mcd_stores'] + d['local_shops']
    # 快照里的 industries 由 provider / 包在采集时写好（见 geo.py 契约）
    n_ind = len([s for s in pool if s.get('industries')])

    c = counts(pool)
    print(u'K11 淡点池 %d 家（其中 %d 家带 industries）：' % (len(pool), n_ind))
    for ind in INDUSTRIES:
        mark = u'   ← 聚合（= 非中餐正餐）' if ind in GROUPS else ''
        print(u'  %-6s %3d 家%s' % (ind, c.get(ind, 0), mark))
    cov = coverage(pool)
    print(u'\n分类覆盖率 %d/%d = %.0f%%（未分类 %d 家 %s）'
          % (cov['tagged'], cov['total'], cov['rate'], cov['untagged'],
             cov['samples']))
    print(u'\n「西餐」下钻 —— 它到底包含谁：')
    for s in apply(pool, u'西餐')[:8]:
        print(u'   %-26s %s' % (s['name'][:24], u'/'.join(classify(s))))
