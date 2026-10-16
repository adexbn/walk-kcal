# -*- coding: utf-8 -*-
"""
补抓门店详情：特色菜 tag / 人均 / 营业时间
=============================================
用户纠正（2026-10-12 14:34）：「之前历史里，高德推荐菜，我给你截图了，
你说你能抓到的，而且验证了可以抓，你找下聊天历史」

**查证结果：是我忘了，不是没做过。**
2026-10-10 第二十八轮实测记录（.workbuddy/memory/2026-10-10.md:872）：
    高德 POI 详情（/v3/place/detail）在 extensions=all 时，餐饮类 POI 返回：
    tag ★ "该 POI 的特色内容，主要出现在美食类POI中，代表特色菜"
         例：「烤鱼, 麻辣香锅, 老干妈回锅肉」
    同批还有：rating（评分）/ cost（人均）/ photos（照片）

★ 本轮踩的坑：字段层级读错了。
  `business` 对象是空的；真实字段在**顶层**和 `biz_ext` 里：
    - tag            → 顶层
    - biz_ext.cost   → 人均（字符串，如 '35.00'）
    - biz_ext.opentime2 → 营业时间
"""
import io, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from walkkcal import geo  # noqa

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)

# ★ 地图服务商从 provider 取（默认高德，WALKKCAL_GEO 可切换）
#   本文件不直接碰任何地图 API —— 换地图不用改这里。
PROV = geo.get()

# 候选池：从快照读（由 fetch_pois_at.py 生成）
_SNAPSHOT = os.path.join(ROOT, 'data', 'stores.json')
D = json.load(io.open(_SNAPSHOT, encoding='utf-8')) if os.path.exists(_SNAPSHOT) else {}


def call(path, **q):
    u"""保留兼容：直接调 provider 的底层 HTTP（不推荐，优先用 store_detail）"""
    if not hasattr(PROV, '_call_retry'):
        raise RuntimeError(u'当前地图 provider 不支持详情查询')
    return PROV._call_retry(path, **q)


def fetch(pid):
    u"""拉单个 POI 详情 —— 委托给 provider（见 walkkcal/providers/geo_amap.py）"""
    if not hasattr(PROV, 'store_detail'):
        raise RuntimeError(
            u'当前地图 provider（%s）未实现 store_detail，无法拉店铺详情。'
            u'见 docs/扩展开发.md' % PROV.name)
    return PROV.store_detail(pid)


# ═══════════════════════════════════════════════════════════════════
print(u'【麦当劳门店】')
mcd = []
for s in D['mcd_stores'][:6]:
    d = fetch(s['id'])
    if not d:
        continue
    d['walk'] = s['walk']
    mcd.append(d)
    print(u'   %-26s 人均%-6s 特色=%s'
          % (d['name'][:26], d['cost'], (d['tag'] or '（无）')[:34]))
    # ★ 不自己 sleep —— 节奏由 provider 的限速器管（见 walkkcal/geo.py）

# ═══════════════════════════════════════════════════════════════════
print(u'\n【本地餐厅 —— 特色菜即推荐菜】')
# ★★ 2026-10-12 用户第 3 条修复：**按象限配额抓取**
#   旧逻辑 `for s in D['local_shops']` 按原始顺序遍历、取满 14 家就停 ——
#   而 local_shops 里西侧的店排在前面，结果 14 家全落在南西/北西，
#   图上东侧空一片（用户质疑「二三四象限真的没有餐厅？」→ 实测有，是选取偏斜）。
#   改为：按方位分 4 个象限，每象限按距离取最近的 N 家，保证四象限覆盖。
import math as _math
_OLON, _OLAT = float(D['origin']['lon']), float(D['origin']['lat'])
_KL = 111000 * _math.cos(_math.radians(_OLAT))


def _quad_of_shop(s):
    dx = (float(s['lon']) - _OLON) * _KL
    dy = (float(s['lat']) - _OLAT) * 111000.0
    b = _math.degrees(_math.atan2(dx, dy)) % 360.0
    return 0 if b < 90 else (1 if b < 180 else (2 if b < 270 else 3))


_QB = {0: [], 1: [], 2: [], 3: []}
for _s in D['local_shops']:
    if not _s.get('tag') or not _s.get('lon'):
        continue
    if (_s.get('walk') or 9999) > 900:      # 只考虑 900m 内
        continue
    _QB[_quad_of_shop(_s)].append(_s)
for _k in _QB:
    _QB[_k].sort(key=lambda z: z['walk'])

# 每象限取最近的 4 家（共 ≤16 家），再按距离合并
_cand_shops = []
for _k in (0, 1, 2, 3):
    _cand_shops.extend(_QB[_k][:4])
_cand_shops.sort(key=lambda z: z['walk'])
print(u'  象限候选：北东%d 南东%d 南西%d 北西%d，合计 %d'
      % (len(_QB[0]), len(_QB[1]), len(_QB[2]), len(_QB[3]), len(_cand_shops)))

local = []
for s in _cand_shops:
    d = fetch(s['id'])
    if not d or not d['tag']:
        continue
    d['walk'] = s['walk']
    d['quad'] = _quad_of_shop(s)
    local.append(d)
    print(u'   [%s] %-24s 人均%-6s ★%s'
          % (u'北东南东北西南西南西北西'[d['quad'] * 2:d['quad'] * 2 + 2],
             d['name'][:24], d['cost'] or '-', d['tag'][:40]))
    # ★ 不自己 sleep —— 同上
    if len(local) >= 20:
        break

out = {'origin': D['origin'], 'mcd': mcd, 'local': local}
p = os.path.join(BASE, '_demo_detail.json')
io.open(p, 'w', encoding='utf-8').write(json.dumps(out, ensure_ascii=False, indent=1))
print(u'\n已存 _demo_detail.json ｜ 麦当劳 %d 家 ｜ 本地 %d 家' % (len(mcd), len(local)))
