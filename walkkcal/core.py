# -*- coding: utf-8 -*-
"""
walkkcal/core.py —— 核心算法层（品牌无关）

★ 本轮修复的 5 个 bug（都是「不报错、数字明显不合理」型）：
  ① 「一条桂鱼」被当成 100g→ 实际一条鱼约 500-600g，整条菜少了 5 倍鱼
  ② 「1500g鱼」食材名带数量前缀 → 查表失败，克数完全丢失
  ③ 解析失败的做法（配料全是散文）被当作 0 kcal 参与中位数 → 拉低到68
  ④「盐（少许）」括号内容未剥离→ 查表失败
  ⑤ 调味料区间把「油 10-30g」累加到每个做法 → 区间上限虚高到 4975
"""
from __future__ import annotations
import io, json, os, re, math, statistics
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════
# 一、步行消耗（Compendium of Physical Activities, 2011）
# ═══════════════════════════════════════════════════════════════════
MET_WALK = {
    'flat':   (3.0, u'平路 4.5km/h', 'Compendium 2011 Table 2: walking 4.5km/h = 3.0 MET'),
    'slope3': (4.3, u'3% 坡',      'Compendium 2011: 4.5km/h + 3% grade ≈ 4.3 MET'),
    'slope6': (8.0, u'6% 坡',      'Compendium 2011: 6% grade substantially increases cost'),
}
WALK_KMH = 4.5


def walk_burn(distance_m: float, weight_kg: float, grade: str = 'flat',
              round_trip: bool = True) -> dict:
    met, label, src = MET_WALK[grade]
    m = distance_m * (2 if round_trip else 1)
    hours = m / 1000.0 / WALK_KMH
    kcal = met * weight_kg * hours
    return {
        'kcal': round(kcal, 1),
        'minutes': round(hours * 60),
        'distance_m': distance_m,
        'round_trip_m': m,
        'met': met, 'grade': label,
        'formula': u'kcal = MET × 体重(kg) × 步行时长(h)',
        'sources': [u'MET 取值：%s' % src,
                    u'速度 4.5km/h（走快走慢总消耗仅差 26%）'],
        'caveat': u'若返程本就是上下班的路，应只计入去程。',
    }


# ═══════════════════════════════════════════════════════════════════
# 二、用户额度模型
# ═══════════════════════════════════════════════════════════════════
def bmr_mifflin(weight_kg: float, height_cm: float, age: int) -> dict:
    b = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    return {'kcal': round(b),
            'formula': u'BMR(Mifflin-St Jeor 1990, 男) = 10×kg + 6.25×cm − 5×age + 5',
            'accuracy': u'±10%，82% 人群在误差内'}


@dataclass
class UserProfile:
    weight_kg: float = 65.0
    height_cm: float = 170.0
    age: int = 30
    daily_deficit: float = 1200.0
    meals_per_day: int = 3
    meal_split: dict = field(default_factory=lambda: {
        u'早餐': 0.25, u'午餐': 0.35, u'晚餐': 0.30, u'下午茶': 0.10})
    neat_level: str = 'normal'
    grade: str = 'flat'

    NEAT = {
        'sedentary': (300, u'久坐（几乎不起身）', 'Levine 2005: 瘦者比肥胖者每天多站多走 152 分钟'),
        'normal':    (600, u'普通（通勤+日常走动）', '文献典型区间 300–800 kcal/天'),
        'active':    (2000, u'高活动（站立工作/频繁走动）', 'Levine: 个体间 NEAT 极差可达 2000 kcal/天'),
    }

    def neat(self) -> dict:
        v, label, src = self.NEAT[self.neat_level]
        return {'kcal': v, 'label': label, 'source': src,
                'note': u'NEAT 个体差异可达 2000 kcal/天但外部算不出来 —— 所以让你自己选。'}

    def base_allowance(self, meal: str = u'午餐') -> dict:
        ratio = self.meal_split.get(meal, 1.0 / self.meals_per_day)
        v = self.daily_deficit * ratio
        return {'kcal': round(v),
                'formula': u'本餐额度 = 每日缺口 %d × %s 占比 %.0f%%'
                            % (self.daily_deficit, meal, ratio * 100),
                'note': u'额度由你自己设定，不由算法推导。'}


def allowance(profile: UserProfile, walk: dict, meal: str = u'午餐') -> dict:
    base = profile.base_allowance(meal)
    total = base['kcal'] + walk['kcal']
    up = walk['kcal'] / base['kcal'] * 100 if base['kcal'] else 0
    return {'base': base['kcal'], 'walk_bonus': walk['kcal'], 'total': round(total),
            'uplift_pct': round(up, 1),
            'formula': u'可用额度 = 基准 %d + 步行 %.0f = %.0f'
                       % (base['kcal'], walk['kcal'], total)}


# ═══════════════════════════════════════════════════════════════════
# 三、食材热量表（每 100 克）
# ═══════════════════════════════════════════════════════════════════
KCAL_PER_100G = {
    # ★ bug⑬：补齐最基础、最常见的品种 —— 缺一个就整道菜算不出来。
    #   实测缺「鸡蛋」「鸭」时，「三黄鸡」会被误匹配到「鸡」(167)，
    #   而「鸡蛋」真正应该 144。这是主料热量算错而非仅仅缺失。
    '鸡蛋': 144, '蛋': 144, '鸭': 240, '鸡': 167, '鱼': 104,
    '大米': 346, '白米饭': 116, '米饭': 116, '糙米': 348, '小米': 361,
    '面粉': 366, '面条': 284, '挂面': 284, '方便面': 472, '馒头': 223,
    '年糕': 154, '米粉': 348, '粉丝': 335, '粉条': 337, '藕粉': 268,
    '燕麦': 367, '玉米': 112, '玉米粒': 106, '红薯': 90, '紫薯': 82,
    '土豆': 81, '山药': 57, '芋头': 79, '莲藕': 73,
    '猪肉': 395, '五花肉': 518, '里脊': 155, '猪瘦肉': 143, '猪肝': 129,
    '猪蹄': 260, '排骨': 278, '猪排骨': 278, '腊肉': 498, '火腿': 330,
    '培根': 181, '香肠': 508, '腊肠': 586,
    '牛肉': 125, '肥牛': 250, '牛腩': 332, '牛排': 125, '牛肉馅': 190,
    '羊肉': 203, '肥羊': 203, '鸭肉': 240, '烤鸭': 436, '鸭肝': 117,
    '鸡肉': 167, '鸡胸肉': 133, '鸡腿': 181, '鸡翅': 194, '鸡爪': 106,
    '鳜鱼': 113, '桂鱼': 113, '鲈鱼': 105, '带鱼': 127, '黄花鱼': 127,
    '鲳鱼': 137, '黄鱼': 127, '鳝鱼': 69, '三文鱼': 139, '金枪鱼': 132,
    '巴沙鱼': 90, '鲤鱼': 109, '鲫鱼': 108, '虾': 93, '基围虾': 101,
    '大虾': 93, '虾仁': 48, '龙虾': 83, '小龙虾': 93, '螃蟹': 103,
    '面包蟹': 250, '生蚝': 73, '牡蛎': 73, '扇贝': 60, '鱿鱼': 84,
    '墨鱼': 84, '鱼': 104,
    '青菜': 20, '小白菜': 15, '菠菜': 28, '生菜': 16, '芹菜': 22,
    '草头': 22, '韭菜': 26, '豆芽': 18, '黄豆芽': 18, '绿豆芽': 18,
    '西红柿': 20, '番茄': 20, '黄瓜': 16, '茄子': 23, '青椒': 22,
    '辣椒': 32, '洋葱': 40, '胡萝卜': 39, '白萝卜': 23, '萝卜': 23,
    '香菇': 26, '平菇': 20, '金针菇': 32, '木耳': 27, '笋': 23, '竹笋': 23,
    '莴笋': 15, '冬瓜': 12, '丝瓜': 20, '苦瓜': 22, '南瓜': 23, '冬笋': 40,
    '荠菜': 27, '空心菜': 20, '茼蒿': 21, '苋菜': 25, '豆角': 34,
    '四季豆': 31, '蒜苗': 37, '葱': 30, '青豆': 131, '豌豆': 105,
    '玉米笋': 26, '荷兰豆': 30,
    '豆腐': 82, '嫩豆腐': 57, '老豆腐': 98, '豆腐干': 140, '油豆腐': 244,
    '腐竹': 459, '豆皮': 409, '油面筋': 244, '水面筋': 146, '素鸡': 190,
    '牛奶': 54, '淡奶油': 345, '黄油': 888, '奶酪': 328, '酸奶': 72,
    '炼乳': 328, '奶粉': 460,
    '花生': 574, '花生米': 574, '芝麻': 631, '白芝麻': 631, '瓜子': 606,
    '核桃': 646, '杏仁': 578, '腰果': 559, '松子': 628, '瓜子仁': 606,
    '白糖': 400, '冰糖': 400, '红糖': 420, '糖': 400, '蜂蜜': 321,
    '食用油': 899, '植物油': 899, '色拉油': 899, '花生油': 899,
    '菜籽油': 899, '橄榄油': 899, '猪油': 897, '油': 899,
    '黄酒': 66, '料酒': 68, '米酒': 46, '葡萄酒': 682, '啤酒': 32,
    '盐': 0, '酱油': 63, '生抽': 63, '老抽': 70, '醋': 31, '白醋': 31,
    '香醋': 31, '蚝油': 114, '豆瓣酱': 181, '番茄酱': 81, '沙拉酱': 680,
    '芝麻酱': 618, '花生酱': 600, '胡椒': 361, '花椒': 258, '八角': 195,
    '桂皮': 295, '香叶': 195, '孜然': 375, '辣椒粉': 374, '五香粉': 375,
    '淀粉': 346, '生粉': 346, '玉米淀粉': 346, '红薯淀粉': 337,
    '酵母': 121, '泡打粉': 0, '豆豉': 244,
    # 追加：解析验证时发现的缺词
    '蒜': 128, '姜': 41, '香菜': 20, '芫荽': 20,
    '鸡毛菜': 20, '娃娃菜': 13, '圆白菜': 24, '包菜': 24, '花菜': 24,
    '西葫芦': 19, '佛手瓜': 16, '秋葵': 37,
    '童子鸡': 146, '鸡': 167, '鸡翅中': 194, '鸡腿肉': 181,
    '鲫鱼': 108, '草鱼': 113, '鲢鱼': 104, '花生仁': 574,
    '土豆淀粉': 337, '番茄沙司': 81, '千岛酱': 680, '蛋黄酱': 680,
    '鱼露': 60, '咖喱': 480, '咖喱粉': 480, '椰浆': 230, '椰奶': 230,
    '芥末': 490, '紫苏': 32, '罗勒': 23, '苦瓜': 22, '高筋面粉': 366, '低筋面粉': 366, '中筋面粉': 350,
    # ★ 饮品类（实测发现全部缺词，导致美式咖啡被算成 1048）
    # 一杯美式 ≈ 5 kcal；一份意式浓缩 ≈ 30ml ≈ 2 kcal
    '咖啡': 1, '美式咖啡': 2, '黑咖啡': 1, '浓缩咖啡': 2,
    '意式浓缩咖啡': 2, '咖啡液': 2, '速溶咖啡': 2, '挂耳': 1,
    '胶囊咖啡': 2, '热水': 0, '纯净水': 0, '矿泉水': 0, '温水': 0,
    '冰块': 0, '碎冰': 0, '巴黎水': 0, '苏打水': 0, '气泡水': 0,
    '牛奶': 54, '全脂牛奶': 64, '脱脂牛奶': 33,
    '冰淇淋': 127, '冰淇淋球': 127, '绿豆': 329, '绿豆汤': 60,
    '卡布奇诺': 55, '拿铁': 55, '摩卡': 80, '奶茶': 65,
    '茶': 1, '绿茶': 1, '红茶': 1, '乌龙茶': 1, '柠檬茶': 30,
    '可可': 220, '可可粉': 220, '糖浆': 300, '果糖': 400,
}

ALIAS = {
    '大蒜': '蒜', '蒜瓣': '蒜', '蒜头': '蒜',
    '生姜': '姜', '姜片': '姜', '姜末': '姜', '姜丝': '姜',
    '小葱': '葱', '大葱': '葱', '葱段': '葱', '香葱': '葱', '京葱': '葱',
    '圣女果': '西红柿', '圣女': '西红柿',
    '油麦菜': '生菜', '花菜': '花菜', '包菜': '圆白菜',
    '猪肋排': '排骨', '肋排': '排骨', '排骨肉': '排骨',
    '花鲢鱼': '鲢鱼', '鲢鱼': '草鱼', '草鱼': '草鱼',
    '干香菇': '香菇', '鲜香菇': '香菇', '香菇片': '香菇',
    '干木耳': '木耳', '黑木耳': '木耳',
    '龙利鱼': '巴沙鱼', '巴比鲨鱼': '巴沙鱼',
    '目鱼': '鳕鱼', '银鱼': '鳕鱼',
    '蒜（白皮）': '蒜', '蒜蓉': '蒜', '姜末': '姜', '姜丝': '姜',
    '芫荽': '香菜', '香葱末': '葱', '白葱': '葱', '胡萝卜丝': '胡萝卜',
    '豆腐干': '豆腐干', '豆干': '豆腐干',
}

# ★ 数量词 → 克数修正（修 bug①：一条鱼不是 50g）
COUNT_FIX = {
    u'条鱼': 500, u'条桂鱼': 500, u'条鲈鱼': 500, u'条鲤鱼': 600,
    u'条': 60,
}

# ★ 兜底：常见食材的典型重量区间（修 bug①：整条鱼/整只鸡）
ITEM_WEIGHT = {
    u'鱼': (300, 600), u'桂鱼': (400, 700), u'鲈鱼': (300, 600),
    u'鲤鱼': (500, 900), u'鲫鱼': (200, 400), u'带鱼': (200, 400),
    u'虾': (150, 400), u'螃蟹': (200, 500), u'生蚝': (30, 100),
    u'鸡': (800, 1200), u'整鸡': (900, 1400), u'童子鸡': (400, 700),
    u'鸭': (1000, 1500), u'猪蹄': (400, 800), u'排骨': (400, 800),
    u'豆腐': (200, 400), u'茄子': (200, 400), u'土豆': (150, 300),
    u'黄瓜': (150, 250), u'西红柿': (150, 250), u'白菜': (500, 1200),
    u'娃娃菜': (200, 400), u'包菜': (500, 1000), u'圆白菜': (500, 1000),
    u'花菜': (300, 500), u'豆干': (150, 300),
}


def kcal_of(ingredient: str):
    """查单个食材的每 100g 热量。查不到返回 None（不猜）。"""
    if not ingredient:
        return None
    name = ingredient.strip()
    name = re.sub(u'[（(].*?[)）]', '', name).strip()      # ★修 bug④：剥括号
    name = re.sub(u'[,，].*$', '', name).strip()           # 「白糖，白醋，」→ 白糖
    if not name:
        return None
    if name in KCAL_PER_100G:
        return KCAL_PER_100G[name]
    if name in ALIAS:
        return KCAL_PER_100G.get(ALIAS[name])
    for suf in [u'丝', u'片', u'末', u'丁', u'块', u'段', u'粒', u'蓉', u'泥',
                u'干', u'鲜', u'生', u'净']:
        if name.endswith(suf) and len(name) > 2:
            cand = name[:-1]
            if cand in KCAL_PER_100G:
                return KCAL_PER_100G[cand]
            if cand in ALIAS:
                return KCAL_PER_100G.get(ALIAS[cand])
    if name + '干' in KCAL_PER_100G:
        return KCAL_PER_100G[name + '干']
    # ★ bug⑫：品种名查不到时，退到「包含关系」。
    #   实测「三黄鸡」「文昌鸡」「鸭」「螺蛳」都查不到 → 主料热量整段算不出来
    #   （白斩鸡 5/6 条配方因此被主料闸拦掉，中位数从 346 掉到 224）。
    #   判据：去掉「性/肉/蛋」等后缀后，若能在表里找到更短的同名键就用它。
    for suf in (u'性', u'肉', u'蛋'):
        if name.endswith(suf) and len(name) > 2:
            cand = name[:-1]
            if cand in KCAL_PER_100G:
                return KCAL_PER_100G[cand]
    # ★ bug⑫：品种名查不到时，退到「前后缀包含关系」。
    #   实测「三黄鸡」「文昌鸡」「鸭」「鸡蛋」「螺蛳」都查不到 → 主料热量整段算不出来
    #   （白斩鸡 5/6 条配方因此被主料闸拦掉，中位数从 346 掉到 224）。
    #   注意方向：这些是**前缀**修饰（鸡→三黄鸡/鸡蛋），不是后缀（鸡→鸡腿）。
    #   后缀已在上面的 suf 列表处理，这里只补前缀方向，且要求候选名 ≥2 字。
    best = None
    for k, v in KCAL_PER_100G.items():
        # ★ 单字键（鸡/鸭/鱼/肉）也要参与 —— 「三黄鸡」正是靠「鸡」命中的。
        #   但单字键只允许做**前缀或整词**，不允许出现在名字中间。
        d = len(name) - len(k)
        if d < 0 or d > 3:
            continue
        if d == 0:
            continue                     # 上面已 return，走不到这
        if not (name.startswith(k) or name.endswith(k)):
            continue
        if best is None or d < best[0]:
            best = (d, v)
    return best[1] if best else None


# ═══════════════════════════════════════════════════════════════════
# 四、配料解析
# ═══════════════════════════════════════════════════════════════════
UNIT_MAP = {
    u'克': 1, u'g': 1, u'G': 1, u'千克': 1000, u'kg': 1000, u'公斤': 1000,
    u'斤': 500, u'两': 50, u'毫升': 1, u'ml': 1, u'升': 1000,
    u'勺': 10, u'汤匙': 15, u'汤勺': 15, u'茶匙': 5, u'小勺': 5, u'大勺': 15,
    u'个': 50, u'只': 150, u'片': 20, u'块': 100, u'根': 15, u'瓣': 5,
    u'粒': 2, u'颗': 5, u'段': 30, u'棵': 20, u'朵': 15, u'包': 100,
    u'袋': 100, u'扎': 50, u'把': 30, u'条': 100, u'张': 50, u'碗': 200,
    u'盘': 150, u'杯': 240, u'听': 330, u'罐': 330, u'瓶': 500,
    u'撮': 2, u'滴': 1, u'角': 5, u'头': 100, u'尾': 200,
}

# ★ bug⑨：量词的克数必须按「食材」区分，不能用统一默认值。
#   实测「1只鸡」按默认只=150g 折算 → 一只整鸡（约 1500g）少算 10 倍，
#   直接导致肉类菜全线偏低：白斩鸡 -54%、水煮肉片 -52%、烤鸭 -52%（7 道平均 -43%）。
#   蔬菜类用默认值反而是对的（1 根葱 ≈15g），所以必须分食材覆盖。
UNIT_BY_ITEM = {
    # 整只/整条 = 整只动物（大件）
    u'只': {u'鸡': 1500, u'三黄鸡': 1500, u'文昌鸡': 1500, u'童子鸡': 500,
            u'鸡腿': 200, u'鸡翅': 150, u'鸭': 2000, u'鸭胸': 200, u'鸭腿': 250,
            u'鹅': 3000, u'乳鸽': 500, u'兔': 1500, u'牛': 500, u'猪': 100},
    # 条 = 一条鱼/一根油条
    u'条': {u'鱼': 500, u'鲈鱼': 500, u'鲫鱼': 400, u'桂鱼': 500, u'带鱼': 400,
            u'黄花鱼': 400, u'油条': 60, u'培根': 100, u'火腿': 100, u'腊肠': 150},
    # 个 → 分大小件
    u'个': {u'鸡蛋': 55, u'蛋': 55, u'鸭蛋': 65, u'鹌鹑蛋': 12, u'番茄': 150,
            u'西红柿': 150, u'土豆': 150, u'馒头': 100, u'包子': 100, u'饺子': 20,
            u'苹果': 200, u'橙子': 180, u'柠檬': 100, u'豆腐': 300, u'洋葱': 180,
            u'青椒': 80, u'辣椒': 80, u'茄子': 250, u'南瓜': 400, u'冬瓜': 500},
    # 根/棵 = 蔬菜（默认值 15/20 基本正确，此处仅补漏）
    u'根': {u'葱': 15, u'香葱': 10, u'大葱': 100, u'姜': 20, u'蒜': 5, u'芹菜': 50},
    u'块': {u'豆腐': 300, u'排骨': 400, u'肉': 300, u'五花肉': 400, u'姜': 20,
            u'土豆': 150, u'南瓜': 400, u'巧克力': 20, u'奶酪': 25},
    u'片': {u'姜': 15, u'蒜': 5, u'培根': 25, u'火腿': 25, u'牛肉': 60},
    u'把': {u'葱': 20, u'香菜': 30, u'青菜': 250, u'菠菜': 250, u'花椒': 5,
            u'豆角': 200, u'蒜苗': 200},
    u'碗': {u'米': 150, u'面': 200, u'饭': 250, u'汤': 300, u'粥': 300},
    u'份': {}, u'尾': {u'鱼': 250, u'虾': 15}, u'头': {u'虾': 20},
    u'个蛋': {u'': 55},
    # ★ 小件副产物：优先于「只/个」等大件规则生效
    u'__SMALL__': {
        u'鸡爪': 40, u'凤爪': 35, u'鸡翅': 80, u'鸡翅中': 60, u'鸡翅根': 55,
        u'鸡腿': 150, u'鸡腿肉': 120, u'鸡头': 60, u'鸡架': 200, u'鸡骨': 50,
        u'鸡肝': 20, u'鸡心': 25, u'鸡胗': 30, u'鸡皮': 25,
        u'鸭爪': 45, u'鸭掌': 40, u'鸭翅': 90, u'鸭头': 70, u'鸭架': 250,
        u'鸭肝': 25, u'鸭肠': 40, u'鸭血': 100,
        u'鱼头': 250, u'鱼骨': 150, u'鱼尾': 120, u'鱼籽': 30,
        u'猪蹄': 350, u'猪头': 400, u'猪尾': 300, u'猪骨': 250, u'猪肝': 40,
        u'猪心': 60, u'猪肠': 150, u'猪肚': 250, u'猪耳': 100,
        u'排骨': 350, u'牛尾': 400, u'牛骨': 350, u'牛筋': 200,
        # ★ bug⑯（2026-10-12 修）：香料按「个/片/瓣」计量时，
        #   一个八角约 3 克，但通用规则按「个蛋 55 克」折算成 50 克，
        #   导致「2个八角」= 100 克 × 195 卡 = 195 卡（实际约 12 克 = 23 卡），
        #   单这一项就把一锅菜的热量抬高 5 倍。
        u'八角': 3, u'大料': 3, u'香叶': 0.3, u'桂皮': 5, u'花椒': 2,
        u'小茴香': 2, u'草果': 3, u'丁香': 1, u'白胡椒': 2, u'干辣椒': 2,
        u'姜片': 8, u'蒜瓣': 5, u'葱段': 15, u'洋葱': 100,
    },
}


# ★ 动物部位词表：这些词修饰的是「部件」而非「整只」，
#   用于模糊匹配时拒绝（一只鸡 1500g ≠ 一只鸡爪 40g）。
CUT_PARTS = (u'爪', u'翅', u'腿', u'头', u'骨', u'肝', u'心', u'胗', u'皮',
             u'掌', u'架', u'尾', u'肠', u'肚', u'耳', u'血', u'籽', u'肉',
             u'胸', u'腩', u'排', u'舌', u'腰', u'肾', u'胚')


def _is_cut_part(name):
    """食材名是否是「部位」而非「整只/品种」"""
    return any(p in name for p in CUT_PARTS)


def unit_grams(unit, name):
    """按食材查量词克数；查不到返回 None 表示用 UNIT_MAP 默认值"""
    if not unit:
        return None
    # ★ 小件副产物优先级最高（鸡爪永远不会是 1500 克）
    if unit == 'SMALL':
        tbl = UNIT_BY_ITEM.get(u'__SMALL__')
    else:
        tbl = UNIT_BY_ITEM.get(unit)
    if not tbl or not name:
        return None
    name = str(name).strip()
    if name in tbl:
        return float(tbl[name])
    # ★ 模糊匹配只允许「查询名 ⊂ 候选键」，且候选键必须是**品种名**而非部位名。
    #   正例：「三黄鸡」→命中「鸡」1500g；「文昌鸡」→命中「鸡」
    #   反例（必须拒绝）：「鸡」→命中「鸡爪」40g、「鸡翅」80g —— 整只 vs 部件差 20-40 倍
    #   做法：用 PARTS 标出所有部位词，候选键若是部位词则拒绝。
    for k, v in tbl.items():
        if not k or len(k) <= len(name) or len(k) - len(name) > 2:
            continue
        if name not in k:
            continue
        if _is_cut_part(k):          # 候选是部位 → 拒绝
            continue
        return float(v)              # 候选是品种（如「鸡」匹配「三黄鸡」）→ 采纳
    return None
CN_NUM = {u'一': 1, u'两': 2, u'二': 2, u'三': 3, u'四': 4, u'五': 5,
          u'六': 6, u'七': 7, u'八': 8, u'九': 9, u'十': 10, u'半': 0.5}
UNIT_RE = (u'(千克|公斤|毫升|汤匙|茶匙|汤勺|小勺|大勺|小碗|大碗|kg|ml|ML|'
           u'克|g|G|斤|两|升|勺|个|只|片|块|根|瓣|粒|颗|段|棵|朵|包|袋|扎|'
           u'把|条|张|碗|盘|杯|听|罐|瓶|撮|滴|角|头|尾)')
PAT_HEAD = re.compile(r'^\s*(\d+(?:\.\d+)?|[一两二三四五六七八九十半])\s*' + UNIT_RE)
PAT_MEASURE = re.compile(r'^\s*([大中小半整])\s*(' + UNIT_RE + r')')
AMOUNT_ONLY = re.compile(r'^\s*(适量|少许|若干|一点|随意|按口味|数滴|几滴)\s*$')
# ★ bug④补充：程度词在括号/后缀里 ——「盐（少许）」「葱姜末」「白糖，白醋，」
AMOUNT_INNER = re.compile(r'[（(]?(适量|少许|若干|一点|随意|按口味)[）)]?\s*$')
# 顿号/逗号并列的多个食材 ——「白糖，白醋，番茄酱，」
MULTI_SEP = re.compile(r'[,，、;；].*$')
# ★ 「葱姜末」「蒜苗」这类双食材连写 —— 两字都常见，需拆分尝试
COMPOUND2 = re.compile(r'^(.)(.)(末|丝|丁|片|段|粒|块|蓉|泥)$')
# ★ bug②：「1500g鱼」—— 数字单位与食材连写
PAT_FUSED = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(kg|g|克|斤|两|毫升|ml)\s*(\D.*)$', re.I)
PAT_BRACKET = re.compile(r'^\s*(.+?)[（(][^)）]*[)）]\s*(\d+(?:\.\d+)?)\s*(kg|g|克|斤|两|毫升|ml|勺|个|只)\s*$', re.I)
PAT_SUFFIX_UNIT = re.compile(r'^\s*(.+?)\s*(\d+(?:\.\d+)?)\s*(kg|g|克|斤|两|毫升|ml|勺|个|只)\s*$', re.I)


def parse_ingredient(s: str):
    """
    解析一条配料 → (克数|None, 食材名, 状态)
    状态: 'exact' 有明确克数 / 'range' 用兜底区间 / 'seasoning' 调味料 / 'fail' 解析不出
    """
    t = str(s).strip()
    if not t:
        return None, None, 'fail'

    # ★ bug⑪：括号内若带明确克数/斤两，且括号前有食材名，取括号内的值。
    #   「1只三黄鸡（2斤左右）」= 1000g；剥掉括号只按「1只」=150g 会少算 6 倍。
    mbi = re.match(
        r'^\s*(.{1,20}?)[（(]\s*(\d+(?:\.\d+)?)\s*(kg|千克|公斤|克|g|斤|两)\s*[^)）]*[)）]\s*$',
        t, re.I)
    if mbi:
        _nm = re.sub(u'[（(].*?[)）]', '', mbi.group(1)).strip()
        _nm = re.sub(r'^\s*\d+\s*(?:只|个|条|根|块|片|棵|朵|把)\s*', '', _nm).strip()
        _u = mbi.group(3).lower()
        _f = {u'kg': 1000, u'千克': 1000, u'公斤': 1000, u'克': 1, u'g': 1,
               u'斤': 500, u'两': 50}.get(_u)
        if _f and _nm:
            return float(mbi.group(2)) * _f, _nm, 'exact'

    # ★ 「蒜（白皮）50克」—— 括号在前、单位数字在后
    mb = PAT_BRACKET.match(t)
    if mb:
        f = {u'kg': 1000, u'克': 1, u'g': 1, u'斤': 500, u'两': 50,
             u'毫升': 1, u'ml': 1, u'勺': 10, u'个': 50, u'只': 150}[mb.group(3).lower()]
        return float(mb.group(2)) * f, mb.group(1).strip(), 'exact'

    # ★ 「1只600g童子鸡」—— 量词 + 克数 + 食材，需按序剥离
    mm = re.match(r'^\s*\d+\s*(?:只|个|条|棵)\s*\d+\s*(?:g|克|kg)\s*(\D.*)$', t)
    if mm:
        nm2 = mm.group(1).strip()
        if kcal_of(nm2) is not None:
            g2 = {'鸡': 1000, '童子鸡': 500, '鸡腿': 350, '鸡翅': 250}.get(nm2, 800)
            return g2, nm2, 'exact'

    # ★ 「葱姜末」这类双食材连写 —— 拆开试
    mc = COMPOUND2.match(t)
    if mc:
        for cand in (mc.group(1) + mc.group(3), mc.group(2) + mc.group(3),
                     mc.group(1) + mc.group(2)):
            if kcal_of(cand) is not None:
                return None, cand, 'seasoning'

    # bug②：先处理「1500g鱼」这种连写
    mf = PAT_FUSED.match(t)
    if mf:
        g = float(mf.group(1))
        unit = mf.group(2).lower()
        name = mf.group(3).strip()
        f = {u'kg': 1000, u'克': 1, u'g': 1, u'斤': 500, u'两': 50,
             u'毫升': 1, u'ml': 1}[unit]
        return g * f, name, 'exact'

    if AMOUNT_ONLY.match(t):
        return None, t, 'seasoning'

    m = PAT_HEAD.match(t)
    if m:
        q, unit = m.group(1), m.group(2)
        num = float(q) if re.match(r'^\d+(\.\d+)?$', q) else CN_NUM.get(q, 1)
        name = t[m.end():].strip()
        g = num * UNIT_MAP.get(unit, 1)
        # ★ bug⑨：量词克数必须按食材查（一只鸡 ≈1500g，不是默认的 150g）
        _by_item = unit_grams(unit, name)
        if _by_item:
            g = num * _by_item
        # ★ bug⑩：小件副产物不能按「整只」算。
        #   实测「10只鸡爪」按一只 1500g 折算 = 15000 克，多算 100 倍。
        _by_item = unit_grams(u'SMALL', name)
        if _by_item:
            g = num * _by_item
        # ★ bug⑪：括号里若带明确克数/斤两，取它更准。
        #   「1只三黄鸡（2斤左右）」括号里 2斤=1000g，剥括号后只剩量词默认 150g。
        _mb = re.search(u'[（(]\\s*(\\d+(?:\\.\\d+)?)\\s*(kg|千克|公斤|克|g|斤|两)\\b', name)
        if _mb:
            _u = _mb.group(2)
            _f = {u'kg': 1000, u'千克': 1000, u'公斤': 1000, u'克': 1, u'g': 1,
                   u'斤': 500, u'两': 50}[_u]
            g = float(_mb.group(1)) * _f
            name = re.sub(u'[（(].*?[)）]', '', name).strip()
        # ★ bug①：数量词修正
        if unit in (u'条',) and name in (u'鱼', u'桂鱼', u'鲈鱼', u'鲤鱼', u'鲫鱼'):
            g = {'鱼': 500, '桂鱼': 550, '鲈鱼': 500,
                 '鲤鱼': 700, '鲫鱼': 300}.get(name, 500)
        return g, name, 'exact'

    m3 = PAT_MEASURE.match(t)
    if m3:
        mult = {u'大': 1.5, u'小': 0.7, u'半': 0.5, u'整': 1.0, u'中': 1.0}[m3.group(1)]
        name = t[m3.end():].strip()
        return 50 * mult, name, 'exact'

    m2 = re.match(r'^([一两二三四五六七八九十半])\s*个?\s*(.+)$', t)
    if m2:
        return CN_NUM.get(m2.group(1), 1) * UNIT_MAP[u'个'], m2.group(2), 'exact'

    # ★ bug④：程度词在括号里 → 剥掉括号与程度词，剩下才是食材名
    if AMOUNT_INNER.search(t):
        cleaned = re.sub(u'[（(].*?[)）]', '', t).strip()
        cleaned = AMOUNT_INNER.sub('', cleaned).strip()
        if cleaned:
            return None, cleaned, 'seasoning'

    # ★ 并列多食材「白糖，白醋，番茄酱，」→ 只取第一个能查到的
    if MULTI_SEP.search(t):
        head = MULTI_SEP.split(t)[0].strip()
        if head and kcal_of(head) is not None:
            return None, head, 'seasoning'
        return None, t, 'fail'

    # 无克数但可识别食材
    return None, t, 'seasoning'


# ★ bug⑤：调味料区间 —— 只在「该做法确实用了这个调料」时计入，且给窄区间
SEASONING_RANGE = {
    u'油': (8, 25), u'食用油': (8, 25), u'植物油': (8, 25), u'色拉油': (8, 25),
    u'花生油': (8, 25), u'橄榄油': (5, 20), u'猪油': (8, 20), u'黄油': (10, 25),
    u'盐': (2, 4), u'糖': (8, 20), u'白糖': (8, 20), u'冰糖': (8, 20),
    u'生抽': (8, 12), u'老抽': (5, 10), u'酱油': (8, 12), u'醋': (5, 12),
    u'料酒': (10, 15), u'黄酒': (10, 15), u'淀粉': (5, 12), u'生粉': (5, 12),
    u'面粉': (50, 100), u'蒜': (5, 12), u'姜': (5, 12), u'葱': (5, 15),
    u'香葱': (5, 15), u'青蒜': (5, 15), u'胡椒': (1, 3), u'花椒': (1, 3),
    u'八角': (1, 3), u'桂皮': (1, 3), u'香叶': (1, 2), u'孜然': (2, 5),
    u'番茄酱': (15, 30), u'蚝油': (8, 15), u'豆瓣酱': (10, 20),
    u'芝麻酱': (10, 20), u'花生酱': (10, 20), u'沙拉酱': (10, 20),
    u'蜂蜜': (5, 15), u'酵母': (2, 5), u'泡打粉': (1, 3),
}

# ★ 这些是「主食类」—— 无克数时用整道菜的大致配比，不能算调料
STAPLE_NEED_AMOUNT = {
    u'面粉', u'淀粉', u'生粉', u'玉米淀粉', u'红薯淀粉', u'米', u'大米',
    u'面条', u'米粉', u'粉丝', u'藕粉',
}


def _calc_one_recipe(ings: list, cook: dict = None):
    """
    算单个做法。
    ★ bug③：返回 None 表示「这个做法不可用」，不是 0 kcal。
    ★ 新增：cook 参数携带「烹饪方式判定的用油量区间」，覆盖掉固定的油猜测值。
    """
    fixed, ranged, need_amount, unknown = 0.0, 0.0, [], []
    n_ok = 0
    oil_added = False

    # ★ 烹饪方式给出的用油量（优先级高于调味料表里的固定猜测）
    if cook and cook.get('oil_lo') is not None:
        k_oil = KCAL_PER_100G.get(u'油')      # 899 kcal/100g
        fixed += k_oil * cook['oil_lo'] / 100.0
        ranged += k_oil * (cook['oil_hi'] - cook['oil_lo']) / 100.0
        oil_added = True

    for s in ings:
        g, name, st = parse_ingredient(s)
        k = kcal_of(name) if name else None

        if st == 'exact' and g and k is not None:
            fixed += k * g / 100.0
            n_ok += 1
            continue
        if st == 'exact' and g and k is None:
            if name in STAPLE_NEED_AMOUNT:
                need_amount.append(name)
            else:
                unknown.append(name)
            continue
        # seasoning：查得到热量且在调味料表里 → 给区间
        if k is not None:
            key = None
            for kk in SEASONING_RANGE:
                if name and (name == kk or kk in name):
                    key = kk
                    break
            if key:
                # ★ 用油已由烹饪方式决定，跳过，避免重复计入
                if key in (u'油', u'食用油', u'植物油', u'色拉油',
                           u'花生油', u'橄榄油', u'猪油', u'黄油') and oil_added:
                    n_ok += 1
                    continue
                lo_g, hi_g = SEASONING_RANGE[key]
                ranged += k * hi_g / 100.0
                fixed += k * lo_g / 100.0
                n_ok += 1
                continue
        if name in STAPLE_NEED_AMOUNT:
            need_amount.append(name)
        elif k is not None:
            if name in ITEM_WEIGHT:
                lo_g, hi_g = ITEM_WEIGHT[name]
                ranged += k * hi_g / 100.0
                fixed += k * lo_g / 100.0
                n_ok += 1
            else:
                unknown.append(name)
        else:
            unknown.append(name)

    if n_ok == 0:
        return None      # ★ bug③：这个做法完全没法算，剔除

    # 主食无克数 → 按常见配比补（每道菜主食 50-150g）
    for st_name in need_amount:
        k = kcal_of(st_name)
        if k:
            fixed += k * 50 / 100.0
            ranged += k * 150 / 100.0

    return {
        'low': fixed,
        'high': fixed + ranged,
        'ok': n_ok,
        'unknown': unknown,
        'cook': cook.get('cook') if cook else None,
    }


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
# 四之二、烹饪方式识别（★ 用户的观察：菜名与步骤本身就含烹饪方式）
#   「荔浦芋头」  = 芋片与肉片蒸后淋酱   → 蒸，几乎不用油
#   「蒜蓉开背虾」= 虾开片蒸后浇酱       → 蒸，油极少
#   「松鼠桂鱼」  = 鱼炸后浇糖醋汁       → 油炸 + 糖醋，重油
#   实测：155 万条中 84.2% 能从「菜名 + 步骤文本」判定烹饪方式
#   ★ 这是薄荷健康做不到的：它是「预置热量值」，我们是「按做法算」
# ═══════════════════════════════════════════════════════════════════

# key 的顺序即优先级（从重油到轻油），匹配时取第一个命中的
COOK_METHODS = [
    (u'酥炸', 10, 40, u'酥炸', u'极高油（裹粉复炸）'),
    (u'油爆', 25, 60, u'爆炒/油爆', u'重油（旺火短炒）'),
    (u'干煸', 30, 70, u'干煸/煸炒', u'重油（煸干水分）'),
    (u'糖醋', 25, 60, u'糖醋/糖醋汁', u'炸 + 裹糖醋汁'),
    (u'油炸', 40, 100, u'油炸/炸', u'最多油'),
    (u'爆炒', 25, 60, u'爆炒', u'重油'),
    (u'红烧', 15, 35, u'红烧', u'中等油 + 糖'),
    (u'清炒', 10, 25, u'清炒', u'中等油'),
    (u'煎', 10, 25, u'煎', u'中等油（需防粘）'),
    (u'蒸', 0, 5, u'蒸', u'几乎不用油'),
    (u'白灼', 0, 5, u'白灼', u'不用油'),
    (u'汆', 0, 5, u'汆', u'不用油'),
    (u'煮', 0, 5, u'煮', u'不用油'),
    (u'焯', 0, 3, u'焯/汆', u'不用油'),
    (u'炖', 5, 15, u'炖/焖', u'少量油'),
    (u'烤', 5, 15, u'烤', u'少量刷油'),
    (u'凉拌', 5, 15, u'凉拌', u'酱汁带油'),
    (u'炒', 12, 30, u'炒（未细分）', u'中等油'),
]
_NO_OIL = re.compile(u'蒸|煮|白灼|汆|焯|凉拌')
# ★ bug⑥：标题里含「餐包/蛋糕/三明治/饭/面…」说明这是组合菜，不是该 dish 本身
_COMPOSITE_TITLE = re.compile(u'餐包|蛋糕|三明治|便当|套餐|拼盘|饭团|卷饼|汉堡|披萨|炒饭|盖饭|拌面|汤面')
# ★ bug⑦：饮品/简单菜不该有 6 项以上含面粉/酵母等主料
_MEAL_MARKERS = (u'高筋面粉', u'低筋面粉', u'酵母', u'牛奶', u'黄油', u'糯米粉',
                 u'澄粉', u'泡打粉', u'三花淡奶', u'淡奶', u'杏仁片', u'糖粉',
                 u'细砂糖', u'淡奶油', u'奶油')

def _looks_like_meal(ings):
    """≥2 个「烘焙/主料类」标记即判为组合菜或烘焙配方。"""
    hits = 0
    for s in ings:
        g, name, st = parse_ingredient(s)
        if name and any(m in name for m in _MEAL_MARKERS):
            hits += 1
    return hits >= 2   # 这类即便提到油也很少


def classify_cook(dish_name, steps_text):
    """
    从菜名 + 步骤文本判定烹饪方式与用油量区间。

    ★ 关键修正（bug：判定被步骤里的无关词干扰）：
      「松鼠桂鱼」每条做法都是油炸，但步骤里含「焯水」「炒糖」等词，
      早期版本按「文本中任意位置出现关键词」判定，结果被判成「煮/白灼」或「炒」，
      油量估成 0–5 克（严重低估）。
      修正为**两级**：
        ① 菜名里的烹饪词可信度最高（菜名就是做法）
        ② 步骤里的词只在「有明确动词」时才生效（如「油炸」「大火蒸」「锅中放油」）
        ③ 步骤里的「焯/汆/煮」若与主方式矛盾，视为配套工序（如油炸前焯水定味）
    """
    name = dish_name or ''
    steps = steps_text or ''
    all_text = name + ' ' + steps

    # ① 菜名优先
    for key, lo, hi, label, note in COOK_METHODS:
        if key in name:
            if _NO_OIL.search(name) and lo > 10:
                lo, hi = 3, 8
            return {'cook': label, 'oil_lo': lo, 'oil_hi': hi,
                    'source': u'由菜名中的「%s」判定' % key, 'note': note}

    # ② 步骤里有明确动词
    PRIORITY = [u'油炸', u'酥炸', u'炸', u'爆炒', u'油爆', u'干煸', u'糖醋',
                u'红烧', u'清炒', u'煎', u'蒸', u'炖', u'焖', u'烤', u'凉拌']
    VERB = {
        u'油炸': (40, 100, u'油炸'), u'酥炸': (10, 40, u'酥炸'),
        u'炸': (40, 100, u'炸'), u'爆炒': (25, 60, u'爆炒'),
        u'油爆': (25, 60, u'油爆'), u'干煸': (30, 70, u'干煸'),
        u'糖醋': (25, 60, u'糖醋'), u'红烧': (15, 35, u'红烧'),
        u'清炒': (10, 25, u'清炒'), u'煎': (10, 25, u'煎'),
        u'蒸': (0, 5, u'蒸'), u'炖': (5, 15, u'炖'), u'焖': (5, 15, u'焖'),
        u'烤': (5, 15, u'烤'), u'凉拌': (5, 15, u'凉拌'),
    }
    for key in PRIORITY:
        # 必须是有上下文的主谓短语，避免「焯水」「炒糖」这类配套工序误判
        pat = VERB[key][2]
        if re.search(pat + u'(?![水料姜葱])', steps) or re.search(
                u'(?:大火|油锅|热油|锅中|下油|入油)[^。]{0,6}' + pat, steps):
            lo, hi, label, note = VERB[key][0], VERB[key][1], VERB[key][2], u'由步骤判定'
            for _k, _l, _h, _lab, _nt in COOK_METHODS:
                if _k == key:
                    label, note = _lab, _nt
                    break
            return {'cook': label, 'oil_lo': lo, 'oil_hi': hi,
                    'source': u'由步骤中的「%s」判定' % pat, 'note': note}

    # ③ 回退：明确的放油动词
    for pat, lo, hi, lab in [(u'炸|过油', 40, 100, u'过油'),
                             (u'热油|油锅|倒油|放油|加油|锅中放油|锅里放油',
                              10, 25, u'放油'),
                             (u'油稍多|油多一点|多放油', 20, 40, u'油稍多')]:
        if re.search(pat, steps):
            lo2, hi2 = (3, 8) if (_NO_OIL.search(name) and lo >= 10) else (lo, hi)
            return {'cook': u'未识别方式', 'oil_lo': lo2, 'oil_hi': hi2,
                    'source': u'由步骤中的「%s」推断' % lab,
                    'note': u'未能识别烹饪方式，仅从步骤提取用油量'}
    return {'cook': None, 'oil_lo': None, 'oil_hi': None,
            'source': u'', 'note': u'菜名与步骤均无烹饪线索'}


# 五、菜名 → 热量（多做法取中位数）
# ═══════════════════════════════════════════════════════════════════
@dataclass
class DishHeat:
    name: str
    median: Optional[float]
    low: Optional[float]
    high: Optional[float]
    method_count: int
    confidence: str
    basis: str
    top_unknown: list = field(default_factory=list)

    def text(self) -> str:
        if self.median is None:
            return u'—'
        if self.low is not None and self.high is not None and self.high > self.low * 1.25:
            return u'%d（%d–%d）' % (round(self.median), round(self.low), round(self.high))
        return u'%d' % round(self.median)


# ─────────────────────────────────────────────────────────────────────
# ★ 2026-10-13：原 `dish_heat()`（扫原始语料 JSONL 算热量）已**移出本文件**。
#   原因：它读的是**下厨房的 JSONL schema**（`recipeIngredient` /
#   `recipeInstructions`）—— 那是一家语料源的私有格式，不该待在
#   品牌与数据源都无关的内核里。现在它在
#   `walkkcal/providers/corpus_xiachufang.py`。
#   本文件只保留**纯数学**：食材表、量词折算、份量归一、做法用油模型。
#   要算某道菜的热量，请走 `walkkcal/corpus.py::lookup()`。
# ─────────────────────────────────────────────────────────────────────


GRADES = {
    'A': (u'A 档·官方精确', u'品牌方官方营养表'),
    'B': (u'B 档·官方参考', u'连锁公开参考值'),
    'C': (u'C 档·语料推算', u'多做法中位数，误差 ±40%'),
    'D': (u'D 档·仅店名距离', u'数据源无菜单数据'),
}


if __name__ == '__main__':
    BASE = os.path.dirname(os.path.abspath(__file__))
    print(u'核心模块自检')
    print('=' * 62)
    p = UserProfile()
    w = walk_burn(500, p.weight_kg, 'flat')
    print(u'走 500m 往返： %.0f kcal / %d 分钟' % (w['kcal'], w['minutes']))
    a = allowance(p, w, u'午餐')
    print(u'午餐额度：基准 %d + 步行 %.0f = %.0f（+%.1f%%）'
          % (a['base'], a['walk_bonus'], a['total'], a['uplift_pct']))
    print(u'BMR： %d kcal' % bmr_mifflin(65, 170, 30)['kcal'])
    print(u'食材表 %d 项' % len(KCAL_PER_100G))

    print(u'\n解析器验证（针对刚修的 bug）：')
    for t in [u'1500g鱼', u'一条桂鱼', u'盐（少许）', u'白糖，白醋，番茄酱，',
              u'20克番茄酱', u'一把香菜']:
        g, n, st = parse_ingredient(t)
        print(u'  %-22s -> 克数 %-8s 食材 %-10s %s'
              % (t, ('%.0f' % g) if g else '无', n, st))