# -*- coding: utf-8 -*-
u"""抓周边餐饮 POI —— **薄 CLI，不含任何地图服务商的知识**

用法
────
    # ① 只抓、存原始结果（排查用）
    python walkkcal/fetch_pois_at.py <lat> <lon> <半径米> <输出名>

    # ② 直接生成一个「场景目录」，之后就能渲染
    python walkkcal/fetch_pois_at.py <lat> <lon> <半径米> <场景名> --scene

        → data/<场景名>/stores.json   （全量池，画淡点用）
        → data/<场景名>/detail.json   （有推荐菜的，出卡片用）
        → WALKKCAL_DATA=data/<场景名> python walkkcal/render.py

    # ③ 指定长图顶端的「当前位置」标签
    #    （不指定就问地图的逆地理编码，形如 `上海·静安区宝山路街道`）
    python walkkcal/fetch_pois_at.py 31.23 121.38 800 jintie --scene \
           --name "上海·普陀近铁城市广场"

    # ④ 只想要「地址 → 出图」一条命令？
    #    别用这个文件，用 walkkcal/shoot.py（它会调本文件的 build_scene）

它做的事只有四件：
    ① 解析命令行参数
    ② 调 walkkcal/geo.py 的 provider（默认高德，WALKKCAL_GEO 可切换）
    ③ 把结果**按标准结构**写成快照（走 walkkcal/snapshot.py）
    ④ 可选：算真实路网步行距离（慢，但额度才准）

★ 想换成百度/腾讯地图？
   **不要改这个文件。** 去 walkkcal/providers/ 加一个实现，
   然后 WALKKCAL_GEO=你的服务商 即可。详见 docs/扩展文档.md

★ 关于距离
   地图的周边搜索返回的是**直线**距离（高德自己算 haversine，百度给
   detail_info.distance）。而额度公式吃的是**步行**距离 —— 直线会低估。
   所以 `--scene` 默认会调 `walk_route_batch` 校正（可用 --no-route 关掉）。

★★ 2026-10-15：**批量校正**（这是抓取提速的关键）
   原来逐店调 `/v3/direction/walking`，限速器把墙钟钉死在请求数上 ——
   实测 104 家 × 1.2 秒 = **125 秒**，占整个抓取的 93%。

   现在走 provider 的 `walk_route_batch()`（高德 `/v3/distance` 一次收
   100 个起点）→ **104 家 = 2 次请求 ≈ 2.4 秒**。
   单批里算不出的那几项**仍然回退逐店**，所以精度不降级。

   ★ 附带修了一个口径不一致：以前**只校正「有推荐菜」的店**，
     于是 `detail.json` 里混着两种口径（有菜的=路网、没菜的=直线，
     实测同一小区门口两家店一条 458 米、一条 21 米）。
     现在批量便宜，**半径内全部校正**，口径统一。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from walkkcal import geo  # noqa
from walkkcal import snapshot as snap  # noqa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _usage():
    print(__doc__)
    raise SystemExit(2)


def _split_name(args):
    u"""把 `--name <值>` 抽出来 —— 它的值不能混进位置参数里。"""
    rest, disp, i = [], None, 0
    while i < len(args):
        if args[i] == '--name' and i + 1 < len(args):
            disp = args[i + 1]
            i += 2
            continue
        rest.append(args[i])
        i += 1
    return rest, disp


# ══════════════════════════════════════════════════════════════════
# ★★ 唯一的「抓一个场景」实现 —— CLI 与 shoot.py 共用
#    （抽出来是为了避免两套实现漂移：shoot.py 只做「地址→坐标」和调它）
# ══════════════════════════════════════════════════════════════════
def build_scene(prov, lat, lon, radius, scene_dir, name=None,
                do_route=True, on_progress=None):
    u"""抓 POI → 校正路网距离 → 写 stores.json / detail.json 到 scene_dir。

    返回统计 dict：
        {'pool': 全量家数, 'cardable': 半径内家数, 'route_ok': 拿到路网的家数,
         'route_fallback': 回退逐店的家数, 'label': 地名,
         'seconds': {'nearby': 秒, 'route': 秒, 'total': 秒},
         'requests': 本场景发出的请求数}

    ★ `requests` 用限速器的计数器差额算 —— 它是「有没有变快」的唯一真相。
    """
    t0 = time.time()
    req0 = getattr(getattr(prov, 'limiter', None), 'count', 0)

    rows = prov.nearby_dining(lon, lat, radius)
    rows.sort(key=lambda x: x['distance_m'])
    t_nearby = time.time()

    # ── 路网校正（★ 半径内**全部**校正，不再只挑有推荐菜的）──
    stats = {'route_ok': 0, 'route_fallback': 0}
    if do_route and rows:
        dests = [(r['lon'], r['lat']) for r in rows]
        batch_fn = getattr(prov, 'walk_route_batch', None)
        res = batch_fn((lon, lat), dests) if batch_fn else [None] * len(dests)

        for i, r in enumerate(rows):
            w = res[i] if i < len(res) else None
            if not (w and w.get('distance_m')):
                # ★ 批量没给出来 → 回退逐店（保证精度不降级）
                stats['route_fallback'] += 1
                try:
                    w = prov.walk_route((lon, lat), (r['lon'], r['lat']))
                except Exception:
                    w = None
            if w and w.get('distance_m'):
                r['distance_m'] = int(w['distance_m'])
                stats['route_ok'] += 1
            if on_progress and (i + 1) % 50 == 0:
                on_progress(i + 1, len(rows))
        rows.sort(key=lambda x: x['distance_m'])
    t_route = time.time()

    # ★ 地名：`--name` > provider 逆地理编码 > 「纬度,经度（服务商）」
    label = name
    if not label:
        try:
            label = prov.describe_point(lon, lat)
        except Exception:
            label = None
    if not label:
        label = u'%.4f,%.4f（%s）' % (lat, lon, prov.name)
    origin = {'name': label, 'lon': lon, 'lat': lat}

    in_radius = [r for r in rows if (r.get('distance_m') or 0) <= radius]
    # 池子：全量（淡点层要的是「附近到底有多少家」）
    snap.write(os.path.join(scene_dir, 'stores.json'), origin, rows,
               extra={'provider': prov.name, 'radius': radius})
    # 详情：半径内的（能出卡片的都在这儿）
    snap.write(os.path.join(scene_dir, 'detail.json'), origin, in_radius,
               extra={'provider': prov.name, 'radius': radius})

    req1 = getattr(getattr(prov, 'limiter', None), 'count', 0)
    return {
        'pool': len(rows),
        'cardable': len(in_radius),
        'route_ok': stats['route_ok'],
        'route_fallback': stats['route_fallback'],
        'label': label,
        'requests': req1 - req0,
        'seconds': {'nearby': t_nearby - t0, 'route': t_route - t_nearby,
                    'total': time.time() - t0},
    }


def main():
    args, disp_name = _split_name(sys.argv[1:])
    argv = [a for a in args if not a.startswith('--')]
    flags = set(a for a in args if a.startswith('--'))
    if len(argv) < 2:
        _usage()

    lat = float(argv[0])
    lon = float(argv[1])
    radius = int(argv[2]) if len(argv) > 2 else 1500
    name = argv[3] if len(argv) > 3 else 'pois'

    prov = geo.get()

    if '--scene' not in flags:
        # ── 只抓（排查用）：不校正、不建场景目录 ──
        rows = prov.nearby_dining(lon, lat, radius)
        rows.sort(key=lambda x: x['distance_m'])
        out_dir = os.path.join(ROOT, 'datasets')
        path = os.path.join(out_dir, name + '.json')
        snap.write(path, {'name': u'%.4f,%.4f' % (lat, lon), 'lon': lon,
                          'lat': lat}, rows,
                   extra={'radius': radius, 'provider': prov.name})
        print(u'%s · 中心 %.6f,%.6f · 半径 %d米 · 共 %d 家餐饮'
              % (prov.name, lat, lon, radius, len(rows)))
        print(u'已存 %s\n' % path)
        _print_table(rows[:30])
        return

    # ── 场景目录模式 ────────────────────────────────────────
    scene = os.path.join(ROOT, 'data', name)
    do_route = '--no-route' not in flags
    if do_route:
        _iv = getattr(prov, 'min_interval', None)
        print(u'校正步行距离（批量接口，失败项回退逐店）…')
        if _iv:
            print(u'   限速：%.1f 秒/请求（批量后请求数从「家数」降到「批数」）' % _iv)
    else:
        print(u'（未校正，用的是直线距离 —— 额度会偏小，仅够看形态）')

    st = build_scene(prov, lat, lon, radius, scene, name=disp_name,
                     do_route=do_route,
                     on_progress=lambda i, n: None)
    print(u'地名：%s' % st['label'])
    print(u'   路网距离 %d/%d 家（回退逐店 %d 家）· 实发 %d 次请求 · 用时 %.1f 秒'
          % (st['route_ok'], st['pool'], st['route_fallback'],
             st['requests'], st['seconds']['total']))

    print(u'\n场景已生成 → %s' % scene)
    print(u'  全量池 %d 家 ｜ 半径内 %d 家' % (st['pool'], st['cardable']))
    print(u'  渲染：WALKKCAL_DATA=data/%s python walkkcal/render.py' % name)

    rows = snap.read_detail(os.path.join(scene, 'detail.json'))
    _print_table((rows['mcd'] + rows['local'])[:20])


def _print_table(rows):
    print(u'%-4s %-30s %7s  %-12s %s'
          % (u'#', u'门店', u'距离', u'业态', u'推荐菜'))
    print('-' * 96)
    for i, r in enumerate(rows, 1):
        walk = r.get('walk') or r.get('distance_m') or 0
        print(u'%-4d %-30s %5dm  %-12s %s'
              % (i, (r['name'] or u'')[:28], walk,
                 u'/'.join(r.get('industries') or [])[:12],
                 u','.join(r.get('dish_hints') or [])[:34]))


if __name__ == '__main__':
    main()
