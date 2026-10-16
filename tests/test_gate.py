# -*- coding: utf-8 -*-
u"""质量闸门回归测试 —— 防止「算得出但算错」的 bug 复发。

跑法：python tests/test_gate.py
无第三方依赖（纯 assert）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from walkkcal import allowance, verdict  # noqa

_fail = []

# ★★ 测试不该往交付目录 out/ 里写东西。
#   而 `from walkkcal import render`（见 test_shop_tier）**会执行 render.py 的
#   模块级代码** —— 也就是完整跑一遍渲染并写出 PNG/SVG。
#   所以在这里就把输出重定向到临时目录（必须在任何 import render 之前设置）。
#   ⚠ 已知设计问题：render.py 是脚本式结构，import 有副作用。
#     要根治得把渲染逻辑包进 main()。当前用这个环境变量兜住。
os.environ.setdefault('WALKKCAL_OUT',
                      tempfile.mkdtemp(prefix='walkkcal_testout_'))


def check(name, got, want):
    ok = (got == want)
    print(u'%s %s  got=%r want=%r' % ('✅' if ok else '❌', name, got, want))
    if not ok:
        _fail.append(name)


# ── 额度公式 ────────────────────────────────────────────────
#   额度 = 450 + (walk//80 + 1) * 4
#   ★ 注意：(walk//80 + 1) —— 走 0 米也记 1 个 80 米单位（+4 卡）
def test_allowance():
    check(u'额度(0m)=454', allowance(0), 454)        # 0//80+1=1 → 450+4
    check(u'额度(79m)=454', allowance(79), 454)      # 同上（未跨档）
    check(u'额度(80m)=458', allowance(80), 458)      # 1+1=2 → 450+8
    check(u'额度(253m)=466', allowance(253), 466)    # 3+1=4 → 450+16
    check(u'额度(1480m)=526', allowance(1480), 526)  # 18+1=19 → 450+76


# ── 判档 ────────────────────────────────────────────────────
def test_verdict():
    check(u'麦香鱼325@450=吃下', verdict(325, 450), u'吃下')
    check(u'麦辣鸡腿堡485@450=超了', verdict(485, 450), u'超了')
    check(u'410@450=刚好', verdict(410, 450), u'刚好')   # 差 40 <= 50
    check(u'450@450=刚好', verdict(450, 450), u'刚好')


# ── 店铺三态（相对缺口）────────────────────────────────────
def test_shop_tier():
    from walkkcal.render import shop_tier_of
    from walkkcal.theme import TIER_ORDER
    q = 450
    # 全部低于 0.7q=315 → 低于缺口（绿）
    check(u'全低于缺口→很低', shop_tier_of([200, 250, 300], q), TIER_ORDER[0])
    # 全部在 [315,450] → 适配缺口（黄）
    check(u'全适配缺口→中等', shop_tier_of([330, 400, 440], q), TIER_ORDER[2])
    # 全部 > 450 → 高于缺口（红）
    check(u'全高于缺口→很高', shop_tier_of([500, 600, 700], q), TIER_ORDER[4])
    # 空值兜底 → 中等
    check(u'空→中等', shop_tier_of([], q), TIER_ORDER[2])


# ── 麦当劳快照完整性 ────────────────────────────────────────
def test_mcd_snapshot():
    from packs import mcd_nutrition_snapshot as S
    check(u'快照条目>20', len(S.ALL) > 20, True)
    check(u'麦香鱼=325', S.kcal_of(u'麦香鱼'), 325)
    check(u'未知菜返回None', S.kcal_of(u'不存在的菜'), None)


# ── 分层估算（prior）────────────────────────────────────────
def test_prior():
    from walkkcal import prior
    # 品类词：不是菜 → 弃权
    check(u'「单人餐」不是菜', prior.estimate(u'单人餐', u'中餐厅'), None)
    check(u'「coffee」不是菜', prior.estimate(u'coffee', u'咖啡厅'), None)
    # L2：菜名关键词定品类
    # ★★ 2026-10-15 茶饮**分档**（原来整组统一 150 卡 →
    #    纯茶 ~10 / 果茶 ~180 / 奶茶 ~350 / 茶饮兜底 250）。
    #    依据：现制茶饮跨度极大（纯茶几乎 0 卡，奶茶 219~400 卡）。
    check(u'高山四季春（纯茶档）→10',
          prior.estimate(u'高山四季春', {u'industries': []})['kcal'], 10)
    check(u'芝士奶盖（奶茶档）→350',
          prior.estimate(u'芝士奶盖', {u'industries': []})['kcal'], 350)
    check(u'奇兰粉芭乐（果茶档）→180',
          prior.estimate(u'奇兰粉芭乐', {u'industries': []})['kcal'], 180)
    # ★ 「多肉葡萄」这类**没有字面线索**的产品名只能走 L3 业态兜底
    check(u'多肉葡萄（无线索→业态兜底）→250',
          prior.estimate(u'多肉葡萄', {u'industries': [u'茶饮']})['kcal'], 250)
    # ★★ 「美式」必须与 is_drink_item 同口径（后缀锚定）：
    #    原来 L2 写裸「美式」→ 「美式豪情牛肉培根菠萝比萨」落进咖啡档 130 卡，
    #    而**同一张卡片**另外 4 款比萨都是 400（实测达美乐广州/北京两城都出现）。
    _pizza = prior.estimate(u'美式豪情牛肉培根菠萝比萨', {u'industries': [u'西餐']})
    check(u'★「美式豪情…比萨」不再落进咖啡档（≠130）', _pizza['kcal'] != 130, True)
    check(u'★「美式豪情…比萨」按门店业态兜底→450', _pizza['kcal'], 450)
    check(u'「冰美式」仍走咖啡档→130',
          prior.estimate(u'冰美式', {u'industries': []})['kcal'], 130)
    # ★★ 素菜/配菜**单独一档**（2026-10-15 用户同意）。
    #    病根：L3 门店兜底给的是「一餐正餐」的量级（中餐 450），
    #    落在素菜上高估 3~8 倍（白灼生菜 450 → 实际 50~80）。
    _zh = {u'industries': [u'中餐本帮']}
    check(u'白灼生菜（凉/白灼档）→100',
          prior.estimate(u'白灼生菜', _zh)['kcal'], 100)
    check(u'爽口云耳（凉拌档）→100', prior.estimate(u'爽口云耳', _zh)['kcal'], 100)
    check(u'蒜泥黄瓜（凉拌档）→100', prior.estimate(u'蒜泥黄瓜', _zh)['kcal'], 100)
    check(u'手撕包菜（素菜档）→150', prior.estimate(u'手撕包菜', _zh)['kcal'], 150)
    check(u'土豆丝（素菜档）→150', prior.estimate(u'土豆丝', _zh)['kcal'], 150)
    check(u'曾经的老南瓜（素菜档）→150',
          prior.estimate(u'曾经的老南瓜', _zh)['kcal'], 150)
    check(u'干锅土豆片（重油档）→250',
          prior.estimate(u'干锅土豆片', _zh)['kcal'], 250)
    check(u'烤茄子（重油档）→250', prior.estimate(u'烤茄子', _zh)['kcal'], 250)
    check(u'云南文山红甸山药（不再按火锅 700）',
          prior.estimate(u'云南文山红甸山药', {u'industries': [u'火锅烧烤']})['kcal'], 150)
    # ★ 素菜档**不能吃掉**主食与荤菜（离线实测的真实反例）
    check(u'★青菜肉丝炒面仍是面→450',
          prior.estimate(u'青菜肉丝炒面', _zh)['kcal'], 450)
    check(u'★香煎澳州带子芦笋烩饭仍是饭类→600',
          prior.estimate(u'香煎澳州带子芦笋烩饭', _zh)['kcal'], 600)
    check(u'★皮蛋拌茄子有蛋→不走素菜档',
          prior.is_veg_item(u'皮蛋拌茄子'), False)
    check(u'★大白菜炒蛰头有海鲜→不走素菜档',
          prior.is_veg_item(u'大白菜炒蛰头'), False)
    check(u'★青菜香菇包是包子→不走素菜档',
          prior.is_veg_item(u'青菜香菇包'), False)
    check(u'★卷心菜（不能因为含「卷」被当点心）',
          prior.is_veg_item(u'卷心菜'), True)
    check(u'蟹粉豆腐→豆制品220', prior.estimate(u'蟹粉豆腐', u'中餐厅')['kcal'], 220)
    check(u'素什锦面→面450', prior.estimate(u'素什锦面', u'中餐厅')['kcal'], 450)
    check(u'掼奶油→西点350', prior.estimate(u'掼奶油', u'中餐厅')['kcal'], 350)

    # ★★ L3 现在是**结构化三级**：业态 → 品类线索/店名 → 推荐菜名
    #    这样与地图服务商无关（不再依赖高德类目名）
    r = prior.estimate(u'草头圈子', {'industries': [u'中餐本帮'], 'text': u''})
    check(u'业态=中餐本帮→L3中餐450', r['kcal'], 450)
    check(u'L3 tier 标记', r['tier'], 'L3')
    r2 = prior.estimate(u'某些特调',
                        {'industries': [u'茶饮'], 'text': u''})
    check(u'业态=茶饮→饮品250', r2['kcal'], 250)
    r3 = prior.estimate(u'某些特调', {'industries': [],
                                      'text': u'冷饮店;某某店'})
    check(u'品类线索=冷饮店→饮品250', r3['kcal'], 250)
    r4 = prior.estimate(u'某些特调', {'industries': [], 'text': u'',
                                      'dish_names': [u'手工面包']})
    check(u'菜名兜底（面包→西点350）', r4['kcal'], 350)
    # ★ 分层不互相污染：菜名里有「面包」的火锅店仍按火锅算
    r5 = prior.estimate(u'某些特调',
                        {'industries': [u'火锅烧烤'], 'text': u'某某火锅',
                         'dish_names': [u'手工面包']})
    check(u'★业态优先于菜名（不被菜名污染）', r5['kcal'], 700)

    # ★★ 架构断言：**原始高德类目串不再被直接识别**
    #    （必须由 provider 翻译成 industries 后上层才认）
    check(u'★高德类目串不再直通',
          prior.estimate(u'草头圈子', u'餐饮服务;中餐厅;上海菜'), None)
    # 旧式字符串仍兼容（约定：industries 用 / 连接放开头）
    check(u'旧式串：业态前缀',
          prior.estimate(u'某些特调', u'茶饮;;某店')['kcal'], 250)

    # 修复过的误判（回归）
    check(u'蟹粉豆腐不判成面', prior.estimate(u'蟹粉豆腐', u'中餐厅')['category'], u'豆制品')
    check(u'鲜奶小方不判成饮品', prior.estimate(u'鲜奶小方', u'中餐厅')['category'], u'西点')



# ── 选店规则（select）───────────────────────────────────────
def test_select():
    from walkkcal import select
    cands = ([{'name': u'麦%d' % i, 'quad': i % 4, 'walk': 100 + i * 10,
               'fit_n': 4, 'is_mcd': True} for i in range(30)]
             + [{'name': u'本地%d' % i, 'quad': i % 4, 'walk': 50 + i * 10,
                 'fit_n': 2, 'is_mcd': False} for i in range(200)])
    got, meta = select.pick(cands, 15)
    check(u'选出 15 家', len(got), 15)
    mcd_n = len([s for s in got if s['is_mcd']])
    check(u'麦当劳不超 1/3', mcd_n <= 5, True)
    quads = set(s['quad'] for s in got)
    check(u'四象限都有', len(quads), 4)


# ── 业态筛选（filter）──────────────────────────────────────
def test_filter():
    from walkkcal import filter as F
    from walkkcal.providers.geo_amap import industries_of

    # ① 高德类目 → 归一化业态（★ 这层在 provider 里，不在 filter 里）
    check(u'高德 咖啡厅→咖啡', industries_of(u'餐饮服务;咖啡厅;咖啡厅'), [u'咖啡'])
    check(u'高德 快餐厅→快餐', industries_of(u'餐饮服务;快餐厅;快餐厅'), [u'快餐'])
    check(u'高德 日本料理→日料',
          industries_of(u'餐饮服务;外国餐厅;日本料理'), [u'日料'])
    check(u'高德 其它亚洲菜→东南亚',
          industries_of(u'餐饮服务;外国餐厅;其它亚洲菜'), [u'东南亚'])
    check(u'高德 中餐厅;火锅店→火锅烧烤',
          industries_of(u'餐饮服务;中餐厅;火锅店'), [u'火锅烧烤'])
    check(u'高德 上海菜→中餐本帮',
          industries_of(u'餐饮服务;中餐厅;上海菜'), [u'中餐本帮'])
    # 三级命中就不套二级伞（日料店不该同时被打上「西餐」）
    check(u'三级优先于二级伞',
          industries_of(u'餐饮服务;外国餐厅;日本料理'), [u'日料'])
    # 没有二级/三级 → 返回空（**返回空是合法的，不许瞎猜**）
    check(u'高德 只有一级→空', industries_of(u'餐饮服务'), [])
    check(u'高德 餐饮相关→空', industries_of(u'餐饮服务;餐饮相关场所;餐饮相关'), [])

    # ② filter.classify 只消费归一化结果 + keytag + 店名
    check(u'industries 直通', F.classify({'industries': [u'日料']}), [u'日料'])
    # ★ keytag/atag 由 snapshot 层负责归一化成 category_hint 后才被 filter 认
    check(u'★filter 不认原始 keytag', F.classify({'keytag': u'牛肉面'}), [])
    check(u'category_hint 兜底', F.classify({'category_hint': u'咖啡'}), [u'咖啡'])
    check(u'店名兜底', F.classify({'name': u'麦当劳(虬江路店)'}), [u'快餐'])
    check(u'星巴克→咖啡', F.classify({'name': u'星巴克(雁荡路店)'}), [u'咖啡'])
    # ★★ 架构断言：filter **不认识**高德 type
    #    这条保证了换百度时只要 provider 产出 industries，filter 一行不用改
    check(u'★filter 不认识高德 type',
          F.classify({'type': u'餐饮服务;外国餐厅;日本料理'}), [])
    # ★ 回归：atag 是「推荐菜名列表」不是品类
    check(u'atag 不参与判业态',
          F.classify({'atag': u'热红酒美式-无酒,拿铁,美式咖啡'}), [])

    # ★★ 「西餐」= 非中餐的**正餐**（2026-10-13 用户裁定的世俗逻辑）
    def m(ind, kw):
        return F.match({'industries': [ind]}, kw)
    check(u'日料 ∈ 西餐', m(u'日料', u'西餐'), True)
    check(u'韩餐 ∈ 西餐', m(u'韩餐', u'西餐'), True)
    check(u'东南亚 ∈ 西餐', m(u'东南亚', u'西餐'), True)
    check(u'欧美菜 ∈ 西餐', m(u'西餐', u'西餐'), True)
    # ★ 快餐**不往上升** —— 我们只做到「快餐」这一层
    check(u'★快餐 ∉ 西餐', m(u'快餐', u'西餐'), False)
    check(u'★小吃 ∉ 西餐', m(u'小吃', u'西餐'), False)
    check(u'★咖啡 ∉ 西餐', m(u'咖啡', u'西餐'), False)
    check(u'★面包甜点 ∉ 西餐', m(u'面包甜点', u'西餐'), False)
    check(u'★中餐本帮 ∉ 西餐', m(u'中餐本帮', u'西餐'), False)
    # 细筛仍然可用
    check(u'日料 ∈ 日料', m(u'日料', u'日料'), True)
    check(u'日料 ∉ 韩餐', m(u'日料', u'韩餐'), False)

    # 筛选（麦当劳是快餐，不进西餐）
    rows = [{'name': u'麦当劳', 'industries': [u'快餐']},
            {'name': u'某牛排馆', 'industries': [u'西餐']},
            {'name': u'沙县小吃', 'keytag': u'牛肉面'},
            {'name': u'某日料', 'industries': [u'日料']}]
    check(u'筛西餐出 2 家（牛排馆+日料）', len(F.apply(rows, u'西餐')), 2)
    check(u'筛快餐只出 1 家（麦当劳）', len(F.apply(rows, u'快餐')), 1)
    check(u'「全部」不过滤', len(F.apply(rows, u'全部')), 4)
    check(u'空关键词不过滤', len(F.apply(rows, None)), 4)
    # ★ counts 不能把「西餐」算两遍（它既是细标签又是聚合键）
    cc = F.counts(rows)
    check(u'counts[西餐] 不重复计', cc.get(u'西餐'), len(F.apply(rows, u'西餐')))

    # ★ 覆盖率自检：换地图后靠这个数发现退化
    cov = F.coverage(rows + [{'name': u'无名小店'}])
    check(u'coverage 能报出未分类', cov['untagged'], 1)
    check(u'coverage 百分比', cov['rate'], 80.0)


# ── 翻页（select.pick 的 page）─────────────────────────────
def test_paging():
    from walkkcal import select as S
    cands = [{'name': u'店%d' % i, 'quad': i % 4, 'walk': 100 + i * 10,
              'fit_n': 2, 'is_mcd': False} for i in range(40)]
    b0, m0 = S.pick(cands, 15, 0)
    b1, m1 = S.pick(cands, 15, 1)
    b2, _ = S.pick(cands, 15, 2)
    check(u'共 40 家 → 3 批', m0['pages'], 3)
    check(u'第 1 批 15 家', len(b0), 15)
    check(u'第 2 批 15 家', len(b1), 15)
    check(u'第 3 批 10 家', len(b2), 10)
    n0 = set(s['name'] for s in b0)
    n1 = set(s['name'] for s in b1)
    check(u'两批不重叠', len(n0 & n1), 0)
    # 稳定性：同样的输入，同一批结果必须一样（否则「换一批」会重复）
    b0b, _ = S.pick(cands, 15, 0)
    check(u'同批结果稳定', [s['name'] for s in b0b], [s['name'] for s in b0])
    # 越界页被钳制
    _, mX = S.pick(cands, 15, 99)
    check(u'越界页钳到末页', mX['page'], 2)

    # ★ 回归：**所有批次合起来不能有重复**。
    #   曾经在 pick() 里对单批做「麦当劳超了换本地店」，替换会从批次外借店，
    #   结果每批都借同一批 → 翻页严重串批（第 1/2/3 批都出现同几家）。实测踩过。
    mixed = ([{'name': u'麦%d' % i, 'quad': i % 4, 'walk': 100 + i * 10,
               'fit_n': 9, 'is_mcd': True} for i in range(30)]
             + [{'name': u'本地%d' % i, 'quad': i % 4, 'walk': 50 + i * 10,
                 'fit_n': 2, 'is_mcd': False} for i in range(20)])
    seen, dup = set(), 0
    _, mm = S.pick(mixed, 15, 0)
    for pg in range(mm['pages']):
        b, _ = S.pick(mixed, 15, pg)
        for s in b:
            if s['name'] in seen:
                dup += 1
            seen.add(s['name'])
    check(u'翻页无串批（全批次不重复）', dup, 0)
    # 每批的麦当劳占比 ≤ 1/3
    b0, _ = S.pick(mixed, 15, 0)
    check(u'首启麦当劳 ≤1/3', len([s for s in b0 if s['is_mcd']]) <= 5, True)


# ── SVG 文本转义（esc）─────────────────────────────────────
def test_esc():
    from walkkcal import render as R
    check(u'& 转义', R.esc(u'Baker&Spice'), u'Baker&amp;Spice')
    check(u'< 转义', R.esc(u'a<b'), u'a&lt;b')
    check(u'> 转义', R.esc(u'a>b'), u'a&gt;b')
    check(u'None 安全', R.esc(None), u'')
    # ★ 回归：品牌名带 & 时，整张 SVG 必须仍能被 XML 解析
    import xml.etree.ElementTree as ET
    bad = u'<svg><text>%s</text></svg>' % R.esc(u'Baker&Spice')
    try:
        ET.fromstring(bad)
        check(u'带 & 的 SVG 可解析', True, True)
    except Exception as e:
        check(u'带 & 的 SVG 可解析', u'失败 %s' % e, True)


# ── 返回契约（reply）与下一步建议（hints）──────────────────
def test_reply_hints():
    import re
    from walkkcal import hints, reply
    meta = {'total': 24, 'shown': 15, 'page': 0, 'pages': 2,
            'keyword': u'全部', 'candidates': 47}
    batch = [
        {'name': u'麦当劳(虬江路店)', 'walk': 253, 'fit_n': 3,
         'is_mcd': True, 'type': u'餐饮服务;快餐厅;快餐厅', 'dishes': []},
        {'name': u'某面馆', 'walk': 121, 'fit_n': 2, 'is_mcd': False,
         'keytag': u'牛肉面', 'dishes': []},
    ]
    nx = hints.next_actions(meta, batch, radius=1500)
    check(u'hints 最多 3 条', len(nx) <= 3, True)
    # ★★ 2026-10-15：选项文案一律**第一人称口语**（用户会说的话），
    #   不写系统动作名。「换一批」→「我想看看别的」；「别走太远」→「不想走太远」；
    #   「只看X」→「只想吃X」；「看全部」→「都看看」。
    #   理由：这些标签会直接变成按钮文字，动作名是机器的口吻。
    check(u'多批时提示「我想看看别的」', nx[0]['label'], u'我想看看别的')
    check(u'翻批参数 = page+1', nx[0]['params']['page'], 1)
    check(u'radius>600 提示「不想走太远」',
          any(x['label'] == u'不想走太远' for x in nx), True)
    #   业态提示需要 ≥MIN_FOR_INDUSTRY_HINT 家才会给，这里单独造一个池子
    _pool = [{'name': u'快餐店%d' % i, 'is_mcd': False,
              'industries': [u'快餐']} for i in range(3)]
    _nxi = hints.next_actions({'total': 30, 'shown': 15, 'page': 0,
                               'pages': 2, 'keyword': u'全部'}, _pool)
    check(u'业态提示写成「只想吃X」',
          any(x['label'].startswith(u'只想吃') for x in _nxi), True)
    check(u'★ 不再出现旧动作名',
          any(x['label'] in (u'换一批', u'别走太远', u'看全部') for x in nx), False)

    # 已筛业态 → 提示「都看看」，且不再提示筛业态
    meta2 = dict(meta, keyword=u'西餐', total=15, pages=1)
    nx2 = hints.next_actions(meta2, batch, radius=1500)
    check(u'已筛时提示「都看看」',
          any(x['label'] == u'都看看' for x in nx2), True)
    check(u'已筛时不再提示筛业态',
          any(x['kind'] == 'industry' for x in nx2), False)

    # 三段式文案（★ 2026-10-15 口语化：内部术语一律不出现）
    pay = reply.build(450, batch, meta, nx, pool_size=47, walk_m=253)
    check(u'含额度（口语）', u'按 450 卡给你挑' in pay['text'], True)
    check(u'含挑选家数（如实）', u'我挑了 24 家' in pay['text'], True)
    check(u'含最推荐的一家', u'麦当劳' in pay['text'], True)
    check(u'结尾是问句（第二人称）',
          u'要我再翻一批给你看看吗？' in pay['text'], True)
    check(u'★ 内部术语不泄露（进入候选/封顶/占 1/3）',
          any(t in pay['text'] for t in (u'进入候选', u'封顶', u'占 1/3')),
          False)
    check(u'契约四键齐', sorted(pay.keys()),
          sorted(['text', 'image', 'meta', 'next']))
    check(u'无量词泄露', bool(re.search(u'\\[|\\{', pay['text'])), False)

    # ★ 官方热量那家要单独点名（麦当劳是唯一有官方营养表的渠道）
    _mcd = {'name': u'麦当劳(测试店)', 'walk': 300, 'quota': 400, 'fit_n': 1,
            'dishes': [{'name': u'麦香鱼', 'kcal': 325, 'ok': True,
                        'src': u'MCP 官方'}]}
    _other = {'name': u'某面馆', 'walk': 100, 'quota': 400, 'fit_n': 9,
              'dishes': [{'name': u'牛肉面', 'kcal': 400, 'ok': True,
                          'src': u'菜谱推算'}]}
    _mt = {'total': 2, 'shown': 2, 'page': 0, 'pages': 1,
           'keyword': u'全部'}
    _t = reply.line_result([_other, _mcd], _mt, pool_size=2)
    check(u'官方热量那家被点名', u'麦香鱼 325 卡' in _t, True)
    check(u'点名时说明是官方实数', u'官方给的实数' in _t, True)
    check(u'没有官方数据时不乱说',
          u'官方给的实数' in reply.line_result([_other], _mt, pool_size=2),
          False)

    # 截断：每店最多 8 道；全超额度时末 2 位换成额度内的
    rows = [{'dishes': [{'kcal': 900, 'ok': False} for _ in range(12)]}]
    reply.trim_dishes(rows)
    check(u'菜截到 8 道', len(rows[0]['dishes']), 8)

    # 单店详情（第三轮收敛）
    one = {'name': u'某面馆', 'walk': 121, 'quota': 450,
           'dishes': [{'name': u'牛肉面', 'kcal': 400, 'src': u'菜谱推算'},
                      {'name': u'大排面', 'kcal': 700, 'src': u'菜谱推算'}]}
    d = reply.describe_store(one, 450)
    check(u'详情含店名', u'某面馆' in d, True)
    check(u'详情分「能点的」/「超出这顿的」',
          u'能点的有' in d and u'超出这顿的' in d, True)
    check(u'少于额度时给出条数', u'能点的有 1 道' in d, True)
    # ★ 官方值门店（麦当劳）要加一句来源说明
    dm = reply.describe_store(_mcd, 400)
    check(u'官方值门店加注来源', u'官方营养表' in dm, True)
    check(u'非官方门店不加注', u'官方营养表' in d, False)


# ── 服务商边界（★ 换代守卫：换地图/换语料必须仍能跑）──────────
#   用户 2026-10-13：「哪些是硬绑定高德/下厨房的？换薄荷就跑不通的？」
#   这一组测试就是那个问题的**可执行答案**。
_FORBIDDEN = [
    u'amap', u'gaode', u'高德', u'restapi', u'餐饮服务', u'外国餐厅',
    u'其它亚洲菜', u'餐饮相关场所', u'detail_info',
    u'keytag', u'atag',
    u'xiachufang', u'xcx_dishes', u'下厨房', u'recipe_corpus',
    u'recipeIngredient', u'recipeInstructions',
    u'boohee', u'薄荷', u'dianping', u'大众点评',
    u'meituan', u'美团',
]
# 这些模块是**通用层**，不允许出现任何服务商私有词汇
#   ★ 例外：`walkkcal/snapshot.py` —— 它的**职责就是**认识历史快照的字段名
#     （也叫「防腐层」：把旧字段名挡在这一层，不让它们渗进业务逻辑）。
#     让别处不认 keytag/atag，才有可能把这一层换掉。
_GENERIC_MODULES = [
    'walkkcal/filter.py', 'walkkcal/prior.py', 'walkkcal/select.py',
    'walkkcal/reply.py', 'walkkcal/hints.py',
    'walkkcal/tiers.py', 'walkkcal/core.py', 'walkkcal/theme.py',
    'packs/registry.py', 'packs/pack_nearby_dining.py',
]
_FORBIDDEN_STRICT = [w for w in _FORBIDDEN if w not in (u'keytag', u'atag')]


def test_provider_boundary():
    u"""① 通用层不得出现服务商私有词汇（用 AST，只查**代码里的字符串**）。

    用 AST 而不是 grep 的原因：docstring 与注释里**应该**点名服务商
    （那是给人看的解释）；被禁的是写进**逻辑**里的私有词汇。
    """
    import ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def code_strings(path):
        u"""收集「非 docstring」的字符串常量"""
        src = io.open(path, encoding='utf-8').read()
        tree = ast.parse(src)
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                b = getattr(node, 'body', None)
                if b and isinstance(b[0], ast.Expr) and \
                        isinstance(b[0].value, ast.Constant) and \
                        isinstance(b[0].value.value, str):
                    docs.add(id(b[0].value))
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docs:
                    continue
                out.append(node.value)
        return out

    bad = {}
    for rel in _GENERIC_MODULES:
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue
        hits = set()
        for s in code_strings(p):
            low = s.lower()
            for w in _FORBIDDEN_STRICT:
                if w in low:
                    hits.add(w)
        if hits:
            bad[rel] = sorted(hits)
    check(u'★通用层无服务商私有词汇', bad, {})


def test_map_swap_end_to_end():
    u"""② 换一家地图的**端到端**验证（不碰高德一行）。

    做法：造一份「假地图」的快照（标准结构 + 它自己的私有类目词），
    换上一个假 provider 去翻译，再把整条链路（registry → select →
    filter → snapshot）跑一遍。跑得通 = 换服务商不用改主流程。
    """
    import importlib
    import os
    import tempfile
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    tmp = tempfile.mkdtemp(prefix='walkkcal_fakegeo_')
    # 假地图的私有类目词：故意**一个都不与高德相同**
    fake = [
        {'id': 'F1', 'name': u'假地图日式食堂', 'lon': 121.4701, 'lat': 31.2209,
         'distance_m': 120, 'address': u'测试路1号',
         'private_cat': u'和食', 'dish_hints': [u'寿喜烧', u'味增汤']},
        {'id': 'F2', 'name': u'假地图面包坊', 'lon': 121.4712, 'lat': 31.2215,
         'distance_m': 260, 'address': u'测试路2号',
         'private_cat': u'烘焙', 'dish_hints': [u'可颂', u'栗子挞']},
        {'id': 'F3', 'name': u'假地图快餐站', 'lon': 121.4695, 'lat': 31.2221,
         'distance_m': 380, 'address': u'测试路3号',
         'private_cat': u'汉堡炸物', 'dish_hints': [u'香辣鸡腿堡', u'鸡米花']},
        {'id': 'F4', 'name': u'假地图面铺', 'lon': 121.4720, 'lat': 31.2200,
         'distance_m': 470, 'address': u'测试路4号',
         'private_cat': u'面食', 'dish_hints': [u'红烧牛肉面', u'大排面']},
    ]
    io.open(os.path.join(tmp, 'stores.json'), 'w', encoding='utf-8').write(
        json.dumps({'origin': {'name': u'假地图中心', 'lon': 121.47,
                               'lat': 31.2209},
                    'stores': [dict(x, distance_m=x['distance_m'])
                               for x in fake]}, ensure_ascii=False))
    io.open(os.path.join(tmp, 'detail.json'), 'w', encoding='utf-8').write(
        json.dumps({'origin': {'name': u'假地图中心', 'lon': 121.47,
                               'lat': 31.2209},
                    'local': fake}, ensure_ascii=False))

    class FakeProvider(object):
        name = u'假地图'
        VOCAB = {u'和食': [u'日料'], u'烘焙': [u'面包甜点'],
                 u'汉堡炸物': [u'快餐'], u'面食': [u'面馆']}

        def industries_of(self, raw):
            return list(self.VOCAB.get(raw.get('private_cat') or u'', []))

    from walkkcal import geo
    real_get = geo.get
    geo.get = lambda name=None: FakeProvider()
    os.environ['WALKKCAL_DATA'] = tmp
    try:
        from packs import registry
        importlib.reload(registry)
        rows = registry.collect()
        check(u'假地图：聚合到门店', len(rows) > 0, True)
        inds = set()
        for r in rows:
            inds |= set(r.get('industries') or [])
        check(u'★假地图私有类目已翻译成归一化业态',
              inds, {u'日料', u'面包甜点', u'快餐', u'面馆'})

        # 业态筛选（换地图后仍然工作）
        from walkkcal import filter as F
        inds_only = u'日料'
        check(u'假地图：能按业态筛出日料',
              len(F.apply(rows, inds_only)), 1)
        check(u'假地图：能按聚合键筛「西餐」',
              len(F.apply(rows, u'西餐')), 1)

        # 选店链路
        from walkkcal import select as S
        cands = S.to_candidates(rows, origin=(121.47, 31.2209))
        batch, meta = S.pick(cands, 15, 0, None)
        check(u'假地图：选店链路通畅', len(batch), 4)
        check(u'假地图：每家有距离', all(b.get('walk') for b in batch), True)

        # 热量走的是**当前语料 provider**（与地图无关）
        check(u'假地图：热量仍能算出',
              all(any(d['kcal'] for d in b['dishes']) for b in batch), True)
    finally:
        geo.get = real_get
        os.environ.pop('WALKKCAL_DATA', None)
        importlib.reload(registry)


def test_snapshot_adapters():
    u"""③ 三种历史快照格式 → 同一个归一化结构。"""
    from walkkcal import snapshot as snap
    # 池子格式（walk/straight）
    a = snap.adapt({'id': '1', 'name': u'某店', 'lon': '121.4', 'lat': '31.2',
                    'walk': 300, 'straight': 250, 'addr': u'某路',
                    'tag': u'红烧肉,油爆虾', 'industries': ['中餐本帮']})
    check(u'池子格式：距离取 walk', a['distance_m'], 300)
    check(u'池子格式：直线另存', a['straight_m'], 250)
    check(u'池子格式：菜名拆开', a['dish_hints'], [u'红烧肉', u'油爆虾'])
    # 详情格式（keytag/atag）
    b = snap.adapt({'id': '2', 'name': u'某面馆', 'walk': 100,
                    'keytag': u'牛肉面', 'atag': u'红烧牛肉面', 'cost': '35'})
    check(u'详情格式：keytag→品类线索', b['category_hint'], u'牛肉面')
    check(u'详情格式：atag 当菜名兜底', b['dish_hints'], [u'红烧牛肉面'])
    # 标准格式
    c = snap.adapt({'id': '3', 'name': u'某咖啡', 'distance_m': 500,
                    'category_hint': u'咖啡', 'dish_hints': [u'拿铁'],
                    'industries': ['咖啡']})
    check(u'标准格式：直通', c['industries'], ['咖啡'])
    # 三者的键完全一致（这是「换格式不用改上层」的前提）
    check(u'★三种格式归一化后键一致',
          sorted(a.keys()), sorted(c.keys()))
    # 距离兜底：只有直线时也要有距离（否则地图投影会崩）
    d = snap.adapt({'name': u'只有直线', 'straight': 400})
    check(u'仅直线时 distance_m 兜底', d['distance_m'], 400)
    # 麦当劳自动识别
    check(u'麦当劳自动标记', snap.adapt({'name': u'麦当劳(某店)'})['is_mcd'], True)

    # ★ 第 64 轮：推荐菜**必须去重** —— 数据源原文就有重复
    #   （实测辛香汇的「毛血旺」出现两次、贯贯吉有 4 个重复项）
    check(u'★字符串菜名去重（保序）',
          snap.split_dishes(u'毛血旺,酸菜鱼,毛血旺'),
          [u'毛血旺', u'酸菜鱼'])
    check(u'★列表菜名去重（保序）',
          snap.split_dishes([u'红烧肉', u'红烧肉', u'油爆虾']),
          [u'红烧肉', u'油爆虾'])
    check(u'★混合分隔符 + 去重',
          snap.split_dishes(u'甲，乙；丙、甲'),
          [u'甲', u'乙', u'丙'])
    check(u'空值与空白项', snap.split_dishes(u' , 甲 , , '), [u'甲'])
    check(u'None → 空列表', snap.split_dishes(None), [])
    # 去重要落到归一化结果上（不只是工具函数）
    e = snap.adapt({'name': u'某店', 'tag': u'毛血旺,毛血旺'})
    check(u'★归一化后无重复菜名', e['dish_hints'], [u'毛血旺'])


def test_provider_without_dishes():
    u"""④ 服务商不提供推荐菜时，不能把门店全丢掉（★ 实测踩过的坑）。

    实测（2026-10-14，百度 AK）：K11 半径 600m 抓到 50 家门店，
    因为百度**没有推荐菜字段**，50 家全被判「没有可展示的菜」剔除，
    最终只剩 1 家麦当劳 + 一张缩放退化（0×0 px）的废图。

    ★ 本测试不需要联网：`BaiduProvider()` 构造函数不校验 AK，
      只有真正发请求时才校验 —— 所以 `provides_dishes` 能直接读到。
    """
    import importlib
    import os
    import subprocess
    import tempfile

    # 造一个「有门店、无推荐菜」的标准结构场景
    tmp = tempfile.mkdtemp(prefix='walkkcal_nodish_')
    stores = []
    for i, ind in enumerate([u'中餐本帮', u'日料', u'快餐', u'咖啡', u'面馆']):
        stores.append({'id': 'N%d' % i, 'name': u'无菜测试店%d' % i,
                       'lon': 121.4700 + i * 0.0006, 'lat': 31.2209 + i * 0.0004,
                       'distance_m': 80 + i * 60, 'address': u'测试路%d号' % i,
                       'category_hint': ind, 'industries': [ind],
                       'dish_hints': []})              # ★ 没有推荐菜
    io.open(os.path.join(tmp, 'stores.json'), 'w', encoding='utf-8').write(
        json.dumps({'origin': {'name': u'无菜中心', 'lon': 121.47, 'lat': 31.2209},
                    'provider': u'百度地图', 'stores': stores}, ensure_ascii=False))
    io.open(os.path.join(tmp, 'detail.json'), 'w', encoding='utf-8').write(
        json.dumps({'origin': {'name': u'无菜中心', 'lon': 121.47, 'lat': 31.2209},
                    'local': stores}, ensure_ascii=False))

    env = dict(os.environ)
    env['WALKKCAL_DATA'] = tmp
    env['WALKKCAL_GEO'] = 'baidu'          # provides_dishes = False
    env.pop('BAIDU_AK', None)              # ★ 故意不给 Key：不该联网
    env['DISH_MODE'] = 'C'
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # ★ 输出重定向到临时目录 —— 测试不该往交付目录 out/ 里写东西
    out_dir = os.path.join(tmp, 'out')
    env['WALKKCAL_OUT'] = out_dir
    out_svg = os.path.join(out_dir, 'skill_mobile_C.svg')
    if os.path.exists(out_svg):
        os.remove(out_svg)

    p = subprocess.run([sys.executable, os.path.join('walkkcal', 'render.py')],
                       cwd=root, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=180)
    log = p.stdout.decode('utf-8', 'ignore')
    check(u'不提供推荐菜时渲染不崩', p.returncode, 0)
    check(u'★门店没被全丢掉', u'无菜测试店' in log, True)
    svg = io.open(out_svg, encoding='utf-8').read() if os.path.exists(out_svg) else u''
    check(u'★卡片标了「无菜品数据」', u'无菜品数据' in svg, True)
    check(u'★图例说明了数据源缺字段', u'不提供推荐菜' in svg, True)
    check(u'地图正常投影（不是 0×0 退化）', u'缩放 横向0.001' not in log, True)
    # ── 对照组 A（★ 2026-10-15 新增）：**安全带 —— 声明与事实矛盾时以事实为准**
    #   同一份「全无推荐菜」的数据，但**不设 WALKKCAL_GEO**（默认高德，
    #   声明 provides_dishes=True）。旧行为是「门店被全部剔除 → 报错退出」；
    #   现在应由「数据事实」救回来（门店模式），并打印退回原因。
    #   ★ 断言改了不是放松：出图比报错有用，而且这才修掉了「漏声明就静默毁图」。
    env2 = dict(env)
    env2.pop('WALKKCAL_GEO', None)
    if os.path.exists(out_svg):
        os.remove(out_svg)                 # 关键：避免读到上一轮的旧文件
    p2 = subprocess.run([sys.executable, os.path.join('walkkcal', 'render.py')],
                        cwd=root, env=env2, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=180)
    log2 = p2.stdout.decode('utf-8', 'ignore')
    check(u'★安全带：声明与事实矛盾时门店仍被保住',
          u'无菜测试店' in log2, True)
    check(u'★安全带：打印了退回原因', u'没有任何推荐菜' in log2, True)

    # ── 对照组 B：**口径没松** —— 数据源确实有推荐菜时，
    #   没有推荐菜的店仍应被剔除。（四象限各一，排除配额干扰）
    mixed = tempfile.mkdtemp(prefix='walkkcal_mixed_')
    rows = []
    for i, (dx, dy) in enumerate([(0.0015, 0.0), (0.0, 0.0015),
                                  (-0.0015, 0.0), (0.0, -0.0015)]):
        has = i < 3                        # 前 3 家有菜，第 4 家没有
        rows.append({'id': 'M%d' % i,
                     'name': (u'有菜店%d' % i) if has else (u'无菜店%d' % i),
                     'lon': 121.4700 + dx, 'lat': 31.2209 + dy,
                     'distance_m': 120 + i * 20, 'address': u'测试路%d号' % i,
                     'category_hint': u'中餐本帮', 'industries': [u'中餐本帮'],
                     'dish_hints': [u'红烧牛肉面', u'麻婆豆腐'] if has else []})
    for fn, key in (('stores.json', 'stores'), ('detail.json', 'local')):
        io.open(os.path.join(mixed, fn), 'w', encoding='utf-8').write(
            json.dumps({'origin': {'name': u'混合中心', 'lon': 121.47,
                                   'lat': 31.2209}, key: rows},
                       ensure_ascii=False))
    env3 = dict(env)
    env3['WALKKCAL_DATA'] = mixed
    env3.pop('WALKKCAL_GEO', None)
    p3 = subprocess.run([sys.executable, os.path.join('walkkcal', 'render.py')],
                        cwd=root, env=env3, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=180)
    log3 = p3.stdout.decode('utf-8', 'ignore')
    check(u'对照组 B：有推荐菜的店保留', u'有菜店' in log3, True)
    check(u'★对照组 B：没推荐菜的店仍被剔除（口径没松）',
          u'无菜店' not in log3, True)


def test_rate_limit():
    u"""⑤ 限速与并发（★ 2026-10-15 收到百度告警短信后补）。

    背景：百度个人 AK 的**并发上限是 3**。超了不报错，只发短信告警：
      「并发量超过上限 3/3，需要回复工单」。
    成因：探针循环 `sleep 0.30` + provider 默认 `qps_wait=0.35`，
          正好贴着 3 QPS 跑了几百次。

    ★ 本测试全离线，不发任何请求。
    """
    import time as _t
    from walkkcal.geo import RateLimiter, new_limiter

    # ① 间隔真的生效
    rl = RateLimiter(0.25, 0)
    t0 = _t.time()
    for _ in range(3):
        rl.wait()
    dt = _t.time() - t0
    check(u'★限速器强制最小间隔', dt >= 0.48, True)
    check(u'限速器计数', rl.count, 3)

    # ② 保险丝：超上限必须抛，不能静默继续
    rl2 = RateLimiter(0.01, 2)
    rl2.wait(); rl2.wait()
    _tripped = False
    try:
        rl2.wait()
    except SystemExit:
        _tripped = True
    check(u'★超过请求上限会抛错（不静默）', _tripped, True)

    # ③ 环境变量可调
    os.environ['WALKKCAL_MIN_INTERVAL'] = '2.5'
    os.environ['WALKKCAL_MAX_REQUESTS'] = '7'
    try:
        rl3 = new_limiter()
        check(u'env 覆盖间隔', rl3.min_interval, 2.5)
        check(u'env 覆盖上限', rl3.max_requests, 7)
    finally:
        os.environ.pop('WALKKCAL_MIN_INTERVAL', None)
        os.environ.pop('WALKKCAL_MAX_REQUESTS', None)

    # ④ 两个 provider 都必须**实际**接上限速器（不是只有类属性）
    from walkkcal.providers.geo_amap import AmapProvider
    from walkkcal.providers.geo_baidu import BaiduProvider
    a, b = AmapProvider(key='x'), BaiduProvider(ak='x')
    check(u'高德挂了限速器', isinstance(a.limiter, RateLimiter), True)
    check(u'百度挂了限速器', isinstance(b.limiter, RateLimiter), True)
    check(u'高德并发恒为 1（间隔 >0）', a.limiter.min_interval > 0, True)
    check(u'百度并发恒为 1（间隔 >0）', b.limiter.min_interval > 0, True)

    # ⑤ ★ provides_dishes 必须显式声明（默认 False 会误伤高德）
    #    踩过：忘了给高德声明 True → 高德也被当成「不提供推荐菜」，
    #    门店不再按「有没有菜」剔除，渲染口径悄悄变了。
    check(u'★高德声明提供推荐菜', AmapProvider(key='x').provides_dishes, True)
    check(u'★百度声明不提供推荐菜', BaiduProvider(ak='x').provides_dishes, False)


def test_local_menu_hook():
    u"""⑥ 自带菜品表（`data/menu.json`）：把「店 → 推荐菜」补上。

    场景：地图不给推荐菜（如百度），但**用户自己**手上有菜品数据。
    这一步让链路不必依赖任何地图就能出完整卡片。
    ★ 全离线。
    """
    import importlib
    import os
    import tempfile

    tmp = tempfile.mkdtemp(prefix='walkkcal_menu_')
    stores = [
        {'id': 'M1', 'name': u'测试面馆(总店)', 'lon': 121.4700, 'lat': 31.2209,
         'distance_m': 100, 'category_hint': u'面馆',
         'industries': [u'面馆'], 'dish_hints': []},
        {'id': 'M2', 'name': u'测试火锅店', 'lon': 121.4706, 'lat': 31.2213,
         'distance_m': 200, 'category_hint': u'火锅店',
         'industries': [u'火锅烧烤'], 'dish_hints': []},
    ]
    io.open(os.path.join(tmp, 'detail.json'), 'w', encoding='utf-8').write(
        json.dumps({'origin': {'name': u'测试', 'lon': 121.47, 'lat': 31.2209},
                    'local': stores}, ensure_ascii=False))
    io.open(os.path.join(tmp, 'stores.json'), 'w', encoding='utf-8').write(
        json.dumps({'origin': {'name': u'测试', 'lon': 121.47, 'lat': 31.2209},
                    'stores': stores}, ensure_ascii=False))

    os.environ['WALKKCAL_DATA'] = tmp
    from packs import registry
    try:
        # ① 没有 menu.json → 菜品为空（地图也没给）
        importlib.reload(registry)
        rows = registry.collect()
        check(u'无菜品表：本地店菜品为空',
              sum(len(r['dishes']) for r in rows), 0)

        # ② 放一份菜品表：一个按完整店名、一个按**去分店后缀**的店名、一个按 id
        io.open(os.path.join(tmp, 'menu.json'), 'w', encoding='utf-8').write(
            json.dumps({u'测试面馆': [u'红烧牛肉面', u'大排面'],   # 去后缀匹配
                        # ★ 用完整菜名：`毛肚`/`黄喉` 这类 2 字涮菜会被
                        #   `prior.is_menu_item` 正确过滤（食材/加料，见 2026-10-15）
                        u'M2': [u'毛血旺', u'麻辣牛肉']},        # id 匹配
                       ensure_ascii=False))
        importlib.reload(registry)
        rows = registry.collect()
        by = {r['name']: r for r in rows}
        a = by.get(u'测试面馆(总店)')
        b = by.get(u'测试火锅店')
        check(u'★按去后缀店名匹配上', bool(a and len(a['dishes']) == 2), True)
        check(u'★按店铺 id 匹配上', bool(b and len(b['dishes']) == 2), True)
        check(u'菜品名正确',
              sorted(d['name'] for d in (a or {}).get('dishes', [])),
              sorted([u'红烧牛肉面', u'大排面']))
        # ③ 热量仍走语料 provider（自带表只给菜名，不给热量）
        check(u'热量走分层兜底（菜谱/品类）',
              all(d['kcal'] for d in (a or {}).get('dishes', [])), True)
    finally:
        os.environ.pop('WALKKCAL_DATA', None)
        importlib.reload(registry)


# ── 渠道启用制（registry）──────────────────────────────────
def test_channels():
    u"""⑥ 渠道启用/禁用（★ 2026-10-15）。

    模型：**一个渠道 = 通用插槽**，一口气给「店铺 + 距离 + 菜品」。
    启用哪些由 WALKKCAL_PACKS 决定，默认麦当劳 + 周边（参赛作品），多个则合并。
    """
    import os
    from packs import registry
    from packs.pack_mcd import McdPack
    from packs.pack_nearby_dining import NearbyDiningPack

    old = os.environ.pop('WALKKCAL_PACKS', None)
    try:
        check(u'默认启用 = 麦当劳 + 周边',
              registry.enabled_names(), ['mcd', 'nearby'])
        check(u'★默认第一个是麦当劳（参赛作品）',
              registry.enabled_names()[0], 'mcd')

        os.environ['WALKKCAL_PACKS'] = 'mcd'
        check(u'只启用麦当劳', registry.enabled_names(), ['mcd'])
        check(u'实例化也只有一个',
              [type(p).__name__ for p in registry.get_packs()], ['McdPack'])

        os.environ['WALKKCAL_PACKS'] = 'nearby'
        check(u'只启用周边（即「只要高德」）',
              [type(p).__name__ for p in registry.get_packs()],
              ['NearbyDiningPack'])

        os.environ['WALKKCAL_PACKS'] = 'mcd,nearby'
        check(u'多个 → 顺序保留（合并靠 collect）',
              [type(p).__name__ for p in registry.get_packs()],
              ['McdPack', 'NearbyDiningPack'])

        os.environ['WALKKCAL_PACKS'] = '   '
        check(u'空值回退默认', registry.enabled_names(), ['mcd', 'nearby'])

        os.environ['WALKKCAL_PACKS'] = 'dianping'
        try:
            registry.enabled_names()
            check(u'未知渠道应报错', u'(未抛错)', u'(SystemExit)')
        except SystemExit as e:
            check(u'未知渠道报错里列了可用值', u'mcd' in str(e), True)
    finally:
        os.environ.pop('WALKKCAL_PACKS', None)
        if old is not None:
            os.environ['WALKKCAL_PACKS'] = old

    # 合并的正确性：只启用麦当劳时，门店应当**全是**麦当劳
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    old_data = os.environ.get('WALKKCAL_DATA')
    os.environ['WALKKCAL_DATA'] = os.path.join(root, 'data')
    try:
        mcd_rows = registry.collect(packs=[McdPack(offline=True)])
        check(u'★只启用麦当劳 → 全是麦当劳',
              all(u'麦当劳' in r['name'] for r in mcd_rows), True)
        check(u'只启用麦当劳 → 至少 1 家', len(mcd_rows) > 0, True)
        both = registry.collect()
        check(u'★两个都启用 → 门店数 ≥ 只有麦当劳',
              len(both) >= len(mcd_rows), True)
    finally:
        if old_data is None:
            os.environ.pop('WALKKCAL_DATA', None)
        else:
            os.environ['WALKKCAL_DATA'] = old_data

    # 能力声明 + 「源装配」报告
    check(u'麦当劳声明给推荐菜', McdPack(offline=True).provides_dishes, True)
    check(u'周边渠道的能力来自地图 provider（布尔）',
          isinstance(NearbyDiningPack().provides_dishes, bool), True)
    line = registry.describe([McdPack(offline=True), NearbyDiningPack()])
    check(u'源装配写了渠道名',
          u'麦当劳' in line and u'周边餐厅' in line, True)
    check(u'源装配标了菜品能力', u'菜✓' in line, True)

    # ★ 没有渠道供菜时要告警（这是「退化可见」的关键一环）
    from packs.base import BasePack

    class _NoDishPack(BasePack):
        name = u'无菜渠道'
        provides_dishes = False

        def search_stores(self, lon, lat, radius=1500):
            return []

        def menu_of(self, store):
            return []

    d_nodish = registry.describe([_NoDishPack()])
    check(u'★源装配在无渠道供菜时告警', u'没有渠道提供推荐菜' in d_nodish, True)
    check(u'源装配标出该渠道不给菜', u'菜✗' in d_nodish, True)
    check(u'空渠道列表有专门提示',
          u'没有启用任何渠道' in registry.describe([]), True)


# ── 「只卖喝的」隐去规则（select._is_drink_only）────────────────
def test_drink_only():
    u"""用户 2026-10-15：饮品名很有诱惑性（多肉葡萄/奇兰粉芭乐），
    光看菜名认不出是喝的还是吃的 → 判据必须以**店铺官方类目**为主，
    菜名只做第二层「救回」。本测试守的就是这两层。
    """
    from walkkcal import select as S

    def st(name, inds, dishes, hint=u''):
        return {'name': name, 'industries': inds, 'category_hint': hint,
                'dish_hints': dishes, 'walk': 100, 'quad': 0, 'fit_n': 1,
                'is_mcd': False}

    # ① 纯饮品店 + 菜名全是花哨的饮品名 → 隐去
    check(u'喜茶（茶饮·全饮品名）→ 隐去',
          S._is_drink_only(st(u'喜茶', [u'茶饮'],
                              [u'芝芝绿妍茶后', u'多肉葡萄', u'奇兰粉芭乐'])), True)
    check(u'星巴克（咖啡·只有咖啡/牛乳）→ 隐去',
          S._is_drink_only(st(u'星巴克', [u'咖啡', u'茶饮'],
                              [u'美式咖啡', u'牛乳', u'单人餐'])), True)
    # ★ 回归：裸「粉」曾误伤「奇兰粉芭乐」「粉盐金凤梨果茶」
    check(u'★「奇兰粉芭乐」不算能当一餐',
          S._is_drink_only(st(u'X', [u'茶饮'], [u'奇兰粉芭乐'])), True)
    check(u'★「粉盐金凤梨果茶」不算能当一餐',
          S._is_drink_only(st(u'X', [u'茶饮'], [u'粉盐金凤梨果茶'])), True)

    # ② 第 ① 层：挂着正餐业态 → 一律保留（挡住类目误判）
    check(u'★Chili\'s（咖啡+西餐）→ 保留',
          S._is_drink_only(st(u"Chili's", [u'咖啡', u'西餐'],
                              [u'玉米', u'白切鸡'])), False)
    check(u'★阿喵说麻辣香锅（茶饮+中餐本帮）→ 保留',
          S._is_drink_only(st(u'阿喵说', [u'茶饮', u'中餐本帮'],
                              [u'麻辣香锅'])), False)
    # ★★ 2026-10-15 用户裁决：**「小吃」不算正餐业态**（快餐仍算）。
    #    高德的「小吃」是过宽的筐 —— 茶话弄/霸王茶姬的 type 归小吃、
    #    category_hint 却是「奶茶/茶饮」，把它当正餐凭证会让茶饮店全漏出
    #    （全场景实测 54 家，其中约 21 家是纯茶饮品牌）。
    check(u'★茶话弄（小吃+茶饮）→ 隐去（小吃不再是正餐凭证）',
          S._is_drink_only(st(u'茶话弄', [u'小吃', u'茶饮'],
                              [u'桂花引', u'伯牙绝弦'])), True)
    check(u'★CHAGEE（小吃+茶饮）→ 隐去',
          S._is_drink_only(st(u'CHAGEE霸王茶姬', [u'小吃', u'茶饮'],
                              [u'伯牙绝弦', u'桂馥兰香'])), True)
    check(u'★「快餐」仍算正餐（快餐 ≠ 小吃）：茶餐厅 → 保留',
          S._is_drink_only(st(u'揸哥茶餐厅', [u'快餐', u'茶饮'],
                              [u'冻柠茶', u'菠萝油'])), False)
    check(u'★纯小吃店不受影响（不挂饮品业态）→ 保留',
          S._is_drink_only(st(u'沙县小吃', [u'小吃'],
                              [u'拌面', u'蒸饺'])), False)

    # ③ 第 ② 层：咖啡店里真有能当一餐的 → 保留
    check(u'Tims（咖啡·有贝果三明治）→ 保留',
          S._is_drink_only(st(u'Tims', [u'咖啡'],
                              [u'拿铁', u'无肉也欢火腿蛋贝果三明治', u'农夫卷'])), False)
    check(u'多乐之日（面包甜点·面包）→ 保留',
          S._is_drink_only(st(u'多乐之日', [u'面包甜点'],
                              [u'奶油毛毛虫面包'])), False)

    # ④ 集成：pick() 的 meta 与候选数
    rows = ([st(u'喜茶', [u'茶饮'], [u'芝芝绿妍茶后'])]
            + [st(u'中餐%d' % i, [u'中餐本帮'], [u'小炒肉'])
               for i in range(20)])
    _b, _m = S.pick(rows, 15)
    check(u'pick 隐去 1 家饮品店', _m['excluded_drink'], 1)
    check(u'meta 里没有喜茶', u'喜茶' in [x['name'] for x in _b], False)

    # ⑤ 开关：WALKKCAL_DRINK=1 → 不隐
    old = os.environ.get('WALKKCAL_DRINK')
    os.environ['WALKKCAL_DRINK'] = '1'
    try:
        _b2, _m2 = S.pick(rows, 15)
        check(u'WALKKCAL_DRINK=1 时不隐去', _m2['excluded_drink'], 0)
    finally:
        if old is None:
            os.environ.pop('WALKKCAL_DRINK', None)
        else:
            os.environ['WALKKCAL_DRINK'] = old

    # ⑥ 点名业态筛选 → 强制包含（用户点「咖啡」就是要看咖啡店）
    _b3, _m3 = S.pick(rows, 15, 0, u'茶饮')
    check(u'筛「茶饮」时不隐去', _m3['excluded_drink'], 0)
    check(u'筛「茶饮」能出喜茶', u'喜茶' in [x['name'] for x in _b3], True)

    # ⑦ 兜底：排除后连一页都不够 → 退回不过滤（宁可出图，不要空白）
    tiny = [st(u'喜茶', [u'茶饮'], [u'芝芝绿妍茶后']),
            st(u'瑞幸', [u'咖啡'], [u'生椰拿铁'])]
    _b4, _m4 = S.pick(tiny, 15)
    check(u'★兜底：不足一页时退回不过滤', _m4['excluded_drink'], 0)
    check(u'兜底后仍列出 2 家', len(_b4), 2)


# ── 菜品级判断：这个词该不该上卡片（prior.is_menu_item）──────────
def test_menu_item():
    u"""用户 2026-10-15 实测两条：
      · Tims 列了「鲜萃奶咖」「柠檬气泡饮」——饮品挤掉三明治
      · 胖哥俩肉蟹煲列「螃蟹/鸡翅/大虾/泡菜/玉米/豆腐/鸡爪」——是煲里加的料
    用户建议「≤2 字的也可以（当食材）」——方向对，但**不能一刀切**：
    ≤2 字里还有「拌面/炒饭/米线/牛排/烧鹅」这些正经主食。
    所以判据 = 「≤2 字 **且** 没有菜品信号」。
    """
    from walkkcal import prior as P

    # ① 食材/加料（≤2 字且无菜品信号）→ 剔除
    for n in [u'螃蟹', u'鸡翅', u'大虾', u'泡菜', u'玉米', u'豆腐', u'鸡爪',
              u'芝士', u'奶油', u'蘑菇', u'毛肚', u'黄喉']:
        check(u'食材名剔除：%s' % n, P.is_menu_item(n), False)
    # ② ≤2 字但**是**成品菜 → 必须保留（这是「不能一刀切」的关键）
    for n in [u'拌面', u'炒饭', u'米线', u'米粉', u'牛排', u'烧鹅', u'生煎',
              u'冒菜', u'油条', u'沙拉']:
        check(u'2 字成品菜保留：%s' % n, P.is_menu_item(n), True)
    # ③ 喝的 → 剔除（Tims 实测）
    for n in [u'鲜萃奶咖', u'柠檬气泡饮', u'不止拿铁', u'牛油果生椰冷萃',
              u'美式', u'巴旦木炼乳澳白', u'椰子水']:
        check(u'饮品剔除：%s' % n, P.is_menu_item(n), False)
    # ③b ★ 2026-10-15 用户实测扩容
    #     （图上把 Costa 的 `Cappuccino`、王奋斗的「柠檬茶」当成了「菜」）
    #     离线验证：全库 2537 条菜名 → 新增剔除 99 条、**误伤 0**、
    #     边界词 40/40 通过。
    for n in [u'柠檬茶', u'手打柠檬茶', u'佛手柠檬茶', u'冰爽葡萄柠檬茶',
              u'锡兰红茶', u'红茶鸳鸯', u'长岛冰茶', u'望梅碧螺冰茶',
              u'草莓桃桃茶', u'CBT经典柠檬红',
              u'冰镇杨梅瑞纳冰', u'夏日桃桃冰', u'牛油果森林瑞纳冰',
              u'茉莉奶白', u'栀子奶白', u'茉莉奶绿', u'(中杯)珍珠奶绿',
              u'伯爵黄金珍奶', u'白玉厚椰乳', u'芒果生打椰', u'栀香椰椰',
              u'芒果牛奶', u'木瓜牛奶', u'奇异果香蕉牛奶', u'热鲜奶',
              u'甘蔗雪梨汁', u'玉米汁', u'山楂汁', u'羽衣甘蓝果蔬汁',
              u'冰镇酸角汁', u'小麦胚芽饮', u'薏米燕麦饮', u'元气姨妈饮',
              u'玫瑰米露柠檬气泡', u'老盐话梅柠檬气泡', u'芋圆牛奶',
              u'SOE耶加雪菲美式', u'椰子OK瑞纳冰', u'cocktail', u'玫瑰fizz',
              u'活力绝绝紫酸奶']:
        check(u'饮品剔除(扩容)：%s' % n, P.is_menu_item(n), False)
    # ③c ★ 英文名（**大小写不敏感**）—— 用户实测 Costa 的 Cappuccino/Espresso
    for n in [u'Cappuccino', u'cappuccino', u'CAPPUCCINO', u'Espresso',
              u'ESPRESSO', u'Latte', u'Americano', u'Mocha', u'Macchiato',
              u'Affogato', u'Frappuccino', u'Flat White', u'Cold Brew']:
        check(u'英文饮品剔除：%s' % n, P.is_menu_item(n), False)
    # ③d ★ 边界：英文词必须按**拉丁字母**设边界（不能用 \b，中英相连会失效）
    for n in [u'Steak', u'Steakhouse']:
        check(u'★「%s」不是茶（tea 不能误伤）' % n, P.is_menu_item(n), True)
    # ③e ★ 边界：含茶/奶/冰/鲜奶 但是正经菜或甜品 → 必须保留
    #     （这是「不加裸字」的代价校验：不加裸 茶/冰/奶/乌龙/柠檬/鲜奶）
    for n in [u'乌龙茶杏仁', u'茶香鸡', u'茶叶蛋', u'双人下午茶套餐',
              u'沙茶焗纽龙', u'抹茶奶豆腐', u'抹茶布朗尼', u'炸鲜奶',
              u'法式牛奶超软吐司', u'柠檬酸菜鱼', u'奶香肥牛',
              u'芒果奶油三明治', u'姜汁牛奶芝麻汤圆']:
        check(u'★含茶/奶但是菜，保留：%s' % n, P.is_menu_item(n), True)
    # ③f ★★ 「美式」看**位置**（2026-10-15 用户指正）——
    #     「美式汉堡」和「美式咖啡」是两件事：美式做后缀=咖啡，做前缀=通常是菜。
    #     原来用「含美式 且 ≤6 字」，能挡 13 字的比萨却挡不住「美式汉堡」。
    for n in [u'美式', u'冰美式', u'热美式', u'SOE耶加雪菲美式',
              u'夏黑葡萄气泡美式', u'美式咖啡']:
        check(u'★美式做后缀/含咖啡 → 是饮品：%s' % n, P.is_menu_item(n), False)
    for n in [u'美式汉堡', u'美式豪情牛肉培根菠萝比萨']:
        check(u'★美式做前缀 → 是菜：%s' % n, P.is_menu_item(n), True)
    # ③g ★ 饮品漏网（外地四城回归实测）
    for n in [u'酸梅汤', u'鸡尾酒', u'玛格丽特鸡尾酒', u'果粒橙']:
        check(u'饮品剔除(四城回归)：%s' % n, P.is_menu_item(n), False)
    # ③h ★ 「汁」必须**词尾锚定** —— 中间有「汁」的是菜
    for n in [u'橙汁鸡腿', u'鸡汁豆腐', u'蜜汁叉烧', u'鲍汁凤爪',
              u'茄汁海鲜烩意粉']:
        check(u'★「汁」在中间是菜：%s' % n, P.is_menu_item(n), True)
    # ④ 饮品店里**能当一餐的** → 必须保留（否则 Tims 整店会消失）
    for n in [u'无肉也欢火腿蛋贝果三明治', u'农夫卷', u'全麦贝果']:
        check(u'Tims 的轻食保留：%s' % n, P.is_menu_item(n), True)
    # ⑤ 甜的 → 剔除（与既有 _DESSERTS 行为一致）
    for n in [u'蛋挞', u'杨枝甘露', u'冰淇淋', u'提拉米苏']:
        check(u'甜品剔除：%s' % n, P.is_menu_item(n), False)
    # ⑥ 品类/餐次词 → 剔除
    for n in [u'单人餐', u'下午茶', u'堂食', u'料理', u'热饮', u'番茄酱']:
        check(u'品类词剔除：%s' % n, P.is_menu_item(n), False)
    # ⑦ ★ 两个实测误伤必须避开
    check(u'★「可乐鸡翅」是菜（不能被「可乐」误伤）',
          P.is_menu_item(u'可乐鸡翅'), True)
    check(u'★「美式豪情牛肉培根菠萝比萨」是比萨（长名里的「美式」不算饮品）',
          P.is_menu_item(u'美式豪情牛肉培根菠萝比萨'), True)
    # ⑧ 正常菜名
    for n in [u'红烧牛肉面', u'小酥肉', u'麻婆豆腐', u'巨无霸汉堡', u'云海肴汽锅鸡']:
        check(u'正常菜保留：%s' % n, P.is_menu_item(n), True)

    # ⑨ ★★ 架构断言：**两条链路必须用同一个判断**
    #   （2026-10-15 之前 render 私有 _EXCLUDE、registry 用 is_category_word，
    #     同一条数据在网页上被滤、在 MCP 上没滤。）
    import io as _io
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    src = _io.open(_os.path.join(root, 'walkkcal', 'render.py'),
                   encoding='utf-8').read()
    check(u'★render 的 usable() 转发到 prior.is_menu_item',
          u'return prior.is_menu_item(dish)' in src, True)
    reg = _io.open(_os.path.join(root, 'packs', 'registry.py'),
                   encoding='utf-8').read()
    check(u'★registry 也用 is_menu_item',
          u'prior.is_menu_item(name)' in reg, True)


# ── 两套「只卖喝的」判据必须等价（防漂移）──────────────────────
def test_drink_only_parity():
    u"""`prior.is_drink_only_store` 与 `select._is_drink_only` **必须逐字等价**。

    ★ 为什么需要：`render.py` 的象限分桶会丢掉「一道可用菜都没有」的店，
      那里要用**同一个判据**认出「只卖喝的」并计数 —— 否则脚注「已隐去 N 家」
      会漏数（店静默消失）。项目踩过「两条链路两套规则」的坑
      （同一条数据在网页被滤、在 MCP 没滤），所以用并行断言守住。
    """
    import walkkcal.select as S
    from walkkcal import prior as P

    def st(name, inds, dishes):
        return {'name': name, 'industries': list(inds), 'category_hint': u'',
                'dish_hints': list(dishes)}

    cases = [
        st(u'喜茶', [u'茶饮'], [u'芝芝绿妍茶后', u'多肉葡萄']),
        st(u'星巴克', [u'咖啡', u'茶饮'], [u'美式咖啡', u'牛乳']),
        st(u'Tims', [u'咖啡'], [u'拿铁', u'无肉也欢火腿蛋贝果三明治']),
        st(u'多乐之日', [u'面包甜点'], [u'奶油毛毛虫面包']),
        st(u"Chili's", [u'咖啡', u'西餐'], [u'玉米', u'白切鸡']),
        st(u'阿喵说', [u'茶饮', u'中餐本帮'], [u'麻辣香锅']),
        st(u'柠季·手打柠檬茶', [u'茶饮'], [u'手打柠檬茶', u'柠檬茶']),
        st(u'LINLEE·手打柠檬茶', [u'小吃'], [u'手打柠檬茶']),
        st(u'某中餐馆', [u'中餐本帮'], [u'红烧肉', u'麻婆豆腐']),
        st(u'无名咖啡', [], [u'拿铁']),
    ]
    for c in cases:
        check(u'★两套判据一致：%s' % c['name'],
              P.is_drink_only_store(c), S._is_drink_only(c))


# ── 渲染阶段丢掉的纯饮品店，必须计入「已隐去」（防静默消失）──────
def test_excluded_drink_count():
    u"""★ 纯饮品店在**渲染阶段**被丢弃时也要计数。

    起因：把「柠檬茶」正确识别为饮品后，柠季/LINLEE 这类店在渲染阶段
    （「一道可用菜都没有」）就被丢掉，根本到不了 select —— 于是脚注
    「已隐去 N 家」凭空少了一批（店一家没少，只是没被数到）。
    这正是本项目反复强调的「**静默消失才是真问题**」。

    实测背景：凌河路场景改动前脚注**根本没有**这句话（= 0 家），
    改动后如实写 18 家。
    """
    import io as _io
    import json as _json
    import os as _os
    import shutil as _sh
    import subprocess as _sp
    import sys as _sys
    import tempfile as _tf

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    tmp = _tf.mkdtemp(prefix='walkkcal_drinkcount_')
    try:
        stores = []
        for i in range(20):                      # 20 家中餐馆，四象限铺开
            stores.append({
                'id': 'z%d' % i, 'name': u'中餐馆%d店' % i,
                'lon': 121.4700 + (i % 5) * 0.0025 - 0.005,
                'lat': 31.2500 + (i // 5) * 0.0025 - 0.0025,
                'distance_m': 120 + i * 25,
                'dish_hints': [u'红烧肉', u'麻婆豆腐', u'宫保鸡丁'],
                'industries': [u'中餐本帮']})
        stores.append({                          # 1 家纯柠檬茶店
            'id': 'drink1', 'name': u'柠季·手打柠檬茶',
            'lon': 121.4715, 'lat': 31.2515, 'distance_m': 90,
            'dish_hints': [u'手打柠檬茶', u'柠檬茶'],
            'industries': [u'茶饮']})
        doc = {'origin': {'name': u'测试点', 'lon': 121.4712, 'lat': 31.2500},
               'stores': stores}
        for fn in ('stores.json', 'detail.json'):
            _io.open(_os.path.join(tmp, fn), 'w', encoding='utf-8').write(
                _json.dumps(doc, ensure_ascii=False))

        def _run(extra=None):
            env = dict(_os.environ)
            env['WALKKCAL_DATA'] = tmp
            env['WALKKCAL_OUT'] = _os.path.join(tmp, 'out')
            env['DISH_MODE'] = 'C'
            env.pop('WALKKCAL_FILTER', None)
            env.pop('WALKKCAL_DRINK', None)
            if extra:
                env.update(extra)
            r = _sp.run([_sys.executable,
                         _os.path.join(root, 'walkkcal', 'render.py')],
                        cwd=root, env=env, stdout=_sp.PIPE,
                        stderr=_sp.STDOUT, timeout=180)
            return r.stdout.decode('utf-8', 'ignore')

        log = _run()
        check(u'★纯饮品店被计入「已隐去」（不静默消失）',
              u'已隐去 1 家' in log, True)
        check(u'★并说明它是在渲染阶段被跳过的',
              u'在渲染阶段就被跳过' in log, True)
        # 对照组：点名业态 = 用户明确要看这类店 → 不该再说「已隐去」
        log2 = _run({'WALKKCAL_FILTER': u'中餐本帮'})
        check(u'点名业态时不写「已隐去」', u'已隐去' in log2, False)
    finally:
        _sh.rmtree(tmp, ignore_errors=True)


def test_mcp_tool_forwards_coords():
    u"""★ MCP 工具层必须把经纬度**真正传下去**（2026-10-16 实测发现）。

    病根：`tool_what_can_i_eat` 里写死 `_pipeline(None, None, …)` ——
    调用方传了经纬度也被丢掉，于是麦当劳实时路径永远报
    「没有坐标 —— 翻译不出 city/keyword」，**token 有效也照样回退快照**。

    ★ 这条链路是四跳：Agent 传参 → 工具 → `_pipeline` → `registry.collect`
      → `pack.search_stores`。以前只测了**最下游的 pack**
      （`test_mcd_live_translates_lonlat_to_keyword`），**中间两跳没人守** ——
      所以「token 有了但跑不通」这种事故能一直藏着。

    发现方式：用**假 token** 压测文字链（`MCD_MCP_TOKEN=invalid-probe
    WALKKCAL_MCD_LIVE=1`），显式传经纬度仍看到回退 → 才查到这一行。
    正因如此，改完必须两边各一个断言：**schema 要声明** + **实现要转发**。
    """
    import os as _os
    import sys as _sys

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    entries = [_os.path.join(root, 'entry'), root]
    for p in entries:
        if p not in _sys.path:
            _sys.path.insert(0, p)
    import mcp_server as M

    seen = {}

    def _fake_pipeline(lon=None, lat=None, quota=None, industry=None,
                       radius=None, page=0):
        seen['args'] = (lon, lat, quota, industry, radius)
        return [], [], {'total': 0, 'shown': 0, 'page': 0, 'pages': 1,
                        'keyword': u'全部', 'candidates': 0}, []

    real = M._pipeline
    M._pipeline = _fake_pipeline
    try:
        M.tool_what_can_i_eat({'longitude': 121.4759, 'latitude': 31.2523,
                               'quota': 350})
    finally:
        M._pipeline = real

    lon, lat, quota, industry, radius = seen['args']
    check(u'★经度真的传到了流水线（不是 None）', lon, 121.4759)
    check(u'★纬度真的传到了流水线（不是 None）', lat, 31.2523)
    check(u'额度也传对了', quota, 350)

    # ★ 另一半：schema 不声明，Agent 就不知道能传 —— 两头都要有
    _tool = [t for t in M.TOOLS if t['name'] == 'what_can_i_eat'][0]
    _props = _tool['inputSchema']['properties']
    check(u'★schema 声明了 longitude', 'longitude' in _props, True)
    check(u'★schema 声明了 latitude', 'latitude' in _props, True)
    check(u'★schema 里说明了「传了才能走实时」',
          u'实时' in (_props['longitude'].get('description') or u''), True)


# ── 渲染端：额度必须能由调用方指定（WALKKCAL_QUOTA）─────────────
def test_render_quota_env():
    u"""★ 文字链说 350，长图上也必须是 350。

    起因（2026-10-15）：`_pipeline(quota=350)` 一直支持**用户自己设额度**
    （这正是项目定位：「额度分母由用户设定，不由算法推导」），
    但渲染器把 `ALLOWANCE_BASE` 写死 450 —— 用户说「这顿只吃 350 卡」时，
    **话术说 350、图上印 450**。用户一眼就能看出自相矛盾。

    这类「两条链两套口径」是项目的老毛病（文案链与图片链是两套实现），
    所以判据要**两边各测一次**。
    """
    import io as _io
    import json as _json
    import os as _os
    import shutil as _sh
    import subprocess as _sp
    import sys as _sys
    import tempfile as _tf

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    tmp = _tf.mkdtemp(prefix='walkkcal_quota_')
    try:
        stores = []
        for i in range(8):                       # 四象限各 2 家
            stores.append({
                'id': 'q%d' % i, 'name': u'测试餐厅%d店' % i,
                'lon': 121.4700 + (i % 2) * 0.006 - 0.003,
                'lat': 31.2500 + (i // 2) * 0.006 - 0.006,
                'distance_m': 100 + i * 60,
                'dish_hints': [u'红烧肉', u'麻婆豆腐', u'宫保鸡丁',
                               u'青椒炒蛋', u'番茄炒蛋', u'回锅肉',
                                u'酸辣土豆丝', u'干煸四季豆'],
                'industries': [u'中餐本帮']})
        stores.append({                          # 1 家店名标了「暂停营业」
            'id': 'closed1', 'name': u'闭店测试餐厅(暂停营业)',
            'lon': 121.4725, 'lat': 31.2525, 'distance_m': 80,
            'dish_hints': [u'红烧肉', u'麻婆豆腐'],
            'industries': [u'中餐本帮']})
        doc = {'origin': {'name': u'测试点', 'lon': 121.4712, 'lat': 31.2500},
               'stores': stores}
        for fn in ('stores.json', 'detail.json'):
            _io.open(_os.path.join(tmp, fn), 'w', encoding='utf-8').write(
                _json.dumps(doc, ensure_ascii=False))

        def _run(extra=None):
            env = dict(_os.environ)
            env['WALKKCAL_DATA'] = tmp
            env['WALKKCAL_OUT'] = _os.path.join(tmp, 'out')
            env['DISH_MODE'] = 'C'
            env.pop('WALKKCAL_FILTER', None)
            env.pop('WALKKCAL_QUOTA', None)
            if extra:
                env.update(extra)
            _sp.run([_sys.executable,
                     _os.path.join(root, 'walkkcal', 'render.py')],
                    cwd=root, env=env, stdout=_sp.DEVNULL,
                    stderr=_sp.DEVNULL, timeout=180)
            svg = _os.path.join(tmp, 'out', 'skill_mobile_C.svg')
            if not _os.path.exists(svg):
                return u''
            return _io.open(svg, encoding='utf-8').read()

        _svg = _run()
        check(u'默认额度 450 画上图', u'本餐额度 450 卡' in _svg, True)
        # ★ 店名里标了「暂停营业」的店：文字链早就剔了，图片链本轮才补上
        check(u'★ 暂停营业的门店不上图',
              u'闭店测试餐厅' in _svg, False)
        check(u'★ 并如实计数（不静默消失）',
              u'已剔除 1 家店名标注' in _svg, True)
        _svg350 = _run({'WALKKCAL_QUOTA': '350'})
        check(u'★ WALKKCAL_QUOTA=350 时图上印 350',
              u'本餐额度 350 卡' in _svg350, True)
        check(u'★ 且不再出现 450', u'本餐额度 450 卡' in _svg350, False)
    finally:
        _sh.rmtree(tmp, ignore_errors=True)


# ── 调料/碟料：词尾锚定（prior.is_seasoning_item）────────────────
def test_seasoning():
    u"""2026-10-15 补：

    实测（凌河路场景）火锅店的推荐菜里列着「现炸辣椒油」「祖传麻酱碗料」——
    它们是**蘸料**，却因菜品词表没命中而走 L3 兜底 → 按门店业态估成
    「火锅 700 卡」上了卡片。

    规则 = **词尾锚定**（`endswith`）：
      · 调料在真实数据里几乎总是组合词 → 精确匹配必漏（「奶咖」那次的同款坑）
      · 用子串匹配会误杀「麻酱拌面」「豆瓣酱炒饭」→ 必须锚在词尾

    ★ 离线验证（全库 734 条 unique 菜名）：
      新增剔除 2 条（正是上面那两条）、误伤 0、反向检查 0。
    """
    from walkkcal import prior as P

    # ① 调料/碟料 → 剔除
    for n in [u'现炸辣椒油', u'祖传麻酱碗料', u'自制辣椒油', u'秘制芝麻酱',
              u'蒜蓉辣椒酱', u'油碟', u'干碟', u'火锅底料', u'蘸料', u'小料',
              u'油醋汁', u'花生酱']:
        check(u'调料剔除：%s' % n, P.is_menu_item(n), False)
    # ② ★ 防误伤：调料 + 主食/菜 是正经菜，必须保留（词尾锚定的意义在此）
    for n in [u'麻酱拌面', u'辣椒油拌面', u'麻酱菠菜', u'麻酱鸡丝',
              u'豆瓣酱炒饭', u'沙茶酱牛肉', u'油醋汁沙拉', u'蒜泥白肉']:
        check(u'★调料做的菜保留：%s' % n, P.is_menu_item(n), True)
    # ③ 同前缀、不同词尾 → 一个剔一个留
    check(u'★「芝麻酱」剔、「芝麻酱拌面」留',
          (P.is_menu_item(u'芝麻酱'), P.is_menu_item(u'芝麻酱拌面')),
          (False, True))
    # ④ estimate 必须与卡片过滤一致（否则直接调它会给调料估 700 卡）
    check(u'★estimate 对调料弃权（不给 700 卡）',
          P.estimate(u'现炸辣椒油', {u'industries': [u'火锅烧烤']}), None)
    check(u'estimate 对「麻酱拌面」仍给值',
          (P.estimate(u'麻酱拌面') or {}).get('kcal') is not None, True)


# ── 批量距离 / 地址解析：可选的 provider 能力 ────────────────────
def test_provider_batch():
    u"""2026-10-15 抓取提速（134s → 11s）的契约与算法守卫。

    ★ 三条红线，改坏任何一条都会「静默变慢」或「触发地图并发告警」：
      ① 基类必须有默认实现 —— 第三方 provider 不实现也不能破
      ② 两个 provider 的批量**必须经 `_call_retry`**（= 过限速器）
         —— 地图配额按**并发**算，绕过去会收到告警短信（百度实测过）
      ③ 分批与 `origin_id` 重映射必须正确 —— 错位会把距离安到别的店上
    """
    import io as _io
    import os as _os
    from walkkcal import geo as G
    from walkkcal.providers.geo_amap import AmapProvider
    from walkkcal.providers.geo_baidu import BaiduProvider

    # ① 基类默认实现 = 降级逐个（不实现也不会破）
    base = G.GeoProvider()
    check(u'★基类有 walk_route_batch 默认实现',
          hasattr(base, 'walk_route_batch'), True)
    check(u'基类空列表安全', base.walk_route_batch((0.0, 0.0), []), [])
    check(u'基类 geocode 默认 None', base.geocode(u'某地'), None)

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    for fn, cls, kw in ((u'geo_amap.py', AmapProvider, 'key'),
                        (u'geo_baidu.py', BaiduProvider, 'ak')):
        src = _io.open(_os.path.join(root, 'walkkcal', 'providers', fn),
                       encoding='utf-8').read()
        p = cls(**{kw: 'dummy'})
        check(u'%s 实现 walk_route_batch' % fn,
              callable(getattr(p, 'walk_route_batch', None)), True)
        check(u'%s 实现 geocode' % fn,
              callable(getattr(p, 'geocode', None)), True)
        body = src.split(u'def walk_route_batch')[1].split(u'\n    def ')[0]
        check(u'★%s 批量走 _call_retry（不绕限速器）' % fn,
              u'_call_retry(' in body, True)
        check(u'★%s 批量没有裸调 urlopen' % fn, u'urlopen' in body, False)

    # ② 高德分批（100/批）+ origin_id 重映射
    p = AmapProvider(key='dummy')
    calls = []

    def fake_amap(path, **q):
        calls.append(q)
        n = len(q['origins'].split('|'))
        return {'status': '1',
                'results': [{'origin_id': str(i + 1),
                             'distance': str(100 * (i + 1)),
                             'duration': '60'} for i in range(n)]}

    p._call_retry = fake_amap
    out = p.walk_route_batch((121.0, 31.0),
                             [(121.0 + i / 1000.0, 31.0) for i in range(150)])
    check(u'★高德按 100 分批（150 家 → 2 次请求）', len(calls), 2)
    check(u'★每批 origins 数正确',
          [len(c['origins'].split('|')) for c in calls], [100, 50])
    check(u'★结果长度与入参一致', len(out), 150)
    check(u'★第 1 家距离', out[0]['distance_m'], 100)
    check(u'★第二批重映射正确（第 101 家 = 该批第 1 个）',
          out[100]['distance_m'], 100)
    check(u'★第 150 家（该批第 50 个）', out[149]['distance_m'], 5000)
    check(u'★请求参数：步行 type=3 + destination',
          calls[0].get('type') == '3' and 'destination' in calls[0], True)

    # 单项算不出 → 留 None（由调用方回退逐店）
    def fake_missing(path, **q):
        return {'status': '1', 'results': [
            {'origin_id': '1', 'distance': '123', 'duration': '60'}]}

    p2 = AmapProvider(key='dummy')
    p2._call_retry = fake_missing
    out2 = p2.walk_route_batch((121.0, 31.0), [(121.1, 31.1)] * 3)
    check(u'★缺失项留 None（交给上层回退逐店）',
          [x is None for x in out2], [False, True, True])

    # ③ 百度分批（起点×终点 ≤50）+ geocode 坐标系转换
    b = BaiduProvider(ak='dummy')
    bcalls = []

    def fake_baidu(path, **q):
        bcalls.append(q)
        n = len(q['origins'].split('|'))
        return {'status': 0,
                'result': [{'distance': {'value': 100 * (i + 1)},
                            'duration': {'value': 60}} for i in range(n)]}

    b._call_retry = fake_baidu
    bout = b.walk_route_batch((121.0, 31.0),
                              [(121.0 + i / 1000.0, 31.0) for i in range(120)])
    check(u'★百度按 50 分批（120 家 → 3 次请求）', len(bcalls), 3)
    check(u'★百度每批起点数正确',
          [len(c['origins'].split('|')) for c in bcalls], [50, 50, 20])
    check(u'★百度结果重映射正确（第 51 家）', bout[50]['distance_m'], 100)

    # ④ geocode：高德解 location，百度要转 BD-09 → GCJ-02
    p3 = AmapProvider(key='dummy')
    p3._call_retry = lambda path, **q: {
        'status': '1',
        'geocodes': [{'location': '121.4737,31.2304', 'level': u'门牌号',
                      'formatted_address': u'上海市黄浦区人民大道200号'}]}
    g = p3.geocode(u'人民大道200号')
    check(u'★高德 geocode 解析坐标',
          (round(g['lon'], 3), round(g['lat'], 3)), (121.474, 31.23))
    check(u'高德 geocode 带回地名', bool(g['formatted']), True)

    p3._call_retry = lambda path, **q: {'status': '1', 'geocodes': []}
    check(u'★geocode 空结果返回 None', p3.geocode(u'不存在的地方'), None)

    b2 = BaiduProvider(ak='dummy')
    b2._call_retry = lambda path, **q: {
        'status': 0, 'result': {'location': {'lng': 121.4799, 'lat': 31.2365},
                                'level': u'门牌号'}}
    bg = b2.geocode(u'人民大道200号')
    check(u'★百度 geocode 输出的是 GCJ-02（不是原始 BD-09）',
          abs(bg['lon'] - 121.4799) > 1e-5, True)


def test_output_mode():
    u"""★ 输出形态判据（2026-10-15）：要同时比较 ≥2 个对象才出图。

    契约里 `image` 本来就是可选、MCP 入口本来就不出图、`describe_store()`
    早就是纯文字 —— 但「什么时候该出图」以前一处都没写。本测试把它钉住。
    """
    from walkkcal import reply

    check(u'15 家候选 → poster', reply.choose_mode([{}] * 15, {'shown': 15}),
          reply.MODE_POSTER)
    check(u'2 家候选 → poster（要比较）', reply.choose_mode([{}, {}], {'shown': 2}),
          reply.MODE_POSTER)
    check(u'1 家候选 → text（没有可比对象）', reply.choose_mode([{}], {'shown': 1}),
          reply.MODE_TEXT)
    check(u'0 家候选 → text', reply.choose_mode([], {'shown': 0}), reply.MODE_TEXT)
    check(u'★单店详情 → text（点名一家店，出图是过度交付）',
          reply.choose_mode([{}] * 15, {'shown': 15}, store={'name': u'某店'}),
          reply.MODE_TEXT)
    # meta.shown 比 batch 小（翻页尾部）时以 shown 为准
    check(u'batch 15 但实际只出 1 家 → text',
          reply.choose_mode([{}] * 15, {'shown': 1}), reply.MODE_TEXT)
    # build() 自动写进 meta
    out = reply.build(450, [{}] * 15, {'total': 15, 'shown': 15})
    check(u'build() 自动判定并写进 meta.mode', out['meta'].get('mode'),
          reply.MODE_POSTER)
    out2 = reply.build(450, [{}], {'total': 1, 'shown': 1})
    check(u'单条时 build() 判为 text', out2['meta'].get('mode'), reply.MODE_TEXT)
    check(u'显式传 mode 可覆盖', reply.build(450, [{}], {'shown': 1},
                                            mode=reply.MODE_POSTER)['meta']['mode'],
          reply.MODE_POSTER)


def test_business_status():
    u"""★★ 店名里的营业状态（2026-10-15 实测发现）。

    地图源**没有**营业状态字段，但偶尔把状态写进店名。全库 5475 条命中 2 家。
    ⚠ 顺带修一个隐性 bug：`render.clean_name` 显示店名时按 `(` 截断，
    正好把这个标记删掉 —— 我们读得到却主动丢了。
    """
    from walkkcal import snapshot, select

    # ① 解析：真实命中过的两条 + 干净店名不受影响
    check(u'★真实案例：黄山菜饭(凌河路店)(暂停营业)',
          snapshot.parse_status(u'黄山菜饭(凌河路店)(暂停营业)'),
          (u'黄山菜饭(凌河路店)', 'paused'))
    check(u'★真实案例：八喜潮皇…(珠江新城店)(暂停营业)',
          snapshot.parse_status(u'八喜潮皇动物奶油冰淇淋蛋糕(珠江新城店)(暂停营业)'),
          (u'八喜潮皇动物奶油冰淇淋蛋糕(珠江新城店)', 'paused'))
    check(u'已关闭 → closed',
          snapshot.parse_status(u'某某面馆(已关闭)'),
          (u'某某面馆', 'closed'))
    check(u'干净店名 → 状态 None，名字不变',
          snapshot.parse_status(u'星巴克(淮海路店)'),
          (u'星巴克(淮海路店)', None))
    check(u'名字里含「停业」但不是状态词也不会误伤（正常名字）',
          snapshot.parse_status(u'老王烤肉(凌河路店)'),
          (u'老王烤肉(凌河路店)', None))

    # ② 归一化（读路径）会自动带上 status / name_clean
    row = snapshot.adapt({'id': 'X', 'name': u'黄山菜饭(凌河路店)(暂停营业)',
                          'lon': 121.6, 'lat': 31.2, 'distance_m': 100})
    check(u'adapt 带出 status', row.get('status'), 'paused')
    check(u'adapt 带出 name_clean', row.get('name_clean'), u'黄山菜饭(凌河路店)')

    # ③ 选店时排除，并**如实计数**（不静默消失）
    def st(name, status=None, walk=100):
        return {'name': name, 'status': status, 'walk': walk, 'quad': 0,
                'dish_hints': [u'红烧牛肉面'], 'industries': [u'面馆'],
                'is_mcd': False}
    cands = [st(u'A面馆'), st(u'B面馆'), st(u'C面馆', 'paused'), st(u'D面馆')]
    batch, meta = select.pick(cands, 3, 0)
    check(u'★暂停营业的店不进候选',
          [c['name'] for c in batch].count(u'C面馆'), 0)
    check(u'★如实计数 excluded_closed', meta.get('excluded_closed'), 1)
    # 兜底：剔完凑不满一页 → 退回不过滤（宁可出图，不要空白）
    tiny = [st(u'A面馆'), st(u'B面馆', 'paused')]
    batch2, meta2 = select.pick(tiny, 3, 0)
    check(u'★剔完不够一页时兜底保留', len(batch2), 2)
    check(u'兜底时不计入 excluded_closed', meta2.get('excluded_closed'), 0)
    # 排查开关
    batch3, meta3 = select.pick(cands, 4, 0, include_closed=True)
    check(u'WALKKCAL_CLOSED / include_closed=True 可放行',
          len(batch3), 4)
    check(u'放行时仍包含那家暂停营业的店',
          u'C面馆' in [c['name'] for c in batch3], True)
    check(u'放行时不计数', meta3.get('excluded_closed'), 0)


# ── ★★ 两条链的麦当劳必须同源（2026-10-16 新增）─────────────────
#   触发的事故（Trae CN 实测暴露）：`data/linghelu` 里文字链说「麦当劳 807 m」，
#   图上连一家麦当劳都没有 —— 宿主为此向用户道歉，而且编了个**错的**理由
#   （它猜「快照半径 800 米把 807 米的店挡在外面」，真因是 `detail.json` 缺这条）。
#
#   根因：两条链读**两个不同的文件**，而两个文件的**采集半径本来就不一样**
#        · 文字链 `packs/pack_mcd.py::_snapshot_stores()` → `stores.json`（池子，≈1.4 km）
#        · 图片链 `walkkcal/render.py`                     → `detail.json`（明细，≈0.8 km）
#   那家 807 m 的店落在池子范围内、却越过了明细的 ~800 m 采集线。
#   修法：`render.py` 的 `DET['mcd']` 改取两文件**并集**（`snapshot.merge_mcd`）。
#
#   ⚠ 别搞反：「31 vs 6」**不是** bug —— 默认场景两个文件条数天然不同，
#     只要**展示半径内**一致就没问题。所以断言的是「展示半径内两边同一批店」，
#     不是「条数相等」。判据同理，见 `internal/_check_scene_mcd.py`。
def test_mcd_two_chains_same_source():
    import subprocess
    from walkkcal import snapshot as snap
    from packs.pack_mcd import McdPack

    MCD_MAX = 1500                     # ★ 必须与 render.py::_MCD_MAX_DIST 同步

    def text_side(scene):
        u"""文字链那一侧：走 `McdPack`（它读 stores.json），再按展示半径筛。"""
        import walkkcal  # noqa
        old = os.environ.get('WALKKCAL_DATA')
        os.environ['WALKKCAL_DATA'] = scene
        try:
            rows = McdPack(offline=True).search_stores(None, None, MCD_MAX)
        finally:
            if old is None:
                os.environ.pop('WALKKCAL_DATA', None)
            else:
                os.environ['WALKKCAL_DATA'] = old
        return sorted(x['name'] for x in rows
                      if (x.get('distance') or 0) <= MCD_MAX)

    def image_side(scene, tag):
        u"""图片链那一侧：**跑真渲染**，从日志取家数、从 SVG 取产物。"""
        tmp = tempfile.mkdtemp(prefix='walkkcal_mcd_%s_' % tag)
        env = dict(os.environ)
        env['WALKKCAL_DATA'] = scene
        env['WALKKCAL_OUT'] = tmp
        env['DISH_MODE'] = 'B'
        p = subprocess.run([sys.executable,
                            os.path.join('walkkcal', 'render.py')],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=240)
        log = p.stdout.decode('utf-8', 'ignore')
        svg_path = os.path.join(tmp, 'skill_mobile.svg')
        svg = io.open(svg_path, encoding='utf-8').read() \
            if os.path.exists(svg_path) else u''
        return p.returncode, log, svg

    # ── ① 事故点位：凌河路那家 807 m 的麦当劳 ─────────────────────
    scene = os.path.join(ROOT, 'data', 'linghelu')
    text_names = text_side(scene)
    check(u'★文字链看得到那家 807 m 的麦当劳',
          text_names, [u'麦当劳(浦东融创精彩天地餐厅)'])

    rc, log, svg = image_side(scene, 'linghelu')
    check(u'凌河路场景渲染成功', rc, 0)
    check(u'★图片链（渲染日志）报的麦当劳家数 == 文字链',
          (u'（麦当劳 %d +' % len(text_names)) in log, True)
    # ★ 铁律 5：核查**渲染产物**，不是重算 —— 图上真的画出来了才算数
    check(u'★渲染产物里真有麦当劳卡片',
          u'>麦当劳</text>' in svg, True)
    check(u'★渲染产物里那家店的距离是 807 m',
          u'>807m</text>' in svg, True)

    # ── ② 反向保护：「31 vs 6」不许被当成 bug 修掉 ────────────────
    #   默认场景两个文件条数天然不同（池子 31 / 明细 6），
    #   但展示半径内是**同样那 5 家** —— 两链必须仍然一致。
    scene2 = os.path.join(ROOT, 'data')
    S = snap.read_stores(os.path.join(scene2, 'stores.json'))
    D = snap.read_detail(os.path.join(scene2, 'detail.json'))
    check(u'默认场景两个文件条数确实不同（这就是「31 vs 6」的来源）',
          len(S['mcd']) != len(D['mcd']), True)
    merged = snap.merge_mcd(S['mcd'], D['mcd'])
    check(u'★并集在展示半径内与明细同集合（默认场景出图零改动）',
          sorted(x['name'] for x in merged
                 if (x.get('distance_m') or 0) <= MCD_MAX),
          sorted(x['name'] for x in D['mcd']
                 if (x.get('distance_m') or 0) <= MCD_MAX))
    check(u'★并集在展示半径内与文字链同集合',
          sorted(x['name'] for x in merged
                 if (x.get('distance_m') or 0) <= MCD_MAX),
          text_side(scene2))


# ── 麦当劳「实时找店」：通用接口用经纬度，翻译在包内做 ──────────────
#   ★ 铁律 4：这一组必须**两侧各一个用例** ——
#     · test_mcd_live_translates_lonlat_to_keyword / ..._falls_back_loudly  → 文字链侧
#     · test_mcd_live_scope_is_text_chain_only                             → 图片链侧
def _fake_prov(hint=None, name=u'假地图'):
    u"""造一个只实现 `locate_hint` 的地图 provider（不发任何请求）。"""
    from walkkcal import geo as _geo

    class _P(_geo.GeoProvider):
        pass

    _P.name = name
    if hint is not None:
        _P.locate_hint = lambda self, lon, lat: dict(hint)
    return _P()


def test_mcd_live_translates_lonlat_to_keyword():
    u"""★ 文字链侧：**通用接口收经纬度，翻译成 city/keyword 是麦当劳自己的事**。

    2026-10-16 修掉的 bug：以前直接传 `{searchType, longitude, latitude}` ——
    官方 schema 里**根本没有** longitude/latitude，而必填的 `city`/`keyword`/
    `beType` **全缺**；异常又被 `except Exception: pass` 吞掉 → 静默拿快照冒充实时。

    ★ 设计原则（用户 2026-10-16 拍板）：经纬度是普适的，**接口不为某一家降级**；
      麦当劳只吃文字是**它的缺陷**，所以翻译关在 `McdPack` 这个适配层里做。
    """
    from packs.pack_mcd import McdPack
    from walkkcal import geo as _geo

    class _Cli(object):
        def __init__(self):
            self.seen = None

        def call(self, tool, args):
            self.seen = (tool, args)
            return {'data': [{'storeCode': 'S1', 'storeName': u'麦当劳(富平路店)',
                              'distance': 420, 'address': u'上海市普陀区富平路1号'}]}

    real_get = _geo.get
    _geo.get = lambda name=None: _fake_prov(
        {'city': u'上海', 'keyword': u'富平路',
         'district': u'普陀区', 'township': u'甘泉路街道'})
    try:
        cli = _Cli()
        pack = McdPack(client=cli, offline=False)
        rows = pack.search_stores(121.40968, 31.26596, 1500)
    finally:
        _geo.get = real_get

    seen_tool, seen_args = (cli.seen if cli.seen else (None, None))
    check(u'★实时走的是 query-nearby-stores', seen_tool, 'query-nearby-stores')
    check(u'★参数 == 官方 schema（city/keyword/beType，且不带经纬度）',
          seen_args,
          {'searchType': 2, 'city': u'上海', 'keyword': u'富平路', 'beType': 1})
    check(u'通用接口签名仍是 (lon, lat, radius)',
          McdPack.search_stores.__code__.co_varnames[:3], ('self', 'lon', 'lat'))
    check(u'实时拿到的行标 src=mcp', [r['src'] for r in rows], ['mcp'])
    check(u'距离用 MCP 返的那个', rows[0]['distance'], 420)
    check(u'★不假装有坐标（MCP 不返回门店坐标）',
          (rows[0]['lon'], rows[0]['lat']), (None, None))
    check(u'★距离的参照点如实标注', rows[0].get('distance_note'), u'相对「富平路」')
    check(u'成功时 last_note 为空（没有回退）', pack.last_note, None)


def test_mcd_live_falls_back_loudly():
    u"""★ 文字链侧：想走实时却走不成时，**原因必须留下来**（不许再静默）。

    旧代码 `except Exception: pass` + 无条件回退快照 —— 调用方拿到的是快照，
    却以为在用官方实时。这就是「静默用快照冒充实时」。
    """
    from packs.pack_mcd import McdPack
    from packs import registry as _reg
    from walkkcal import geo as _geo

    class _Cli(object):
        def __init__(self, boom=None):
            self.boom = boom
            self.seen = None

        def call(self, tool, args):
            self.seen = (tool, args)
            if self.boom:
                raise RuntimeError(self.boom)
            return {'data': [{'storeCode': 'S1',
                              'storeName': u'麦当劳(富平路店)',
                              'distance': 420}]}

    real_get = _geo.get
    try:
        # ① 没有「坐标 → 文字」的能力（没 AMAP_KEY）→ 连 MCP 都不该发
        _geo.get = lambda name=None: _fake_prov(None, u'没有逆地理的假地图')
        p1 = McdPack(client=_Cli(), offline=False)
        r1 = p1.search_stores(121.4, 31.2, 1500)
        check(u'① 拿不到 city/keyword → 全部回退快照',
              sorted(set(r['src'] for r in r1)), ['snapshot'])
        check(u'① 连 MCP 调用都没发出（省一次注定失败的请求）',
              p1.client.seen, None)
        check(u'★① 原因写进 last_note（以前是静默）',
              u'AMAP_KEY' in (p1.last_note or u''), True)

        # ② MCP 抛错 → 同样回退，但异常文本留下来（不再被吞）
        _geo.get = lambda name=None: _fake_prov(
            {'city': u'上海', 'keyword': u'富平路'})
        p2 = McdPack(client=_Cli(boom='Missing required parameter: city'),
                     offline=False)
        r2 = p2.search_stores(121.4, 31.2, 1500)
        check(u'② MCP 失败 → 全部回退快照',
              sorted(set(r['src'] for r in r2)), ['snapshot'])
        check(u'★② 异常信息写进 last_note（以前被 except: pass 吞掉）',
              u'Missing required parameter: city' in (p2.last_note or u''), True)

        # ③ 回退原因能从 registry 读到 —— 「本该实时、实际快照」要看得见
        _reg.collect(packs=[p2], radius=1500)
        check(u'★③ registry.notes() 挂出了回退原因',
              u'麦当劳' in _reg.notes(), True)

        # ④ 铁律 3：报错文本里的 Key 必须被屏蔽
        old = os.environ.get('MCD_MCP_TOKEN')
        os.environ['MCD_MCP_TOKEN'] = 'TOKEN-SHOULD-NEVER-LEAK-1234'
        try:
            p4 = McdPack(client=_Cli(
                boom=u'401 for url https://mcp.mcd.cn?token=TOKEN-SHOULD-NEVER-LEAK-1234'),
                offline=False)
            p4.search_stores(121.4, 31.2, 1500)
            check(u'★④ 回退说明里的 Key 已被屏蔽',
                  'TOKEN-SHOULD-NEVER-LEAK-1234' in (p4.last_note or u''), False)
        finally:
            if old is None:
                os.environ.pop('MCD_MCP_TOKEN', None)
            else:
                os.environ['MCD_MCP_TOKEN'] = old
    finally:
        _geo.get = real_get

    # ⑤ 默认配置（offline=True）是**设计如此**，不算回退 → 不该一直报警
    p5 = McdPack(offline=True)
    r5 = p5.search_stores(None, None, 1500)
    check(u'⑤ 默认走快照', sorted(set(r['src'] for r in r5)), ['snapshot'])
    check(u'⑤ 默认配置不算「回退」，last_note 保持空', p5.last_note, None)


def test_mcd_live_scope_is_text_chain_only():
    u"""★ 图片链侧（铁律 4）：实时路径**只动文字链**，不许碰图片链读的文件。

    图片链（`walkkcal/render.py`）读的是 `data/<场景>/{stores,detail}.json` 快照，
    **不经过 `McdPack`**。所以「打开实时」天然会让两侧不一致 —— 这件事必须**显式**：
      · 默认装配两侧同源（`test_mcd_two_chains_same_source` 守着）；
      · 实时路径**绝不改写**那两个文件（本用例守）；
      · 打开实时后文字链每行带 `src='mcp'`，一眼看得出换了源。
    """
    import hashlib

    from packs import registry as _reg
    from packs.pack_mcd import McdPack
    from walkkcal import geo as _geo

    check(u'实时路径的作用域被显式标注', McdPack.LIVE_SCOPE, 'text-chain-only')

    d = _reg.CHANNELS['mcd']()
    check(u'registry 默认装配 offline=True', d.offline, True)
    check(u'registry 默认装配没有 MCP client', d.client, None)

    class _Cli(object):
        def call(self, tool, args):
            return {'data': [{'storeCode': 'S1',
                              'storeName': u'麦当劳(富平路店)',
                              'distance': 420}]}

    scene = os.path.join(ROOT, 'data', 'linghelu')
    files = [os.path.join(scene, 'stores.json'),
             os.path.join(scene, 'detail.json')]

    def _md5(p):
        with io.open(p, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()

    before = [_md5(p) for p in files]
    real_get = _geo.get
    _geo.get = lambda name=None: _fake_prov(
        {'city': u'上海', 'keyword': u'富平路'})
    old_data = os.environ.get('WALKKCAL_DATA')
    os.environ['WALKKCAL_DATA'] = scene
    try:
        pack = McdPack(client=_Cli(), offline=False)
        rows = pack.search_stores(121.40968, 31.26596, 1500)
    finally:
        _geo.get = real_get
        if old_data is None:
            os.environ.pop('WALKKCAL_DATA', None)
        else:
            os.environ['WALKKCAL_DATA'] = old_data
    after = [_md5(p) for p in files]

    check(u'★实时路径真的走了 MCP（不是空转）',
          sorted(set(r['src'] for r in rows)), ['mcp'])
    check(u'★实时路径没有改写图片链读的快照文件（两侧数据边界）',
          after, before)


# ═══════════════════════════════════════════════════════════════════
# ★ 2026-10-16 用户要求的两件事
#   ① `McdPack.last_note` 要接到**用户看得见**的回复里（不许只活在变量里）
#   ② 密钥不许写在 `.py` 里（进配置文件 / 环境变量）
# ═══════════════════════════════════════════════════════════════════

def test_reply_source_notes():
    u"""★ 回退原因必须走到**用户看得见**的文案里。

    背景：`McdPack.last_note` 修好了「回退不静默」，但原因只活在 Python 变量里
    —— `registry.notes()` 读得到，**用户读不到**。
    存在看不见的地方 = 还是静默的（Trae 实测里宿主为此道歉，还把理由猜错）。

    两个方向各一断言（铁律 4 的思路：两侧都钉住）：
      · 没回退 → 正文一个字都不许多（默认配置必须保持安静）
      · 有回退 → 文案里出现说明，且 `meta.source_notes` 同步可读
    """
    from walkkcal import reply
    from packs import registry as _reg

    batch = [{'name': u'某店', 'walk': 100, 'quota': 400, 'fit_n': 1},
             {'name': u'另一家', 'walk': 200, 'quota': 400, 'fit_n': 0}]
    meta = {'total': 2, 'shown': 2, 'keyword': u'全部'}

    # ① 没有回退 → 不出声
    quiet = reply.build(450, batch, meta, notes={})
    check(u'①没有回退时正文不出现「数据来源」',
          u'数据来源' in quiet['text'], False)
    check(u'①没有回退时 meta.source_notes 为空',
          quiet['meta']['source_notes'], {})

    # ② 有回退 → 文案 + meta 同时出现，且**插在「下一步」之前**（三段式不乱）
    notes = {u'麦当劳': u'没有可用的麦当劳 MCP 客户端'
                        u'（缺 MCD_MCP_TOKEN）→ 只能走本地快照'}
    nx = [{'label': u'只看快餐', 'kind': 'industry'}]
    loud = reply.build(450, batch, meta, nx, notes=notes)
    check(u'★②有回退时正文出现「数据来源」', u'数据来源' in loud['text'], True)
    check(u'★②原因原样进文案（不吞细节）',
          u'缺 MCD_MCP_TOKEN' in loud['text'], True)
    check(u'★②meta.source_notes 同步可读（给 Agent 转述）',
          loud['meta']['source_notes'], notes)
    check(u'★②说明插在「下一步问句」之前',
          loud['text'].index(u'数据来源') < loud['text'].index(u'只看快餐吗？'),
          True)

    # ③ 不传 notes → 自动回 registry.notes()（三入口的默认路径，谁都不用改）
    old = dict(_reg.LAST_NOTES)
    try:
        _reg.LAST_NOTES.clear()
        _reg.LAST_NOTES[u'麦当劳'] = u'测试回退原因'
        auto = reply.build(450, batch, meta)
        check(u'★③不传 notes 时自动回 registry.notes()',
              u'测试回退原因' in auto['text'], True)
    finally:
        _reg.LAST_NOTES.clear()
        _reg.LAST_NOTES.update(old)


def test_keys_not_in_source():
    u"""★ 密钥不许写在 `.py` 里（用户要求；只进配置文件 / 环境变量）。

    三件事：
      ① 配置文件读得到，且**环境变量优先**；
      ② `config.redact()` 能屏蔽配置里的密钥（铁律 3）；
      ③ 仓库里所有 `.py` 都没有明文密钥。
    """
    import re
    from walkkcal import config as C

    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False,
                                     encoding='utf-8') as f:
        f.write(u'{"AMAP_KEY": "TEST-CONFIG-VALUE-0123456789"}')
        cfg_path = f.name
    old_key = os.environ.get('AMAP_KEY')
    old_path = os.environ.get(C.ENV_PATH)
    try:
        os.environ.pop('AMAP_KEY', None)
        os.environ[C.ENV_PATH] = cfg_path
        C.load(force=True)
        check(u'①配置文件里的值能被读到',
              C.get('AMAP_KEY'), 'TEST-CONFIG-VALUE-0123456789')
        os.environ['AMAP_KEY'] = 'ENV-WINS-9876543210'
        check(u'★①环境变量优先于配置文件',
              C.get('AMAP_KEY'), 'ENV-WINS-9876543210')
        check(u'★②报错文本里的密钥被屏蔽',
              C.redact(u'https://x/y?key=TEST-CONFIG-VALUE-0123456789&a=1'),
              u'https://x/y?key=<已屏蔽 AMAP_KEY>&a=1')
    finally:
        if old_key is None:
            os.environ.pop('AMAP_KEY', None)
        else:
            os.environ['AMAP_KEY'] = old_key
        if old_path is None:
            os.environ.pop(C.ENV_PATH, None)
        else:
            os.environ[C.ENV_PATH] = old_path
        C.load(force=True)           # 恢复本机真实配置
        try:
            os.remove(cfg_path)
        except OSError:
            pass

    # ③ 仓库里没有明文密钥 —— 判据与 internal/_check_secrets.py 保持一致
    hex32 = re.compile(r'\b[0-9a-f]{32}\b')
    assign = re.compile(u'(?i)\\b[A-Z_]{0,20}(KEY|TOKEN|AK|SECRET)[A-Z_]{0,20}'
                        u'\\s*=\\s*[\\x22\\x27][A-Za-z0-9_\\-]{16,}[\\x22\\x27]')
    bad = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ('__pycache__', '.git')]
        for fn in filenames:
            if not fn.endswith('.py'):
                continue
            fp = os.path.join(dirpath, fn)
            if os.path.getsize(fp) > 1024 * 1024:
                continue
            txt = io.open(fp, encoding='utf-8', errors='ignore').read()
            for i, line in enumerate(txt.splitlines(), 1):
                low = line.lower()
                if 'md5' in low or 'traceid' in low or 'noqa: secret' in line:
                    continue
                if hex32.search(line) or assign.search(line):
                    bad.append(u'%s:%d' % (os.path.relpath(fp, ROOT), i))
    check(u'★③仓库里没有明文密钥的 .py', bad, [])


if __name__ == '__main__':

    test_allowance()
    test_verdict()
    test_shop_tier()
    test_mcd_snapshot()
    test_prior()
    test_select()
    test_filter()
    test_paging()
    test_esc()
    test_reply_hints()
    test_provider_boundary()
    test_snapshot_adapters()
    test_map_swap_end_to_end()
    test_provider_without_dishes()
    test_rate_limit()
    test_local_menu_hook()
    test_channels()
    test_drink_only()
    test_output_mode()
    test_business_status()
    test_menu_item()
    test_seasoning()
    test_provider_batch()
    test_drink_only_parity()
    test_excluded_drink_count()
    test_render_quota_env()
    test_mcd_two_chains_same_source()
    test_mcd_live_translates_lonlat_to_keyword()
    test_mcp_tool_forwards_coords()
    test_mcd_live_falls_back_loudly()
    test_mcd_live_scope_is_text_chain_only()
    test_reply_source_notes()
    test_keys_not_in_source()
    print()
    if _fail:
        print(u'❌ %d 项失败：%s' % (len(_fail), u'、'.join(_fail)))
        sys.exit(1)
    print(u'✅ 全部通过')
