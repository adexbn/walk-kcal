# -*- coding: utf-8 -*-
u"""locate —— 「人在哪」的三档降级定位

档位（按可靠性从高到低）
────────────────────────
  ① 显式坐标    `WALKKCAL_LAT` / `WALKKCAL_LON` 环境变量，或调用方直接传
                —— 宿主 Agent / 用户给的最可靠，优先级最高
  ② 自动定位    Windows：WinRT Geolocator（`Windows.Devices.Geolocation`）
                ★ 在无 GPS 芯片的笔记本上，它的**底层就是 WiFi 指纹**
                  （实测 `PositionSource = WiFi`，精度 159~500 米）
                ★ **必须校验来源**：只接受 WiFi / Satellite 且 `IsRemoteSource=false`。
                  否则说明是 IP/机房源 —— 实测开代理时会指到**美国机房**，
                  这种坐标宁可不要，也不能拿去算「附近 800 米」。
  ③ 拿不到      返回 `ok=False` + `hint`，由上层去要授权 / 问用户

为什么不用 netsh 扫 BSSID 自己反查
──────────────────────────────────
  `netsh wlan show networks mode=bssid` 只能拿到周边热点的 MAC（BSSID），
  而 BSSID → 坐标 需要**地理定位数据库**，Google / 高德都没有开放公开接口。
  Windows 的 WinRT Geolocator 已经内置了这套 WiFi 指纹库 ——
  直接用它，既等价又不用自己维护数据库。

返回结构（统一）
────────────────
  {'ok': bool, 'lat': float|None, 'lon': float|None, 'accuracy_m': int|None,
   'source': str|None,          # 'explicit' | 'WiFi' | 'Satellite'
   'reason': str|None,          # 失败原因码（机器可读）
   'hint': str|None}            # 给 Agent / 用户的一句话（人可读）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CS_SRC = os.path.join(ROOT, 'walkkcal', 'tools', 'winrt_locate.cs')

ENV_LAT = 'WALKKCAL_LAT'
ENV_LON = 'WALKKCAL_LON'

# ★ 只信这两种来源。IPAddress / Default / Unknown 一律弃权 —— 它们可能是代理/机房。
TRUSTED_SOURCES = ('wifi', 'satellite')

_LOCATE_HINT_BASE = (
    u'请直接告诉我你在哪（越具体越好）：\n'
    u'     例：上海市浦东新区凌河路618弄   或   上海市普陀区富平路真华路路口\n'
    u'     跨城市记得带城市名（如「苏州市姑苏区观前街」）。')

_HINT_PERM = (
    u'自动定位失败：位置权限未开启（或系统定位服务关闭）。\n'
    u'     Windows 开启方法：设置 → 隐私和安全性 → 位置 →\n'
    u'       ① 打开「定位服务」；② 打开「允许桌面应用访问你的位置」。\n'
    u'     然后重试一次；也可以直接告诉我你在哪。')


# ─────────────────────────────────────────────────────────────
# ① 显式坐标
# ─────────────────────────────────────────────────────────────
def from_explicit(lat=None, lon=None):
    u"""显式坐标：参数优先，其次环境变量。拿不到返回 None。"""
    if lat is None:
        lat = os.environ.get(ENV_LAT)
    if lon is None:
        lon = os.environ.get(ENV_LON)
    if lat is None or lon is None:
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return _ok(lat, lon, None, 'explicit')


# ─────────────────────────────────────────────────────────────
# ② 自动定位（Windows · WinRT）
# ─────────────────────────────────────────────────────────────
def _csc_path():
    u"""找系统自带的 .NET Framework C# 编译器。找不到返回 None。"""
    windir = os.environ.get('WINDIR') or r'C:\Windows'
    for sub in ('Framework64', 'Framework'):
        p = os.path.join(windir, 'Microsoft.NET', sub, 'v4.0.30319', 'csc.exe')
        if os.path.isfile(p):
            return p
    return None


def _exe_path():
    u"""编译产物的缓存位置（不进仓库）。优先 out/_tools/，不可写则退回临时目录。"""
    cand = os.path.join(ROOT, 'out', '_tools')
    try:
        if not os.path.isdir(cand):
            os.makedirs(cand)
        return os.path.join(cand, 'winrt_locate.exe')
    except OSError:
        d = os.path.join(tempfile.gettempdir(), 'walkkcal_tools')
        if not os.path.isdir(d):
            os.makedirs(d)
        return os.path.join(d, 'winrt_locate.exe')


def _build_exe(csc, exe):
    u"""编译 C# 源码 → exe。返回 (ok, 错误信息)。已是最新则跳过编译。"""
    try:
        if os.path.isfile(exe) and os.path.getmtime(exe) >= os.path.getmtime(CS_SRC):
            return True, None
    except OSError:
        pass
    fw = os.path.dirname(csc)
    cmd = [csc, '/nologo', '/target:exe',
           '/r:' + os.path.join(fw, 'System.Runtime.WindowsRuntime.dll'),
           '/r:' + os.path.join(fw, 'System.dll'),
           '/out:' + exe, CS_SRC]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return False, u'调用编译器失败：%s' % e
    if p.returncode != 0:
        msg = (p.stdout or b'').decode('gbk', 'ignore')[:400]
        return False, u'编译失败：%s' % msg
    return True, None


def from_winrt(timeout_ms=8000):
    u"""WinRT 自动定位（WiFi 指纹）。任何一步不满足都返回 ok=False 的结构。"""
    if not sys.platform.startswith('win'):
        return _fail('unsupported-platform',
                     u'当前系统（%s）不支持自动定位。\n     ' % sys.platform +
                     _LOCATE_HINT_BASE)

    csc = _csc_path()
    if not csc:
        return _fail('no-csc',
                     u'本机没找到 .NET Framework 编译器（csc.exe），无法自动定位。\n     ' +
                     _LOCATE_HINT_BASE)

    exe = _exe_path()
    built, err = _build_exe(csc, exe)
    if not built:
        return _fail('build-failed', u'%s\n     ' % err + _LOCATE_HINT_BASE)

    try:
        p = subprocess.run([exe, str(int(timeout_ms))],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=max(15, int(timeout_ms) / 1000.0 + 10))
    except subprocess.TimeoutExpired:
        return _fail('timeout', _HINT_PERM)
    except OSError as e:
        return _fail('run-failed', u'定位程序无法运行：%s\n     ' % e + _LOCATE_HINT_BASE)

    raw = (p.stdout or b'').decode('utf-8', 'ignore').strip()
    line = raw.splitlines()[-1] if raw else ''
    try:
        d = json.loads(line)
    except ValueError:
        return _fail('bad-json', u'定位程序输出无法解析（%s）\n     ' % line[:120] +
                     _LOCATE_HINT_BASE)

    if not d.get('ok'):
        e = str(d.get('error') or '?')
        if 'timeout' in e:
            return _fail('timeout', _HINT_PERM)
        return _fail('winrt:' + e, u'自动定位失败（%s）\n     ' % e + _LOCATE_HINT_BASE)

    # ── ★ 来源校验：这一步决定「会不会把美国机房当成你家楼下」 ──
    src = (d.get('source') or '').lower()
    if d.get('remote'):
        return _fail(
            'untrusted-remote',
            u'自动定位返回的是**远端源**坐标（通常是开着代理 / VPN 导致的机房位置，'
            u'实测会指到美国）。\n     已弃权，不敢拿它算「附近 800 米」。\n     ' +
            _LOCATE_HINT_BASE)
    if src not in TRUSTED_SOURCES:
        return _fail(
            'untrusted-source:' + (src or 'unknown'),
            u'自动定位的来源是「%s」，不是 WiFi/GPS 这类真实定位 —— 不可信。\n     '
            % (src or 'unknown') + _LOCATE_HINT_BASE)

    acc = d.get('accuracy_m')
    try:
        acc = int(float(acc)) if acc is not None else None
    except (TypeError, ValueError):
        acc = None
    return _ok(float(d['lat']), float(d['lon']), acc,
               'WiFi' if src == 'wifi' else 'Satellite')


# ─────────────────────────────────────────────────────────────
# ③ 编排
# ─────────────────────────────────────────────────────────────
def locate(lat=None, lon=None, timeout_ms=8000):
    u"""三档降级定位。永远返回结构化的 dict，不抛异常。"""
    r = from_explicit(lat, lon)
    if r:
        return r
    return from_winrt(timeout_ms)


# ── 内部构造 ────────────────────────────────────────────────
def _ok(lat, lon, accuracy_m, source):
    return {'ok': True, 'lat': lat, 'lon': lon, 'accuracy_m': accuracy_m,
            'source': source, 'reason': None, 'hint': None}


def _fail(reason, hint):
    return {'ok': False, 'lat': None, 'lon': None, 'accuracy_m': None,
            'source': None, 'reason': reason, 'hint': hint}


if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    r = locate()
    print(json.dumps(r, ensure_ascii=False, indent=2))
