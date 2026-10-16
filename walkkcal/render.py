# -*- coding: utf-8 -*-
u"""虹口/静安点位 · walk-kcal 主视觉（圆心图 + 表格）v8

本轮改动（用户 2026-10-12 15:12 反馈）：
① 出发点继续往左下角移动、图铺满 —— 用**包围盒自适应缩放 + 平移**
   （x/y 独立缩放），并保留少量页边距
   ★ 不旋转地图：北始终朝上，图上方位即真实方位
② 卡片里的编号不再是又粗又黑的大字 —— 改成左侧独立序号列
   （图标缩小到 r=14，序号用小号灰字）
③ 菜品分左右两列：左「奢华 · 推荐」（超出额度的大份菜）
   右「额度内」（刚好 / 偏低）；判定用「热量与额度差 ±50 卡」，
   不再显示「中等 / 偏高」这类文字档位，改为色块 + 卡数
   ★ 配色沿用原档位色板（TIER_FILL），不改动视觉体系

数据源（**全部通过 provider 层，本文件不认识任何服务商**）：
  麦当劳  → MCP `list-nutrition-foods` 快照（官方精确值）
  本地店  → 当前地图 provider 的推荐菜字段 + 人均（默认高德，可用
            WALKKCAL_GEO 切到百度/腾讯…）
  步行距离 → 当前地图 provider 的真实路网规划（非直线）
  热量    → 当前语料 provider（默认下厨房，可用 WALKKCAL_CORPUS 切）

★ 快照读取一律走 `walkkcal/snapshot.py`（唯一认快照格式的地方）。
  历史上留过三套互不相同的快照字段名，直接读会锁死在某一家服务商上。
"""
import io, json, math, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, ROOT)
from walkkcal import prior  # ★ 菜品级判断（is_menu_item）的唯一归属地
from walkkcal.olist import OList
from walkkcal.icons import ICONS, pin as draw_pin
from walkkcal.theme import (TIER_FILL, TIER_TEXT, TIER_BG, TIER_ORDER,
                            category_of, WB_TEAL)
from packs.mcd_nutrition_snapshot import nutrition_of, SNAPSHOT_DATE

# ★ 场景化数据目录：默认 data/，可用 WALKKCAL_DATA=data/k11 切到别的点位
DATA = os.environ.get('WALKKCAL_DATA') or os.path.join(ROOT, 'data')
if not os.path.isabs(DATA):
    DATA = os.path.join(ROOT, DATA)

# ★★ 快照统一走 snapshot 层（**本文件不认识任何地图服务商**）
#   旧快照里只有服务商私有类目 → 由**当前 provider** 现场翻译（translate 钩子）。
#   这样 WALKKCAL_GEO=baidu 时读百度快照也能正确分类。
from walkkcal import snapshot as _snap  # noqa


def _translate(raw):
    try:
        from walkkcal import geo as _geo
        return _geo.get().industries_of(raw)
    except Exception:
        return []


def _provider_caps():
    u"""当前地图服务商的能力声明（见 walkkcal/geo.py 的契约）。

    ★ `provides_dishes` = 这家地图给不给「推荐菜」。
      高德给（POI 详情的 `tag`）；**百度不给**（实测 2026-10-14）。
      不给的时候，整条链路只能做到「门店 + 业态 + 距离」，
      不能因为「没有菜」就把门店丢掉 —— 否则整张图会空掉。
      实测：百度在 K11 抓到 50 家，全被剔除后只剩 1 家麦当劳 + 废地图。
    """
    try:
        from walkkcal import geo as _geo
        p = _geo.get()
        return {'name': getattr(p, 'name', u'未知'),
                'provides_dishes': bool(getattr(p, 'provides_dishes', True))}
    except Exception:
        return {'name': u'未知', 'provides_dishes': True}


CAPS = _provider_caps()
_PROV_DISHES = CAPS['provides_dishes']

# ★ 2026-10-15：「源装配」一行 —— 渠道 / 店 / 菜 / 热 各自来自谁。
#   作用是把「退化」显形：只剩麦当劳、或换到不提供推荐菜的渠道，
#   这行会写清楚，而不是让图静默变空。
try:
    from packs import registry as _reg
    print(_reg.describe())
except Exception as _e:                                    # noqa
    print(u'（源装配探测失败：%s）' % _e)


_S = _snap.read_stores(os.path.join(DATA, 'stores.json'), translate=_translate)
_DT = _snap.read_detail(os.path.join(DATA, 'detail.json'), translate=_translate)
# 保留 D / DET 两个名字与键位（下游代码量大，不值得为改名再动一遍）
D = {'origin': _S['origin'], 'mcd_stores': _S['mcd'],
     'local_shops': _S['local'], 'scenes': _S['scenes']}
# ★★ 2026-10-16：麦当劳取**两个文件的并集**，不再「只认 detail.json」。
#
#   根因：文字链（`packs/pack_mcd.py`）读 `stores.json` 的 `mcd`，本文件只读
#   `detail.json` 的 `mcd` —— 而**两个文件采集半径不同**（池子 ≈1.4 km / 明细 ≈0.8 km）。
#        data/linghelu：文字链说得出「麦当劳 807 m」，图上连一家都没有
#        （807 m 那家落在 detail 的 ~800 m 采集线之外）
#   Trae CN 实测里宿主为此向用户道歉，而且编了个**错的**理由（猜「800 米半径挡住」）。
#
#   ★ 是「并集」不是「改成只读池子」：任一侧多出来的店都不该丢。
#   ★ 实测 6 个场景并集 == 池子那一侧 → **出图内容与旧版完全一致**
#     （默认场景 1500 m 内仍是同样那 5 家；k11/jintie/mchq/baidu_k11 全等；
#      只有 linghelu 从 0 家变 1 家 —— 正是要修的那条）。
#   ★ 判据 / 回归：`tests/test_gate.py::test_mcd_two_chains_same_source`
#     （两条链在**展示半径内**必须同源；两边各一个断言）。
DET = {'mcd': _snap.merge_mcd(_S['mcd'], _DT['mcd']), 'local': _DT['local']}

# ═══════════════════════════════════════════════════════════════════
# ★★ 2026-10-15：店名里标了营业状态的店 **不进图**。
#
#   地图源**没有营业状态字段**（实测高德 v3 45 字段、v5 `business`、百度 POI
#   详情都没有），但偶尔会把状态写进**店名**（全库实测命中 3 家 ≈ 0.05%）：
#       「黄山菜饭(凌河路店)(暂停营业)」
#   频率极低 → 当不了通用关店判据；但「命中即确定」，没理由还用。
#
#   ★ 文字链（`select.pick`）已经剔了，**图片链之前没剔** —— 于是交付图上
#     赫然印着这家暂停营业的店，而 `clean_name` 按 `(` 截断显示成「黄山菜饭」，
#     用户连标记都看不到。更糟的是脚注当时会写「已剔除 N 家」而图里明明还在。
#     **同一个概念在两条链里必须同口径**（这条教训项目里反复出现过多次）。
#
#   ★ 命中面比预想的大：全库 3 家里，**2 家出现在已交付的图上** ——
#     K11 的「猴哒喇小酒馆(暖暖锅)」、凌河路的「黄山菜饭(凌河路店)」。
#   ★ **两个层都要滤**：卡片层（`detail.json` → `DET['local']`）**和**
#     地图淡点层（`stores.json` → `D['local_shops']`）。只滤前者的话，
#     那家店还会以淡点形式留在图上。
#     两层来自两个文件，同一家店可能在两层都出现 → **去重后再计数**。
# ═══════════════════════════════════════════════════════════════════
_CLOSED = []
_CLOSED_KEYS = set()
for _l in list(DET['local']) + list(D['local_shops']):
    if not _l.get('status'):
        continue
    _k = _l.get('id') or _l.get('name')
    if _k in _CLOSED_KEYS:
        continue
    _CLOSED_KEYS.add(_k)
    _CLOSED.append(_l)
_CLOSED_N = len(_CLOSED)
# 状态码 → 中文（`parse_status` 返回的是 'paused'/'closed'/'moved'）
_STATUS_CN = {'paused': u'暂停营业', 'closed': u'已关闭', 'moved': u'已搬迁'}
if _CLOSED_N:
    DET['local'] = [l for l in DET['local'] if not l.get('status')]
    D['local_shops'] = [s for s in D['local_shops'] if not s.get('status')]
    print(u'已剔除 %d 家店名标注「%s」的店 —— %s'
          % (_CLOSED_N, _STATUS_CN.get(_CLOSED[0].get('status'),
                                       _CLOSED[0].get('status') or u''),
             u'、'.join((l.get('name_clean') or l.get('name') or u'')
                        for l in _CLOSED[:3])))

INK, SUB, MUTE, LINE = '#16191d', '#5b6570', '#98a2ac', '#e6e2dc'
BG, MAP_BG = '#fbf8f4', '#f2f7f5'
# ★ 本餐额度（用户设定的分母）—— 允许由调用方覆盖。
#   2026-10-15 修：文字链（`_pipeline(quota=…)`）一直支持用户指定，
#   渲染链却把 450 写死 → 用户说「我只想吃 350 卡」时，
#   **话术说 350、图上印 450**，自相矛盾。这里补上同一个口子。
ALLOWANCE_BASE = int(os.environ.get('WALKKCAL_QUOTA') or 450)
PCM = 4


def esc(s):
    u"""★ SVG 文本转义 —— 店名/菜名里的 `&` `<` `>` 必须转，否则 XML 解析失败。

    实测中出过问题：「Baker&Spice」里的 `&` 未转义 → `not well-formed` 整张图挂掉。
    只有品牌名带 `&` 时才触发，所以很久没被发现。
    """
    if s is None:
        return u''
    return (u'%s' % s).replace(u'&', u'&amp;').replace(
        u'<', u'&lt;').replace(u'>', u'&gt;')

# 画布
W, PAD = 480, 20
MAP_H = 440
MAP_X, MAP_Y = PAD - 7, 86
MAP_W, MAP_HH = W - 2 * PAD + 14, MAP_H + 14

# ── ③ 颜色档位 ──────────────────────────────────────────────
#   菜的颜色 = **这道菜的热量档位**（tier_of）
#   店的颜色 = **「额度内能吃的菜占比」**（shop_tier_of）★ 2026-10-12 改
#   ★ 为什么改：旧逻辑取「该店所有菜里出现次数最多的档位」——
#     那是「这家店菜有多热」，和「这家店适不适合我」没关系。
#     用户建议按「能吃的比例」上色，一眼就能看出哪家适合自己。
#   ±FIT_TOL 只用于标「刚好」
FIT_TOL = 50                      # 差值在此范围内算「刚好」
DISH_MAX = 8                      # ★ 每店最多列 8 道菜（不足全列）
RESERVE_FIT = 2                   # ★ 若前 8 道全超额度，腾出最后 2 个给额度内的
_TIER_BOUNDS = [120, 250, 400, 600]      # 5 档对应 4 个上界


def tier_of(k):
    for i, b in enumerate(_TIER_BOUNDS):
        if k < b:
            return TIER_ORDER[i]
    return TIER_ORDER[-1]


def shop_tier_of(kcals, quota):
    u'''店铺颜色 = **推荐菜整体相对「热量缺口（本餐额度）」是什么关系**
    （2026-10-12 第 42 轮重定义 —— 从"绝对 400 卡"改为"相对额度"）

    ★ 用户原话：颜色标准写成「高于热量缺口 / 适配热量缺口 / 低于热量缺口」

    口径：以**该店**的推荐菜为样本，统计「低于额度」「接近额度」「高于额度」
      各占几成，取占比最高的那档定性。
        · 低于缺口（绿）= 大部分推荐菜都在你的额度之下 —— 随便点都行
        · 适配缺口（黄）= 一半左右正好卡在额度附近 —— 点一道刚好
        · 高于缺口（红）= 大部分推荐菜都超过你的额度 —— 容易吃超

    「接近额度」= 单道落在 [0.70·额度, 1.00·额度]（七成以上、且不超）；
    超过额度即算「高于缺口」。统计三档占比，取最高的定性。
    兜底：若三档数量并列，按 高于 > 适配 > 低于 优先（偏保守提示）。
    '''
    _lo, _hi = quota * 0.70, quota * 1.00
    _below = len([k for k in kcals if k < _lo])
    _near = len([k for k in kcals if _lo <= k <= _hi])
    _over = len([k for k in kcals if k > _hi])
    if not kcals:
        return TIER_ORDER[2]
    _n = float(len(kcals))
    _ra, _rn, _ro = _below / _n, _near / _n, _over / _n
    if _ro >= _rn and _ro >= _ra:
        return TIER_ORDER[4]          # 高于缺口（红）
    if _rn >= _ra:
        return TIER_ORDER[2]          # 适配缺口（黄）
    return TIER_ORDER[0]              # 低于缺口（绿）

# 复用现有 5 档色阶的 3 个锚点：很低(绿) / 中等(黄) / 很高(红)
SHOP_LEVEL = {0: u'低于缺口', 2: u'适配缺口', 4: u'高于缺口'}
HIGH_BOUND = 400                  # （仅旧逻辑遗留，现由额度决定门槛）


def verdict(k, q):
    u'''吃得起 = 不超额度；刚好 = 差值在 ±FIT_TOL 内'''
    if k > q:
        return u'超了'
    return u'刚好' if abs(q - k) <= FIT_TOL else u'吃下'


# ═══════════════════════════════════════════════════════════════════
# 屏蔽饮料与甜品
# ═══════════════════════════════════════════════════════════════════
_NOISE = (u'双人餐', u'单人餐', u'下午茶', u'商务宴请', u'宴会', u'聚餐',
          u'商务宴请', u'家宴', u'私宴', u'宴会厅', u'包间', u'雅间', u'外卖')
# 店名里的噪声词（高德把商家宣传语拼进了 name）
_NAME_NOISE = (u'商务宴请', u'家宴', u'私宴', u'宴会厅', u'宴会', u'聚餐',
               u'包间', u'外卖', u'旗舰店', u'概念店', u'餐厅', u'饭店')


def clean_name(n):
    u'''去掉店名里的宣传语与「餐厅」后缀，保留品牌与分店

    高德把商家宣传语拼进 name，形式是「品牌·宣传1·宣传2·宣传3(分店)」。
    ★ 用「按分隔符切段 + 逐段过滤」而不是 replace：
      replace 会在段间留下孤立的分隔符（实测留下「····」）。
    '''
    head, _, tail = n.partition(u'(')
    segs = [s.strip() for s in head.replace(u'·', u'\n').split(u'\n')]
    keep = [s for s in segs if s and not any(w in s for w in _NAME_NOISE)]
    if not keep:
        keep = segs[:1]
    return u'·'.join(keep) + (u'(' + tail if tail else u'')
# ═══════════════════════════════════════════════════════════════════
# ★ 2026-10-15：菜品过滤规则**已上移到 `walkkcal/prior.py::is_menu_item`**。
#
#   以前这里有一份私有实现（`_EXCLUDE` / `_CATEGORY_WORDS` / `_DRINK_SUBSTR`
#   / `_EXTRA_CAT`），而 `packs/registry.py` 用的是 `prior.is_category_word`
#   —— **两条链路两套规则**，结果同一条数据在网页上被滤、在 MCP 上没滤。
#
#   更关键的是：那套用的是**精确匹配**（`dish not in _EXCLUDE`），
#   对多变体完全失效 —— 表里有「奶咖」，但数据里是「鲜萃奶咖」→ 漏。
#   实测漏掉的：「鲜萃奶咖」「柠檬气泡饮」「螃蟹」「鸡翅」「泡菜」「玉米」。
#
#   统一后：render / registry / select 用**同一个判断**，且是**词根子串**匹配。
# ═══════════════════════════════════════════════════════════════════
def usable(dish):
    u"""这道菜该不该上卡片（转发 `prior.is_menu_item`）"""
    return prior.is_menu_item(dish)


def split_tag(t):
    if not t:
        return []
    return [x.strip() for x in t.split(',') if x.strip()]


# ── 麦当劳：MCP 营养表 ────────────────────────────────────────
# ★ 同品牌不同门店套餐组合不同（真实情况），全用同一批菜像复制粘贴
# ★ 菜名必须与 packs/mcd_nutrition_snapshot.py 里的键**精确一致**：
#   快照里是「麦辣鸡翅（3块）」，写成「麦鸡翅（3块）」查不到 → 套餐只剩 2 道菜，
#   左右两列就会显示同一道（实测过）。
_MCD_COMBOS = [
    (u'麦辣鸡腿堡', u'麦辣鸡翅（3块）', u'麦满分', u'吉士汉堡包'),
    (u'麦香鱼', u'薯条（中）', u'麦鸡腿汉堡', u'麦满分'),
    (u'麦鸡腿汉堡', u'麦辣鸡翅（3块）', u'吉士汉堡包', u'香芋派'),
    (u'麦辣鸡腿堡', u'双层深海鳕鱼堡', u'麦辣鸡翅（3块）', u'麦满分'),
]

items = []
mcd_ok = 0
# ★★ 2026-10-12 用户第 3 条：**选址不能只看「最近」** ——
#   实测最近 7 家全在西侧/北侧（方位 257°~354°），图上东南空一片，
#   但全量 233 家里东南方向明明有 59 家（只是最近的在 558m 外）。
#   改成 **象限配额选店**：每个象限都要照顾到，但不能为了凑数硬塞。
#
#   规则：
#     · 麦当劳**全部保留**（比赛要求，且是主线）
#     · 本地店按象限分组，每象限取最近的若干家；
#     · 象限配额 = 4 家，但**距离超过 MCD_CAP 的不取**（远的象限不硬凑，
#       因为「走 800 米才有一家沙县」这件事本身就是有用信息，
#       但把 800 米外的店和 120 米的店并列会误导用户以为它们一样近）。
#     · 麦当劳不占本地店配额。
_MCD_MAX_DIST = 1500     # 麦当劳取 1.5km 内
_LOC_MAX_DIST = 1200     # 本地店取 1.2km 内
# ★ 第 50 轮：象限配额**不再在 render 里做** —— 交给 select.pick 统一处理。
#   原来这里每象限只留 3 家（本地共 12 家），导致：
#     · 业态筛选（「附近西餐」）只能在这 12 家里筛 → 结果严重不全
#     · 翻页没有足够候选 → 「换一批」换不出新东西
#   现在 render 只做「距离上限」粗筛，真正的排序/均衡/封顶/翻页都在 select.py。
_LOC_PER_QUAD = 999      # 等于不限（保留变量名，避免其它引用报错）

# ── 先收集麦当劳（按距离，保留配额内全部）────────────────────
for _mi, m in enumerate(sorted(DET['mcd'], key=lambda z: z['distance_m'] or 0)):
    if (m['distance_m'] or 0) > _MCD_MAX_DIST:
        continue
    # ★ 麦当劳的高德 tag 是「汉堡/单人餐/下午茶」这类**品类词**，
    #   不是单品，所以麦当劳一律走 MCP 菜单，不用高德 tag。
    mins = (m['distance_m'] or 0) // 80 + 1
    q = ALLOWANCE_BASE + mins * PCM
    ds = []
    for nm in _MCD_COMBOS[_mi % len(_MCD_COMBOS)]:
        v = nutrition_of(nm)
        if v:
            ds.append(dict(name=nm, kcal=v[0], src=u'mcd'))
    if not ds:
        continue
    mcd_ok += 1
    items.append(dict(name=m['name'], disp=clean_name(m['name']),
                      walk=m['distance_m'], mins=mins, quota=q,
                      dishes=ds, cost=m.get('cost'), cat=u'fastfood',
                      catlabel=u'麦当劳', is_mcd=True,
                      industries=list(m.get('industries') or []),
                      category_hint=m.get('category_hint') or u''))

# ── 热量取值：走分层入口（与 registry 一致）──────────────────
#   ★ 第 48 轮：原来直接用 LOCAL_HEAT 查表，语料没有的菜会被判「算不出」
#     并导致整家店被剔除（市中心茶饮/地方菜大量命中）。
#     改为调 corpus.lookup()：L1 语料 → L2 菜名品类 → L3 门店类型。
from walkkcal import corpus as _corpus  # noqa


def _hint_of(store):
    u"""门店线索（供 L3 兜底）—— **结构化三级，不互相污染**。

    ★ 只用**归一化字段**：`industries`（provider 翻译好的业态）
      + `category_hint` + 店名 + 推荐菜名。
      以前拼的是 `type;keytag;atag`（高德私有字段），换地图后全空 → L3 失效。
    """
    return {'industries': list(store.get('industries') or []),
            'text': u';'.join([store.get('category_hint') or u'',
                               store.get('name') or u'']),
            'dish_names': list(store.get('dish_hints') or [])}


def heat_lookup(dish, store=None):
    u"""分层取热量，返回 {'kcal','label','tier'} 或 None"""
    return _corpus.lookup(dish, _hint_of(store or {}))


# ── 本地店：先按象限分桶，再每桶取最近的 N 家 ─────────────────
OLON0, OLAT0 = float(D['origin']['lon']), float(D['origin']['lat'])
_KM_LON0 = 111000 * math.cos(math.radians(OLAT0))


def _bearing_of(lon, lat):
    u'''方位角：0=正北，90=正东（顺时针）'''
    dx = (float(lon) - OLON0) * _KM_LON0
    dy = (float(lat) - OLAT0) * 111000.0
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _quad_of(b):
    u'''0=北东 1=南东 2=南西 3=北西'''
    return 0 if b < 90 else (1 if b < 180 else (2 if b < 270 else 3))


# ═══════════════════════════════════════════════════════════════════
# ★★ 2026-10-15：安全带 —— 不靠「声明」，靠「事实」
#   原来「是否按有没有菜剔除门店」完全取决于 `provides_dishes` 声明，
#   一旦漏声明，后果是**静默**的：实测百度那 50 家门店被全部剔除，
#   只剩 1 家麦当劳 + 一张 480×916 的废图（地图缩放退化到 0×0）。
#   现在加一道事实检查：整批门店一道可用的推荐菜都没有 →
#   无论声明怎么写，都按「不提供推荐菜」处理（门店模式）。
# ═══════════════════════════════════════════════════════════════════
_HAS_ANY_DISH = any(any(usable(c) for c in (l.get('dish_hints') or []))
                    for l in DET['local'])
if _PROV_DISHES and not _HAS_ANY_DISH:
    print(u'⚠ 当前数据源没有任何推荐菜 → 退回「门店模式」'
          u'（只展示门店 / 距离 / 业态）')
    _PROV_DISHES = False

# ★ 2026-10-15：渲染阶段「因为一道可用菜都没有」被丢掉的**纯饮品店**计数。
#   它们到不了 `select`，所以不会进 select 的 `excluded_drink` —— 不补这一笔，
#   脚注「已隐去 N 家」的数字会凭空变小（店其实一家没少，只是没被数到）。
#   起因：把「柠檬茶」正确识别为饮品后，柠季/LINLEE 这类店在**渲染阶段**就没了。
_DROPPED_DRINK = 0
_quad_buckets = {0: [], 1: [], 2: [], 3: []}
for l in DET['local']:
    if not l.get('lon') or (l['distance_m'] or 0) > _LOC_MAX_DIST:
        continue
    # ★ 先排除「没有可用推荐菜」的店（tag 只有单人餐/双人餐/下午茶等品类词），
    #   否则它们会白占象限配额，把真正有数据的店挤出去（太琼火锅就是这么丢的）。
    _c = [c for c in (l.get('dish_hints') or []) if usable(c)]
    # ★ 第 48 轮：改用分层判定（语料查不到但能按品类/门店类型兜底 → 仍算「有数据」）
    # ★ 2026-10-14：当前服务商不提供推荐菜时（实测百度），**不能按「有没有菜」筛**，
    #   否则 50 家门店全在这里被剔除，只剩麦当劳，出一张废图。
    if _c:
        if not any(heat_lookup(c, l) for c in _c):
            continue
    elif _PROV_DISHES:
        # ★ 一道可用菜都没有 → 丢掉这家店。
        #   若它是「只卖喝的」，这一笔要记账，否则它就**静默消失**了
        #   （脚注失实）—— 见上方 _DROPPED_DRINK 的说明。
        if prior.is_drink_only_store(l):
            _DROPPED_DRINK += 1
        continue
    _quad_buckets[_quad_of(_bearing_of(l['lon'], l['lat']))].append(l)

# 每象限按距离升序取前 N
_loc_pick = []
for _q in (0, 1, 2, 3):
    _bucket = sorted(_quad_buckets[_q], key=lambda z: z['distance_m'] or 0)
    _loc_pick.extend(_bucket[:_LOC_PER_QUAD])
# 最终按距离排序（后面统一重排编号）
_loc_pick.sort(key=lambda z: z['distance_m'] or 0)

loc_ok = 0
for l in _loc_pick:
    # ★ 2026-10-12 用户第 1 条：**不按店名排除甜品店** ——
    #   判据应该是「这家店的推荐菜有没有一道是正餐」，不是店名里有没有「蛋糕」。
    #   （朱光玉火锅馆/太琼火锅的 tag 里都带「点心/冰淇淋」，按店名或关键词排除都是错的。）
    #   纯甜品/纯咖啡店会在下面「无可用正餐菜」这步自然被剔除。
    cand = [c for c in (l.get('dish_hints') or []) if usable(c)]
    # ★ 当前地图服务商不提供推荐菜时（实测：百度），门店**不因缺菜被丢掉**
    if not cand and not _PROV_DISHES:
        cand = []
    elif not cand:
        continue
    mins = (l['distance_m'] or 0) // 80 + 1
    q = ALLOWANCE_BASE + mins * PCM
    ds = []
    for c in cand[:10]:
        _h = heat_lookup(c, l)
        ds.append(dict(name=c, kcal=(_h['kcal'] if _h else None),
                       src=(_h['label'] if _h else u'算不出'),
                       tier=(_h['tier'] if _h else None)))
    # 既无推荐菜 → 不显示（**除非**当前服务商本来就不提供推荐菜）；
    # 整家店的菜都算不出 → 也不显示（如纯咖啡店）
    _nodish = (not ds) or (not any(d['kcal'] for d in ds))
    if _nodish and not (not _PROV_DISHES and not ds):
        continue
    # ★ 图标：先看归一化业态（换地图不会失效），再退回文本兜底
    cat, catlabel = category_of({'name': l['name'], 'tags': cand,
                                 'industries': l.get('industries'),
                                 'category_hint': l.get('category_hint'),
                                 'type': l.get('raw', {}).get('type', u'')})
    items.append(dict(name=l['name'], disp=clean_name(l['name']),
                      walk=l['distance_m'], mins=mins, quota=q,
                      dishes=ds, cost=l.get('cost'), cat=cat,
                      catlabel=catlabel, is_mcd=False,
                      nodish=_nodish,
                      industries=list(l.get('industries') or []),
                      category_hint=l.get('category_hint') or u''))
    loc_ok += 1

# ── 排序 + 每道菜是否吃得起 ─────────────────────────────────
for it in items:
    for d in it['dishes']:
        if d.get('kcal'):
            d['tier'] = tier_of(d['kcal'])
            d['ok'] = d['kcal'] <= it['quota']
            d['gap'] = it['quota'] - d['kcal']
        else:
            d['tier'], d['ok'], d['gap'] = None, False, 0
    # 菜品保持**原始顺序**（= 高德 tag 的网友推荐顺序），不再重排
    known = [d for d in it['dishes'] if d['kcal']]
    fit_n = len([d for d in known if d['ok']])
    it['fit_n'] = fit_n
    it['known_n'] = len(known)
    # ★ 2026-10-12 第 42 轮：店铺颜色 = **推荐菜相对「热量缺口（额度）」的关系**
    #   （低于缺口 / 适配缺口 / 高于缺口 —— 由 shop_tier_of 统计占比定性）
    it['tier'] = shop_tier_of([d['kcal'] for d in known], it['quota'])
    it['all_over'] = (len(known) > 0 and fit_n == 0)   # 一道都吃不下

# ★★ 第 48 轮：**用 select.py 的统一规则封顶**（默认 15 家）
#   原来 render 只按象限配额选（12 本地 + 6 麦当劳 = 18 家），
#   与 select.py 的规则不一致 → 现在统一走同一份规则，界面也就能复述。
import walkkcal.select as _sel  # noqa
_COORD_BY_NAME = {}
for _l in DET['local'] + DET['mcd']:
    if _l.get('lon'):
        _COORD_BY_NAME[_l['name']] = (float(_l['lon']), float(_l['lat']))
for it in items:
    _c = _COORD_BY_NAME.get(it['name'])
    it['quad'] = _quad_of(_bearing_of(_c[0], _c[1])) if _c else 0
# ★ 第 51 轮：业态限定 + 翻页 + 半径（用户需求）
#   WALKKCAL_FILTER=西餐  → 只显示西餐
#   WALKKCAL_PAGE=1       → 第 2 批（「换一批」）
#   WALKKCAL_RADIUS=500   → 只保留 500 米内（「别走太远」）
_KEYWORD = os.environ.get('WALKKCAL_FILTER') or None
_PAGE = int(os.environ.get('WALKKCAL_PAGE') or 0)
_RADIUS = int(os.environ.get('WALKKCAL_RADIUS') or 0)
if _RADIUS:
    _before = len(items)
    items = [it for it in items if (it.get('walk') or 0) <= _RADIUS]
    print(u'半径 %d 米：%d 家 → %d 家' % (_RADIUS, _before, len(items)))
_picked, _META = _sel.pick(items, _sel.DEFAULT_N, _PAGE, _KEYWORD)
# ★ 把「渲染阶段因推荐菜清一色是饮品而跳过」的店并进同一个计数。
#   门控与 select 保持一致：用户**点名业态**或**明确要看饮品**时不记 ——
#   否则会出现「脚注说已隐去，而用户正想看」的自相矛盾。
_can_count_dropped = (not _KEYWORD) and (not _sel.include_drink_only())
if _can_count_dropped and _DROPPED_DRINK:
    _META['excluded_drink'] = (_META.get('excluded_drink') or 0) + _DROPPED_DRINK
if _META.get('excluded_drink'):
    print(u'已隐去 %d 家「只卖喝的」（咖啡/茶饮店，推荐菜里没有能当一餐的；'
          u'想看用 WALKKCAL_FILTER=咖啡）' % _META['excluded_drink'])
    if _can_count_dropped and _DROPPED_DRINK:
        print(u'   其中 %d 家在渲染阶段就被跳过（推荐菜里清一色是饮品）'
              % _DROPPED_DRINK)
if _META['pages'] > 1 or _KEYWORD or _RADIUS:
    print(u'筛选「%s」：%d 家 → %d 批，本批 %d 家（第 %d 批）'
          % (_META['keyword'], _META['total'], _META['pages'],
             _META['shown'], _META['page'] + 1))
items = _picked

# ★★ 2026-10-12 第 36 轮：**按颜色排序**（绿→黄→红），同色内按距离。
#   用户要「先显示绿色，再黄色，再红色」——卡片与地图编号保持一致。
#   shop_tier_of 只返回 TIER_ORDER 的 0/2/4 三档，用其 index 当排序键即可。
items.sort(key=lambda x: (TIER_ORDER.index(x['tier']), x['walk']))

# ★★ 第 48 轮：为每家店算一条**入选理由** —— 回答「为什么是这 15 家」
#   用户第 2 问：「我为什么选了这 15 家？」→ 规则要能被逐条复述。
#   判据与 select.py 一致：能吃最多 > 最近 > 均衡。
_by_walk = sorted(range(len(items)), key=lambda i: items[i]['walk'])
_near2 = set(_by_walk[:2])
_maxfit = max((it['fit_n'] for it in items), default=0)
_avg = lambda it: (sum(d['kcal'] for d in it['dishes'] if d['kcal']) /
                   max(len([d for d in it['dishes'] if d['kcal']]), 1))
_lowest_obj = min(items, key=_avg) if items else None
for _i, it in enumerate(items):
    if it['fit_n'] >= _maxfit and _maxfit > 0:
        it['why'] = u'最多'
    elif _i in _near2:
        it['why'] = u'最近'
    elif it is _lowest_obj:
        it['why'] = u'最淡'
    else:
        it['why'] = u'均衡'

# ★★ 2026-10-12 用户新思路（第 30 轮）：**每店按推荐顺序列前 8 道**，
#   规则：
#     · 默认取推荐顺序（高德 tag）的前 DISH_MAX 道
#     · 若这 8 道**全超额度**，则把**最后 2 个位置**换成额度内的菜
#       （保证"最适合我的那道"一定露脸）
#     · 若整家店**一道额度内的都没有** → 保留全部（用户选择"显示"），
#       门店颜色为红 + 标「不合额度」
for i, it in enumerate(items):
    it['idx'] = i + 1
    # ★「算不出」热量的菜**不占展示位**（信息量低）——
    #   只有整家店都算不出时才展示它们（否则那家店就没内容了）。
    known = [d for d in it['dishes'] if d.get('kcal')]
    unknown = [d for d in it['dishes'] if not d.get('kcal')]
    pool = known if known else unknown          # 优先只排「算得出」的
    tail = [] if known else unknown
    sel = list(pool[:DISH_MAX])
    it['sel_unknown'] = False
    if tail:
        sel = tail[:DISH_MAX]
        it['sel_unknown'] = True
    else:
        # 前 DISH_MAX 道全超额度 → 末 RESERVE_FIT 位换成额度内的
        if it['known_n'] > 0 and it['fit_n'] > 0 and not any(d.get('ok') for d in sel):
            _outsiders = [d for d in pool[DISH_MAX:] if d.get('ok')][:RESERVE_FIT]
            if _outsiders:
                sel = sel[:DISH_MAX - len(_outsiders)] + _outsiders
        # 若额度内的菜没进前 DISH_MAX，至少补 1 道进来（保证"你能吃啥"有答案）
        elif it['known_n'] > 0 and it['fit_n'] > 0 and not any(d.get('ok') for d in sel):
            pass
    it['sel'] = sel
    it['rows'] = max(len(sel), 1)
    # 额度内的菜**没出现在展示区**时，追加 1 道（标在右侧「另有」）
    it['extra_fit'] = [d for d in known
                       if d.get('ok') and d not in sel][:1]

# 封顶后重算计数（原来是 pre-cap 的计数，会与 len(items) 对不上）
mcd_ok = len([_it for _it in items if _it.get('is_mcd')])
loc_ok = len(items) - mcd_ok
print(u'共 %d 家（麦当劳 %d + 本地 %d），额度 %d 卡'
      % (len(items), mcd_ok, loc_ok, ALLOWANCE_BASE))
for r in items:
    print(u'  %2d. %-22s %-8s %4dm 额度%4d 能吃%2d/%2d道 %s'
          % (r['idx'], (r.get('disp') or r['name'])[:20], r['catlabel'], r['walk'],
             r['quota'], r['fit_n'], r['known_n'],
             u'全超' if r['all_over'] else r['tier']))

# ★★ 一家都留不下时，**明确报错**，不要带着空列表往下走。
#   出过的问题：百度场景里 50 家门店因「没有推荐菜」被全部剔除 →
#   后面 `max(it['walk'] for it in items)` 直接 ValueError，
#   报错信息完全指不到真正的原因。
if not items:
    _n_snap = len(DET['local']) + len(DET['mcd'])
    raise SystemExit(
        u'⛔ 没有可展示的门店（快照里共 %d 家）。\n'
        u'   当前地图服务商：%s（提供推荐菜：%s）\n'
        u'   常见原因：\n'
        u'     ① 该服务商不提供「推荐菜」，而快照里也没有菜品数据\n'
        u'        → 看 walkkcal/providers/geo_*.py 的 `provides_dishes`；\n'
        u'          百度实测为 False（只能出「门店 + 业态」，本函数会自动降级，\n'
        u'          但若快照里连门店都没有就会走到这里）\n'
        u'     ② 门店都有推荐菜、但一道都算不出热量 → 语料源覆盖不到这个点位\n'
        u'     ③ 半径太小 / 坐标不对 → 快照里本来就没有店\n'
        u'   排查：python walkkcal/snapshot.py 看快照里到底有什么'
        % (_n_snap, CAPS['name'],
           u'是' if _PROV_DISHES else u'否'))


# ═══════════════════════════════════════════════════════════════════
# ② 真实相对方位（北朝上，不旋转）+ 起点自适应偏移
# ═══════════════════════════════════════════════════════════════════

OLON, OLAT = D['origin']['lon'], D['origin']['lat']
_KM_LON = 111000 * math.cos(math.radians(OLAT))

_COORD, _NAME2ID = {}, {}
for l in DET['local']:
    if l.get('lon'):
        _COORD[l['id']] = (float(l['lon']), float(l['lat']))
    _NAME2ID[l['name']] = l['id']
for s in D['mcd_stores']:
    _COORD[s['id']] = (s['lon'], s['lat'])
for _m in DET['mcd']:
    _NAME2ID[_m['name']] = _m['id']

for it in items:
    p = _COORD.get(_NAME2ID.get(it['name']))
    if not p:
        for s in D['local_shops'] + D['mcd_stores']:
            if s['name'] == it['name']:
                p = (s['lon'], s['lat'])
                break
    if p:
        dx = (p[0] - OLON) * _KM_LON
        dy = (p[1] - OLAT) * 111000.0
        it['bearing'] = math.degrees(math.atan2(dx, dy))       # 正北0 顺时针
    else:
        it['bearing'] = 45.0

dmax_true = max(it['walk'] for it in items)


def nice_rings(dm, want=3):
    u'''生成「好读」的等距圈层刻度

    ★ 2026-10-12 用户第 3 条：**圈层不再一路画到最远店**。
      本点位本地店都在 600m 内、麦当劳在 1.0~1.5km —— 两者差 12 倍，
      若外圈画到 1500m，近处 7 家会挤进半径 1/3 的圆盘（实测叠在一起）。
      改为：外圈取「本地店的最远距离（+1 档）」，
      更远的麦当劳**钉在最外圈上**并加「更远」角标（见 r_nom 的钳制）。
    '''
    for step in (100, 150, 200, 250, 300, 400, 500):
        if step * want >= dm:
            return [step * (i + 1) for i in range(want)]
    return [500 * (i + 1) for i in range(want)]


# 圈层覆盖范围 = 本地店最远距离（若无本地店则用全局最远）
_loc_walks = [it['walk'] for it in items if not it['is_mcd']]
_ring_max = max(_loc_walks) if _loc_walks else dmax_true
RINGS = nice_rings(_ring_max)
R_OUT = float(RINGS[-1])
# spread() 记录下来的「半径整体放大系数」（正常必须是 1.0 —— 见 spread 里的说明）
RAD_FACTOR_SEEN = 1.0
_near_n = len([r for r in items if r['walk'] <= RINGS[0]])
# ★★ 2026-10-12 用户第 3 条：半径映射取**折中指数 0.75**（见下方说明）。
_EXP = 0.75
Y_SQUASH = 1.0   # ★ 不再纵向压扁：0.68 会让南北向距离变短（方位失真）
print(u'近圈 %d 家 → 半径指数 %.2f ｜ 圈层 %s（本地最远 %d m，全局最远 %d m）'
      % (_near_n, _EXP, RINGS, _ring_max, dmax_true))


def r_nom(m):
    u'''标称半径（未乘缩放系数），最外圈 = R_OUT

    ★ 圈层刻度**只由这个函数决定**，与 spread() 里为避免重叠而
      调整的 _rn 无关 —— 圈层是「多少米」的真相来源，店铺位置是示意。
    ★ 超过最外圈的店（远处的麦当劳）**半径钳制在最外圈**，
      标记为「更远 1.4km」，位置只是示意 —— 否则会把整张图撑爆。
    '''
    if m > R_OUT:
        return R_OUT
    return (m / R_OUT) ** _EXP * R_OUT


def spread(minsep_rel, max_swing=44.0):
    u'''同象限拥挤时按角度微调散开（方位象限保持正确）

    ★ 三个易错点（都经实测确认）：
      1. 不限制摆动幅度的话，近圈 7 家挤在一起时角度能被推开上百度，
         方位全乱（顶部一排店的真实相对方位都在正西附近，散完跑到正北了）。
      2. **摆动方向必须远离冲突点**：只往同一方向（+3°）加，
         会把冲突点越推越近，最后一堆店落到同一位置（实测出现间距 0px）。
      3. ★ 只调角度不够：本点位近店方位挤在一个扇形内，光摆角度排不开，
         **必须同时沿半径方向拉开**（用全局 factor 整体放大半径）。
    '''
    placed = []
    # ① 按真实步行距离算出标称半径
    for it in items:
        it['_rn'] = r_nom(it['walk'])

    # ② 半径防重叠：按**比例放大**而不是累加，保持分布形状不变
    #    累加会把外圈越推越远（实测店铺跑出圈层之外）。
    #    做法：算出需要的最小组距，再整体乘一个系数把半径拉开。
    #
    # ★★ 2026-10-15 修：**必须给系数设上限**，否则正反馈会炸掉整张图。
    #    病根：`_need` 取的是**最小**半径差。当几家店距离几乎相同
    #    （实测近铁有 690 / 699 / 703 米三家）→ 半径差趋近 0
    #    → `_factor = minsep_rel / _need` 爆炸（实测 17 万倍）
    #    → `_rx` 变成 17 万 → `fit()` 得 KX=0.002 → 第二次
    #    `spread(38/KX)` 的入参更大 → **正反馈，图彻底毁掉**。
    #    日志里的征兆是 `缩放 横向0.002`（正常应是 0.2~0.6）。
    #
    # ★★ 2026-10-15 二次修：上限从 3.0 收到 **1.0（＝不再放大半径）**。
    #    病根不是「爆炸」而是「**地图位置与圈层刻度脱节**」：
    #      店铺像素半径 = r_nom(距离) × _factor，而圈层刻度只由 r_nom 决定
    #      （见 r_nom 的注释：圈层是「多少米」的真相来源）。
    #    实测凌河路 618 弄：_factor 顶满 3.0 →
    #      **所有店的表观距离是真实的 3~7 倍**，
    #      458 m 的店被画在 900 m 圈之外 1.8 倍处（反算像素验证）。
    #    近铁同样（缩放 KX 0.138 → 修后 0.263，失真 4~5.5 倍）。
    #    为何敢直接归 1：**实测四个场景 factor=1.0 时都不重叠**
    #      （自检最近间距 31~38 px，阈值 29 px），
    #      说明「必须整体放大半径才排得开」这个前提不成立 —— 第 ③ 步
    #      的角度散开（≤44°）已经够了。
    #    ⚠ 若将来真有场景排不开，自检会报「店铺图标重叠」，
    #      那时应**改小图标或放宽角度**，而不是放大半径（会毁比例尺）。
    _RAD_SPREAD_MAX = 1.0
    rad_items = sorted(items, key=lambda z: z['_rn'])
    _gaps = [rad_items[i + 1]['_rn'] - rad_items[i]['_rn']
             for i in range(len(rad_items) - 1)]
    _need = min(_gaps) if _gaps else 1.0
    _factor = (max(1.0, min(minsep_rel / _need, _RAD_SPREAD_MAX))
               if _need > 1e-9 else 1.0)
    for it in rad_items:
        it['_rn'] *= _factor
    # ★ 汇报给构建期自检：一旦 >1 就说明店铺位置与圈层刻度开始脱节
    global RAD_FACTOR_SEEN
    if _factor > RAD_FACTOR_SEEN:
        RAD_FACTOR_SEEN = _factor

    for it in rad_items:
        brg0 = math.radians(it['bearing'])
        rad = it['_rn']
        for step in range(40):
            px = math.sin(brg0) * rad
            py = -math.cos(brg0) * rad * Y_SQUASH
            hit = min(placed, key=lambda p: math.hypot(px - p[0], py - p[1])) \
                if placed else None
            if hit is None or math.hypot(px - hit[0], py - hit[1]) >= minsep_rel:
                break
            cur = math.atan2(px, -py)
            away = math.atan2(2 * px - hit[0], -(2 * py - hit[1]))
            brg0 += math.copysign(math.radians(2.5 + step * 0.6), away - cur) \
                if abs(away - cur) > 1e-6 else math.radians(2.5)
        # 最终钳制：与原方位夹角 ≤ max_swing
        px = math.sin(brg0) * rad
        py = -math.cos(brg0) * rad * Y_SQUASH
        cur = math.degrees(math.atan2(px, -py)) % 360.0
        orig = math.degrees(math.radians(it['bearing'])) % 360.0
        d = (cur - orig + 180) % 360.0 - 180
        if abs(d) > max_swing:
            brg0 = math.radians(orig + math.copysign(max_swing, d))
            px = math.sin(brg0) * rad
            py = -math.cos(brg0) * rad * Y_SQUASH
        it['_rx'], it['_ry'] = px, py
        placed.append((px, py))


# ★★ 2026-10-12 用户裁定：**x/y 必须等比缩放**。
#   之前用独立缩放（实测 0.145 / 0.279，纵向拉伸近 2 倍），
#   把「东边 1.4km 的麦当劳」和「西边 130m 的本地店」压成同一方向，
#   **图是失真的**。等比后纵向可能留白 —— 用真实性换铺满，值。
#
#   起点不再「独占一块左边距」（那样横向只剩 328px，等比后近处 8 家全挤在左上），
#   改为：店铺群占满整个地图区，**起点落在方位最空的那个角落** ——
#   真实方位跨 301°，四个角里必然有一个没有店。
MAP_PAD_X, MAP_PAD_Y = 30, 30         # 页边距
FIT_X = MAP_W - 2 * MAP_PAD_X
FIT_Y = MAP_HH - 2 * MAP_PAD_Y


def fit():
    u'''用**单一等比系数**把「格局」缩放到地图区

    ★ 格局 = 店铺点 ∪ 出发点 ∪ **最外圈整圆**（以起点为圆心、R_OUT 为半径）。
      必须把最外圈算进去，否则缩放到店铺包围盒后，最外的圆会溢出画布
      （实测：起点在中间，圆底部跑到地图外，图上出现大片空白）。
    '''
    xs = [it['_rx'] for it in items] + [0.0]
    ys = [it['_ry'] for it in items] + [0.0]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    # 叠加最外圈（起点 ± R_OUT，注意 r_nom(R_OUT) = R_OUT）
    x0, x1 = min(x0, -R_OUT), max(x1, R_OUT)
    y0, y1 = min(y0, -R_OUT), max(y1, R_OUT)
    k = min(FIT_X / max(x1 - x0, 1e-6), FIT_Y / max(y1 - y0, 1e-6))
    return k, k, x0, y0


def project(kx, ky, fx, fy):
    u'''「店铺 + 出发点」包围盒对齐到地图区（含起点的真实相对位置）'''
    ox = MAP_X + MAP_PAD_X - fx * kx
    oy = MAP_Y + MAP_PAD_Y - fy * ky
    for it in items:
        it['px'] = ox + it['_rx'] * kx
        it['py'] = oy + it['_ry'] * ky
    return None


# 散布 → 求缩放 → 再散布（用真实像素尺度精修）
spread(28.0)
KX, KY, FX, FY = fit()
spread(38.0 / max(KX, 0.05))
KX, KY, FX, FY = fit()
project(KX, KY, FX, FY)
R_KX, R_KY = KX, KY

# ★ 最后的兜底避让：在**屏幕像素**层面做纯平移。
#   前面 spread 的避让在「标称半径」空间做，最后 max_swing 钳制可能把
#   已散开的点又推回重叠位置（实测出现两店完全重叠，间距 0px）。
#   这里在最终像素上再推一次 —— 纯平移不改任何相对方位。
#
#   ★ 边界钳制必须**内嵌在避让循环里**：分开写的话（先避让再钳制），
#     被钳在边界上的店会一起贴到同一条边上，重叠依旧（实测间距 21px）。
_MINSEP_PX = 30.0   # 图标直径 22px + 上方编号圆点 15px，30 留 4px 余量
_LO = (MAP_X + MAP_PAD_X, MAP_Y + MAP_PAD_Y)
_HI = (MAP_X + MAP_W - MAP_PAD_X, MAP_Y + MAP_HH - MAP_PAD_Y)


def _push_apart():
    u'''沿连线方向各推一半，直到全部间距达标或达到迭代上限'''
    for _ in range(80):
        _worst, _wa, _wb = None, None, None
        for _i in range(len(items)):
            for _j in range(_i + 1, len(items)):
                a, b = items[_i], items[_j]
                d = math.hypot(a['px'] - b['px'], a['py'] - b['py'])
                if d < _MINSEP_PX and (_worst is None or d < _worst):
                    _worst, _wa, _wb = d, a, b
        if _worst is None:
            return True
        vx = _wb['px'] - _wa['px']
        vy = _wb['py'] - _wa['py']
        n = math.hypot(vx, vy)
        if n < 1e-6:                       # 完全重合：随便给个方向分开
            vx, vy, n = 1.0, 0.0, 1.0
        push = (_MINSEP_PX - _worst) / 2.0 + 0.6
        for _it, _s in ((_wa, -1), (_wb, 1)):
            _it['px'] = max(min(_it['px'] + vx / n * push * _s, _HI[0]), _LO[0])
            _it['py'] = max(min(_it['py'] + vy / n * push * _s, _HI[1]), _LO[1])
    return False


_push_apart()

# ★ 起点用**真实投影位置**（不再甩到空角）：project() 里 (0,0) 已被算进包围盒，
#   所以起点就落在它相对店铺群的真实位置上（四面包围 → 接近中心）。
PIN_X = MAP_X + MAP_PAD_X + (0.0 - FX) * KX
PIN_Y = MAP_Y + MAP_PAD_Y + (0.0 - FY) * KY

_used_x = max(it['px'] for it in items) - min(it['px'] for it in items)
_used_y = max(it['py'] for it in items) - min(it['py'] for it in items)
print(u'缩放 横向%.3f 纵向%.3f ｜ 起点 (%.0f, %.0f) ｜ 店铺群铺开 %.0f×%.0f px'
      '（可用 %.0f×%.0f）' % (KX, KY, PIN_X, PIN_Y, _used_x, _used_y, FIT_X, FIT_Y))

# ── 圈层：以出发点为圆心的**完整圆**（2026-10-12 用户第 3 条）
#   ★ 之前按「有店的方向」裁剪成弧，结果图上大半个圆是空的 —— 用户质疑
#     「二三四象限真的没有餐厅？」。实测四象限都有店（本次已按象限抓数据），
#     所以圈层必须画**整圆**：它表达的是「距出发点 R 米」这个几何事实，
#     与「这个方向有没有店」无关。
A_START = 0.0
A_SPAN = 2 * math.pi


def arc_pts(r_nominal, a0, a1, n=72):
    u'''圈层弧：**正圆**（2026-10-12 用户要求恢复圆形）。

    ★ x/y 已改为等比缩放（用户裁定），且 Y_SQUASH=1.0 不再纵向压扁，
      所以圈层是**真圆**，读数可信。之前是两套系数 → 椭圆 + 刻度失真。
    '''
    r_px = r_nominal * R_KX
    return [(PIN_X + math.sin(a0 + (a1 - a0) * i / float(n)) * r_px,
             PIN_Y - math.cos(a0 + (a1 - a0) * i / float(n)) * r_px)
            for i in range(n + 1)]


# ═══════════════════════════════════════════════════════════════════
# 画布
# ═══════════════════════════════════════════════════════════════════
# ★ 2026-10-12 第 35 轮：卡片区改**左右两列瀑布流**（小红书/点评风格）。
#   单张卡高度 = 表头(50) + 菜品行(rows × 19) + 下沿(12)。
#   贪心装填：每张卡进当前**较矮**的那一列 → 两列高度自动接近，页面高度减半。
#   ★ 第 38 轮：表头 38 → 50（用户反馈图标下端压到第一道菜，加大卡头留白）
_CARD_H = lambda _r: 50 + _r['rows'] * 19 + 12
_COL_GAP = 10
_COL_W = (W - 2 * PAD - _COL_GAP) / 2.0
_col_h = [0.0, 0.0]
_col_cards = [[], []]          # 每列：[(item, 顶部相对偏移)]
for _it in items:
    _ci = 0 if _col_h[0] <= _col_h[1] else 1
    _col_cards[_ci].append((_it, _col_h[_ci]))
    _col_h[_ci] += _CARD_H(_it) + 10
CARDS_H = max(_col_h[0], _col_h[1])
H = int(MAP_Y + MAP_HH + 26 + CARDS_H + 14 + 110 + 78)

o = OList()
o.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" '
         'height="%d" font-family="system-ui,-apple-system,\'PingFang SC\','
         '\'Microsoft YaHei\',sans-serif">' % (W, H, W, H))
o.append('<rect width="%d" height="%d" fill="%s"/>' % (W, H, BG))

# 标题：走多远 · 吃什么 ｜ 当前位置
o.append('<text x="%d" y="34" font-size="17" font-weight="700" fill="%s">走多远</text>'
         % (PAD, INK))
o.append('<text x="%d" y="34" font-size="17" font-weight="700" fill="%s">·</text>'
         % (PAD + 54, WB_TEAL))
o.append('<text x="%d" y="34" font-size="17" font-weight="700" fill="%s">吃什么</text>'
         % (PAD + 64, INK))
o.append('<rect x="%d" y="21" width="4" height="15" rx="2" fill="%s"/>' % (PAD + 132, WB_TEAL))
o.append('<text x="%d" y="22" font-size="9.5" fill="%s">当前位置</text>'
         % (PAD + 143, MUTE))
o.append('<text x="%d" y="37" font-size="13" font-weight="600" fill="%s">%s</text>'
         % (PAD + 143, SUB, esc((D.get('origin') or {}).get('name') or u'未知位置')))
# 第二行：本餐额度（★ 第 43 轮：强调"走得越远额度越高"的机制）
o.append('<text x="%d" y="62" font-size="11.5" fill="%s">'
         u'本餐额度 %d 卡 —— 走得越远，额度越高，能吃的越多</text>'
         % (PAD, SUB, ALLOWANCE_BASE))
# ★★ 2026-10-15：把「当前生效的限定」画成右上角的标签。
#   起因：用户问「怎么看出这张图当前是只选了茶饮业态」——
#   原来只有**最底部脚注**一行 9.5px 小字（`只看茶饮：附近 187 家里有 3 家…`），
#   位置太深、字号太小，翻到图中间根本看不出来。
#   ★ 只显示**最显著的一项**（业态筛选 > 半径 > 翻页），
#     避免三个标签并排把额度行挤爆（额度行本身已经有 24 个全角字）。
_FILT_ACT = _META.get('keyword')
if _FILT_ACT == u'全部':
    _FILT_ACT = None
_MARK = u''
if _FILT_ACT:
    _MARK = u'只看 %s' % _FILT_ACT
elif _RADIUS:
    _MARK = u'%d 米内' % _RADIUS
elif _META.get('page'):
    _MARK = u'第 %d 批' % (_META['page'] + 1)
if _MARK:
    _mw = len(_MARK) * 10.5 + 20
    _mx = W - PAD - _mw
    # ★ 用**实色淡底**而不是 opacity —— lunasvg 对部分 SVG 属性不认
    #   （项目在 `paint-order` 上出过问题），实色最保险。
    o.append('<rect x="%.1f" y="50.5" width="%.1f" height="17" rx="8.5" '
             'fill="#E4F7F1"/>' % (_mx, _mw))
    o.append('<text x="%.1f" y="63" font-size="10" font-weight="700" '
             'fill="%s" text-anchor="middle">%s</text>'
             % (_mx + _mw / 2.0, WB_TEAL, esc(_MARK)))
o.append('<line x1="%d" y1="74" x2="%d" y2="74" stroke="%s" stroke-width="1"/>'
         % (PAD, W - PAD, LINE))
# ★ 第 47 轮：市中心一个路口就近 200 家 → 选店必须【如实说明】（见 select.py）
_NEARBY = len(D.get('mcd_stores', [])) + len(D.get('local_shops', []))

# 地图底
o.append('<rect x="%d" y="%d" width="%d" height="%d" rx="11" fill="%s"/>'
         % (MAP_X, MAP_Y, MAP_W, MAP_HH, MAP_BG))

# ★★ 第 48 轮：**全量门店淡点层**
#   用户第 2 问：「怎么让评委知道附近有那么多家店？」
#   —— 把抓到的店（含没入选的）画成淡点，入选的才画图标+编号。
#   一眼能看出「这条街餐饮密集」，同时不牺牲可读性。
#   ★ 只画最外圈以内（m ≤ R_OUT）：r_nom 会把更远的钳到 R_OUT，
#     若不筛掉，55 家 750m 外的店会**叠成一个人造圆环**（实测中出过问题）。
_ALL_DOT = []
for _s in (D.get('local_shops') or []) + (D.get('mcd_stores') or []):
    _lo, _la = _s.get('lon'), _s.get('lat')
    if not _lo:
        continue
    _w = float(_s.get('distance_m') or _s.get('straight_m') or 0)
    if _w <= 0 or _w > R_OUT:          # ★ 超出最外圈的不画（否则叠成假圆环）
        continue
    _dx = (_lo - OLON) * _KM_LON
    _dy = (_la - OLAT) * 111000.0
    _brg = math.atan2(_dx, _dy)
    _rr = r_nom(_w)
    _x = (MAP_X + MAP_PAD_X - FX * KX) + math.sin(_brg) * _rr * KX
    _y = (MAP_Y + MAP_PAD_Y - FY * KY) + (-math.cos(_brg) * _rr * Y_SQUASH) * KY
    if MAP_X + 4 <= _x <= MAP_X + MAP_W - 4 and MAP_Y + 4 <= _y <= MAP_Y + MAP_HH - 4:
        _ALL_DOT.append((_x, _y))
for _x, _y in _ALL_DOT:
    o.append('<circle cx="%.1f" cy="%.1f" r="1.9" fill="#bcc9c2"/>' % (_x, _y))

# 圈层弧（完整同心圆）
# ★ 刻度文字方向**按数据选**：找出与所有已选店铺夹角最大的方向，
#   保证「250米」这类标签不会压在店铺图标上（2026-10-12 实测 150° 撞到南侧店群）。
_shop_deg = sorted(((it['bearing'] % 360.0) for it in items)) if items else [0.0]
_LABEL_DIR = 0.0
if _shop_deg:
    _best_gap = -1.0
    for _c in [i * 5.0 for i in range(72)]:
        _mind = min(min(abs(_c - d), 360.0 - abs(_c - d)) for d in _shop_deg)
        if _mind > _best_gap:
            _best_gap, _LABEL_DIR = _mind, _c
    _LABEL_DIR = math.radians(_LABEL_DIR)
for rm in RINGS:
    rn = r_nom(rm)
    if rn < 0.02:
        continue
    _pts = arc_pts(rn, A_START, A_START + A_SPAN)
    for (ax, ay), (bx, by) in zip(_pts[:-1], _pts[1:]):
        o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#cfdad6" '
                 'stroke-width="1"/>' % (ax, ay, bx, by))
    # 刻度文字放在**最空的方位**（数据驱动，见上方 _LABEL_DIR 计算）
    am = _LABEL_DIR
    _rc = rn * R_KX
    lx = PIN_X + math.sin(am) * (_rc + 14)
    ly = PIN_Y - math.cos(am) * (_rc + 14)
    o.append('<text x="%.1f" y="%.1f" font-size="9.5" fill="%s" text-anchor="middle">'
             u'%d米</text>' % (lx, ly, MUTE, rm))

# 店铺（★ 2026-10-12 用户要求：删掉出发点→店铺的连线）
for it in items:
    tf = TIER_FILL[it['tier']]
    ICONS[it['cat']](o, it['px'], it['py'] + 4, 12, tf, 0)
    # 编号圆点放大到 r=9，白底 + 档位色描边，数字用档位深色（保证可读）
    o.append('<circle cx="%.1f" cy="%.1f" r="9" fill="#ffffff" stroke="%s" '
             'stroke-width="1.4"/>' % (it['px'], it['py'] - 13, tf))
    o.append('<text x="%.1f" y="%.1f" font-size="11" font-weight="700" fill="%s" '
             'text-anchor="middle" dominant-baseline="central">%d</text>'
             % (it['px'], it['py'] - 12.5, TIER_TEXT[it['tier']], it['idx']))

# 出发点（★ 第 43 轮：标签改"我在这"，更口语、指向更明确）
#   ★ 第 48 轮：市中心店极密时标签会压住店铺图标（K11 实测压住 14 号店）
#     → 给标签加**白色光晕**，即使重叠也能读清。
#   ★ 用「描边文字 + 填充文字」两层实现，**不用 paint-order** ——
#     实测 pillow_lunasvg 不认 paint-order，会把描边盖在文字上（字全糊掉）。
draw_pin(o, PIN_X, PIN_Y, 16, WB_TEAL)
_LBL_Y = PIN_Y + 40
o.append('<text x="%.1f" y="%.1f" font-size="12" font-weight="700" fill="none" '
         'stroke="%s" stroke-width="3.4" stroke-linejoin="round" '
         'text-anchor="middle">我在这</text>' % (PIN_X, _LBL_Y, MAP_BG))
o.append('<text x="%.1f" y="%.1f" font-size="12" font-weight="700" fill="%s" '
         'text-anchor="middle">我在这</text>' % (PIN_X, _LBL_Y, WB_TEAL))

# 指北针（放最空的角；北朝上）
_cx = sum(it['px'] for it in items) / len(items)
_cy = sum(it['py'] for it in items) / len(items)
_nx, _ny = max([(MAP_X + 18, MAP_Y + 24), (MAP_X + MAP_W - 18, MAP_Y + 24),
                (MAP_X + 18, MAP_Y + MAP_HH - 22), (MAP_X + MAP_W - 18, MAP_Y + MAP_HH - 22)],
               key=lambda p: (p[0] - _cx) ** 2 + (p[1] - _cy) ** 2)
o.append('<g opacity="0.5">')
o.append('<path d="M %.1f %.1f L %.1f %.1f L %.1f %.1f Z" fill="%s"/>'
         % (_nx, _ny - 11, _nx - 4, _ny + 3, _nx + 4, _ny + 3, SUB))
o.append('<text x="%.1f" y="%.1f" font-size="8.5" fill="%s" text-anchor="middle">N</text>'
         % (_nx, _ny + 14, MUTE))
o.append('</g>')

# ★ 2026-10-12 用户要求：删掉雷达图下方的小字说明

# ═══════════════════════════════════════════════════════════════════
# 卡片：左右两列瀑布流（2026-10-12 第 35 轮）
# ═══════════════════════════════════════════════════════════════════
TY = MAP_Y + MAP_HH + 18
# ★ 用户要求：删掉表格上方的整句标题（原来写「这些店，你的额度能吃下几个？」）

_L_X = PAD + 30          # 菜名起点（图标之后）
ROW_H = 19
# ★ 第 38 轮：卡头下移到 y+22、首行菜下移到 y+48 —— 之前卡头中线 y+20、
#   图标半径 13 → 图标底边 y+33，而首行菜文字顶边约 y+30 → **图标下端压到
#   第一道菜**（用户反馈的重叠）。现留出 46px 卡头空间，图标底 35 vs 菜顶 40。
_CARD_TOP_PAD = 48       # 卡内首行菜品的基线相对偏移
_HDR_CY = 22             # 卡头中线（序号/图标/店名/距离共用）


# ═══════════════════════════════════════════════════════════════════
# ★ 2026-10-12 第 37 轮：菜品上色「与餐厅同源」的两种方案（对比用）
#   问题：餐厅色条按「占比」上色、菜品按「绝对阈值」上色 → 两套绿黄红
#         标准不同，用户看到「325 卡为什么是黄」的困惑。
#   方案 B：菜品也按「占本餐额度几成」上色（与餐厅同用「一顿额度」当尺子）
#   方案 C：菜品只分「能吃 / 超了」两态（不再用绿黄红，消除混淆）
DISH_MODE = os.environ.get('DISH_MODE', 'B').upper()   # 'B' 或 'C'

# 方案 B 的比例分界（占本餐额度）：<20% / 20-45% / 45-70% / 70-90% / ≥90%
_B_RATIO = [0.20, 0.45, 0.70, 0.90]


def dish_fill(d, quota):
    u'''菜品颜色：方案 B = 占额度比例上色；方案 C = 能吃/超了两态。'''
    if DISH_MODE == 'C':
        return u'#3fa66b' if d.get('ok') else u'#da291c'
    _r = float(d['kcal']) / float(quota)
    for _i, _b in enumerate(_B_RATIO):
        if _r < _b:
            return TIER_FILL[TIER_ORDER[_i]]
    return TIER_FILL[TIER_ORDER[-1]]


def _dish_row(x0, xend, dy, d, card_r):
    u'''画一行菜：色点 + 菜名（左）+ 卡数（右）；超额度菜名加删除线。

    ★ 列宽变窄，菜名按每字 11px 估，超出截断加「…」。
    '''
    _maxch = max(int((xend - 44 - x0) / 11.0), 3)
    _nm = esc(d['name'])
    if len(_nm) > _maxch:
        _nm = _nm[:_maxch - 1] + u'…'
    if d['kcal'] is None:
        o.append('<circle cx="%.1f" cy="%.1f" r="3" fill="#c4cbd1"/>' % (x0 - 11, dy - 3.4))
        o.append('<text x="%d" y="%.1f" font-size="11" fill="%s">%s</text>'
                 % (x0, dy, MUTE, _nm))
        o.append('<text x="%.1f" y="%.1f" font-size="9" fill="%s" '
                 'text-anchor="end">算不出</text>' % (xend, dy, MUTE))
        return
    c = dish_fill(d, card_r['quota'])
    o.append('<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>' % (x0 - 11, dy - 3.4, c))
    o.append('<text x="%d" y="%.1f" font-size="11" fill="%s">%s</text>'
             % (x0, dy, INK, _nm))
    # 方案 C：数字用中性墨色（颜色只留给色点表达能吃/超了）
    _ctxt = INK if DISH_MODE == 'C' else c
    o.append('<text x="%.1f" y="%.1f" font-size="10" font-weight="700" '
             'fill="%s" text-anchor="end">%d<tspan font-size="7" font-weight="400" '
             'fill="%s">卡</tspan></text>' % (xend, dy, _ctxt, d['kcal'], _ctxt))
    # 超额度：菜名上加一道细删除线
    if not d['ok']:
        _w = min(len(_nm) * 11.0 + 1, xend - 44 - x0)
        o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#c9a3a0" '
                 'stroke-width="0.9"/>' % (x0 - 1, dy - 4.0, x0 + _w, dy - 4.0))


def _card(x, y, r, cw):
    u'''画一张店卡（两列瀑布流版）。

    右侧小字只留**距离**（用户第 4 条：额度/能吃几道先不显示）。
    '''
    tf = TIER_FILL[r['tier']]
    # ★ 服务商不提供推荐菜时，颜色条没有依据 → 用中性灰，不假装有结论
    if r.get('nodish'):
        tf = u'#c8d2cc'
    ch = _CARD_H(r)
    o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="10" fill="#ffffff"/>'
             % (x, y, cw, ch - 6))
    o.append('<rect x="%.1f" y="%.1f" width="3.5" height="%.1f" rx="1.8" fill="%s"/>'
             % (x, y, ch - 6, tf))

    # ★ 第 36/38 轮：序号 / 图标 / 店名 / 距离 **统一卡头中线 _HDR_CY（y+22）**。
    #   第 38 轮把中线从 y+20 下移到 y+22，并把图标-店名间距从 14 拉到 18px。
    _HCY = y + _HDR_CY
    # 序号
    o.append('<text x="%.1f" y="%.1f" font-size="11.5" font-weight="600" fill="%s" '
             'text-anchor="middle" dominant-baseline="central">%d</text>'
             % (x + 13, _HCY, MUTE, r['idx']))
    # 业态图标（中心对齐中线）
    ICONS[r['cat']](o, x + 33, _HCY, 13, tf, 0)

    # 店名（窄列 → 有入选理由时限 8 字，否则 9 字）
    # ★ 第 38 轮：图标右缘 ≈ x+46 → 店名起点 x+51，留 5px 呼吸
    _nm_max = 8 if r.get('why') else 9
    nm = esc((r.get('disp') or r['name']).split(u'(')[0].split(u'（')[0][:_nm_max])
    o.append('<text x="%.1f" y="%.1f" font-size="12.5" font-weight="600" fill="%s">%s</text>'
             % (x + 51, _HCY + 4.2, INK, nm))
    # 右侧：入选理由（小徽标）+ 距离
    # ★ 第 48 轮：让「为什么是这 15 家」在每张卡上可复述
    _rx = x + cw - 10
    o.append('<text x="%.1f" y="%.1f" font-size="9" fill="%s" text-anchor="end" '
             'dominant-baseline="central">%dm</text>'
             % (_rx, _HCY, MUTE, r['walk']))
    if r.get('why'):
        o.append('<text x="%.1f" y="%.1f" font-size="8.5" fill="%s" text-anchor="end" '
                 'dominant-baseline="central">%s</text>'
                 % (_rx - 26, _HCY, WB_TEAL if r['why'] != u'均衡' else MUTE, r['why']))

    _x0 = x + 24
    _xend = x + cw - 10
    if r.get('nodish'):
        # ★ 门店模式：当前地图服务商不提供推荐菜（实测：百度）
        #   只画一行实话，**不编造菜品**
        _cat = r.get('category_hint') or r.get('catlabel') or u'—'
        _bits = [_cat]
        if r.get('cost'):
            try:
                _bits.append(u'人均约 ¥%d' % int(float(r['cost'])))
            except (TypeError, ValueError):
                pass
        o.append('<circle cx="%.1f" cy="%.1f" r="3" fill="#c4cbd1"/>'
                 % (_x0 - 11, y + _CARD_TOP_PAD - 3.4))
        o.append('<text x="%d" y="%.1f" font-size="11" fill="%s">%s</text>'
                 % (_x0, y + _CARD_TOP_PAD, MUTE,
                    esc(u' · '.join(_bits))[:14]))
        o.append('<text x="%.1f" y="%.1f" font-size="9" fill="%s" '
                 'text-anchor="end">无菜品数据</text>'
                 % (_xend, y + _CARD_TOP_PAD, MUTE))
        return
    for i, d in enumerate(r['sel']):
        _dish_row(_x0, _xend, y + _CARD_TOP_PAD + i * ROW_H, d, r)


for _ci, _cards in enumerate(_col_cards):
    _cx = PAD + _ci * (_COL_W + _COL_GAP)
    for _it, _off in _cards:
        _card(_cx, TY + _off, _it, _COL_W)

y = TY + CARDS_H + 6

# ★ 数据能力：有没有店是「没有菜品数据」的（服务商不提供推荐菜时）
_ANY_NODISH = any(_it.get('nodish') for _it in items)
_ALL_NODISH = bool(items) and all(_it.get('nodish') for _it in items)

# ── 图例（★ 第 42 轮：颜色 = 推荐菜相对「热量缺口」的关系）─────────────
_ly = y + 14
if _ALL_NODISH:
    o.append('<text x="%d" y="%.1f" font-size="11" font-weight="700" fill="%s">'
             u'本数据源（%s）不提供推荐菜 —— 只展示门店、距离与业态</text>'
             % (PAD, _ly + 10, SUB, esc(CAPS['name'])))
    o.append('<text x="%d" y="%.1f" font-size="9.5" fill="%s">'
             u'菜品热量需要内容源（推荐菜），高德地图提供该字段；'
             u'换回高德即可看到每道菜的卡数</text>'
             % (PAD, _ly + 30, MUTE))
    _lc = PAD
else:
    o.append('<text x="%d" y="%.1f" font-size="11" font-weight="700" fill="%s">'
             u'颜色看这家店的网友推荐菜，和你的热量缺口比</text>' % (PAD, _ly + 10, SUB))
    _lc = PAD
    for _lv, _nv in [(u'低于缺口', 0), (u'适配缺口', 2), (u'高于缺口', 4)]:
        o.append('<rect x="%.1f" y="%.1f" width="13" height="13" rx="3" fill="%s"/>'
                 % (_lc, _ly + 20, TIER_FILL[TIER_ORDER[_nv]]))
        o.append('<text x="%.1f" y="%.1f" font-size="10" font-weight="700" fill="%s">%s</text>'
                 % (_lc + 17, _ly + 31, SUB, esc(_lv)))
        _lc += 17 + len(_lv) * 10 + 12
    o.append('<text x="%.1f" y="%.1f" font-size="9.5" fill="%s">'
             u'（绿=大多在缺口之下 · 黄=一半刚好 · 红=大多超过）</text>'
             % (_lc, _ly + 31, MUTE))

_FILT = _META['keyword']
if _FILT and _FILT != u'全部':
    _NOTE1 = (u'只看%s：附近 %d 家里有 %d 家，本批显示 %d 家（第 %d/%d 批）'
              % (_FILT, _NEARBY, _META['total'], _META['shown'],
                 _META['page'] + 1, _META['pages']))
else:
    _NOTE1 = (u'淡点 = 附近一家餐饮（共 %d 家，仅画 %d 米内）· %d 家可评估 · '
              u'本批 %d 家（%d/%d 批）'
              % (_NEARBY, int(R_OUT), _META['total'], _META['shown'],
                 _META['page'] + 1, _META['pages']))
# ★ 脚注**没有自动换行**（超出画布会被裁）→ 每条按 ≤ 44 全角字控制。
#   宽度自检见下面「构建期自检」里的脚注检查。
# ★ 第 64 轮：如实写明「网友推荐」—— 它本来就是 UGC（网友/口碑内容），
#   说清楚既准确，也让「某家店的数据不对」这类反馈有正确预期。
#   ★ 但不能无条件写：数据源**不提供**推荐菜时（如百度），说「菜名来自网友推荐」
#     会和下面那行「本数据源不提供推荐菜」自相矛盾 —— 实测踩到，故二选一。
if _ANY_NODISH:
    _NOTE_SRC = u'★ 本数据源不提供网友推荐菜：门店只给业态/人均，不编菜品'
else:
    _NOTE_SRC = (u'菜名来自地图的网友推荐（非店家官方菜单，会有遗漏）'
                 u'· 每店最多 8 道')
NOTES = [
    _NOTE1,
    u'选店规则：四象限各取几家 · 麦当劳最多占 1/3 · 同档优先「额度内能吃几道」再比远近',
    u'缺口 = 本餐额度；每道菜按"能不能填进这个缺口"判定，绿吃得下、红超了（划掉）',
    u'热量分三层：菜谱推算 → 品类估算 → 同类估算（误差 25~45%）；算不出的不猜',
    _NOTE_SRC,
    u'步行距离为地图路径规划 · 数据快照 %s · 不构成医疗建议' % SNAPSHOT_DATE,
]
# ★ 第 63 轮：隐去「只卖喝的」要说明（原因见 docs/设计取舍.md 第 13 节）
if _META.get('excluded_drink'):
    NOTES.insert(2, u'已隐去 %d 家只卖饮品的店 —— 想看请按业态筛选'
                     % _META['excluded_drink'])
# ★ 2026-10-15：店名里标了「暂停营业/已关闭」的店要说明。
#   地图源没有营业状态字段，但偶尔把状态写进店名（全库命中 ≈0.07%）——
#   命中即确定，不该推荐；但**不能静默消失**，所以如实写出来。
#   ★ 计数用渲染自己的 `_CLOSED_N`（上面剔除时数的），
#     而不是 select 的 meta —— 渲染的 items 有自己的字段口径，
#     靠 select 转一手会出现「脚注说剔了、图上却还在」的自相矛盾。
if _CLOSED_N:
    NOTES.insert(2, u'已剔除 %d 家店名标注「暂停营业 / 已关闭」的店'
                     % _CLOSED_N)
_ny = _ly + 56
for i, t in enumerate(NOTES):
    o.append('<text x="%d" y="%.1f" font-size="9.5" fill="%s">· %s</text>'
             % (PAD, _ny + i * 17, MUTE, t))

o.append('</svg>')
# ★ 第 37 轮：非默认模式（C）输出到独立文件，方便 B/C 对比
_SFX = '' if DISH_MODE == 'B' else '_' + DISH_MODE
# ★ 输出到仓库根的 out/（不落在包目录里，保持包干净）
OUT_DIR = os.environ.get('WALKKCAL_OUT') or os.path.join(ROOT, 'out')
if not os.path.isabs(OUT_DIR):
    OUT_DIR = os.path.join(ROOT, OUT_DIR)
if not os.path.isdir(OUT_DIR):
    os.makedirs(OUT_DIR)
_SVG_PATH = os.path.join(OUT_DIR, 'skill_mobile%s.svg' % _SFX)
io.open(_SVG_PATH, 'w', encoding='utf-8').write('\n'.join(o) + '\n')

# ── SVG → PNG（★ 可选步骤：缺 Pillow 时优雅降级，不再抛异常）─────────
#  2026-10-16：SVG 在上一行就已**完整落盘**（浏览器可直接打开），
#  PIL 只负责最后一步「位图化」。所以缺依赖不该让整个渲染失败 ——
#  保留 SVG + 给出安装指引，比抛 ModuleNotFoundError 有用得多。
#  ⚠ 两条链（PNG 链 / SVG 链）各有回归测试：tests/test_render_fallback.py
_PNG_PATH = os.path.join(OUT_DIR, 'skill_mobile%s.png' % _SFX)
_PNG_OK = False
_PNG_WHY = u''
try:
    from PIL import Image
    import pillow_lunasvg  # noqa: F401
except ImportError as _e:
    _PNG_WHY = u'缺少 %s' % (getattr(_e, 'name', None) or _e)
else:
    try:
        with Image.open(_SVG_PATH) as im:
            bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
            bg.alpha_composite(im.convert('RGBA'))
            bg.convert('RGB').save(_PNG_PATH)
        _PNG_OK = True
    except Exception as _e:                 # 解码失败等（SVG 已在手，仍算降级）
        _PNG_WHY = u'%s: %s' % (type(_e).__name__, _e)
# ═══════════════════════════════════════════════════════════════════
# 构建期自检（比看图发现快得多）
# ═══════════════════════════════════════════════════════════════════
_problems = []
for _i, _c in enumerate(_MCD_COMBOS):
    _miss = [n for n in _c if not nutrition_of(n)]
    if _miss:
        _problems.append(u'MCD 套餐 %d 有 %d 个菜名查不到营养值：%s'
                         % (_i + 1, len(_miss), u'、'.join(_miss)))
    if len(_c) < 2:
        _problems.append(u'MCD 套餐 %d 只剩 %d 道菜' % (_i + 1, len(_c)))
# ★ 单列后：每家店至少要有 1 道可展示的菜，且不超过 DISH_MAX
for _r in items:
    # ★ 例外：服务商不提供推荐菜时，卡片上展示的是「业态/人均」而非菜品
    if not _r['sel'] and not _r.get('nodish'):
        _problems.append(u'%s 没有可展示的菜' % _r['name'])
    if len(_r['sel']) > DISH_MAX:
        _problems.append(u'%s 菜品超过 %d 道（%d）'
                         % (_r['name'], DISH_MAX, len(_r['sel'])))
    if len({d['name'] for d in _r['sel']}) != len(_r['sel']):
        _problems.append(u'%s 菜品有重复' % _r['name'])
    # ★ 全超额度的店：不应误判（fit_n==0 且 known_n>0）
    if _r['fit_n'] > 0 and _r['all_over']:
        _problems.append(u'%s 的「全超额度」标记与 fit_n 矛盾' % _r['name'])
_pts = [(it['px'], it['py']) for it in items]
_minsep = min((math.hypot(a[0] - b[0], a[1] - b[1])
               for i2, a in enumerate(_pts) for b in _pts[i2 + 1:]), default=99)
if _minsep < 29:
    _problems.append(u'地图上有店铺图标重叠（最近间距 %.0fpx < 29px）' % _minsep)
# ★ 2026-10-15 新增：**半径整体放大**会让「店铺位置」与「圈层刻度」脱节。
#   店铺像素半径 = r_nom(距离) × factor，圈层刻度只由 r_nom 决定 ——
#   factor=3 时 458 m 的店会画到 900 m 圈之外 1.8 倍处（实测凌河路 618 弄）。
#   归因：`_need` 取最小半径差，只要有一对店距离接近就顶满上限。
#   现在上限设 1.0（不放大），此断言是防它被改回去。
if RAD_FACTOR_SEEN > 1.001:
    _problems.append(u'★ 地图半径被整体放大 %.2f 倍 —— 店铺位置与圈层刻度脱节'
                     u'（表观距离会大于真实距离）' % RAD_FACTOR_SEEN)
# ★ 2026-10-15：缩放异常小 = 地图已毁（spread 半径发散）。
#   征兆是 `缩放 横向0.002`（正常 0.2~0.6），肉眼看图只觉得「点全挤在一起」。
#   实测：近铁场景因几家店距离几乎相同触发，KX 掉到 0.002，整张图作废。
if KX < 0.02:
    _problems.append(u'★ 地图缩放异常小（KX=%.4f，正常 0.2~0.6）'
                     u'—— 检查 spread() 的半径放大是否发散' % KX)
# ★ 第 64 轮：脚注**没有自动换行**，超宽会被画布裁掉（曾静默溢出 40px）。
#   估算：全角 9.5px、半角 5px，前缀「· 」算 14px。
for _nt in NOTES:
    _tw = 14.0 + sum(9.5 if ord(_ch) > 127 else 5.0 for _ch in _nt)
    if PAD + _tw > W - 4:
        _problems.append(u'脚注超宽（右边界 %.0f > %d）：%s…'
                         % (PAD + _tw, W - 4, _nt[:20]))
if _problems:
    print(u'\n⚠ 构建期自检发现 %d 个问题：' % len(_problems))
    for _p in _problems:
        print(u'  · %s' % _p)
else:
    print(u'\n构建期自检通过（营养值齐全 / 无重复 / 地图无重叠 %.0fpx）' % _minsep)

print(u'\n竖屏图 %dx%d 已生成 ｜ 额度 %d 卡 ｜ 圈层 %s' % (W, H, ALLOWANCE_BASE, RINGS))
if _PNG_OK:
    print(u'   产物：%s' % _PNG_PATH)
    print(u'         %s（浏览器可直接打开）' % _SVG_PATH)
else:
    print(u'   ⚠ PNG 未生成（%s）—— 已保留 SVG：' % _PNG_WHY)
    print(u'     %s  ← 浏览器直接打开即可，效果无损' % _SVG_PATH)
    print(u'     想要 PNG：pip install -r requirements.txt')