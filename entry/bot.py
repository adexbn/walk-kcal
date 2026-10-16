# -*- coding: utf-8 -*-
u"""Telegram Bot 入口 —— walk-kcal

设计（见 docs/交互模式.md）
──────────────────────────
Bot **没有 Agent**，所以「下一步能问什么」不能指望模型去猜 ——
必须把选项**做成按钮**摆出来（零打字成本，转化率最高）。

    /demo  →  [长图] + 一句话 + [只看快餐] [别走太远] [换一批]
                        ↓ 用户点按钮
                  [细化后的长图] + 新的一句话 + [新选项]

按钮 ↔ 参数的映射就是统一返回契约里的 `next` 字段（`walkkcal/reply.py`），
和 MCP 的 tool 参数、Skill 的自然语言映射共用同一份定义。

零第三方依赖：纯 urllib 调 Telegram Bot API。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from walkkcal import ALLOWANCE_BASE  # noqa
from walkkcal import config  # noqa
from walkkcal import hints, reply, select  # noqa
from packs import registry  # noqa

# ★ 配置/密钥先加载 —— 下面 `TELEGRAM_BOT_TOKEN` 是**模块级**读取，
#   晚一步就读不到配置里的值。Key 只在这些地方，不写在 .py 里。
config.load()

TG = 'https://api.telegram.org/bot%s/%s'
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
DEFAULT_RADIUS = 1500

# 每个聊天的当前查询条件 —— 按钮回调靠它把「上一轮」和「这一轮」拼起来
STATE = {}

WELCOME = u"""🍚 *walk-kcal* —— 你今天还能吃多少卡，取决于你愿意走多远。

在哪儿、想吃什么，直接跟我说：
  · 「吃什么」            → 给你附近能吃的
  · 「附近有什么快餐」      → 只看快餐
  · 「不想走太远」         → 只看 500 米内
  · 「还有别的吗」         → 换一批

越往下走，额度越高，能吃的越多。
  /demo   看一个例子（内置快照，不需要任何凭证）
  /quota 300  把本餐额度设成 300 卡

⚠ 中餐热量按菜谱推算，平均误差 25-28%；算不出的我不猜。
"""


# ── Telegram API ────────────────────────────────────────────
def api(method, **params):
    if not TOKEN:
        raise RuntimeError('未设置 TELEGRAM_BOT_TOKEN')
    data = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None}).encode('utf-8')
    with urllib.request.urlopen(TG % (TOKEN, method), data=data,
                                timeout=40) as r:
        return json.loads(r.read().decode('utf-8'))


def send_text(chat_id, text, keyboard=None):
    return api('sendMessage', chat_id=chat_id, text=text,
               parse_mode='Markdown',
               reply_markup=(json.dumps(keyboard) if keyboard else None))


def send_photo(chat_id, path, caption='', keyboard=None):
    u"""发图（multipart/form-data，纯标准库实现）+ 可选 inline 按钮"""
    boundary = '----walkkcal%d' % int(time.time())
    with io.open(path, 'rb') as f:
        img = f.read()
    parts = [('--' + boundary + '\r\n'
              'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
              '%s\r\n' % chat_id).encode('utf-8')]
    if caption:
        parts.append(('--' + boundary + '\r\n'
                      'Content-Disposition: form-data; name="caption"\r\n\r\n'
                      '%s\r\n' % caption).encode('utf-8'))
        parts.append(('--' + boundary + '\r\n'
                      'Content-Disposition: form-data; name="parse_mode"\r\n\r\n'
                      'Markdown\r\n').encode('utf-8'))
    if keyboard:
        parts.append(('--' + boundary + '\r\n'
                      'Content-Disposition: form-data; name="reply_markup"\r\n\r\n'
                      '%s\r\n' % json.dumps(keyboard)).encode('utf-8'))
    parts.append(('--' + boundary + '\r\n'
                  'Content-Disposition: form-data; name="photo"; '
                  'filename="walkkcal.png"\r\n'
                  'Content-Type: image/png\r\n\r\n').encode('utf-8'))
    parts.append(img)
    parts.append(('\r\n--' + boundary + '--\r\n').encode('utf-8'))
    body = b''.join(parts)
    req = urllib.request.Request(
        TG % (TOKEN, 'sendPhoto'), data=body,
        headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode('utf-8'))


def answer_callback(cb_id, text=None):
    return api('answerCallbackQuery', callback_query_id=cb_id, text=text)


# ── 按钮 ↔ 参数 ─────────────────────────────────────────────
def encode(params):
    u"""把 next 里的 params 压成 callback_data（Telegram 限 64 字节）

    ★ 注意：`industry: None` 是**显式的「不限」**（「看全部」按钮），
      不能被当成「没这个字段」丢掉 —— 否则点了没反应。实测踩过。
    """
    q = {}
    if 'industry' in params:
        q['i'] = params.get('industry') or ''      # 空串 = 不限
    if params.get('page') is not None:
        q['p'] = params['page']
    if params.get('radius_m'):
        q['r'] = params['radius_m']
    return urllib.parse.urlencode(q)


def decode(s):
    # ★ keep_blank_values=True：否则 `i=` 会被 parse_qs 丢掉
    d = urllib.parse.parse_qs(s or '', keep_blank_values=True)
    out = {}
    if 'i' in d:
        out['industry'] = d['i'][0] or None       # 空串 → 显式不限
    if d.get('p'):
        out['page'] = int(d['p'][0])
    if d.get('r'):
        out['radius_m'] = int(d['r'][0])
    return out


def keyboard_of(nx):
    if not nx:
        return None
    return {'inline_keyboard': [[
        {'text': h['label'], 'callback_data': encode(h['params'])}
        for h in nx]]}


# ── 共享流水线（与 MCP 完全一致）────────────────────────────
def pipeline(quota=None, industry=None, page=0, radius=None):
    r = radius or DEFAULT_RADIUS
    rows = registry.collect(None, None, r)
    q = int(quota) if quota else ALLOWANCE_BASE
    for row in rows:
        row['quota'] = q
        for d in row['dishes']:
            if d['kcal'] is not None:
                d['ok'] = d['kcal'] <= q
    rows = reply.trim_dishes(rows)
    cands = select.to_candidates(rows)
    batch, meta = select.pick(cands, select.DEFAULT_N, page, industry)
    nx = hints.next_actions(meta, rows, radius=r)
    return q, rows, batch, meta, nx


def render_image(industry=None, page=0, radius=None, out=None, quota=None):
    u"""调核心渲染。四个条件都通过环境变量传进去，和文字口径一致。

    ★ 2026-10-15 补 quota：以前只传 industry/page/radius，**额度没传** ——
      用户设了「这顿只吃 350 卡」，文字说 350、图上门印 450（渲染器默认值）。
      这类「两条链口径不一致」是项目的老毛病，见 docs/设计取舍.md。
    """
    out = out or os.path.join(ROOT, 'out', 'bot.png')
    env = dict(os.environ)
    if quota:
        env['WALKKCAL_QUOTA'] = str(quota)
    if industry:
        env['WALKKCAL_FILTER'] = industry
    if page:
        env['WALKKCAL_PAGE'] = str(page)
    if radius:
        env['WALKKCAL_RADIUS'] = str(radius)
    env['DISH_MODE'] = 'C'
    subprocess.run([sys.executable, os.path.join(ROOT, 'walkkcal', 'render.py')],
                   cwd=ROOT, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    src = os.path.join(ROOT, 'out', 'skill_mobile_C.png')
    if os.path.exists(src):
        import shutil
        if os.path.abspath(src) != os.path.abspath(out):
            shutil.copyfile(src, out)
    return out


def state_of(chat_id):
    return STATE.setdefault(chat_id, {'quota': None, 'industry': None,
                                      'page': 0, 'radius': None})


def respond(chat_id, st, with_image=True, note=None):
    u"""按当前状态出一轮结果：图 + 一句话 + 按钮"""
    q, rows, batch, meta, nx = pipeline(
        st.get('quota'), st.get('industry'), st.get('page'), st.get('radius'))
    payload = reply.build(q, batch, meta, nx, pool_size=len(rows))
    text = payload['text']
    if note:
        text = note + u'\n' + text
    kb = keyboard_of(nx)
    # ★ 2026-10-15：出图与否由 `reply.choose_mode()` 统一判（不再只看入口）。
    #   判据：**需要同时比较 ≥2 个对象 → 出图**；单店/单事实 → 纯文字。
    #   图是「给人看、且可转发」的交付物（MCP 规范建议图片块只给 UI，
    #   一张 1080p 图 ≈12,000 token），不该在只有一个对象时也画一张。
    if with_image and payload['meta'].get('mode') == reply.MODE_POSTER:
        try:
            img = render_image(st.get('industry'), st.get('page'),
                               st.get('radius'), quota=st.get('quota'))
            send_photo(chat_id, img, caption=text, keyboard=kb)
            return
        except Exception as e:
            text += u'\n\n（图片生成失败：%s）' % e
    send_text(chat_id, text, keyboard=kb)


# ── 更新处理 ────────────────────────────────────────────────
def handle(update):
    # ① 按钮回调
    cb = update.get('callback_query')
    if cb:
        chat_id = ((cb.get('message') or {}).get('chat') or {}).get('id')
        if not chat_id:
            return
        st = state_of(chat_id)
        p = decode(cb.get('data') or '')
        if 'industry' in p:
            st['industry'] = p['industry']
            st['page'] = 0
        if 'page' in p:
            st['page'] = p['page']
        if 'radius_m' in p:
            st['radius'] = p['radius_m']
            st['page'] = 0
        answer_callback(cb.get('id'))
        respond(chat_id, st)
        return

    # ② 文本消息
    msg = update.get('message') or {}
    chat_id = (msg.get('chat') or {}).get('id')
    text = (msg.get('text') or '').strip()
    if not chat_id:
        return
    st = state_of(chat_id)

    if text.startswith('/start') or text.startswith('/help'):
        send_text(chat_id, WELCOME)
        return
    if text.startswith('/quota'):
        try:
            st['quota'] = int(text.split()[1])
            st['page'] = 0
            send_text(chat_id, u'额度设为 %d 卡，重算中…' % st['quota'])
            respond(chat_id, st)
        except (IndexError, ValueError):
            send_text(chat_id, u'用法：/quota 300')
        return
    if text.startswith('/demo'):
        send_text(chat_id, u'正在算…（walk-kcal 内核）')
        st.update({'quota': None, 'industry': None, 'page': 0, 'radius': None})
        respond(chat_id, st)
        return

    # ③ 自然语言 → 映射到参数（Bot 没有 LLM，只能做关键词兜底）
    if text:
        from walkkcal import filter as _f
        hit = [ind for ind in _f.INDUSTRIES if ind in text]
        if hit:
            st['industry'] = hit[0]
            st['page'] = 0
        if any(k in text for k in (u'换一批', u'还有', u'别的', u'再看看')):
            st['page'] = (st.get('page') or 0) + 1
        elif any(k in text for k in (u'太远', u'就近', u'近一点', u'不想走')):
            st['radius'] = 500
            st['page'] = 0
        elif u'全部' in text or u'不限' in text:
            st['industry'] = None
            st['radius'] = None
            st['page'] = 0
        respond(chat_id, st)


def main():
    if not TOKEN:
        print(u'⚠ 未设置 TELEGRAM_BOT_TOKEN。离线自检（不需要 Token）：')
        q, rows, batch, meta, nx = pipeline()
        payload = reply.build(q, batch, meta, nx, pool_size=len(rows))
        print(u'\n—— 文案 ——')
        print(payload['text'])
        print(u'\n—— 按钮 ——')
        for h in nx:
            print(u'  [%s]  →  callback_data="%s"'
                  % (h['label'], encode(h['params'])))
        print(u'\n（线上会发一张长图，caption 就是上面这段话，按钮在图片下方）')
        return
    print(u'walk-kcal Bot 启动（长轮询）…')
    offset = 0
    while True:
        try:
            r = api('getUpdates', offset=offset, timeout=30)
            for up in r.get('result', []):
                offset = up['update_id'] + 1
                try:
                    handle(up)
                except Exception as e:
                    print(u'处理失败：%s' % e)
        except Exception as e:
            print(u'轮询异常，5 秒后重试：%s' % e)
            time.sleep(5)


if __name__ == '__main__':
    main()
