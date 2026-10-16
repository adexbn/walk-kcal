# -*- coding: utf-8 -*-
u"""shoot —— 一步式入口：**地址 → 一张图**

用法
────
    python walkkcal/shoot.py "上海市黄浦区南京东路100号"
    python walkkcal/shoot.py "苏州市姑苏区观前街" --city 苏州
    python walkkcal/shoot.py "上海人民广场" --radius 1000 --out out/rmgc.png
    python walkkcal/shoot.py                      # ★ 不给地址 → 自动定位（见 walkkcal/locate.py）

可选参数
────────
    --radius N       扫描半径（米），默认 800
    --quota N        本餐额度（卡），默认 450 —— 用户说「这顿只吃 350 卡」时传它
    --out PATH       输出路径；默认 out/<地址slug>.png
                     （缺 Pillow 时降级为 SVG，扩展名自动换成 .svg）
    --name TEXT      长图顶端「当前位置」标签；默认用逆地理编码
    --city TEXT      地址所属城市（**跨城市消歧**，如「苏州」）
    --lat N --lon N  **直接给坐标**，跳过地址解析（优先级高于地址）
    --dish-mode X    菜品配色方案，默认 C（正式版）
    --keep-scene     保留场景快照到 internal/scenes/<slug>/（排查用）
    --no-route       跳过路网距离校正（快，但额度偏小）

位置从哪来（三档降级，见 `walkkcal/locate.py`）
────────────────────────────────────────────────
    ① 地址 / 显式坐标 —— 最可靠，用户或宿主 Agent 直接给
    ② 自动定位       —— 不给地址时尝试（Windows：WinRT = WiFi 指纹，实测精度 150~500 米）
    ③ 都不行         —— 打印话术后退出（码 2），由 Agent 去问用户「你在哪」

退出码
──────
    0 成功（含「缺 Pillow → 只出 SVG」的降级成功，见下）
    2 缺少地图 Key（或 Key 无权限）／没能确定位置
    3 地址解析不到
    4 附近没有餐饮
    5 出了图、但构建期自检报错（PNG 保留，供排查）
    6 渲染没有产物（PNG 与 SVG 都没有）

产物降级
────────
★ 渲染分两步：① 完整写出 SVG（纯标准库）→ ② 用 Pillow + pillow_lunasvg
  把 SVG 转成 PNG。第 ② 步是可选的。
  没装这两个包时**不算失败** —— SVG 保留下来（浏览器无损可看），
  退出码仍为 0，`WALKKCAL_TIMING` 里 `"artifact": "svg"`。
  PNG 与 SVG 的差异只在位图化，业务内容（店名/菜名/热量）完全一致。

设计说明
────────
★ 为什么要有这个文件
    以前出图必须人工两步 —— 先 `fetch_pois_at.py ... --scene`，
    再 `WALKKCAL_DATA=... python walkkcal/render.py` —— 而且 render.py
    的输出名是硬编码的 `skill_mobile_C.png`，还得再手工改名。
    对「用户报一个地址、我要出一张图」来说，这是 2~3 次工具往返，
    每次都带一轮模型思考。本文件把它压成**一条命令**。

★ 它自己不做抓取逻辑 —— 抓取全在 `fetch_pois_at.build_scene()` 里，
  本文件只负责「地址→坐标」「起子进程渲染」「改名」「报时」。
  这样 CLI 与 shoot 共用同一段实现，不会漂移。

★ 渲染为什么用 subprocess 而不是 import：
  `render.py` 是脚本式结构（无 main()，import 即执行完整渲染），
  子进程能隔离副作用、完整捕获它的日志（含构建期自检），还能设超时。

★ 隐私/整洁：场景快照默认落**临时目录**并在结束时删除，
  绝不写 `data/`（那里是已交付的场景快照）。要留证据就 --keep-scene。

★ 末尾打印 `WALKKCAL_TIMING {json}` —— 机器可读的分阶段耗时，
  用来回答「当作 Skill 用到底要等多久」。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from walkkcal import config  # noqa: E402
from walkkcal import fetch_pois_at as fetch  # noqa: E402
from walkkcal import geo  # noqa: E402
from walkkcal import locate  # noqa: E402

# 仓库外的「内部工作区」（测试产物、场景留档都放这儿，不污染仓库）
INTERNAL = os.path.join(os.path.dirname(ROOT), 'internal')

RENDER_TIMEOUT = 180


def _slug(address, limit=28):
    u"""地址 → 文件名 slug：保留中英文数字，其余折成 `-`，附 6 位哈希防重名。"""
    s = re.sub(u'[^0-9A-Za-z\u4e00-\u9fff]+', '-', address or u'').strip('-')
    h = hashlib.md5((address or u'').encode('utf-8')).hexdigest()[:6]
    return u'%s_%s' % (s[:limit] or u'place', h)


def _parse_argv(argv):
    u"""极简解析：第一个非 `--` 参数是地址，其余 `--k v` / 开关。"""
    address, opts, i = None, {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith('--'):
            k = a[2:]
            if k in ('keep-scene', 'no-route', 'help', 'h'):
                opts[k] = True
                i += 1
                continue
            if i + 1 >= len(argv):
                print(u'⛔ 参数 %s 缺值' % a)
                raise SystemExit(2)
            opts[k] = argv[i + 1]
            i += 2
            continue
        if address is None:
            address = a
        i += 1
    return address, opts


def _pick_artifact(out_tmp, sfx):
    u"""在渲染临时目录里挑产物：**PNG 优先，退而求其次用 SVG**。

    2026-10-16：渲染器缺 Pillow / pillow_lunasvg 时会优雅降级为「只出 SVG」。
    此时 SVG 就是合法产物（浏览器无损可看），不该当成渲染失败。

    返回 `(绝对路径, 是否降级)`；两者都没有则 `(None, False)`。
    """
    png = os.path.join(out_tmp, 'skill_mobile%s.png' % sfx)
    if os.path.isfile(png):
        return png, False
    svg = os.path.join(out_tmp, 'skill_mobile%s.svg' % sfx)
    if os.path.isfile(svg):
        return svg, True
    return None, False


def _mcd_count(scene_dir):
    u"""场景快照里「名字像麦当劳」的门店数 —— 说明**图上的麦当劳来自哪**。

    ★ 2026-10-16：图链的麦当劳来自**地图 POI 的名称匹配**
      （`snapshot.write()` 按店名判 `is_mcd`），**不是**麦当劳 MCP。
      而 MCP 实时只作用于**文字链**（`packs/`）。
      这两件事以前在报告里完全看不出来，容易被说成「官方实时门店」。
    读不到快照 → 返回 `None`（不编数字）。
    """
    try:
        from walkkcal import snapshot as snap
        doc = snap.read_stores(os.path.join(scene_dir, 'stores.json'))
        return len(doc.get('mcd') or [])
    except Exception:
        return None


def main():
    address, opts = _parse_argv(sys.argv[1:])
    if opts.get('help') or opts.get('h'):
        print(__doc__)
        return 0

    # ── 配置/密钥（★ Key 不写在 .py 里，见 walkkcal/config.py）─────────
    #    环境变量 > WALKKCAL_CONFIG > <仓库>/config.local.json > internal/config.local.json
    #    load() 会把值注入 os.environ —— 渲染子进程才拿得到（它只继承环境）。
    config.load()

    # ── 位置从哪来（三档降级，见 walkkcal/locate.py）────────────────
    #    ① 显式坐标（--lat/--lon 或 WALKKCAL_LAT/LON）
    #    ② 没给地址 → 自动定位（Windows：WinRT = WiFi 指纹）
    #    ③ 都不行 → 打印话术并退 2，由 Agent 去问用户「你在哪」
    t_locate0 = time.time()
    pre_spot = None
    loc = None
    if opts.get('lat') is not None and opts.get('lon') is not None:
        loc = locate.from_explicit(opts.get('lat'), opts.get('lon'))
        if loc is None:
            print(u'⛔ --lat/--lon 不是合法坐标。')
            return 2
    elif not address:
        loc = locate.locate()

    if loc is not None:
        if not loc['ok']:
            print(u'📍 没能确定你在哪（%s）。' % loc['reason'])
            if loc.get('hint'):
                print(u'   ' + loc['hint'])
            print(u'\n' + __doc__)
            return 2
        pre_spot = {'lat': loc['lat'], 'lon': loc['lon']}
        address = address or (u'%.5f,%.5f' % (loc['lat'], loc['lon']))
        print(u'📍 位置：%s（来源 %s%s）'
              % (address, loc['source'],
                 u'，精度约 %d 米' % loc['accuracy_m'] if loc['accuracy_m'] else u''))

    if not address:
        print(__doc__)
        return 2
    t_locate = time.time() - t_locate0

    t_user = time.time()
    radius = int(opts.get('radius') or 800)
    quota = int(opts.get('quota') or 0) or None
    city = opts.get('city')
    dish_mode = (opts.get('dish-mode') or 'C').upper()
    keep_scene = bool(opts.get('keep-scene'))
    do_route = not opts.get('no-route')
    slug = _slug(address)

    # ── ① 地址 → 坐标 ───────────────────────────────────────
    prov = geo.get()
    spot = pre_spot
    if spot is None:
        geo_fn = getattr(prov, 'geocode', None)
        if geo_fn is None:
            print(u'⛔ 当前地图服务商（%s）不支持地址解析。\n'
                  u'   请先用坐标入口：python walkkcal/fetch_pois_at.py <lat> <lon> ...'
                  % prov.name)
            return 3
        try:
            spot = geo_fn(address, city=city)
        except SystemExit as e:                  # 缺 Key / Key 无权限
            print(u'%s' % config.redact(e))
            return 2
        except Exception as e:
            print(u'⛔ 地址解析失败：%s' % config.redact(e))
            return 3
        if not spot:
            print(u'⛔ 解析不到这个地址：%s\n'
                  u'   建议：① 写全「市 + 区 + 路 + 门牌号」；'
                  u'② 跨城市加 --city（如 --city 苏州）' % address)
            return 3
    else:
        # 坐标已由定位得到 —— 只需把坐标说成人话，让长图顶端的标签不显示经纬度
        try:
            _desc = getattr(prov, 'describe_point', None)
            if _desc:
                _nm = _desc(spot['lon'], spot['lat'])
                if _nm:
                    address = _nm
        except Exception:
            pass
    t_geocode = time.time()

    # ── ② 抓 POI（含批量路网校正）→ 写场景快照 ──────────────
    tmp_scene = None
    if keep_scene:
        scene_dir = os.path.join(INTERNAL, 'scenes', slug)
        if not os.path.isdir(scene_dir):
            os.makedirs(scene_dir)
    else:
        tmp_scene = tempfile.mkdtemp(prefix='walkkcal_scene_')
        scene_dir = tmp_scene

    log = u''
    target = None
    try:
        try:
            st = fetch.build_scene(prov, spot['lat'], spot['lon'], radius,
                                   scene_dir, name=opts.get('name'),
                                   do_route=do_route)
        except SystemExit as e:
            print(u'%s' % config.redact(e))
            return 2
        t_crawl = time.time()

        if st['pool'] == 0:
            print(u'⛔ %s 附近 %d 米内一家餐饮都没有' % (address, radius))
            return 4

        # ── ③ 渲染（子进程，输出到临时目录再改名）────────────
        out_tmp = tempfile.mkdtemp(prefix='walkkcal_out_')
        env = dict(os.environ)
        env['WALKKCAL_DATA'] = scene_dir
        env['WALKKCAL_OUT'] = out_tmp
        env['DISH_MODE'] = dish_mode
        if quota:
            env['WALKKCAL_QUOTA'] = str(quota)
        try:
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, 'walkkcal', 'render.py')],
                cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=RENDER_TIMEOUT)
            log = (proc.stdout or b'').decode('utf-8', 'ignore')
        except subprocess.TimeoutExpired:
            print(u'⛔ 渲染超时（%d 秒）' % RENDER_TIMEOUT)
            return 6
        t_render = time.time()

        sfx = '' if dish_mode == 'B' else '_' + dish_mode
        src, degraded = _pick_artifact(out_tmp, sfx)
        if src is None:
            print(config.redact(log[-1800:]))
            print(u'⛔ 渲染没有产出任何产物（PNG 与 SVG 都缺）')
            return 6

        target = opts.get('out') or os.path.join(ROOT, 'out', slug + '.png')
        if not os.path.isabs(target):
            target = os.path.join(ROOT, target)
        if degraded:
            # ★ 降级：只拿到 SVG → 目标名同步换成 .svg
            #   （否则会出现「文件叫 .png，内容却是 SVG」，下游解析器会炸）
            target = os.path.splitext(target)[0] + '.svg'
        _d = os.path.dirname(target)
        if _d and not os.path.isdir(_d):
            os.makedirs(_d)
        os.replace(src, target)
        shutil.rmtree(out_tmp, ignore_errors=True)

        # ── ④ 报告 ──────────────────────────────────────────
        bad = u'构建期自检发现' in log
        m = re.search(u'竖屏图 (\\d+)x(\\d+)', log)
        size = u'%s×%s' % (m.group(1), m.group(2)) if m else u'?'
        t_end = time.time()

        print(u'📍 %s' % (spot.get('formatted') or address))
        print(u'   坐标 %.6f,%.6f（%s）' % (spot['lon'], spot['lat'],
                                            spot.get('level') or u'-'))
        print(u'   场景 %s' % scene_dir)
        print(u'   附近 %d 家餐饮 ｜ 半径 %d 米内 %d 家'
              % (st['pool'], radius, st['cardable']))
        _mcd_n = _mcd_count(scene_dir)
        if _mcd_n:
            print(u'   麦当劳 %d 家 · 图上这些来自**地图 POI 名称匹配**'
                  u'（MCP 实时只作用于文字链，不影响本图）' % _mcd_n)
        print(u'   路网距离 %d/%d 家（回退逐店 %d 家）· 实发 %d 次请求'
              % (st['route_ok'], st['pool'], st['route_fallback'],
                 st['requests']))
        if degraded:
            print(u'   出图 %s（%s ｜ SVG，浏览器直接可看）' % (target, size))
            print(u'   ⓘ 未转 PNG：缺 Pillow / pillow_lunasvg。'
                  u'内容与 PNG 完全一致，只是矢量而非位图。')
            print(u'     想要 PNG 就装（可选）：'
                  u'pip install -r requirements.txt')
        else:
            print(u'   出图 %s（%s）' % (target, size))
        if bad:
            for line in log.splitlines():
                if u'构建期自检' in line or line.strip().startswith(u'·'):
                    print(u'   %s' % config.redact(line.strip()))
        print(u'\nWALKKCAL_TIMING %s'
              % json.dumps({
                  'address': address,
                  'locate': round(t_locate, 2),
                  'geocode': round(t_geocode - t_user, 2),
                  'crawl': round(t_crawl - t_geocode, 2),
                  'route_ok': st['route_ok'],
                  'route_fallback': st['route_fallback'],
                  'requests': st['requests'],
                  'pool': st['pool'],
                  'cardable': st['cardable'],
                  'render': round(t_render - t_crawl, 2),
                  'total': round(t_end - t_locate0, 2),
                  'image': target,
                  'artifact': 'svg' if degraded else 'png',
                  'selfcheck': 'FAIL' if bad else 'OK',
              }, ensure_ascii=False))
        return 5 if bad else 0

    finally:
        if tmp_scene:
            shutil.rmtree(tmp_scene, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
